"""Local stdio MCP server exposing the advisor application boundary."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from wt_advisor.domain.models import GameMode, Nation, VehicleStatus
from wt_advisor.services.advisor import AdvisorService

READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)
MUTATION = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=True,
    open_world_hint=False,
)
REFRESH_MUTATION = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,
    idempotent_hint=True,
    open_world_hint=True,
)


BR_TENTHS_DESCRIPTION = (
    "Battle rating in integer tenths: 10 = 1.0, 23 = 2.3, 27 = 2.7, and 40 = 4.0."
)
BR_EXAMPLES = [10, 23, 27, 40]


def _jsonable(value: Any) -> Any:
    """Serialize immutable service DTOs consistently at the MCP boundary."""

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


def _dump(value: Any) -> dict[str, Any]:
    payload = _jsonable(value)
    if not isinstance(payload, dict):
        raise TypeError("expected a structured object result")
    return payload


def create_server(service: AdvisorService) -> MCPServer[Any]:
    """Create an in-process server bound to exactly one application service."""

    server: MCPServer[Any] = MCPServer(
        name="war-thunder-advisor",
        instructions="Deterministic, evidence-first War Thunder Ground RB analysis.",
    )

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def get_data_status() -> dict[str, Any]:
        """Return active evidence snapshots and schema/rules revisions."""

        return _dump(service.data_status())

    @server.tool(annotations=REFRESH_MUTATION, structured_output=True)
    def refresh_community_evidence() -> dict[str, Any]:
        """Fetch bounded community sources and atomically publish valid new evidence."""

        return _dump(service.refresh_community_evidence())

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def list_vehicles(
        nation: Nation = Nation.USA,
        mode: GameMode = GameMode.GROUND_REALISTIC,
        max_br: Annotated[
            int | None,
            Field(description=BR_TENTHS_DESCRIPTION, examples=BR_EXAMPLES),
        ] = None,
    ) -> list[dict[str, Any]]:
        """List resolved vehicles and provenance; BR values are integer tenths."""

        vehicles = service.list_vehicles(nation=nation, mode=mode, max_br=max_br)
        return [_dump(item) for item in vehicles]

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def get_vehicle(vehicle_id: str) -> dict[str, Any]:
        """Return one resolved vehicle with its BR, roles, and provenance."""

        return _dump(service.get_vehicle(vehicle_id))

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def get_vehicle_statistics(vehicle_id: str) -> list[dict[str, Any]]:
        """Return source scopes with current scoring eligibility and proxy use."""

        return service.vehicle_statistics_status(vehicle_id)

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def get_user_progress(profile_id: str = "acceptance") -> dict[str, Any]:
        """Return the profile, vehicle states, and deterministic revision."""

        return _dump(service.get_user_progress(profile_id))

    @server.tool(annotations=MUTATION, structured_output=True)
    def set_user_vehicle_status(
        vehicle_id: str,
        status: VehicleStatus,
        profile_id: str = "acceptance",
        expected_revision: str | None = None,
        expected_write_revision: str | None = None,
    ) -> dict[str, Any]:
        """Idempotently update one user vehicle status and return before/after state."""

        return _dump(
            service.set_user_vehicle_status(
                profile_id, vehicle_id, status, expected_revision=expected_revision,
                expected_write_revision=expected_write_revision,
            )
        )

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def analyze_lineup(vehicle_ids: list[str]) -> dict[str, Any]:
        """Analyze one lineup using the active rules and evidence bundle."""

        return _dump(service.analyze_lineup(vehicle_ids))

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def compare_lineups(lineup_a: list[str], lineup_b: list[str]) -> dict[str, Any]:
        """Compare component scores without declaring a prose winner."""

        return _dump(service.compare_lineups(lineup_a, lineup_b))

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def generate_lineups(
        profile_id: str = "acceptance",
        target_br: Annotated[
            int | None,
            Field(description=BR_TENTHS_DESCRIPTION, examples=BR_EXAMPLES),
        ] = None,
        top_n: int = 10,
        hypothetical_owned: list[str] | None = None,
        required_vehicle_id: str | None = None,
        required_vehicle_ids: list[str] | None = None,
        excluded_vehicle_ids: list[str] | None = None,
        allow_partial: bool = False,
    ) -> dict[str, Any]:
        """Generate deterministic Pareto-frontier lineups grouped by BR."""

        return _dump(
            service.generate_lineups(
                profile_id=profile_id,
                target_br=target_br,
                top_n=top_n,
                hypothetical_owned=frozenset(hypothetical_owned or ()),
                required_vehicle_id=required_vehicle_id,
                required_vehicle_ids=(
                    None if required_vehicle_ids is None else frozenset(required_vehicle_ids)
                ),
                excluded_vehicle_ids=frozenset(excluded_vehicle_ids or ()),
                allow_partial=allow_partial,
            )
        )

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def list_presets(profile_id: str = "acceptance") -> list[dict[str, Any]]:
        """List durable lineup presets for the profile."""

        return [_dump(item) for item in service.list_presets(profile_id)]

    @server.tool(annotations=MUTATION, structured_output=True)
    def create_preset(
        name: str,
        slots: list[str],
        profile_id: str = "acceptance",
        required_vehicle_ids: list[str] | None = None,
        excluded_vehicle_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a durable, revisioned lineup preset."""

        return _dump(
            service.create_preset(
                profile_id,
                name=name,
                slots=tuple(slots),
                required_vehicle_ids=tuple(required_vehicle_ids or ()),
                excluded_vehicle_ids=tuple(excluded_vehicle_ids or ()),
            )
        )

    @server.tool(annotations=MUTATION, structured_output=True)
    def update_preset(
        preset_id: str,
        name: str,
        slots: list[str],
        expected_revision: str,
        profile_id: str = "acceptance",
        required_vehicle_ids: list[str] | None = None,
        excluded_vehicle_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Update a preset when its persisted revision still matches."""

        return _dump(
            service.update_preset(
                profile_id,
                preset_id,
                name=name,
                slots=tuple(slots),
                required_vehicle_ids=tuple(required_vehicle_ids or ()),
                excluded_vehicle_ids=tuple(excluded_vehicle_ids or ()),
                expected_revision=expected_revision,
            )
        )

    @server.tool(annotations=MUTATION, structured_output=True)
    def delete_preset(
        preset_id: str,
        expected_revision: str,
        profile_id: str = "acceptance",
    ) -> dict[str, Any]:
        """Delete a preset when its persisted revision still matches."""

        service.delete_preset(
            profile_id, preset_id, expected_revision=expected_revision
        )
        return {"deleted": True}

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def get_advisor_context(profile_id: str = "acceptance") -> dict[str, Any]:
        """Read the saved advisor context without changing it."""

        return _dump(service.get_advisor_context(profile_id))

    @server.tool(annotations=MUTATION, structured_output=True)
    def update_advisor_context(
        expected_revision: str,
        profile_id: str = "acceptance",
        selected_preset_id: str | None = None,
        target_br: Annotated[
            int | None,
            Field(description=BR_TENTHS_DESCRIPTION, examples=BR_EXAMPLES),
        ] = None,
        required_vehicle_ids: list[str] | None = None,
        excluded_vehicle_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Update the selected preset and saved constrained-generation context."""

        return _dump(
            service.update_advisor_context(
                profile_id,
                selected_preset_id=selected_preset_id,
                target_br=target_br,
                required_vehicle_ids=tuple(required_vehicle_ids or ()),
                excluded_vehicle_ids=tuple(excluded_vehicle_ids or ()),
                expected_revision=expected_revision,
            )
        )

    @server.tool(annotations=MUTATION, structured_output=True)
    def evaluate_and_store(
        profile_id: str = "acceptance",
        target_br: Annotated[
            int | None,
            Field(description=BR_TENTHS_DESCRIPTION, examples=BR_EXAMPLES),
        ] = None,
        top_n: int = 10,
        hypothetical_owned: list[str] | None = None,
        required_vehicle_ids: list[str] | None = None,
        excluded_vehicle_ids: list[str] | None = None,
        allow_partial: bool = False,
    ) -> dict[str, Any]:
        """Calculate from captured inputs and persist the immutable result."""

        return service.evaluate_and_store(
            profile_id=profile_id,
            target_br=target_br,
            top_n=top_n,
            hypothetical_owned=frozenset(hypothetical_owned or ()),
            required_vehicle_ids=(
                None
                if required_vehicle_ids is None
                else frozenset(required_vehicle_ids)
            ),
            excluded_vehicle_ids=frozenset(excluded_vehicle_ids or ()),
            allow_partial=allow_partial,
        )

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def get_stored_evaluation(evaluation_id: str, profile_id: str = "acceptance") -> dict[str, Any]:
        """Read an immutable stored evaluation; this never recalculates it."""

        return service.get_stored_evaluation(profile_id, evaluation_id)

    @server.tool(annotations=MUTATION, structured_output=True)
    def reevaluate_stored_evaluation(
        evaluation_id: str, profile_id: str = "acceptance"
    ) -> dict[str, Any]:
        """Explicitly recalculate a stored evaluation from its saved inputs."""

        return service.reevaluate_stored_evaluation(profile_id, evaluation_id)

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def suggest_lineup_additions(
        lineup: list[str],
        profile_id: str = "acceptance",
        hypothetical_owned: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Evaluate owned additions for a lineup with an empty crew slot."""

        result = service.suggest_lineup_additions(
            profile_id=profile_id,
            lineup=lineup,
            hypothetical_owned=frozenset(hypothetical_owned or ()),
        )
        return [_dump(item) for item in result]

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def evaluate_next_unlocks(profile_id: str = "acceptance") -> dict[str, Any]:
        """Evaluate each directly researchable vehicle as a one-step unlock."""

        return _dump(service.evaluate_next_unlocks_status(profile_id))

    return server


def main() -> None:
    database = Path(os.environ.get("WT_ADVISOR_DB", "wt-advisor.sqlite"))
    create_server(AdvisorService.from_database(database)).run("stdio")


if __name__ == "__main__":
    main()
