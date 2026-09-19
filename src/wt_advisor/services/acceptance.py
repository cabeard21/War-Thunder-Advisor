"""Generate the reproducible Milestone 1 acceptance evidence."""

from __future__ import annotations

import json
from typing import Any

from wt_advisor.data.providers.fixture import FixtureProvider
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


def _rule(candidate: CandidateLineup, name: str) -> Any:
    return next(rule for rule in candidate.analysis.rules if rule.rule == name)


def _scenario_properties(
    scenario_id: str,
    service: AdvisorService,
    result: GenerationResult,
    owned: frozenset[str],
) -> dict[str, bool]:
    best = _best(result)
    scouting = _rule(best, "scouting")
    redundancy = _rule(best, "role_redundancy")
    properties: dict[str, bool] = {
        "ready_recommendation": best.analysis.readiness_passed,
        "no_inaccessible_vehicle": set(best.analysis.lineup.slots) <= owned,
        "spaa_redundancy_diagnostic_only": bool(
            redundancy.evidence.get("diagnostic_only")
        ),
        "scouting_not_verified_absent": (
            scouting.evidence.get("state") != "verified_absent"
        ),
    }
    if scenario_id == "m3_lee_newly_owned":
        forced = _best(
            service.generate_lineups(
                profile_id="acceptance", required_vehicle_id="us_m3_lee", top_n=3
            )
        )
        properties.update(
            {
                "forced_include_contains_m3_lee": (
                    "us_m3_lee" in forced.analysis.lineup.slots
                ),
                "lower_br_alternative_retained": result.lower_br_alternative is not None,
                "cross_br_comparison_labeled": (
                    result.lower_br_alternative is not None
                    and service.compare_lineups(
                        result.lower_br_alternative.candidates[0].analysis.lineup.slots,
                        forced.analysis.lineup.slots,
                    ).cross_br
                ),
            }
        )
    elif scenario_id == "coherent_2_7":
        properties.update(
            {
                "ready_2_7_frontier": (
                    best.analysis.lineup_br == 27 and best.analysis.readiness_passed
                ),
                "single_spaa_sufficient": sum(
                    service.get_vehicle(vehicle_id).vehicle.vehicle_class.value == "spaa"
                    for vehicle_id in best.analysis.lineup.slots
                )
                <= 1,
            }
        )
    elif scenario_id == "isolated_higher_br":
        properties.update(
            {
                "premature_escalation_not_recommended": best.analysis.lineup_br < 30,
                "higher_br_frontier_visible": any(
                    group.lineup_br == 30 for group in result.groups
                ),
            }
        )
    elif scenario_id == "mature_3_3":
        properties.update(
            {
                "ready_3_3_frontier": (
                    best.analysis.lineup_br == 33 and best.analysis.readiness_passed
                ),
                "role_diversity": len(
                    {
                        service.get_vehicle(vehicle_id).vehicle.vehicle_class
                        for vehicle_id in best.analysis.lineup.slots
                    }
                )
                >= 3,
            }
        )
    elif scenario_id == "mature_3_7":
        m24 = service.analyze_lineup(("us_m24",))
        m24_scouting = next(rule for rule in m24.rules if rule.rule == "scouting")
        m24_uptier = next(
            rule for rule in m24.rules if rule.rule == "uptier_resilience"
        )
        m24_inputs = m24_uptier.evidence["inputs_by_vehicle"]["us_m24"]
        pairs = redundancy.evidence.get("redundant_pairs", [])
        properties.update(
            {
                "ready_3_7_frontier": (
                    best.analysis.lineup_br >= 37 and best.analysis.readiness_passed
                ),
                "m24_scouting_present": m24_scouting.evidence.get("state") == "present",
                "m24_vertical_stabilizer_present": (
                    m24_inputs.get("vertical_stabilizer") == "present"
                ),
                "general_medium_overlap_not_penalized": all(
                    not all(
                        service.get_vehicle(vehicle_id).vehicle.vehicle_class.value
                        == "medium_tank"
                        for vehicle_id in pair
                    )
                    for pair in pairs
                ),
            }
        )
    return properties


def build_m2_acceptance_report() -> dict[str, Any]:
    """Evaluate the six deterministic Milestone 2 advisor-quality scenarios."""

    fixture = FixtureProvider.load_m2_scenarios()
    scenario_reports: list[dict[str, Any]] = []
    for raw in fixture["scenarios"]:
        owned = frozenset(str(item) for item in raw["owned"])
        researching = frozenset(str(item) for item in raw["researching"])
        service = AdvisorService.from_m2_acceptance_fixture(
            owned=owned, researching=researching
        )
        result = service.generate_lineups(profile_id="acceptance", top_n=3)
        best = _best(result)
        properties = _scenario_properties(str(raw["id"]), service, result, owned)
        expected = tuple(str(item) for item in raw["expected_properties"])
        scenario_reports.append(
            {
                "id": raw["id"],
                "expected_properties": expected,
                "properties": properties,
                "passed": all(properties.get(item, False) for item in expected),
                "recommended": best.model_dump(mode="json"),
                "frontier_brs": [group.lineup_br for group in result.groups],
            }
        )
    baseline = AdvisorService.from_m2_acceptance_fixture()
    return {
        "milestone": 2,
        "revision": fixture["revision"],
        "evidence_notes": fixture["evidence_notes"],
        "components": baseline.data_status()["components"],
        "scenarios": scenario_reports,
        "passed": all(item["passed"] for item in scenario_reports),
    }


def render_m2_acceptance_markdown(report: dict[str, Any]) -> str:
    """Render the property-based advisor-quality gate."""

    passed = sum(bool(item["passed"]) for item in report["scenarios"])
    lines = [
        "# Milestone 2 Advisor-Quality Acceptance",
        "",
        f"- Result: {passed} / {len(report['scenarios'])} scenarios passed",
        f"- Evidence revision: `{report['revision']}`",
        "",
        "## Scenarios",
        "",
    ]
    for scenario in report["scenarios"]:
        status = "PASS" if scenario["passed"] else "FAIL"
        lines.append(f"- **{scenario['id']}**: {status}")
        for property_name in scenario["expected_properties"]:
            value = scenario["properties"].get(property_name, False)
            lines.append(f"  - {property_name}: {'pass' if value else 'fail'}")
    lines.append("")
    return "\n".join(lines)


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
