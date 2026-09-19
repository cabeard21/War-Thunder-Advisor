import json

import pytest
from mcp.client import Client
from typer.testing import CliRunner

from wt_advisor.cli.main import app, create_app
from wt_advisor.mcp.server import create_server
from wt_advisor.services.advisor import AdvisorService


def test_cli_generates_structured_lineups() -> None:
    result = CliRunner().invoke(
        app,
        ["lineup", "generate", "--profile", "acceptance", "--top", "2", "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["recommended"]
    assert payload["analysis_id"]


def test_cli_data_status_exposes_snapshot_and_schema_revisions() -> None:
    result = CliRunner().invoke(app, ["data", "status", "--json"])

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

    assert tool_names == {
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
    }
    assert result.structured_content is not None
    assert result.structured_content["schema_revision"]


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
async def test_cli_and_mcp_return_identical_data_status_dto() -> None:
    service = AdvisorService.from_acceptance_fixture()
    cli_result = CliRunner().invoke(create_app(service), ["data", "status", "--json"])
    server = create_server(service)

    async with Client(server) as client:
        mcp_result = await client.call_tool("get_data_status", {})

    assert cli_result.exit_code == 0, cli_result.output
    assert mcp_result.structured_content is not None
    assert json.loads(cli_result.stdout) == mcp_result.structured_content
