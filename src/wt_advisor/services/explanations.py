"""Deterministic, player-facing wording for an already-computed advisor snapshot.

Structured reason codes and provenance stay the source of truth; this module only maps
them to sentences. There is no model here and no generated prose: the same snapshot always
yields the same text, and every sentence traces back to a reason code or a measured value.

Because the CLI and MCP tools emit the snapshot dict verbatim, generating the wording here
keeps the dashboard, CLI and MCP surfaces identical for free.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

# One sentence per favourable reason code. A code absent from this table is still carried
# in the raw `reasons` list; it simply has no player-facing sentence yet.
STRENGTH_TEXT: Mapping[str, str] = {
    "HIGHEST_READY_BR": "This is the highest battle rating where your garage has a ready lineup.",
    "STRENGTH_BR_COHESION": "The vehicles sit closely together in battle rating.",
    "STRENGTH_BACKUP_DEPTH": "You have solid backup depth if you lose a vehicle early.",
    "STRENGTH_ROLE_COVERAGE": "The lineup covers a broad spread of battlefield roles.",
    "STRENGTH_ANTI_AIR": "It includes dedicated anti-air cover.",
    "STRENGTH_SCOUTING": "It includes scouting capability.",
    "STRENGTH_UPTIER_RESILIENCE": "It holds up reasonably well when matched into a higher tier.",
    "STRENGTH_STATISTICAL_STRENGTH": "These vehicles perform well in community statistics.",
    "NO_SPECIALIST_OVERLAP": "Its anti-air vehicles do different jobs rather than overlapping.",
}

# OBJECTIVE_SCORE_RANK documents the ranking policy rather than a property of this lineup,
# so it is intentionally not a strength sentence.
_POLICY_ONLY_REASONS = frozenset({"OBJECTIVE_SCORE_RANK"})

ROLE_TEXT: Mapping[str, str] = {
    "tank_destroyer": "tank destroyer",
    "sniper": "long-range sniper",
    "spaa": "anti-air vehicle",
    "scout": "scout",
    "flanker": "light flanker",
    "brawler": "close-quarters brawler",
    "heavy_anchor": "heavy anchor",
    "general_medium": "general-purpose medium",
    "anti_armor_specialist": "anti-armour specialist",
}

WARNING_TEXT: Mapping[str, str] = {
    "missing_effective_spaa": "This lineup has no effective anti-air cover.",
    "no_readiness_passing_lineup": (
        "No lineup in your garage currently passes the readiness checks."
    ),
    "statistics_missing_or_incompatible": (
        "Community statistics are missing or incompatible for part of this lineup."
    ),
}


def _role_name(role: str) -> str:
    return ROLE_TEXT.get(role, role.replace("_", " "))


def _join(items: Sequence[str]) -> str:
    """Oxford-comma join, so generated sentences read naturally."""
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return f"{', '.join(items[:-1])} and {items[-1]}"


def _research_pointer_id(
    roles: Sequence[str], research_priorities: Sequence[Mapping[str, Any]]
) -> str | None:
    """The ranked research target that would make a preference satisfiable."""
    chosen: str | None = None
    for priority in research_priorities:
        reasons = priority.get("reasons") or ()
        if "ADDS_PREFERRED_ROLE" in reasons:
            chosen = str(priority.get("vehicle_id"))
            break
    # Fall back to the top-ranked target: research is the only route to a role the garage
    # cannot field at all, even when the forced-include lineup did not adopt the vehicle.
    if chosen is None and roles and research_priorities:
        chosen = str(research_priorities[0].get("vehicle_id"))
    return chosen


def build_strengths(reasons: Sequence[str]) -> list[str]:
    """Favourable sentences only. An unmet preference is never a strength."""
    return [
        STRENGTH_TEXT[reason]
        for reason in reasons
        if reason in STRENGTH_TEXT and reason not in _POLICY_ONLY_REASONS
    ]


def build_tradeoffs(
    factors: Mapping[str, Any],
    transparency: Mapping[str, Any],
    research_priorities: Sequence[Mapping[str, Any]],
    vehicle_names: Mapping[str, str] | None = None,
) -> list[str]:
    """Preference outcomes, stated as tradeoffs rather than strengths or blockers.

    Distinguishes a preference that lost to a stronger lineup from one that no ready
    lineup at this battle rating could satisfy at all.
    """
    lines: list[str] = []
    unsatisfiable = list(transparency.get("unsatisfiable_preferred_roles") or ())
    unmet = list(transparency.get("unmet_preferred_roles") or ())
    covered = list(factors.get("preferred_roles_covered") or ())

    if covered:
        lines.append(f"Includes your preferred {_join([_role_name(r) for r in covered])}.")

    if unsatisfiable:
        names = _join([_role_name(role) for role in unsatisfiable])
        lines.append(
            f"No ready lineup at this battle rating can include your preferred {names}, "
            "so no preference setting would change that."
        )
        target = _research_pointer_id(unsatisfiable, research_priorities)
        if target is not None:
            named = (vehicle_names or {}).get(target, target)
            lines.append(f"Researching {named} is the ranked path towards changing that.")

    if unmet:
        names = _join([_role_name(role) for role in unmet])
        lines.append(
            f"Another ready lineup includes your preferred {names}, but this one's "
            "objective evaluation was strong enough to stay the primary choice."
        )

    pairs = int(factors.get("duplicate_role_pairs") or 0)
    if pairs:
        noun = "duplicated role" if pairs == 1 else "duplicated roles"
        minimum = int(transparency.get("minimum_duplicate_role_pairs") or 0)
        if transparency.get("duplicate_roles_unavoidable") and pairs <= minimum:
            lines.append(
                f"Carries {pairs} {noun}; no ready lineup at this battle rating avoids that."
            )
        else:
            lines.append(f"Carries {pairs} {noun}.")

    if transparency.get("preference_changed_selection"):
        lines.append("Your preferences changed which lineup is recommended here.")
    elif covered or unmet or pairs:
        lines.append("Your preferences did not change which lineup is recommended here.")
    return lines


def build_warnings(analysis: Mapping[str, Any], blockers: Sequence[str]) -> list[str]:
    """Genuine problems only. A merely absent preference never appears here."""
    lines: list[str] = []
    for code in list(analysis.get("warnings") or ()) + list(blockers):
        text = WARNING_TEXT.get(code)
        if text is not None and text not in lines:
            lines.append(text)
    return lines


def build_evidence_summary(analysis: Mapping[str, Any]) -> dict[str, Any]:
    """Summarise per-vehicle statistical provenance without discarding any of it."""
    rules = analysis.get("rules") or ()
    statistical = next(
        (rule for rule in rules if rule.get("rule") == "statistical_strength"), None
    )
    vehicles: Mapping[str, Any] = (
        (statistical.get("evidence") or {}).get("vehicles") or {} if statistical else {}
    )
    total = len(vehicles)
    eligible = sum(1 for item in vehicles.values() if item.get("eligible"))
    proxy = sum(1 for item in vehicles.values() if item.get("proxy_used"))
    if not total:
        summary = "No community performance data is attached to this lineup."
    elif eligible == total and not proxy:
        summary = f"Community performance data is available for all {total} vehicles."
    elif eligible == total:
        summary = (
            f"Community performance data is available for all {total} vehicles; "
            f"{proxy} rely on proxy statistics."
        )
    elif eligible:
        summary = (
            f"Community performance data covers {eligible} of {total} vehicles"
            + (f", {proxy} through proxy statistics." if proxy else ".")
        )
    else:
        summary = f"No usable community performance data for these {total} vehicles."
    return {
        "summary": summary,
        "vehicle_count": total,
        "eligible_count": eligible,
        "proxy_count": proxy,
    }


def build_lineup_explanation(
    payload: Mapping[str, Any],
    *,
    transparency: Mapping[str, Any],
    blockers: Sequence[str] = (),
    research_priorities: Sequence[Mapping[str, Any]] = (),
    vehicle_names: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Player-facing explanation for one lineup, in three separated categories."""
    analysis = payload.get("analysis") or {}
    factors = payload.get("preference_factors") or {}
    unsatisfiable = list(transparency.get("unsatisfiable_preferred_roles") or ())
    return {
        "research_pointer": _research_pointer_id(unsatisfiable, research_priorities),
        "strengths": build_strengths(payload.get("reasons") or ()),
        "tradeoffs": build_tradeoffs(
            factors, transparency, research_priorities, vehicle_names
        ),
        "warnings": build_warnings(analysis, blockers),
        "evidence_summary": build_evidence_summary(analysis),
    }


