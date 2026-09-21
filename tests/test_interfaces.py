import json
from pathlib import Path

import pytest
from mcp.client import Client
from pydantic import BaseModel
from typer.testing import CliRunner

from wt_advisor.cli.main import create_app
from wt_advisor.mcp.server import create_server
from wt_advisor.services.advisor import AdvisorService


class InspectionResult(BaseModel):
    provider: str
    path: str
    valid: bool


class StatusResult(BaseModel):
    schema_revision: str
    components: dict[str, str]


class MilestoneTwoInterfaceService:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def reconcile_profile(
        self,
        profile_id: str,
        *,
        apply: bool = False,
        expected_revision: str | None = None,
    ) -> dict[str, object]:
        self.calls.append(("reconcile", profile_id, apply, expected_revision))
        return {"profile_id": profile_id, "applied": apply}

    def data_status(self) -> StatusResult:
        return StatusResult(
            schema_revision="0003",
            components={"capabilities": "available"},
        )

    def inspect_statistics(
        self, path: Path, *, provider: str, purpose: object
    ) -> InspectionResult:
        self.calls.append(("inspect", path, provider, str(purpose)))
        return InspectionResult(provider=provider, path=str(path), valid=True)

    def import_statistics(
        self, path: Path, *, provider: str, purpose: object
    ) -> dict[str, object]:
        self.calls.append(("import", path, provider, str(purpose)))
        return {"provider": provider, "path": str(path), "imported": True}


