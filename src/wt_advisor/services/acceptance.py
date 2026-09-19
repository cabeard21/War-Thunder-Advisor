"""Generate the reproducible Milestone 1 acceptance evidence."""

from __future__ import annotations

import json
from typing import Any

from wt_advisor.domain.models import CandidateLineup, GenerationResult
from wt_advisor.services.advisor import AdvisorService


def _best(result: GenerationResult) -> CandidateLineup:
    if result.recommended is not None:
        return result.recommended
    for group in reversed(result.groups):
        if group.candidates:
            return group.candidates[0]
    raise ValueError("generation produced no candidates")


def build_acceptance_report(service: AdvisorService) -> dict[str, Any]:
    """Run the required USA five-slot scenario and return only computed evidence."""

    progress = service.get_user_progress("acceptance")
    current = service.generate_lineups(profile_id="acceptance", top_n=3)
    current_best = _best(current)
    expanded = service.generate_lineups(
        profile_id="acceptance",
        hypothetical_owned=frozenset({"us_m3_lee"}),
        top_n=3,
    )
    forced = service.generate_lineups(
        profile_id="acceptance",
        hypothetical_owned=frozenset({"us_m3_lee"}),
        required_vehicle_id="us_m3_lee",
        top_n=3,
    )
    expanded_best = _best(expanded)
    forced_best = _best(forced)
    comparison = service.compare_lineups(
        current_best.analysis.lineup.slots, forced_best.analysis.lineup.slots
    )
    partial_lineup = current_best.analysis.lineup.slots[: max(progress.profile.crew_slots - 1, 1)]
    suggestions = service.suggest_lineup_additions(
        profile_id="acceptance", lineup=partial_lineup
    )
    unlocks = service.evaluate_next_unlocks("acceptance")
    statistics = {
        view.vehicle.vehicle_id: service.get_vehicle_statistics(view.vehicle.vehicle_id)
        for view in service.list_vehicles(max_br=40)
    }
    missing = sorted(
        vehicle_id
        for vehicle_id, rows in statistics.items()
        if not rows or all(row.battles is None or row.deaths is None for row in rows)
    )
    return {
        "profile": progress.profile.model_dump(mode="json"),
        "user_state_revision": progress.revision,
        "owned": sorted(
            vehicle_id
            for vehicle_id, status in progress.vehicle_statuses.items()
            if status.value == "owned"
        ),
        "researching": sorted(
            vehicle_id
            for vehicle_id, status in progress.vehicle_statuses.items()
            if status.value == "researching"
        ),
        "data_status": service.data_status(),
        "current": {
            "frontiers": [group.model_dump(mode="json") for group in current.groups],
            "recommended": current_best.model_dump(mode="json"),
            "lower_br_alternative": (
                None
                if current.lower_br_alternative is None
                else current.lower_br_alternative.model_dump(mode="json")
            ),
        },
        "after_m3_lee": {
            "expanded_best": expanded_best.model_dump(mode="json"),
            "forced_include": forced_best.model_dump(mode="json"),
            "component_deltas": comparison.component_deltas,
            "br_delta": comparison.lineup_b.lineup_br - comparison.lineup_a.lineup_br,
            "cross_br_non_comparable": comparison.cross_br,
        },
        "suggested_next_additions": [item.model_dump(mode="json") for item in suggestions[:5]],
        "next_unlocks": [item.model_dump(mode="json") for item in unlocks],
        "statistics": {
            "available": {
                vehicle_id: [row.model_dump(mode="json") for row in rows]
                for vehicle_id, rows in statistics.items()
                if rows
            },
            "missing_or_unknown": missing,
            "caveat": (
                "Statistics are peer-relative supporting evidence, not proof of intrinsic quality."
            ),
        },
        "statshark": {
            "automated": False,
            "reason": (
                "No documented, permitted stable endpoint was confirmed; "
                "HTML scraping is disabled."
            ),
            "fallback": "Validated JSON/CSV snapshot import",
        },
    }


def render_acceptance_markdown(report: dict[str, Any]) -> str:
    """Render a human-readable report while retaining canonical JSON evidence."""

    current = report["current"]["recommended"]["analysis"]
    after = report["after_m3_lee"]["forced_include"]["analysis"]
    lines = [
        "# Milestone 1 Acceptance Report",
        "",
        "## Scenario",
        "",
        (
            "USA Ground Realistic, five crew slots, applicable Rank I vehicles owned, "
            "M3 Lee researching, and premium/event/pack vehicles excluded."
        ),
        "",
        "## Current recommendation",
        "",
        f"- Lineup: {', '.join(current['lineup']['slots'])}",
        f"- Matchmaking BR: {current['lineup_br'] / 10:.1f}",
        f"- Construction score: {current['overall_score']:.2f}",
        f"- Readiness gates: {'pass' if current['readiness_passed'] else 'fail'}",
        "",
        "## Hypothetical M3 Lee unlock",
        "",
        f"- Forced-include lineup: {', '.join(after['lineup']['slots'])}",
        f"- Matchmaking BR: {after['lineup_br'] / 10:.1f}",
        f"- Construction score: {after['overall_score']:.2f}",
        (
            "- Cross-BR scores directly comparable: "
            f"{not report['after_m3_lee']['cross_br_non_comparable']}"
        ),
        "- Component deltas: `"
        + json.dumps(report["after_m3_lee"]["component_deltas"], sort_keys=True)
        + "`",
        "",
        "## Snapshot evidence",
        "",
    ]
    for snapshot in report["data_status"]["snapshots"]:
        lines.append(
            f"- {snapshot['dataset_type']}: `{snapshot['snapshot_id']}` "
            f"({snapshot['freshness']}, checksum `{snapshot['checksum']}`)"
        )
    lines.extend(
        [
            "",
            "## Statistics",
            "",
            (
                "- Missing or unknown: "
                f"{', '.join(report['statistics']['missing_or_unknown']) or 'none'}"
            ),
            f"- Caveat: {report['statistics']['caveat']}",
            "- StatShark acquisition: not automated; validated fixture/import fallback is active.",
            "",
            "## Verification payload",
            "",
            (
                "The CLI and MCP return the same structured service DTOs and deterministic "
                "analysis IDs. The full machine-readable acceptance evidence is available from "
                "`wt-advisor acceptance --json`."
            ),
            "",
        ]
    )
    return "\n".join(lines)