def build_alternative_explanation(
    alternative: Mapping[str, Any], primary: Mapping[str, Any]
) -> dict[str, Any]:
    """Why a player might pick this alternative, without implying it is better."""
    alt_analysis = alternative.get("analysis") or {}
    primary_analysis = primary.get("analysis") or {}
    alt_factors = alternative.get("preference_factors") or {}
    primary_factors = primary.get("preference_factors") or {}

    alt_br = alt_analysis.get("lineup_br")
    primary_br = primary_analysis.get("lineup_br")
    objective_delta = float(alt_factors.get("objective_score") or 0.0) - float(
        primary_factors.get("objective_score") or 0.0
    )

    lines: list[str] = []
    if isinstance(alt_br, int) and isinstance(primary_br, int) and alt_br != primary_br:
        direction = "Lowers" if alt_br < primary_br else "Raises"
        lines.append(f"{direction} the battle rating to {alt_br / 10:.1f}.")

    alt_roles = set(alt_factors.get("preferred_roles_covered") or ())
    primary_roles = set(primary_factors.get("preferred_roles_covered") or ())
    gained = sorted(alt_roles - primary_roles)
    lost = sorted(primary_roles - alt_roles)
    if gained:
        lines.append(f"Adds your preferred {_join([_role_name(r) for r in gained])}.")
    if lost:
        lines.append(f"Gives up your preferred {_join([_role_name(r) for r in lost])}.")

    alt_pairs = int(alt_factors.get("duplicate_role_pairs") or 0)
    primary_pairs = int(primary_factors.get("duplicate_role_pairs") or 0)
    if alt_pairs != primary_pairs:
        direction = "Fewer" if alt_pairs < primary_pairs else "More"
        lines.append(f"{direction} duplicated roles ({alt_pairs} against {primary_pairs}).")

    # Raw lineup scores are only comparable within one battle rating, so a cross-BR
    # alternative deliberately reports no score delta at all.
    same_br = alt_br == primary_br
    if same_br and abs(objective_delta) >= 0.01:
        direction = "lower" if objective_delta < 0 else "higher"
        lines.append(
            f"Its objective evaluation is {abs(objective_delta):.1f} points {direction}."
        )
    elif not same_br:
        lines.append("Scores at different battle ratings are not directly comparable.")
    if not lines:
        lines.append("Closely comparable to the primary lineup.")
    return {
        "differences": lines,
        "objective_delta": objective_delta if same_br else None,
        "same_battle_rating": same_br,
    }
