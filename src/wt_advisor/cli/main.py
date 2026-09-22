"""Typer CLI kept deliberately thin over :class:`AdvisorService`."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, Protocol, cast

import typer
import uvicorn
from pydantic import BaseModel

from wt_advisor.domain.models import GameMode, Nation, Role, SnapshotPurpose, VehicleStatus
from wt_advisor.services.acceptance import (
    build_acceptance_report,
    build_m2_acceptance_report,
    build_m3_acceptance_report,
    render_acceptance_markdown,
    render_m2_acceptance_markdown,
)
from wt_advisor.services.advisor import AdvisorService


class _MilestoneTwoService(Protocol):
    """Additional application boundary used by Milestone 2 CLI commands."""

    def reconcile_profile(
        self,
        profile_id: str,
        *,
        apply: bool = False,
        expected_revision: str | None = None,
    ) -> object: ...

    def inspect_statistics(
        self,
        path: Path,
        *,
        provider: str,
        purpose: SnapshotPurpose,
    ) -> object: ...

    def import_statistics(
        self,
        path: Path,
        *,
        provider: str,
        purpose: SnapshotPurpose,
    ) -> object: ...

    def import_capabilities(
        self,
        path: Path,
        *,
        provider: str,
        source_revision: str,
        vehicle_snapshot_id: str,
    ) -> object: ...


def _default_service() -> AdvisorService:
    database = Path(os.environ.get("WT_ADVISOR_DB", "wt-advisor.sqlite"))
    return AdvisorService.from_database(database)


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_jsonable(item) for item in value]
    return value


def _emit(value: Any, *, as_json: bool) -> None:
    payload = _jsonable(value)
    # The human representation intentionally remains inspectable structured output.
    typer.echo(json.dumps(payload, indent=None if as_json else 2, sort_keys=True))


def _br(value: float | None) -> int | None:
    return None if value is None else round(value * 10)


def create_app(service: AdvisorService | None = None) -> typer.Typer:
    """Build an app, optionally binding one service instance for tests/embedding."""

    root = typer.Typer(help="Evidence-first War Thunder Ground RB advisor.")
    data = typer.Typer(help="Inspect and initialize evidence data.")
    vehicles = typer.Typer(help="List vehicles.")
    vehicle = typer.Typer(help="Inspect a vehicle.")
    profile = typer.Typer(help="Inspect or update user progression.")
    lineup = typer.Typer(help="Analyze and compare lineups.")
    progress = typer.Typer(help="Evaluate one-step progression.")
    statistics = typer.Typer(help="Inspect and import validated statistics evidence.")
    capabilities = typer.Typer(help="Import verified capability observations.")
    preset = typer.Typer(help="Create and manage durable lineup presets.")
    context = typer.Typer(help="Inspect and update the saved advisor context.")
    evaluation = typer.Typer(help="Create and read immutable stored evaluations.")
    advisor_snapshot = typer.Typer(help="Read and refresh the deterministic advisor answer.")
    root.add_typer(data, name="data")
    root.add_typer(vehicles, name="vehicles")
    root.add_typer(vehicle, name="vehicle")
    root.add_typer(profile, name="profile")
    root.add_typer(lineup, name="lineup")
    root.add_typer(progress, name="progress")
    root.add_typer(statistics, name="statistics")
    root.add_typer(capabilities, name="capabilities")
    root.add_typer(preset, name="preset")
    root.add_typer(context, name="context")
    root.add_typer(evaluation, name="evaluation")
    root.add_typer(advisor_snapshot, name="advisor")

    @root.command("dashboard")
    def dashboard(port: Annotated[int, typer.Option(min=1024, max=65535)] = 8765) -> None:
        """Serve the bundled local dashboard on loopback only."""
        from wt_advisor.web.app import create_app as create_web_app

        uvicorn.run(create_web_app(advisor()), host="127.0.0.1", port=port)

    resolved_service: AdvisorService | None = service

    def advisor() -> AdvisorService:
        nonlocal resolved_service
        if resolved_service is None:
            resolved_service = _default_service()
        return resolved_service

    @advisor_snapshot.command("show")
    def advisor_show(
        profile_id: Annotated[str, typer.Option("--profile")] = "acceptance",
        json_output: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        _emit(advisor().get_advisor_snapshot(profile_id), as_json=json_output)

    @advisor_snapshot.command("refresh")
    def advisor_refresh(
        profile_id: Annotated[str, typer.Option("--profile")] = "acceptance",
        json_output: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        _emit(advisor().refresh_advisor_snapshot(profile_id), as_json=json_output)

    @data.command("status")
    def data_status(
        json_output: Annotated[bool, typer.Option("--json", help="Emit compact JSON.")] = False,
    ) -> None:
        _emit(advisor().data_status(), as_json=json_output)

    @data.command("refresh-community")
    def data_refresh_community(
        json_output: Annotated[bool, typer.Option("--json", help="Emit compact JSON.")] = False,
    ) -> None:
        """Refresh bounded community evidence without replacing user state."""

        _emit(advisor().refresh_community_evidence(), as_json=json_output)

    @data.command("reprocess-community")
    def data_reprocess_community(
        json_output: Annotated[bool, typer.Option("--json", help="Emit compact JSON.")] = False,
    ) -> None:
        """Reprocess retained community CSV without contacting the vehicle API."""

        _emit(advisor().reprocess_retained_community_statistics(), as_json=json_output)

    @data.command("import")
    def data_import(
        source: Annotated[
            str, typer.Option(help="Evidence provider to initialize.")
        ] = "acceptance",
        json_output: Annotated[bool, typer.Option("--json", help="Emit compact JSON.")] = False,
    ) -> None:
        if source not in {"acceptance", "wt-api"}:
            raise typer.BadParameter(
                "source must be acceptance or wt-api",
                param_hint="--source",
            )
        snapshot = advisor().import_live_vehicles() if source == "wt-api" else None
        _emit(
            {
                "source": source,
                "initialized": True,
                "imported_snapshot": snapshot,
                "status": advisor().data_status(),
                "note": (
                    "The imported snapshot becomes active when the service is next loaded."
                    if snapshot is not None
                    else None
                ),
            },
            as_json=json_output,
        )

    @vehicles.command("list")
    def vehicles_list(
        nation: Annotated[Nation, typer.Option()] = Nation.USA,
        mode: Annotated[GameMode, typer.Option()] = GameMode.GROUND_REALISTIC,
        max_br: Annotated[float | None, typer.Option("--max-br")] = None,
        json_output: Annotated[bool, typer.Option("--json", help="Emit compact JSON.")] = False,
    ) -> None:
        result = advisor().list_vehicles(nation=nation, mode=mode, max_br=_br(max_br))
        _emit(result, as_json=json_output)

    @vehicle.command("show")
    def vehicle_show(
        vehicle_id: str,
        json_output: Annotated[bool, typer.Option("--json", help="Emit compact JSON.")] = False,
    ) -> None:
        _emit(advisor().get_vehicle(vehicle_id), as_json=json_output)

    @vehicle.command("stats")
    def vehicle_stats(
        vehicle_id: str,
        json_output: Annotated[bool, typer.Option("--json", help="Emit compact JSON.")] = False,
    ) -> None:
        _emit(advisor().vehicle_statistics_status(vehicle_id), as_json=json_output)

    @profile.command("show")
    def profile_show(
        profile_id: Annotated[str, typer.Option("--profile")] = "acceptance",
        json_output: Annotated[bool, typer.Option("--json", help="Emit compact JSON.")] = False,
    ) -> None:
        _emit(advisor().get_user_progress(profile_id), as_json=json_output)

    @profile.command("set-status")
    def profile_set_status(
        vehicle_id: str,
        status: VehicleStatus,
        profile_id: Annotated[str, typer.Option("--profile")] = "acceptance",
        expected_revision: Annotated[str | None, typer.Option("--expected-revision")] = None,
        expected_write_revision: Annotated[
            str | None, typer.Option("--expected-write-revision")
        ] = None,
        json_output: Annotated[bool, typer.Option("--json", help="Emit compact JSON.")] = False,
    ) -> None:
        _emit(
            advisor().set_user_vehicle_status(
                profile_id, vehicle_id, status, expected_revision=expected_revision,
                expected_write_revision=expected_write_revision,
            ),
            as_json=json_output,
        )

    @profile.command("reconcile")
    def profile_reconcile(
        profile_id: Annotated[str, typer.Option("--profile")] = "acceptance",
        dry_run: Annotated[
            bool,
            typer.Option("--dry-run", help="Preview the immutable reconciliation plan."),
        ] = False,
        apply: Annotated[
            bool,
            typer.Option("--apply", help="Apply confirmed aliases transactionally."),
        ] = False,
        expected_revision: Annotated[
            str | None,
            typer.Option(
                "--expected-revision",
                help="Reject apply when the profile revision no longer matches.",
            ),
        ] = None,
        json_output: Annotated[bool, typer.Option("--json", help="Emit compact JSON.")] = False,
    ) -> None:
        if dry_run and apply:
            raise typer.BadParameter("--dry-run and --apply are mutually exclusive")
        if expected_revision is not None and not apply:
            raise typer.BadParameter(
                "--expected-revision requires --apply",
                param_hint="--expected-revision",
            )
        _emit(
            cast(_MilestoneTwoService, advisor()).reconcile_profile(
                profile_id,
                apply=apply,
                expected_revision=expected_revision,
            ),
            as_json=json_output,
        )

    @statistics.command("inspect")
    def statistics_inspect(
        source: Annotated[
            Path,
            typer.Argument(exists=True, dir_okay=False, readable=True),
        ],
        provider: Annotated[str, typer.Option("--provider", help="Source provider name.")],
        purpose: Annotated[SnapshotPurpose, typer.Option("--purpose")] = (
            SnapshotPurpose.OPERATIONAL
        ),
        json_output: Annotated[bool, typer.Option("--json", help="Emit compact JSON.")] = False,
    ) -> None:
        _emit(
            cast(_MilestoneTwoService, advisor()).inspect_statistics(
                source, provider=provider, purpose=purpose
            ),
            as_json=json_output,
        )

    @capabilities.command("import")
    def capabilities_import(
        source: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
        provider: Annotated[str, typer.Option("--provider")],
        source_revision: Annotated[str, typer.Option("--source-revision")],
        vehicle_snapshot_id: Annotated[str, typer.Option("--vehicle-snapshot-id")],
        json_output: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        _emit(
            cast(_MilestoneTwoService, advisor()).import_capabilities(
                source,
                provider=provider,
                source_revision=source_revision,
                vehicle_snapshot_id=vehicle_snapshot_id,
            ),
            as_json=json_output,
        )

    @statistics.command("import")
    def statistics_import(
        source: Annotated[
            Path,
            typer.Argument(exists=True, dir_okay=False, readable=True),
        ],
        provider: Annotated[str, typer.Option("--provider", help="Source provider name.")],
        purpose: Annotated[SnapshotPurpose, typer.Option("--purpose")] = (
            SnapshotPurpose.OPERATIONAL
        ),
        json_output: Annotated[bool, typer.Option("--json", help="Emit compact JSON.")] = False,
    ) -> None:
        _emit(
            cast(_MilestoneTwoService, advisor()).import_statistics(
                source, provider=provider, purpose=purpose
            ),
            as_json=json_output,
        )

    @lineup.command("analyze")
    def lineup_analyze(
        vehicle_ids: Annotated[list[str], typer.Argument()],
        json_output: Annotated[bool, typer.Option("--json", help="Emit compact JSON.")] = False,
    ) -> None:
        _emit(advisor().analyze_lineup(vehicle_ids), as_json=json_output)

    @lineup.command("generate")
    def lineup_generate(
        profile_id: Annotated[str, typer.Option("--profile")] = "acceptance",
        target_br: Annotated[float | None, typer.Option("--br")] = None,
        top_n: Annotated[int, typer.Option("--top")] = 10,
        hypothetical_owned: Annotated[
            list[str] | None, typer.Option("--hypothetical-owned")
        ] = None,
        required_vehicle_id: Annotated[str | None, typer.Option("--required-vehicle")] = None,
        required_vehicle_ids: Annotated[
            list[str] | None, typer.Option("--required-vehicle-id")
        ] = None,
        excluded_vehicle_ids: Annotated[
            list[str] | None, typer.Option("--excluded-vehicle-id")
        ] = None,
        allow_partial: Annotated[bool, typer.Option("--allow-partial")] = False,
        json_output: Annotated[bool, typer.Option("--json", help="Emit compact JSON.")] = False,
    ) -> None:
        result = advisor().generate_lineups(
            profile_id=profile_id,
            target_br=_br(target_br),
            top_n=top_n,
            hypothetical_owned=frozenset(hypothetical_owned or ()),
            required_vehicle_id=required_vehicle_id,
            required_vehicle_ids=(
                None if required_vehicle_ids is None else frozenset(required_vehicle_ids)
            ),
            excluded_vehicle_ids=frozenset(excluded_vehicle_ids or ()),
            allow_partial=allow_partial,
        )
        _emit(result, as_json=json_output)

    @lineup.command("compare")
    def lineup_compare(
        lineup_a: Annotated[str, typer.Option("--a", help="Comma-separated vehicle IDs.")],
        lineup_b: Annotated[str, typer.Option("--b", help="Comma-separated vehicle IDs.")],
        json_output: Annotated[bool, typer.Option("--json", help="Emit compact JSON.")] = False,
    ) -> None:
        _emit(
            advisor().compare_lineups(_vehicle_ids(lineup_a), _vehicle_ids(lineup_b)),
            as_json=json_output,
        )

    @lineup.command("suggest-additions")
    def lineup_suggest_additions(
        vehicle_ids: Annotated[list[str], typer.Argument()],
        profile_id: Annotated[str, typer.Option("--profile")] = "acceptance",
        hypothetical_owned: Annotated[
            list[str] | None, typer.Option("--hypothetical-owned")
        ] = None,
        json_output: Annotated[bool, typer.Option("--json", help="Emit compact JSON.")] = False,
    ) -> None:
        result = advisor().suggest_lineup_additions(
            profile_id=profile_id,
            lineup=vehicle_ids,
            hypothetical_owned=frozenset(hypothetical_owned or ()),
        )
        _emit(result, as_json=json_output)

    @progress.command("next")
    def progress_next(
        profile_id: Annotated[str, typer.Option("--profile")] = "acceptance",
        json_output: Annotated[bool, typer.Option("--json", help="Emit compact JSON.")] = False,
    ) -> None:
        _emit(advisor().evaluate_next_unlocks_status(profile_id), as_json=json_output)

    @preset.command("list")
    def preset_list(
        profile_id: Annotated[str, typer.Option("--profile")] = "acceptance",
        json_output: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        _emit(advisor().list_presets(profile_id), as_json=json_output)

    @preset.command("create")
    def preset_create(
        name: str,
        profile_id: Annotated[str, typer.Option("--profile")] = "acceptance",
        slots: Annotated[list[str] | None, typer.Option("--slot")] = None,
        required_vehicle_ids: Annotated[
            list[str] | None, typer.Option("--required-vehicle-id")
        ] = None,
        excluded_vehicle_ids: Annotated[
            list[str] | None, typer.Option("--excluded-vehicle-id")
        ] = None,
        json_output: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        _emit(
            advisor().create_preset(
                profile_id,
                name=name,
                slots=tuple(slots or ()),
                required_vehicle_ids=tuple(required_vehicle_ids or ()),
                excluded_vehicle_ids=tuple(excluded_vehicle_ids or ()),
            ),
            as_json=json_output,
        )

    @preset.command("update")
    def preset_update(
        preset_id: str,
        name: Annotated[str, typer.Option("--name")],
        expected_revision: Annotated[str, typer.Option("--expected-revision")],
        profile_id: Annotated[str, typer.Option("--profile")] = "acceptance",
        slots: Annotated[list[str] | None, typer.Option("--slot")] = None,
        required_vehicle_ids: Annotated[
            list[str] | None, typer.Option("--required-vehicle-id")
        ] = None,
        excluded_vehicle_ids: Annotated[
            list[str] | None, typer.Option("--excluded-vehicle-id")
        ] = None,
        json_output: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        _emit(
            advisor().update_preset(
                profile_id,
                preset_id,
                name=name,
                slots=tuple(slots or ()),
                required_vehicle_ids=tuple(required_vehicle_ids or ()),
                excluded_vehicle_ids=tuple(excluded_vehicle_ids or ()),
                expected_revision=expected_revision,
            ),
            as_json=json_output,
        )

    @preset.command("delete")
    def preset_delete(
        preset_id: str,
        expected_revision: Annotated[str, typer.Option("--expected-revision")],
        profile_id: Annotated[str, typer.Option("--profile")] = "acceptance",
        json_output: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        advisor().delete_preset(
            profile_id, preset_id, expected_revision=expected_revision
        )
        _emit({"deleted": True}, as_json=json_output)

    @context.command("show")
    def context_show(
        profile_id: Annotated[str, typer.Option("--profile")] = "acceptance",
        json_output: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        _emit(advisor().get_advisor_context(profile_id), as_json=json_output)

    @context.command("update")
    def context_update(
        expected_revision: Annotated[str, typer.Option("--expected-revision")],
        profile_id: Annotated[str, typer.Option("--profile")] = "acceptance",
        selected_preset_id: Annotated[
            str | None, typer.Option("--selected-preset")
        ] = None,
        clear_preset: Annotated[bool, typer.Option("--clear-preset")] = False,
        target_br: Annotated[float | None, typer.Option("--br")] = None,
        required_vehicle_ids: Annotated[
            list[str] | None, typer.Option("--required-vehicle-id")
        ] = None,
        excluded_vehicle_ids: Annotated[
            list[str] | None, typer.Option("--excluded-vehicle-id")
        ] = None,
        preferred_roles: Annotated[
            list[Role] | None, typer.Option("--preferred-role")
        ] = None,
        duplicate_role_penalty: Annotated[
            int | None, typer.Option("--duplicate-role-penalty", min=0, max=10)
        ] = None,
        json_output: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        if clear_preset and selected_preset_id is not None:
            raise typer.BadParameter(
                "--selected-preset and --clear-preset are mutually exclusive"
            )
        _emit(
            advisor().update_advisor_context(
                profile_id,
                selected_preset_id=None if clear_preset else selected_preset_id,
                target_br=_br(target_br),
                required_vehicle_ids=tuple(required_vehicle_ids or ()),
                excluded_vehicle_ids=tuple(excluded_vehicle_ids or ()),
                expected_revision=expected_revision,
                preferred_roles=preferred_roles,
                duplicate_role_penalty=duplicate_role_penalty,
            ),
            as_json=json_output,
        )

    @evaluation.command("create")
    def evaluation_create(
        profile_id: Annotated[str, typer.Option("--profile")] = "acceptance",
        target_br: Annotated[float | None, typer.Option("--br")] = None,
        top_n: Annotated[int, typer.Option("--top")] = 10,
        hypothetical_owned: Annotated[
            list[str] | None, typer.Option("--hypothetical-owned")
        ] = None,
        required_vehicle_ids: Annotated[
            list[str] | None, typer.Option("--required-vehicle-id")
        ] = None,
        excluded_vehicle_ids: Annotated[
            list[str] | None, typer.Option("--excluded-vehicle-id")
        ] = None,
        allow_partial: Annotated[bool, typer.Option("--allow-partial")] = False,
        json_output: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        _emit(
            advisor().evaluate_and_store(
                profile_id=profile_id,
                target_br=_br(target_br),
                top_n=top_n,
                hypothetical_owned=frozenset(hypothetical_owned or ()),
                required_vehicle_ids=(
                    None
                    if required_vehicle_ids is None
                    else frozenset(required_vehicle_ids)
                ),
                excluded_vehicle_ids=frozenset(excluded_vehicle_ids or ()),
                allow_partial=allow_partial,
            ),
            as_json=json_output,
        )

    @evaluation.command("show")
    def evaluation_show(
        evaluation_id: str,
        profile_id: Annotated[str, typer.Option("--profile")] = "acceptance",
        json_output: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        _emit(
            advisor().get_stored_evaluation(profile_id, evaluation_id),
            as_json=json_output,
        )

    @evaluation.command("reevaluate")
    def evaluation_reevaluate(
        evaluation_id: str,
        profile_id: Annotated[str, typer.Option("--profile")] = "acceptance",
        json_output: Annotated[bool, typer.Option("--json")] = False,
    ) -> None:
        _emit(
            advisor().reevaluate_stored_evaluation(profile_id, evaluation_id),
            as_json=json_output,
        )

    @root.command("acceptance")
    def acceptance_report(
        milestone: Annotated[
            int, typer.Option("--milestone", min=1, max=3, help="Acceptance milestone.")
        ] = 1,
        json_output: Annotated[bool, typer.Option("--json", help="Emit compact JSON.")] = False,
    ) -> None:
        report = (
            build_acceptance_report(advisor())
            if milestone == 1
            else build_m2_acceptance_report()
            if milestone == 2
            else build_m3_acceptance_report(advisor())
        )
        if json_output:
            _emit(report, as_json=True)
        else:
            typer.echo(
                json.dumps(_jsonable(report), indent=2, sort_keys=True)
                if milestone == 3
                else (
                    render_acceptance_markdown(report)
                    if milestone == 1
                    else render_m2_acceptance_markdown(report)
                )
            )

    return root


def _vehicle_ids(value: str) -> Sequence[str]:
    result = tuple(item.strip() for item in value.split(",") if item.strip())
    if not result:
        raise typer.BadParameter("at least one vehicle ID is required")
    return result


app = create_app()


def main() -> None:
    app()


if __name__ == "__main__":
    main()
