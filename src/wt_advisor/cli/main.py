"""Typer CLI kept deliberately thin over :class:`AdvisorService`."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Annotated, Any, Protocol, cast

import typer
from pydantic import BaseModel

from wt_advisor.domain.models import GameMode, Nation, SnapshotPurpose, VehicleStatus
from wt_advisor.services.acceptance import (
    build_acceptance_report,
    build_m2_acceptance_report,
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


def _default_service() -> AdvisorService:
    database = Path(os.environ.get("WT_ADVISOR_DB", "wt-advisor.sqlite"))
    return AdvisorService.from_database(database)


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
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
    root.add_typer(data, name="data")
    root.add_typer(vehicles, name="vehicles")
    root.add_typer(vehicle, name="vehicle")
    root.add_typer(profile, name="profile")
    root.add_typer(lineup, name="lineup")
    root.add_typer(progress, name="progress")
    root.add_typer(statistics, name="statistics")

    resolved_service: AdvisorService | None = service

    def advisor() -> AdvisorService:
        nonlocal resolved_service
        if resolved_service is None:
            resolved_service = _default_service()
        return resolved_service

    @data.command("status")
    def data_status(
        json_output: Annotated[bool, typer.Option("--json", help="Emit compact JSON.")] = False,
    ) -> None:
        _emit(advisor().data_status(), as_json=json_output)

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
        _emit(advisor().get_vehicle_statistics(vehicle_id), as_json=json_output)

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
        json_output: Annotated[bool, typer.Option("--json", help="Emit compact JSON.")] = False,
    ) -> None:
        _emit(
            advisor().set_user_vehicle_status(profile_id, vehicle_id, status),
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
        allow_partial: Annotated[bool, typer.Option("--allow-partial")] = False,
        json_output: Annotated[bool, typer.Option("--json", help="Emit compact JSON.")] = False,
    ) -> None:
        result = advisor().generate_lineups(
            profile_id=profile_id,
            target_br=_br(target_br),
            top_n=top_n,
            hypothetical_owned=frozenset(hypothetical_owned or ()),
            required_vehicle_id=required_vehicle_id,
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

    @root.command("acceptance")
    def acceptance_report(
        milestone: Annotated[
            int, typer.Option("--milestone", min=1, max=2, help="Acceptance milestone.")
        ] = 1,
        json_output: Annotated[bool, typer.Option("--json", help="Emit compact JSON.")] = False,
    ) -> None:
        report = (
            build_acceptance_report(advisor())
            if milestone == 1
            else build_m2_acceptance_report()
        )
        if json_output:
            _emit(report, as_json=True)
        else:
            typer.echo(
                render_acceptance_markdown(report)
                if milestone == 1
                else render_m2_acceptance_markdown(report)
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