def test_cli_generates_structured_lineups() -> None:
    cli = create_app(AdvisorService.from_acceptance_fixture())
    result = CliRunner().invoke(
        cli,
        ["lineup", "generate", "--profile", "acceptance", "--top", "2", "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["recommended"]
    assert payload["analysis_id"]


def test_cli_data_status_exposes_snapshot_and_schema_revisions() -> None:
    cli = create_app(AdvisorService.from_acceptance_fixture())
    result = CliRunner().invoke(cli, ["data", "status", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["schema_revision"]
    assert payload["snapshots"]
    assert payload["active"]["battle_rating_snapshot_id"]
    assert payload["active"]["bundle_id"]
    assert payload["active"]["statistics_period"]["end"]


@pytest.mark.asyncio
async def test_mcp_lists_all_milestone_tools_and_returns_structured_data() -> None:
    server = create_server(AdvisorService.from_acceptance_fixture())

    async with Client(server) as client:
        tools = await client.list_tools()
        tool_names = {tool.name for tool in tools.tools}
        result = await client.call_tool("get_data_status", {})

    assert {
        "get_data_status",
        "list_vehicles",
        "get_vehicle",
        "get_vehicle_statistics",
        "get_user_progress",
        "set_user_vehicle_status",
        "analyze_lineup",
        "compare_lineups",
        "generate_lineups",
        "suggest_lineup_additions",
        "evaluate_next_unlocks",
    }.issubset(tool_names)
    assert {
        "list_presets",
        "create_preset",
        "update_preset",
        "delete_preset",
        "get_advisor_context",
        "update_advisor_context",
        "evaluate_and_store",
        "get_stored_evaluation",
        "reevaluate_stored_evaluation",
    }.issubset(tool_names)
    assert result.structured_content is not None
    assert result.structured_content["schema_revision"]


@pytest.mark.asyncio
async def test_mcp_br_inputs_document_integer_tenths_with_examples() -> None:
    server = create_server(AdvisorService.from_acceptance_fixture())

    async with Client(server) as client:
        tools = await client.list_tools()

    by_name = {tool.name: tool for tool in tools.tools}
    for tool_name, parameter in (
        ("list_vehicles", "max_br"),
        ("generate_lineups", "target_br"),
    ):
        schema = by_name[tool_name].input_schema["properties"][parameter]
        schema_text = json.dumps(schema)
        assert "integer tenths" in schema_text
        assert all(example in schema_text for example in ("10", "23", "27", "40"))


@pytest.mark.asyncio
async def test_mcp_and_service_generation_have_same_analysis_id() -> None:
    service = AdvisorService.from_acceptance_fixture()
    expected = service.generate_lineups(profile_id="acceptance", top_n=2)
    server = create_server(service)

    async with Client(server) as client:
        result = await client.call_tool(
            "generate_lineups", {"profile_id": "acceptance", "top_n": 2}
        )

    assert result.structured_content is not None
    assert result.structured_content["analysis_id"] == expected.analysis_id


@pytest.mark.asyncio
async def test_targeted_generation_reproduction_is_deterministic_and_cli_mcp_identical() -> None:
    service = AdvisorService.from_acceptance_fixture()
    service._battle_ratings["us_m24"] = 40
    arguments = {
        "profile_id": "acceptance",
        "target_br": 40,
        "top_n": 3,
        "hypothetical_owned": ["us_m24"],
    }
    first = service.generate_lineups(
        profile_id="acceptance",
        target_br=40,
        top_n=3,
        hypothetical_owned=frozenset({"us_m24"}),
    )
    second = service.generate_lineups(
        profile_id="acceptance",
        target_br=40,
        top_n=3,
        hypothetical_owned=frozenset({"us_m24"}),
    )
    cli_result = CliRunner().invoke(
        create_app(service),
        [
            "lineup",
            "generate",
            "--profile",
            "acceptance",
            "--br",
            "4.0",
            "--top",
            "3",
            "--hypothetical-owned",
            "us_m24",
            "--json",
        ],
    )

    async with Client(create_server(service)) as client:
        mcp_result = await client.call_tool("generate_lineups", arguments)

    assert cli_result.exit_code == 0, cli_result.output
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.analysis_id == second.analysis_id
    assert json.loads(cli_result.stdout) == first.model_dump(mode="json")
    assert mcp_result.structured_content == first.model_dump(mode="json")


@pytest.mark.asyncio
async def test_cli_and_mcp_return_identical_data_status_dto() -> None:
    service = AdvisorService.from_acceptance_fixture()
    cli_result = CliRunner().invoke(create_app(service), ["data", "status", "--json"])
    server = create_server(service)

    async with Client(server) as client:
        mcp_result = await client.call_tool("get_data_status", {})

    assert cli_result.exit_code == 0, cli_result.output
    assert mcp_result.structured_content is not None
    assert json.loads(cli_result.stdout) == mcp_result.structured_content


@pytest.mark.asyncio
async def test_mcp_serializes_typed_data_status_dto() -> None:
    service = MilestoneTwoInterfaceService()

    async with Client(create_server(service)) as client:  # type: ignore[arg-type]
        result = await client.call_tool("get_data_status", {})

    assert result.structured_content == {
        "schema_revision": "0003",
        "components": {"capabilities": "available"},
    }


def test_cli_profile_reconcile_defaults_to_dry_run_and_can_apply() -> None:
    service = MilestoneTwoInterfaceService()
    cli = create_app(service)  # type: ignore[arg-type]

    dry_run = CliRunner().invoke(
        cli,
        ["profile", "reconcile", "--profile", "pilot", "--dry-run", "--json"],
    )
    applied = CliRunner().invoke(
        cli,
        [
            "profile",
            "reconcile",
            "--profile",
            "pilot",
            "--apply",
            "--expected-revision",
            "rev-7",
            "--json",
        ],
    )

    assert dry_run.exit_code == 0, dry_run.output
    assert json.loads(dry_run.stdout)["applied"] is False
    assert applied.exit_code == 0, applied.output
    assert json.loads(applied.stdout)["applied"] is True
    assert service.calls == [
        ("reconcile", "pilot", False, None),
        ("reconcile", "pilot", True, "rev-7"),
    ]


def test_cli_profile_reconcile_rejects_conflicting_modes() -> None:
    service = MilestoneTwoInterfaceService()

    result = CliRunner().invoke(
        create_app(service),  # type: ignore[arg-type]
        ["profile", "reconcile", "--dry-run", "--apply"],
    )

    assert result.exit_code != 0
    assert "mutually exclusive" in result.output
    assert service.calls == []


def test_cli_preset_and_context_lifecycle_use_durable_service(tmp_path: Path) -> None:
    service = AdvisorService.from_database(tmp_path / "advisor.sqlite")
    cli = create_app(service)

    created_result = CliRunner().invoke(
        cli,
        [
            "preset",
            "create",
            "First lineup",
            "--slot",
            "us_m2a4",
            "--required-vehicle-id",
            "us_m2a4",
            "--json",
        ],
    )
    assert created_result.exit_code == 0, created_result.output
    created = json.loads(created_result.stdout)

    context = service.get_advisor_context("acceptance")
    selected_result = CliRunner().invoke(
        cli,
        [
            "context",
            "update",
            "--selected-preset",
            created["preset_id"],
            "--expected-revision",
            str(context.revision),
            "--json",
        ],
    )
    assert selected_result.exit_code == 0, selected_result.output
    assert json.loads(selected_result.stdout)["selected_preset_id"] == created["preset_id"]

    listed_result = CliRunner().invoke(cli, ["preset", "list", "--json"])
    assert listed_result.exit_code == 0, listed_result.output
    assert json.loads(listed_result.stdout)[0]["name"] == "First lineup"

    updated_result = CliRunner().invoke(
        cli,
        [
            "preset",
            "update",
            created["preset_id"],
            "--name",
            "Renamed",
            "--slot",
            "us_m2a4",
            "--expected-revision",
            str(created["revision"]),
            "--json",
        ],
    )
    assert updated_result.exit_code == 0, updated_result.output
    updated = json.loads(updated_result.stdout)
    assert updated["name"] == "Renamed"

    deleted_result = CliRunner().invoke(
        cli,
        [
            "preset",
            "delete",
            created["preset_id"],
            "--expected-revision",
            str(updated["revision"]),
            "--json",
        ],
    )
    assert deleted_result.exit_code == 0, deleted_result.output
    assert json.loads(deleted_result.stdout) == {"deleted": True}


@pytest.mark.asyncio
async def test_cli_and_mcp_evaluation_operations_return_same_stored_contract(
    tmp_path: Path,
) -> None:
    service = AdvisorService.from_database(tmp_path / "advisor.sqlite")
    cli_result = CliRunner().invoke(
        create_app(service),
        ["evaluation", "create", "--profile", "acceptance", "--top", "2", "--json"],
    )
    assert cli_result.exit_code == 0, cli_result.output
    created = json.loads(cli_result.stdout)

    async with Client(create_server(service)) as client:
        read_result = await client.call_tool(
            "get_stored_evaluation",
            {"profile_id": "acceptance", "evaluation_id": created["evaluation_id"]},
        )
        reevaluated_result = await client.call_tool(
            "reevaluate_stored_evaluation",
            {"profile_id": "acceptance", "evaluation_id": created["evaluation_id"]},
        )

    assert read_result.structured_content is not None
    assert read_result.structured_content["evaluation_id"] == created["evaluation_id"]
    assert read_result.structured_content["result"] == created["result"]
    assert reevaluated_result.structured_content is not None
    assert reevaluated_result.structured_content["effective_inputs"] == created["effective_inputs"]
    assert (
        reevaluated_result.structured_content["result"]["groups"]
        == created["result"]["groups"]
    )


@pytest.mark.asyncio
async def test_mcp_mutates_preset_and_context_lifecycle(tmp_path: Path) -> None:
    service = AdvisorService.from_database(tmp_path / "advisor.sqlite")

    async with Client(create_server(service)) as client:
        created_result = await client.call_tool(
            "create_preset",
            {"profile_id": "acceptance", "name": "First", "slots": ["us_m2a4"]},
        )
        assert created_result.structured_content is not None
        created = created_result.structured_content
        updated_result = await client.call_tool(
            "update_preset",
            {
                "profile_id": "acceptance",
                "preset_id": created["preset_id"],
                "name": "Renamed",
                "slots": ["us_m2a4"],
                "expected_revision": str(created["revision"]),
            },
        )
        assert updated_result.structured_content is not None
        updated = updated_result.structured_content
        context_result = await client.call_tool("get_advisor_context", {})
        assert context_result.structured_content is not None
        selected_result = await client.call_tool(
            "update_advisor_context",
            {
                "selected_preset_id": created["preset_id"],
                "expected_revision": str(context_result.structured_content["revision"]),
            },
        )
        deleted_result = await client.call_tool(
            "delete_preset",
            {
                "preset_id": created["preset_id"],
                "expected_revision": str(updated["revision"]),
            },
        )

    assert updated["name"] == "Renamed"
    assert selected_result.structured_content is not None
    assert selected_result.structured_content["selected_preset_id"] == created["preset_id"]
    assert deleted_result.structured_content == {"deleted": True}


@pytest.mark.parametrize("command", ["inspect", "import"])
def test_cli_statistics_commands_forward_provider_and_purpose(
    command: str, tmp_path: Path
) -> None:
    service = MilestoneTwoInterfaceService()
    source = tmp_path / "statistics.csv"
    source.write_text("vehicle_id,battles\nus_m2a4,10\n", encoding="utf-8")

    result = CliRunner().invoke(
        create_app(service),  # type: ignore[arg-type]
        [
            "statistics",
            command,
            str(source),
            "--provider",
            "manual-export",
            "--purpose",
            "operational",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["provider"] == "manual-export"
    assert service.calls[0][0] == command
    assert service.calls[0][1] == source
    assert service.calls[0][3] == "operational"
