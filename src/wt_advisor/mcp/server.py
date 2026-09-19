"""Local stdio MCP server exposing the advisor application boundary."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.types import ToolAnnotations
from pydantic import BaseModel

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


def _dump(value: BaseModel) -> dict[str, Any]:
    return value.model_dump(mode="json")


def create_server(service: AdvisorService) -> MCPServer[Any]:
    """Create an in-process server bound to exactly one application service."""

    server: MCPServer[Any] = MCPServer(
        name="war-thunder-advisor",
        instructions="Deterministic, evidence-first War Thunder Ground RB analysis.",
    )

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def get_data_status() -> dict[str, Any]:
        """Return active evidence snapshots and schema/rules revisions."""

        return service.data_status()

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def list_vehicles(
        nation: Nation = Nation.USA,
        mode: GameMode = GameMode.GROUND_REALISTIC,
        max_br: int | None = None,
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
        """Return every source statistics scope available for a vehicle."""

        statistics = service.get_vehicle_statistics(vehicle_id)
        return [_dump(item) for item in statistics]

    @server.tool(annotations=READ_ONLY, structured_output=True)
    def get_user_progress(profile_id: str = "acceptance") -> dict[str, Any]:
        """Return the profile, vehicle states, and deterministic revision."""

        return _dump(service.get_user_progress(profile_id))

    @server.tool(annotations=MUTATION, structured_output=True)
    def set_user_vehicle_status(
        vehicle_id: str,
        status: VehicleStatus,
        profile_id: str = "acceptance",
    ) -> dict[str, Any]:
        """Idempotently update one user vehicle status and return before/after state."""

        return _dump(service.set_user_vehicle_status(profile_id, vehicle_id, status))

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
        target_br: int | None = None,
        top_n: int = 10,
        hypothetical_owned: list[str] | None = None,
        required_vehicle_id: str | None = None,
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
                allow_partial=allow_partial,
            )
        )

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

        return service.evaluate_next_unlocks_status(profile_id)

    return server


def main() -> None:
    database = Path(os.environ.get("WT_ADVISOR_DB", "wt-advisor.sqlite"))
    create_server(AdvisorService.from_database(database)).run("stdio")


if __name__ == "__main__":
    main()
