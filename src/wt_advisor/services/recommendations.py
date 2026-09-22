"""Deterministic advisor answer assembled from the validated lineup services."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import copy
from datetime import UTC, datetime
from itertools import combinations
from typing import TYPE_CHECKING, Any

from wt_advisor.domain.models import CandidateLineup, Role, VehicleClass, stable_hash
from wt_advisor.rules import resolve_roles
from wt_advisor.services.explanations import (
    build_alternative_explanation,
    build_lineup_explanation,
)

if TYPE_CHECKING:
    from wt_advisor.services.advisor import AdvisorService

# Bumped whenever recommendation ordering or its interpretation changes, so that snapshots
# stored under an older policy report as stale through the existing fingerprint mechanism.
# This is deliberately separate from the evidence ruleset hash: ranking policy is code.
RECOMMENDATION_POLICY_VERSION = "bounded-preference-v1"

# Bounded preference ranking.
#
# Objective scores are a weighted mean of rule scores on a 0-100 scale. Measured against the
# live USA ground RB garage, adjacent objective gaps within a readiness-passing BR pool have
# a median of 0.004, p90 of 1.41 and p95 of 2.50, while the smallest per-BR top-to-bottom
# span is 7.32. Capping each term at 1.5 lets preferences cross roughly the lowest 98% of
# adjacent gaps while leaving a bottom-of-pool lineup unable to reach the top of any BR.
ROLE_CAP = 1.5
DUPLICATE_CAP = 1.5

# The invariant that matters is the DIFFERENTIAL: the largest objective-score deficit a
# preference can overcome is one candidate's best adjustment minus another's worst.
MAX_PREFERENCE_SWING = ROLE_CAP + DUPLICATE_CAP

# Outer bound on any single candidate's adjustment. The terms bound their own halves; they
# are not summed into this envelope.
PREFERENCE_CAP = max(ROLE_CAP, DUPLICATE_CAP)

# duplicate_role_penalty is stored as an int 0-10. It expresses duplicate-role AVOIDANCE
# STRENGTH, not a cap on preference influence generally: 0 disables the duplicate term and
# 10 drives it to DUPLICATE_CAP. It says nothing about the preferred-role term.
MAX_DUPLICATE_ROLE_PENALTY = 10

# Duplicate pairs observed under the canonical rule range 0-3, so saturating at 3 gives one
# distinct level per achievable value rather than flattening the top of the range.
DUPLICATE_SATURATION = 3

# One canonical role per vehicle class for duplicate counting. Capability-derived roles
# (scout, anti-armour specialist) never create duplicates, and a tank destroyer counts once
# rather than twice through both tank_destroyer and sniper.
CANONICAL_ROLE: Mapping[VehicleClass, Role] = {
    VehicleClass.LIGHT_TANK: Role.FLANKER,
    VehicleClass.MEDIUM_TANK: Role.GENERAL_MEDIUM,
    VehicleClass.HEAVY_TANK: Role.HEAVY_ANCHOR,
    VehicleClass.TANK_DESTROYER: Role.TANK_DESTROYER,
    VehicleClass.SPAA: Role.SPAA,
}

# SPAA duplication is already governed by the anti-air rule and the diagnostic specialist
# redundancy rule; counting it here would penalise the same thing twice.
DUPLICATE_EXEMPT_ROLES: frozenset[Role] = frozenset({Role.SPAA})

# Weighted-rule GOOD statuses that are genuinely favourable, mapped explicitly so that a
# newly added rule cannot silently become a user-facing strength. role_redundancy is absent
# on purpose: it is zero-weight, diagnostic, SPAA-only, and its GOOD status is vacuous when
# the lineup holds fewer than two SPAA. It is handled by _specialist_reason instead.
STRENGTH_REASONS: Mapping[str, str] = {
    "br_cohesion": "STRENGTH_BR_COHESION",
    "backup_depth": "STRENGTH_BACKUP_DEPTH",
    "role_coverage": "STRENGTH_ROLE_COVERAGE",
    "anti_air": "STRENGTH_ANTI_AIR",
    "scouting": "STRENGTH_SCOUTING",
    "uptier_resilience": "STRENGTH_UPTIER_RESILIENCE",
    "statistical_strength": "STRENGTH_STATISTICAL_STRENGTH",
}

if not (ROLE_CAP <= PREFERENCE_CAP and DUPLICATE_CAP <= PREFERENCE_CAP):  # pragma: no cover
    raise AssertionError("each preference term must stay within the preference envelope")


def capture_inputs(
    service: AdvisorService, profile_id: str
) -> tuple[AdvisorService, Any, str, dict[str, str]]:
    """Freeze mutable profile inputs and fingerprint every relevant revision."""
    resolved = service._resolve_profile_id(profile_id)
    frozen = copy(service)
    frozen._lineup_cache = {}
    captured = (
        None if frozen._repository is None
        else frozen._repository.capture_evaluation_snapshot(resolved)
    )
    if captured is not None:
        frozen._profile = captured.profile.to_domain()
        frozen._statuses = dict(captured.vehicle_statuses)
        frozen._repository = None
    context = None if captured is None else captured.context
    preset = None if captured is None else captured.preset
    inputs = {
        "profile": frozen._profile.model_dump(mode="json"),
        "statuses": {key: value.value for key, value in sorted(frozen._statuses.items())},
        "context": None if context is None else {
            "target_br": context.target_br,
            "required": context.required_vehicle_ids,
            "excluded": context.excluded_vehicle_ids,
            "roles": [role.value for role in context.preferred_roles],
            "duplicate_penalty": context.duplicate_role_penalty,
            "selected_preset": context.selected_preset_id,
        },
        "preset": None if preset is None else {
            "id": preset.preset_id, "revision": preset.revision,
            "slots": preset.slots, "required": preset.required_vehicle_ids,
            "excluded": preset.excluded_vehicle_ids,
        },
        "evidence": frozen._evidence_context.model_dump(mode="json"),
    }
    revisions = {
        "profile_garage": stable_hash({
            "profile": inputs["profile"], "statuses": inputs["statuses"]
        }),
        "context": stable_hash({"context": inputs["context"]}),
        "preset": stable_hash({"preset": inputs["preset"]}),
        "evidence": stable_hash({"evidence": inputs["evidence"]}),
        # Ranking policy lives in code, not in the evidence ruleset, so it needs its own
        # revision. Bumping it stales every snapshot stored under the previous policy.
        "policy": stable_hash({"policy": RECOMMENDATION_POLICY_VERSION}),
    }
    return frozen, captured, stable_hash(revisions), revisions


def _covered_roles(candidate: CandidateLineup, service: AdvisorService) -> frozenset[Role]:
    """Every role the lineup covers, as the active ruleset resolves them."""
    resolutions = (
        {
            vehicle_id: {
                resolution.capability: resolution
                for resolution in service._capability_resolutions.get(vehicle_id, ())
            }
            for vehicle_id in candidate.analysis.lineup.slots
        }
        if service._capability_resolutions
        else None
    )
    covered: frozenset[Role] = frozenset()
    for vehicle_id in candidate.analysis.lineup.slots:
        covered |= resolve_roles(
            service._vehicles[vehicle_id],
            capability_resolutions=resolutions,
            ruleset=service._ruleset,
        )
    return covered


def _duplicate_role_pairs(candidate: CandidateLineup, vehicles: Mapping[str, Any]) -> int:
    """Occurrences beyond the first of each canonical role, excluding exempt roles."""
    counts: dict[Role, int] = {}
    for vehicle_id in candidate.analysis.lineup.slots:
        role = CANONICAL_ROLE[vehicles[vehicle_id].vehicle_class]
        if role in DUPLICATE_EXEMPT_ROLES:
            continue
        counts[role] = counts.get(role, 0) + 1
    return sum(max(0, count - 1) for count in counts.values())


def _preference_factors(
    candidate: CandidateLineup,
    roles: tuple[Role, ...],
    penalty: int,
    service: AdvisorService,
) -> dict[str, Any]:
    """Bounded, inspectable preference adjustment for one candidate.

    Objective scoring is untouched; this is a separate, separately bounded term. Preferred
    roles use the full capability-aware role set, duplicates use one canonical role per
    vehicle. Both normalise to 0..1 before scaling so the two terms are commensurable.
    """
    analysis = candidate.analysis
    covered = _covered_roles(candidate, service)
    satisfied = [role for role in roles if role in covered]
    missing = [role for role in roles if role not in covered]
    # Rank weighted: the first preferred role carries the most weight.
    total_weight = len(roles) * (len(roles) + 1) / 2
    role_satisfaction = (
        sum(len(roles) - index for index, role in enumerate(roles) if role in covered)
        / total_weight
        if roles
        else 0.0
    )
    duplicate_pairs = _duplicate_role_pairs(candidate, service._vehicles)
    duplicate_strength = penalty / MAX_DUPLICATE_ROLE_PENALTY
    duplicate_load = min(duplicate_pairs, DUPLICATE_SATURATION) / DUPLICATE_SATURATION
    role_component = ROLE_CAP * role_satisfaction
    duplicate_component = -DUPLICATE_CAP * duplicate_strength * duplicate_load
    adjustment = role_component + duplicate_component
    return {
        "preferred_roles_configured": [role.value for role in roles],
        "preferred_roles_covered": [role.value for role in satisfied],
        "preferred_roles_missing": [role.value for role in missing],
        "role_satisfaction": role_satisfaction,
        "role_component": role_component,
        "duplicate_role_pairs": duplicate_pairs,
        "duplicate_role_penalty": penalty,
        "duplicate_strength": duplicate_strength,
        "duplicate_load": duplicate_load,
        "duplicate_component": duplicate_component,
        "preference_adjustment": adjustment,
        "objective_score": analysis.overall_score,
        "recommendation_score": analysis.overall_score + adjustment,
        "role_cap": ROLE_CAP,
        "duplicate_cap": DUPLICATE_CAP,
        "preference_cap": PREFERENCE_CAP,
        "max_preference_swing": MAX_PREFERENCE_SWING,
    }


def _lineup_key(
    candidate: CandidateLineup,
    roles: tuple[Role, ...],
    penalty: int,
    service: AdvisorService,
) -> tuple[Any, ...]:
    """Rank by recommendation score, then objective score, then a stable slot tuple."""
    factors = _preference_factors(candidate, roles, penalty, service)
    return (
        -factors["recommendation_score"],
        -candidate.analysis.overall_score,
        candidate.analysis.lineup.slots,
    )


def _objective_key(candidate: CandidateLineup) -> tuple[Any, ...]:
    """The ordering that would apply with every preference disabled."""
    return (-candidate.analysis.overall_score, candidate.analysis.lineup.slots)


def _specialist_reason(analysis: Any) -> str | None:
    """Report absent specialist overlap only where the diagnostic compared a real pair.

    role_redundancy is zero weight, SPAA only and diagnostic. Its GOOD status is vacuous
    below two SPAA, so presenting it as a lineup strength would be misleading.
    """
    rule = next((item for item in analysis.rules if item.rule == "role_redundancy"), None)
    if rule is None or not rule.evidence.get("pair_evidence"):
        return None
    if rule.evidence.get("redundant_pairs"):
        return None
    return "NO_SPECIALIST_OVERLAP"


def _lineup_payload(
    candidate: CandidateLineup,
    roles: tuple[Role, ...],
    penalty: int,
    service: AdvisorService,
) -> dict[str, Any]:
    analysis = candidate.analysis
    reasons = ["HIGHEST_READY_BR", "OBJECTIVE_SCORE_RANK"]
    reasons.extend(
        STRENGTH_REASONS[code] for code in analysis.strengths if code in STRENGTH_REASONS
    )
    specialist = _specialist_reason(analysis)
    if specialist is not None:
        reasons.append(specialist)
    return {
        "analysis": analysis.model_dump(mode="json"),
        "reasons": reasons,
        "preference_factors": _preference_factors(candidate, roles, penalty, service),
    }


def _least_deficient(groups: Any) -> CandidateLineup | None:
    candidates = [candidate for group in groups for candidate in group.candidates]
    return min(candidates, key=lambda item: (
        len(item.analysis.readiness_failures), -item.analysis.overall_score,
        item.analysis.lineup.slots,
    ), default=None)


def _ready_pool_at_br(
    service: AdvisorService,
    *,
    lineup_br: int,
    required: frozenset[str],
    excluded: frozenset[str],
) -> list[CandidateLineup]:
    """Every readiness-passing lineup at one BR, before Pareto pruning.

    Pareto dominance compares objective rule scores only. It is blind to readiness and to
    role composition, so a preference-satisfying lineup can be pruned away before the
    preference layer ever sees it; at the live BR 3.0 case it cuts seven candidates to one.
    Preference ranking therefore uses the full readiness-passing set at the already-selected
    BR. Readiness, ownership, crew slots and the required/excluded constraints are all still
    enforced here, so widening cannot re-admit a candidate that violates a hard invariant.
    Analyses come from the snapshot lineup cache, so this re-enumeration costs lookups only.
    """
    eligible = tuple(
        item for item in service._eligible_owned(frozenset()) if item not in excluded
    )
    if not eligible:
        return []
    slot_count = min(service._profile.crew_slots, len(eligible))
    pool: list[CandidateLineup] = []
    for vehicle_ids in combinations(eligible, slot_count):
        if required and not required.issubset(vehicle_ids):
            continue
        cache = service._lineup_cache
        analysis = None if cache is None else cache.get(vehicle_ids)
        if analysis is None:
            analysis = service.analyze_lineup(vehicle_ids)
            if cache is not None and len(cache) < 5_000:
                cache[vehicle_ids] = analysis
        if analysis.lineup_br != lineup_br or not analysis.readiness_passed:
            continue
        pool.append(CandidateLineup(analysis=analysis))
    return pool


def _preference_transparency(
    ranked: Sequence[CandidateLineup],
    roles: tuple[Role, ...],
    penalty: int,
    service: AdvisorService,
) -> dict[str, Any]:
    """Make the preference calculation answerable without reading source code.

    Separates two very different situations that both look like "my preference was
    ignored": the preference lost to a materially stronger lineup, and no ready lineup at
    this BR can satisfy it at all.
    """
    if not ranked:
        return {
            "preference_changed_selection": False,
            "objective_first_choice": None,
            "candidate_pool_size": 0,
            "unsatisfiable_preferred_roles": [],
            "unmet_preferred_roles": [],
            "minimum_duplicate_role_pairs": 0,
            "duplicate_roles_unavoidable": False,
        }
    objective_first = min(ranked, key=_objective_key)
    changed = objective_first.analysis.lineup.slots != ranked[0].analysis.lineup.slots
    satisfiable: set[Role] = set()
    for candidate in ranked:
        satisfiable |= _covered_roles(candidate, service) & set(roles)
    primary_covered = _covered_roles(ranked[0], service)
    minimum_pairs = min(
        _duplicate_role_pairs(candidate, service._vehicles) for candidate in ranked
    )
    return {
        "preference_changed_selection": changed,
        "objective_first_choice": (
            {
                "slots": list(objective_first.analysis.lineup.slots),
                "objective_score": objective_first.analysis.overall_score,
            }
            if changed
            else None
        ),
        "candidate_pool_size": len(ranked),
        # No ready lineup at this BR can cover these, whatever the preference weighting.
        "unsatisfiable_preferred_roles": [
            role.value for role in roles if role not in satisfiable
        ],
        # The primary lacks these, but another ready lineup at this BR does cover them.
        "unmet_preferred_roles": [
            role.value
            for role in roles
            if role not in primary_covered and role in satisfiable
        ],
        "minimum_duplicate_role_pairs": minimum_pairs,
        "duplicate_roles_unavoidable": minimum_pairs > 0 and penalty > 0,
    }


def _best_expanded(
    before: CandidateLineup | None,
    forced: CandidateLineup | None,
    roles: tuple[Role, ...],
    penalty: int,
    service: AdvisorService,
) -> CandidateLineup | None:
    if before is None:
        return forced
    if forced is None:
        return before
    ready = [item for item in (before, forced) if item.analysis.readiness_passed]
    if ready:
        highest_br = max(item.analysis.lineup_br for item in ready)
        same_br = [item for item in ready if item.analysis.lineup_br == highest_br]
        return min(same_br, key=lambda item: _lineup_key(item, roles, penalty, service))
    return min(
        (before, forced),
        key=lambda item: (
            len(item.analysis.readiness_failures), -item.analysis.overall_score,
            item.analysis.lineup.slots,
        ),
    )


def _research(
    service: AdvisorService,
    profile_id: str,
    baseline: CandidateLineup | None,
    penalty: int,
    target_br: int | None,
    required: frozenset[str],
    excluded: frozenset[str],
    preferred_roles: tuple[Role, ...],
) -> list[dict[str, Any]]:
    try:
        researchable = service._directly_researchable()
    except ValueError:
        return []
    ranked: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    for vehicle_id in researchable:
        if vehicle_id in excluded:
            continue
        try:
            forced = service.generate_lineups(
                profile_id=profile_id, hypothetical_owned=frozenset({vehicle_id}),
                target_br=target_br, required_vehicle_ids=required | {vehicle_id}, top_n=1,
                excluded_vehicle_ids=excluded, allow_partial=True,
            )
        except ValueError:
            continue
        included = forced.recommended or _least_deficient(forced.groups)
        best = _best_expanded(baseline, included, preferred_roles, penalty, service)
        if best is None or included is None:
            continue
        before = None if baseline is None else baseline.analysis
        after = best.analysis
        gains_ready = after.readiness_passed and (before is None or not before.readiness_passed)
        higher_ready = bool(before and before.readiness_passed and after.readiness_passed
                            and after.lineup_br > before.lineup_br)
        resolved = () if before is None else tuple(sorted(
            set(before.readiness_failures) - set(after.readiness_failures)
        ))
        score_delta = (
            after.overall_score - before.overall_score
            if before is not None and after.lineup_br == before.lineup_br else None
        )
        adopted = vehicle_id in after.lineup.slots
        preferred = adopted and bool(
            set(preferred_roles)
            & set(resolve_roles(service._vehicles[vehicle_id], ruleset=service._ruleset))
        )
        statistical = next(
            (rule for rule in included.analysis.rules if rule.rule == "statistical_strength"),
            None,
        )
        vehicle_evidence = (
            {} if statistical is None
            else statistical.evidence.get("vehicles", {}).get(vehicle_id, {})
        )
        evidence_quality = (
            2 if vehicle_evidence.get("eligible") and not vehicle_evidence.get("proxy_used")
            else 1 if vehicle_evidence.get("eligible") else 0
        )
        reasons = [code for code, active in (
            ("GAINS_READY_LINEUP", gains_ready), ("OPENS_HIGHER_READY_BR", higher_ready),
            ("RESOLVES_READINESS_BLOCKER", bool(resolved)),
            ("IMPROVES_SAME_BR_SCORE", score_delta is not None and score_delta > 0),
            ("ADDS_PREFERRED_ROLE", preferred), ("ADOPTED_IN_BEST_LINEUP", adopted),
        ) if active] or ["NO_IMMEDIATE_LINEUP_GAIN"]
        cost = service._vehicles[vehicle_id].research_cost or 0
        key = (-int(gains_ready or higher_ready), -len(resolved),
               -int(score_delta is not None and score_delta > 0),
               -int(preferred), -int(adopted), -evidence_quality, cost, vehicle_id)
        ranked.append((key, {
            "vehicle_id": vehicle_id, "reasons": reasons,
            "recovery_target": baseline is None or not baseline.analysis.readiness_passed,
            "research_cost": cost, "resulting_br": after.lineup_br,
            "readiness_after": after.readiness_passed,
            "resolved_blockers": resolved, "same_br_score_delta": score_delta,
            "evidence_quality": evidence_quality,
            "ranking_factors": {
                "gains_ready_lineup": gains_ready,
                "opens_higher_ready_br": higher_ready,
                "resolved_blocker_count": len(resolved),
                "improves_same_br_score": score_delta is not None and score_delta > 0,
                "adds_preferred_role": preferred,
                "adopted_in_best_lineup": adopted,
                "evidence_quality": evidence_quality,
                "research_cost": cost,
                "vehicle_id_tie_break": vehicle_id,
            },
            "expanded_lineup": after.model_dump(mode="json"),
            "forced_lineup": included.analysis.model_dump(mode="json"),
        }))
    return [dict(item, rank=rank) for rank, (_, item) in enumerate(sorted(ranked), 1)]


def compute_snapshot(service: AdvisorService, profile_id: str) -> dict[str, Any]:
    frozen, captured, fingerprint, revisions = capture_inputs(service, profile_id)
    frozen._advisor_scoring_statistics = frozen._scoring_statistics()
    frozen._advisor_peer_statistics = frozen._peer_statistics()
    frozen._advisor_statistical_rule_cache = {}
    context = None if captured is None else captured.context
    preset = None if captured is None else captured.preset
    required = frozenset((
        *(() if context is None else context.required_vehicle_ids),
        *(() if preset is None else preset.slots),
        *(() if preset is None else preset.required_vehicle_ids),
    ))
    excluded = frozenset((
        *(() if context is None else context.excluded_vehicle_ids),
        *(() if preset is None else preset.excluded_vehicle_ids),
    ))
    roles: tuple[Role, ...] = () if context is None else context.preferred_roles
    penalty = 0 if context is None else context.duplicate_role_penalty
    blockers: list[str] = []
    gaps = list(frozen._service_warnings)
    try:
        generated = frozen.generate_lineups(
            profile_id=profile_id, target_br=None if context is None else context.target_br,
            top_n=100, required_vehicle_ids=required, excluded_vehicle_ids=excluded,
            allow_partial=True,
        )
    except ValueError as exc:
        blockers.append(str(exc))
        generated = None
    primary = None
    alternatives: list[CandidateLineup] = []
    baseline = None
    transparency: dict[str, Any] = _preference_transparency((), roles, penalty, frozen)
    if generated is not None:
        ready = [item for group in generated.groups for item in group.candidates
                 if item.analysis.readiness_passed]
        if ready:
            # The BR frontier still selects the band; preferences only sort within it.
            highest_br = max(item.analysis.lineup_br for item in ready)
            same_br = _ready_pool_at_br(
                frozen, lineup_br=highest_br, required=required, excluded=excluded
            ) or [item for item in ready if item.analysis.lineup_br == highest_br]
            ranked = sorted(
                same_br, key=lambda item: _lineup_key(item, roles, penalty, frozen)
            )
            primary = ranked[0]
            transparency = _preference_transparency(ranked, roles, penalty, frozen)
            alternatives = ranked[1:3]
            if generated.lower_br_alternative is not None:
                lower = _ready_pool_at_br(
                    frozen,
                    lineup_br=generated.lower_br_alternative.lineup_br,
                    required=required,
                    excluded=excluded,
                ) or [item for item in generated.lower_br_alternative.candidates
                      if item.analysis.readiness_passed]
                if lower:
                    alternatives.append(sorted(lower, key=lambda item: _lineup_key(
                        item, roles, penalty, frozen))[0])
        else:
            blockers.append("no_readiness_passing_lineup")
            baseline = _least_deficient(generated.groups)
            if baseline is not None:
                blockers.extend(baseline.analysis.readiness_failures)
    if not frozen._research_edges:
        gaps.append("progression_unavailable")
    diagnostic = primary or baseline
    if diagnostic is not None:
        statistical = next(
            (rule for rule in diagnostic.analysis.rules if rule.rule == "statistical_strength"),
            None,
        )
        if statistical is not None and statistical.warnings:
            gaps.append("statistics_missing_or_incompatible")
    unique_blockers = list(dict.fromkeys(blockers))
    research = _research(
        frozen, profile_id, primary or baseline, penalty,
        None if context is None else context.target_br, required, excluded, roles,
    ) if generated is not None else []
    primary_payload = (
        None if primary is None else _lineup_payload(primary, roles, penalty, frozen)
    )
    if primary_payload is not None:
        primary_payload["explanation"] = build_lineup_explanation(
            primary_payload,
            transparency=transparency,
            blockers=unique_blockers,
            research_priorities=research,
        )
    alternative_payloads = [
        _lineup_payload(item, roles, penalty, frozen) for item in alternatives
    ]
    for payload in alternative_payloads:
        payload["explanation"] = build_lineup_explanation(
            payload,
            transparency=transparency,
            blockers=unique_blockers,
            research_priorities=research,
        )
        if primary_payload is not None:
            payload["differentiation"] = build_alternative_explanation(
                payload, primary_payload
            )
    return {
        "profile_id": frozen._profile.profile_id,
        "recommended_br": None if primary is None else primary.analysis.lineup_br,
        "primary_lineup": primary_payload,
        "alternative_lineups": alternative_payloads,
        "preference_transparency": transparency,
        "research_priorities": research,
        "blockers": unique_blockers,
        "data_gaps": list(dict.fromkeys(gaps)),
        "evidence_context": frozen._evidence_context.model_dump(mode="json"),
        "source_fingerprint": fingerprint,
        "source_revisions": revisions,
        "generated_at": datetime.now(UTC).isoformat(),
    }
