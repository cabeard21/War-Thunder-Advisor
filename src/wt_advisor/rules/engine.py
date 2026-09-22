"""Transparent lineup scoring from immutable domain facts."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping, Sequence
from datetime import date
from importlib.resources import files
from pathlib import Path
from statistics import fmean, pstdev
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from wt_advisor.domain.models import (
    BR_LADDER,
    Capability,
    CapabilityResolution,
    CapabilityState,
    KillTargetDefinition,
    RatioProvenance,
    Role,
    RuleResult,
    RuleStatus,
    StatisticsScope,
    Vehicle,
    VehicleClass,
    VehicleStatistics,
    VehicleStatus,
    br_step_distance,
    stable_hash,
)

CapabilityResolutionInput = CapabilityResolution | CapabilityState | str
CapabilityResolutionMap = Mapping[
    str, Mapping[Capability, CapabilityResolutionInput]
]


class _ConfigModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RuleWeights(_ConfigModel):
    br_cohesion: float = Field(ge=0)
    backup_depth: float = Field(ge=0)
    role_coverage: float = Field(ge=0)
    anti_air: float = Field(ge=0)
    scouting: float = Field(ge=0)
    uptier_resilience: float = Field(ge=0)
    statistical_strength: float = Field(ge=0)
    role_redundancy: float = Field(default=0.0, ge=0)

    @model_validator(mode="after")
    def sum_to_one(self) -> RuleWeights:
        if abs(sum(self.model_dump().values()) - 1.0) > 1e-9:
            raise ValueError("rule weights must sum to 1.0")
        return self


class BandScores(_ConfigModel):
    top: float = Field(ge=0, le=100)
    one_step: float = Field(ge=0, le=100)
    two_steps: float = Field(ge=0, le=100)
    farther: float = Field(ge=0, le=100)


class BackupScores(_ConfigModel):
    top: float = Field(ge=0)
    one_step: float = Field(ge=0)
    two_steps: float = Field(ge=0)
    farther: float = Field(ge=0)
    desired_vehicles: int = Field(gt=0)


class RoleCoverageWeights(_ConfigModel):
    frontline: float = Field(ge=0)
    mobility: float = Field(ge=0)
    anti_armor: float = Field(ge=0)
    anti_air: float = Field(ge=0)

    @model_validator(mode="after")
    def sum_to_one(self) -> RoleCoverageWeights:
        if abs(sum(self.model_dump().values()) - 1.0) > 1e-9:
            raise ValueError("role coverage weights must sum to 1.0")
        return self


class UptierScores(_ConfigModel):
    high: float = Field(ge=0, le=100)
    medium: float = Field(ge=0, le=100)
    low: float = Field(ge=0, le=100)
    unknown: float = Field(ge=0, le=100)


class StatisticsConfig(_ConfigModel):
    win_rate_weight: float = Field(ge=0)
    kd_weight: float = Field(ge=0)
    kills_per_battle_weight: float = Field(ge=0)
    win_rate_k: float = Field(gt=0)
    kills_per_battle_k: float = Field(gt=0)
    kd_k: float = Field(gt=0)
    minimum_peers: int = Field(gt=0)
    neutral_score: float = Field(ge=0, le=100)
    community_proxy_factor: float = Field(default=0.5, ge=0, le=1)

    @model_validator(mode="after")
    def metric_weights_sum_to_one(self) -> StatisticsConfig:
        weights = self.win_rate_weight + self.kd_weight + self.kills_per_battle_weight
        if abs(weights - 1.0) > 1e-9:
            raise ValueError("statistical metric weights must sum to 1.0")
        return self


class ReadinessConfig(_ConfigModel):
    minimum_within_one_step: int = Field(gt=0)
    minimum_within_two_steps: int = Field(gt=0)
    minimum_backup_depth: float = Field(ge=0, le=100)
    minimum_non_spaa_combat: int = Field(gt=0)
    severe_count_more_than_two_steps: int = Field(gt=0)
    severe_any_more_than_steps: int = Field(gt=0)


class StatusConfig(_ConfigModel):
    good_at_least: float = Field(ge=0, le=100)
    critical_below: float = Field(ge=0, le=100)

    @model_validator(mode="after")
    def thresholds_are_ordered(self) -> StatusConfig:
        if self.critical_below >= self.good_at_least:
            raise ValueError("critical threshold must be below good threshold")
        return self


class Ruleset(_ConfigModel):
    ruleset_id: str = "m1-baseline-v1"
    scoring_policy_version: str = "legacy"
    weights: RuleWeights
    br_cohesion: BandScores
    backup_depth: BackupScores
    role_coverage: RoleCoverageWeights
    uptier: UptierScores
    statistics: StatisticsConfig
    readiness: ReadinessConfig
    statuses: StatusConfig

    @computed_field  # type: ignore[prop-decorator]
    @property
    def content_hash(self) -> str:
        # Fields introduced after M1 are excluded when absent from its source TOML. This keeps
        # the frozen M1 hash stable while allowing later rulesets to grow explicitly.
        return stable_hash(
            self.model_dump(exclude={"content_hash"}, exclude_unset=True)
        )


class PeerStatistic(_ConfigModel):
    vehicle: Vehicle
    battle_rating: int
    statistics: VehicleStatistics


class MetricPerformance(_ConfigModel):
    raw_value: float
    adjusted_value: float
    peer_mean: float
    peer_deviation: float
    sample_size: int
    confidence_weight: float
    score: float


class RulesEvaluation(_ConfigModel):
    rules: tuple[RuleResult, ...]
    overall_score: float = Field(ge=0, le=100)
    readiness_passed: bool
    readiness_failures: tuple[str, ...]


def load_ruleset(path: str | Path | None = None) -> Ruleset:
    """Load and validate a ruleset; defaults are packaged with the application."""

    named_rulesets = {
        "m1-baseline-v1": "m1-baseline-v1.toml",
        "m2-capability-aware-v1": "m2-capability-aware-v1.toml",
    }
    if path is None:
        resource = files("wt_advisor").joinpath("config/defaults.toml")
        payload = tomllib.loads(resource.read_text(encoding="utf-8"))
    elif str(path) in named_rulesets:
        resource = files("wt_advisor").joinpath("config", named_rulesets[str(path)])
        payload = tomllib.loads(resource.read_text(encoding="utf-8"))
    else:
        with Path(path).open("rb") as stream:
            payload = tomllib.load(stream)
    return Ruleset.model_validate(payload)


def _ruleset(ruleset: Ruleset | None) -> Ruleset:
    return ruleset if ruleset is not None else load_ruleset()


def _status(score: float, ruleset: Ruleset) -> RuleStatus:
    if score >= ruleset.statuses.good_at_least:
        return RuleStatus.GOOD
    if score < ruleset.statuses.critical_below:
        return RuleStatus.CRITICAL
    return RuleStatus.WARNING


def infer_roles(vehicle: Vehicle) -> frozenset[Role]:
    """Infer lineup roles solely from factual class and capability data."""

    roles = _class_roles(vehicle)
    if Capability.SCOUTING in vehicle.capabilities:
        roles.add(Role.SCOUT)
    if vehicle.capabilities & {Capability.ATGM, Capability.HIGH_CALIBER_HE}:
        roles.add(Role.ANTI_ARMOR_SPECIALIST)
    return frozenset(roles)


def _class_roles(vehicle: Vehicle) -> set[Role]:
    roles: set[Role] = set()
    if vehicle.vehicle_class is VehicleClass.SPAA:
        roles.add(Role.SPAA)
    elif vehicle.vehicle_class is VehicleClass.LIGHT_TANK:
        roles.add(Role.FLANKER)
    elif vehicle.vehicle_class is VehicleClass.MEDIUM_TANK:
        roles.add(Role.GENERAL_MEDIUM)
    elif vehicle.vehicle_class is VehicleClass.HEAVY_TANK:
        roles.update((Role.HEAVY_ANCHOR, Role.BRAWLER))
    elif vehicle.vehicle_class is VehicleClass.TANK_DESTROYER:
        roles.update((Role.TANK_DESTROYER, Role.SNIPER))

    return roles


def _distance_scores(
    resolved_brs: Mapping[str, int], lineup_br: int, bands: BandScores
) -> dict[str, float]:
    return {
        vehicle_id: (
            bands.top
            if (distance := br_step_distance(lineup_br, battle_rating)) == 0
            else bands.one_step
            if distance == 1
            else bands.two_steps
            if distance == 2
            else bands.farther
        )
        for vehicle_id, battle_rating in sorted(resolved_brs.items())
    }


def br_cohesion_rule(
    resolved_brs: Mapping[str, int], lineup_br: int, ruleset: Ruleset | None = None
) -> RuleResult:
    config = _ruleset(ruleset)
    scores = _distance_scores(resolved_brs, lineup_br, config.br_cohesion)
    score = fmean(scores.values())
    weak = tuple(vehicle_id for vehicle_id, value in scores.items() if value == 0)
    return RuleResult(
        rule="br_cohesion",
        score=score,
        effective_score=score,
        status=_status(score, config),
        evidence={"lineup_br": lineup_br, "slot_scores": scores, "far_below": list(weak)},
        warnings=("vehicles more than two BR steps below lineup BR",) if weak else (),
        explanation="Each slot scores 100/80/50/0 at zero/one/two/more BR ladder steps below.",
    )


def backup_depth_rule(
    resolved_brs: Mapping[str, int], lineup_br: int, ruleset: Ruleset | None = None
) -> RuleResult:
    config = _ruleset(ruleset)
    bands = config.backup_depth
    values: dict[str, float] = {}
    for vehicle_id, battle_rating in sorted(resolved_brs.items()):
        distance = br_step_distance(lineup_br, battle_rating)
        values[vehicle_id] = (
            bands.top
            if distance == 0
            else bands.one_step
            if distance == 1
            else bands.two_steps
            if distance == 2
            else bands.farther
        )
    strongest = sorted(values.values(), reverse=True)[: bands.desired_vehicles]
    score = min(100.0, sum(strongest) / bands.desired_vehicles * 100.0)
    competitive_count = sum(value > 0 for value in values.values())
    return RuleResult(
        rule="backup_depth",
        score=score,
        effective_score=score,
        status=_status(score, config),
        evidence={
            "slot_weights": values,
            "desired_depth": bands.desired_vehicles,
            "competitive_count": competitive_count,
        },
        warnings=("lineup lacks three competitive backups",) if competitive_count < 3 else (),
        explanation="The three strongest slots contribute 1.0/0.75/0.25/0 by BR band.",
    )


def role_coverage_rule(
    vehicles: Sequence[Vehicle],
    ruleset: Ruleset | None = None,
    *,
    capability_resolutions: CapabilityResolutionMap | None = None,
) -> RuleResult:
    config = _ruleset(ruleset)
    role_map = {
        vehicle.vehicle_id: (
            _m2_roles(vehicle, capability_resolutions)
            if config.ruleset_id == "m2-capability-aware-v1"
            else infer_roles(vehicle)
        )
        for vehicle in vehicles
    }
    all_roles = frozenset().union(*role_map.values()) if role_map else frozenset()
    categories = {
        "frontline": bool(
            all_roles & {Role.BRAWLER, Role.GENERAL_MEDIUM, Role.HEAVY_ANCHOR}
        ),
        "mobility": bool(all_roles & {Role.FLANKER, Role.SCOUT}),
        "anti_armor": bool(
            all_roles
            & {Role.TANK_DESTROYER, Role.SNIPER, Role.ANTI_ARMOR_SPECIALIST}
        ),
        "anti_air": Role.SPAA in all_roles,
    }
    weights = config.role_coverage.model_dump()
    score = sum(weights[name] * 100 for name, covered in categories.items() if covered)
    missing = [name for name, covered in categories.items() if not covered]
    role_counts = {
        role.value: sum(role in vehicle_roles for vehicle_roles in role_map.values())
        for role in sorted(all_roles, key=lambda item: item.value)
    }
    excessive_overlap = sorted(role for role, count in role_counts.items() if count >= 3)
    return RuleResult(
        rule="role_coverage",
        score=score,
        effective_score=score,
        status=_status(score, config),
        evidence={
            "roles_by_vehicle": {
                key: sorted(role.value for role in value) for key, value in sorted(role_map.items())
            },
            "covered_categories": categories,
            "missing_categories": missing,
            "role_counts": role_counts,
            "excessive_overlap": excessive_overlap,
        },
        warnings=("missing role categories: " + ", ".join(missing),) if missing else (),
        explanation="Coverage is soft-weighted across frontline, mobility, anti-armor, and AA.",
    )


def _m2_roles(
    vehicle: Vehicle, resolutions: CapabilityResolutionMap | None
) -> frozenset[Role]:
    roles = _class_roles(vehicle)
    if _capability_state(vehicle, Capability.SCOUTING, resolutions) == "present":
        roles.add(Role.SCOUT)
    if any(
        _capability_state(vehicle, capability, resolutions) == "present"
        for capability in (Capability.ATGM, Capability.HIGH_CALIBER_HE)
    ):
        roles.add(Role.ANTI_ARMOR_SPECIALIST)
    return frozenset(roles)


def anti_air_rule(
    vehicles: Sequence[Vehicle],
    nearby_spaa: Sequence[tuple[Vehicle, VehicleStatus]] = (),
    ruleset: Ruleset | None = None,
) -> RuleResult:
    _ruleset(ruleset)  # Validate an explicitly supplied ruleset consistently.
    lineup_spaa = sorted(
        vehicle.vehicle_id for vehicle in vehicles if vehicle.vehicle_class is VehicleClass.SPAA
    )
    nearby = sorted((vehicle.vehicle_id, status.value) for vehicle, status in nearby_spaa)
    if lineup_spaa:
        state, score, status = "included", 100.0, RuleStatus.GOOD
        warnings: tuple[str, ...] = ()
    elif any(status is VehicleStatus.OWNED for _, status in nearby_spaa):
        state, score, status = "owned_but_omitted", 0.0, RuleStatus.CRITICAL
        warnings = ("owned relevant SPAA omitted",)
    elif nearby_spaa:
        state, score, status = "available_but_unowned", 35.0, RuleStatus.WARNING
        warnings = ("relevant SPAA is not owned",)
    else:
        state, score, status = "absent_near_br", 50.0, RuleStatus.UNKNOWN
        warnings = ("no relevant SPAA evidence near lineup BR",)
    return RuleResult(
        rule="anti_air",
        score=None if status is RuleStatus.UNKNOWN else score,
        effective_score=score,
        status=status,
        evidence={"state": state, "lineup_spaa": lineup_spaa, "nearby_spaa": nearby},
        warnings=warnings,
        explanation="AA distinguishes included, owned-but-omitted, unowned, and absent evidence.",
    )


def scouting_rule(
    vehicles: Sequence[Vehicle],
    ruleset: Ruleset | None = None,
    *,
    capability_resolutions: CapabilityResolutionMap | None = None,
) -> RuleResult:
    config = _ruleset(ruleset)
    if config.ruleset_id != "m2-capability-aware-v1":
        scouts = sorted(
            vehicle.vehicle_id
            for vehicle in vehicles
            if Capability.SCOUTING in vehicle.capabilities
        )
        legacy_score = 100.0 if scouts else 0.0
        return RuleResult(
            rule="scouting",
            score=legacy_score,
            effective_score=legacy_score,
            status=_status(legacy_score, config),
            evidence={"scouting_vehicle_ids": scouts},
            warnings=() if scouts else ("lineup has no scouting capability",),
            explanation="Scouting is present when at least one vehicle has the factual capability.",
        )

    states = {
        vehicle.vehicle_id: _capability_state(
            vehicle, Capability.SCOUTING, capability_resolutions
        )
        for vehicle in vehicles
    }
    scouts = sorted(
        vehicle_id for vehicle_id, state in states.items() if state == "present"
    )
    heuristic_scouts = sorted(
        vehicle.vehicle_id
        for vehicle in vehicles
        if vehicle.vehicle_class is VehicleClass.LIGHT_TANK
        and vehicle.rank >= 2
        and states[vehicle.vehicle_id] == "unknown"
        and _resolution_value(
            (capability_resolutions or {}).get(vehicle.vehicle_id, {}).get(
                Capability.SCOUTING, "unknown"
            )
        ) == "unknown"
    )
    score: float | None
    if scouts:
        state, score, effective, status = "present", 100.0, 100.0, RuleStatus.GOOD
        warnings: tuple[str, ...] = ()
    elif states and all(value == "verified_absent" for value in states.values()):
        state, score, effective, status = (
            "verified_absent",
            0.0,
            0.0,
            RuleStatus.CRITICAL,
        )
        warnings = ("lineup has no scouting capability",)
    else:
        state, score, effective, status = "unknown", None, 50.0, RuleStatus.UNKNOWN
        warnings = ("capability_evidence_incomplete",)
        if heuristic_scouts:
            effective = 65.0
    evidence: dict[str, Any] = {
        "state": state,
        "scouting_vehicle_ids": scouts,
        "state_by_vehicle": dict(sorted(states.items())),
    }
    if state == "unknown" and heuristic_scouts:
        evidence["heuristic"] = {
            "id": "rank-ii-light-tank-scouting-v1",
            "vehicle_ids": heuristic_scouts,
            "basis": "rank_ii_or_higher_light_tank_with_unknown_scouting",
            "verified_capability": False,
        }
    return RuleResult(
        rule="scouting",
        score=score,
        effective_score=effective,
        status=status,
        evidence=evidence,
        warnings=warnings,
        explanation=(
            "Scouting distinguishes present, verified-absent, and incomplete capability evidence."
        ),
    )


def _resolution_value(value: CapabilityResolutionInput) -> str:
    """Normalize domain resolution enums/models without coupling rules to storage DTOs."""

    candidate = value.state if isinstance(value, CapabilityResolution) else value
    candidate = getattr(candidate, "value", candidate)
    normalized = str(candidate).lower()
    return normalized if normalized in {
        "present",
        "verified_absent",
        "unknown",
        "conflicted",
    } else "unknown"


def _capability_state(
    vehicle: Vehicle,
    capability: Capability,
    resolutions: CapabilityResolutionMap | None,
) -> str:
    observations = resolutions.get(vehicle.vehicle_id, {}) if resolutions else {}
    raw = observations.get(capability)
    if raw is None:
        # Existing explicit positive facts remain useful in M2. Absence is never inferred.
        return "present" if capability in vehicle.capabilities else "unknown"
    state = _resolution_value(raw)
    return "unknown" if state == "conflicted" else state


def _utility(vehicle: Vehicle) -> Literal["high", "medium", "low", "unknown"]:
    roles = infer_roles(vehicle)
    if Capability.SCOUTING in vehicle.capabilities or vehicle.vehicle_class is VehicleClass.SPAA:
        return "high"
    if roles & {
        Role.FLANKER,
        Role.SNIPER,
        Role.TANK_DESTROYER,
        Role.ANTI_ARMOR_SPECIALIST,
    } or Capability.HIGH_CALIBER_HE in vehicle.capabilities:
        return "medium"
    if roles & {Role.BRAWLER, Role.HEAVY_ANCHOR, Role.GENERAL_MEDIUM}:
        return "low"
    return "unknown"


def uptier_resilience_rule(
    vehicles: Sequence[Vehicle],
    ruleset: Ruleset | None = None,
    *,
    capability_resolutions: CapabilityResolutionMap | None = None,
) -> RuleResult:
    config = _ruleset(ruleset)
    if config.ruleset_id == "m2-capability-aware-v1":
        relevant = (
            Capability.SCOUTING,
            Capability.STABILIZER,
            Capability.VERTICAL_STABILIZER,
            Capability.SMOKE,
            Capability.ARTILLERY,
            Capability.HIGH_CALIBER_HE,
        )
        capability_inputs = {
            vehicle.vehicle_id: {
                capability.value: _capability_state(
                    vehicle, capability, capability_resolutions
                )
                for capability in relevant
            }
            for vehicle in vehicles
        }
        utility = {
            vehicle.vehicle_id: _m2_utility(
                vehicle, capability_inputs[vehicle.vehicle_id]
            )
            for vehicle in vehicles
        }
        inputs = {
            vehicle.vehicle_id: capability_inputs[vehicle.vehicle_id]
            | {
                "vehicle_class": vehicle.vehicle_class.value,
                "selected_band": utility[vehicle.vehicle_id],
                "selected_reason": _m2_utility_reason(
                    vehicle, capability_inputs[vehicle.vehicle_id]
                ),
            }
            for vehicle in vehicles
        }
    else:
        utility = {vehicle.vehicle_id: _utility(vehicle) for vehicle in vehicles}
        inputs = {}
    scores = config.uptier.model_dump()
    score = fmean(scores[value] for value in utility.values()) if utility else config.uptier.unknown
    unknown = sorted(key for key, value in utility.items() if value == "unknown")
    evidence: dict[str, Any] = {
        "utility_by_vehicle": dict(sorted(utility.items())),
        "proxy": True,
    }
    if config.ruleset_id == "m2-capability-aware-v1":
        evidence["inputs_by_vehicle"] = dict(sorted(inputs.items()))
    return RuleResult(
        rule="uptier_resilience",
        score=score,
        effective_score=score,
        status=_status(score, config),
        evidence=evidence,
        warnings=("uptier utility unknown: " + ", ".join(unknown),) if unknown else (),
        explanation=(
            "Proxy grades utility/scouting as high, specialists as medium, "
            "armor reliance as low."
        ),
    )


def _m2_utility(
    vehicle: Vehicle, capability_states: Mapping[str, str]
) -> Literal["high", "medium", "low", "unknown"]:
    if (
        capability_states[Capability.SCOUTING.value] == "present"
        or vehicle.vehicle_class is VehicleClass.SPAA
    ):
        return "high"
    if vehicle.vehicle_class in {VehicleClass.LIGHT_TANK, VehicleClass.TANK_DESTROYER}:
        return "medium"
    if any(
        capability_states[item.value] == "present"
        for item in (
            Capability.STABILIZER,
            Capability.VERTICAL_STABILIZER,
            Capability.SMOKE,
            Capability.ARTILLERY,
            Capability.HIGH_CALIBER_HE,
        )
    ):
        return "medium"
    if all(value == "verified_absent" for value in capability_states.values()):
        return "low"
    return "unknown"


def _m2_utility_reason(vehicle: Vehicle, capability_states: Mapping[str, str]) -> str:
    band = _m2_utility(vehicle, capability_states)
    if capability_states[Capability.SCOUTING.value] == "present":
        return "verified_scouting"
    if vehicle.vehicle_class is VehicleClass.SPAA:
        return "spaa_utility"
    if vehicle.vehicle_class is VehicleClass.LIGHT_TANK:
        return "mobility_class"
    if vehicle.vehicle_class is VehicleClass.TANK_DESTROYER:
        return "anti_armor_class"
    if band == "medium":
        return "verified_support_capability"
    if band == "low":
        return "verified_no_relevant_utility"
    return "capability_evidence_incomplete"


def role_redundancy_rule(
    vehicles: Sequence[Vehicle],
    resolved_brs: Mapping[str, int],
    *,
    eligible_alternatives: Sequence[tuple[Vehicle, int]] = (),
    capability_resolutions: CapabilityResolutionMap | None = None,
    statistics_by_vehicle: Mapping[str, VehicleStatistics] | None = None,
    heavy_aa_requested: bool = False,
    ruleset: Ruleset | None = None,
) -> RuleResult:
    """Diagnose near-equivalent specialist slots without affecting score/readiness."""

    config = _ruleset(ruleset)
    specialist = sorted(
        (vehicle for vehicle in vehicles if vehicle.vehicle_class is VehicleClass.SPAA),
        key=lambda item: item.vehicle_id,
    )
    lineup_br = max(resolved_brs.values()) if resolved_brs else None
    alternatives = sorted(
        vehicle.vehicle_id
        for vehicle, battle_rating in eligible_alternatives
        if vehicle.vehicle_class is not VehicleClass.SPAA
        and lineup_br is not None
        and battle_rating <= lineup_br
        and br_step_distance(lineup_br, battle_rating) <= 2
    )
    pairs: list[list[str]] = []
    pair_evidence: list[dict[str, Any]] = []
    for index, first in enumerate(specialist):
        for second in specialist[index + 1 :]:
            distance = br_step_distance(
                max(resolved_brs[first.vehicle_id], resolved_brs[second.vehicle_id]),
                min(resolved_brs[first.vehicle_id], resolved_brs[second.vehicle_id]),
            )
            first_capabilities, first_complete = _specialist_capability_evidence(
                first, capability_resolutions
            )
            second_capabilities, second_complete = _specialist_capability_evidence(
                second, capability_resolutions
            )
            capability_difference = sorted(first_capabilities ^ second_capabilities)
            performance_difference = _substantial_performance_difference(
                first.vehicle_id, second.vehicle_id, statistics_by_vehicle or {}
            )
            redundant = (
                distance <= 1
                and first_complete
                and second_complete
                and not capability_difference
                and not performance_difference
                and bool(alternatives)
                and not heavy_aa_requested
            )
            pair_evidence.append(
                {
                    "vehicles": [first.vehicle_id, second.vehicle_id],
                    "br_step_distance": distance,
                    "capability_difference": capability_difference,
                    "capability_evidence_complete": first_complete and second_complete,
                    "substantial_performance_difference": performance_difference,
                    "redundant": redundant,
                }
            )
            if redundant:
                pairs.append([first.vehicle_id, second.vehicle_id])
    return RuleResult(
        rule="role_redundancy",
        score=None,
        effective_score=100.0,
        status=RuleStatus.WARNING if pairs else RuleStatus.GOOD,
        evidence={
            "redundant_pairs": pairs,
            "pair_evidence": pair_evidence,
            "competitive_non_spaa_alternatives": alternatives,
            "heavy_aa_requested": heavy_aa_requested,
            "diagnostic_only": True,
            "ruleset_id": config.ruleset_id,
        },
        warnings=("near-equivalent specialist redundancy",) if pairs else (),
        explanation=(
            "Near-equivalent specialist slots are diagnostic only; general-purpose duplicates "
            "are exempt."
        ),
    )


def _specialist_capability_evidence(
    vehicle: Vehicle,
    resolutions: CapabilityResolutionMap | None,
) -> tuple[set[str], bool]:
    relevant = (Capability.RADAR, Capability.IRST, Capability.SAM, Capability.AUTOCANNON_AA)
    states = {
        capability: _capability_state(vehicle, capability, resolutions)
        for capability in relevant
    }
    present = {
        capability.value for capability, state in states.items() if state == "present"
    }
    complete = all(state in {"present", "verified_absent"} for state in states.values())
    return present, complete


def _substantial_performance_difference(
    first_id: str,
    second_id: str,
    observations: Mapping[str, VehicleStatistics],
) -> bool:
    first = observations.get(first_id)
    second = observations.get(second_id)
    if (
        first is None
        or second is None
        or first.mode_scope is not StatisticsScope.GROUND_REALISTIC_GROUND_VEHICLES
        or second.mode_scope is not first.mode_scope
        or second.snapshot_id != first.snapshot_id
        or first.battles is None
        or second.battles is None
        or min(first.battles, second.battles) < 1000
    ):
        return False
    first_rate, _ = _metric_value(first, "win_rate")
    second_rate, _ = _metric_value(second, "win_rate")
    return (
        first_rate is not None
        and second_rate is not None
        and abs(first_rate - second_rate) >= 0.05
    )


def performance_metric(
    raw_value: float, sample_size: int, peer_values: Sequence[float], *, shrinkage_k: float
) -> MetricPerformance:
    if not peer_values:
        raise ValueError("at least one peer value is required")
    peer_mean = fmean(peer_values)
    deviation = pstdev(peer_values)
    confidence = sample_size / (sample_size + shrinkage_k)
    adjusted = peer_mean + confidence * (raw_value - peer_mean)
    if deviation == 0:
        score = 50.0
    else:
        z_score = max(-2.0, min(2.0, (adjusted - peer_mean) / deviation))
        score = 50.0 + 25.0 * z_score
    return MetricPerformance(
        raw_value=raw_value,
        adjusted_value=adjusted,
        peer_mean=peer_mean,
        peer_deviation=deviation,
        sample_size=sample_size,
        confidence_weight=confidence,
        score=score,
    )


def _metric_value(stats: VehicleStatistics, metric: str) -> tuple[float | None, int | None]:
    if stats.mode_scope is StatisticsScope.REALISTIC_ALL_CONTEXTS:
        if metric == "win_rate":
            return stats.reported_win_rate, stats.battles
        if metric == "kd":
            sample_size = (
                stats.battles
                if stats.kill_target_definition is KillTargetDefinition.GROUND_TARGETS
                else stats.deaths
            )
            return stats.reported_kd, sample_size
        return stats.reported_kills_per_battle, stats.battles
    if metric == "win_rate":
        value = stats.win_rate
        if value is None and stats.wins is not None and stats.battles:
            value = stats.wins / stats.battles
        return value, stats.battles
    if metric == "kd":
        if stats.kills is None or not stats.deaths:
            return None, stats.deaths
        return stats.kills / stats.deaths, stats.deaths
    if stats.kills is None or not stats.battles:
        return None, stats.battles
    return stats.kills / stats.battles, stats.battles


def _compatible_peers(
    target: Vehicle,
    target_br: int,
    stats: VehicleStatistics,
    peers: Sequence[PeerStatistic],
    minimum: int,
) -> tuple[tuple[PeerStatistic, ...], str]:
    base = tuple(
        peer
        for peer in peers
        if peer.statistics.snapshot_id == stats.snapshot_id
        and peer.statistics.mode_scope is stats.mode_scope
        and peer.statistics.sample_start == stats.sample_start
        and peer.statistics.sample_end == stats.sample_end
        and peer.statistics.vehicle_id != stats.vehicle_id
        and peer.statistics.battles is not None
        and peer.statistics.battles > 0
    )
    class_peers = tuple(peer for peer in base if peer.vehicle.vehicle_class is target.vehicle_class)
    near = tuple(
        peer
        for peer in class_peers
        if abs(BR_LADDER.index(peer.battle_rating) - BR_LADDER.index(target_br)) <= 1
    )
    if len(near) >= minimum:
        return near, "same_class_within_one_step"
    if len(class_peers) >= minimum:
        return class_peers, "same_class_all_brs"
    return base, "same_snapshot_scope_all_classes"


def statistics_eligibility(
    target: Vehicle,
    target_br: int,
    stats: VehicleStatistics,
    peers: Sequence[PeerStatistic],
    *,
    as_of: date,
    ruleset: Ruleset,
) -> tuple[str | None, tuple[str, ...], tuple[PeerStatistic, ...], str]:
    """Return the exclusion reason and metrics with enough like-for-like peers."""
    if stats.mode_scope not in (
        StatisticsScope.GROUND_REALISTIC_GROUND_VEHICLES,
        StatisticsScope.REALISTIC_ALL_CONTEXTS,
    ):
        return "wrong_mode", (), (), "none"
    if stats.battles is None or stats.battles <= 0:
        return "nonpositive_battles", (), (), "none"
    if stats.mode_scope is StatisticsScope.REALISTIC_ALL_CONTEXTS and stats.sample_end is None:
        return "missing_observation_date", (), (), "none"
    if stats.sample_end is not None and (as_of - stats.sample_end).days > 90:
        return "observation_too_old", (), (), "none"
    if stats.sample_end is not None and stats.sample_end > as_of:
        return "future_observation", (), (), "none"
    compatible, fallback = _compatible_peers(
        target, target_br, stats, peers, ruleset.statistics.minimum_peers
    )
    present = tuple(
        metric for metric in ("win_rate", "kd", "kills_per_battle")
        if _has_sampled_metric(stats, metric)
    )
    if not present:
        return "missing_metrics", (), compatible, fallback
    usable = tuple(
        metric for metric in present
        if len(_matching_peer_values(stats, metric, compatible))
        >= ruleset.statistics.minimum_peers
    )
    if not usable:
        return "insufficient_compatible_peers", (), compatible, fallback
    return None, usable, compatible, fallback


def _has_sampled_metric(stats: VehicleStatistics, metric: str) -> bool:
    value, sample_size = _metric_value(stats, metric)
    return value is not None and sample_size is not None and sample_size > 0


def _metric_definition(stats: VehicleStatistics, metric: str) -> str:
    if metric != "win_rate" and stats.kill_target_definition is KillTargetDefinition.GROUND_TARGETS:
        suffix = "per_death" if metric == "kd" else "per_battle"
        return f"ground_kills_{suffix}"
    if stats.mode_scope is StatisticsScope.REALISTIC_ALL_CONTEXTS:
        return RatioProvenance.PROVIDER_REPORTED.value
    return stats.ratio_source(metric).value


def _matching_peer_values(
    stats: VehicleStatistics, metric: str, peers: Sequence[PeerStatistic]
) -> list[float]:
    values: list[float] = []
    for peer in peers:
        if _metric_definition(peer.statistics, metric) != _metric_definition(stats, metric):
            continue
        value, sample_size = _metric_value(peer.statistics, metric)
        if value is not None and sample_size is not None and sample_size > 0:
            values.append(value)
    return values


def _empty_metric(raw_value: float | None, sample_size: int | None) -> dict[str, Any]:
    return {
        "raw_value": raw_value,
        "adjusted_value": None,
        "peer_mean": None,
        "peer_deviation": None,
        "sample_size": sample_size,
        "confidence_weight": None,
        "score": None,
    }


def _legacy_statistical_strength_rule(
    vehicles: Sequence[Vehicle],
    resolved_brs: Mapping[str, int],
    observations: Mapping[str, VehicleStatistics],
    peers: Sequence[PeerStatistic],
    config: Ruleset,
) -> RuleResult:
    """Keep the frozen M1 scoring and serialized evidence byte-for-byte stable."""
    metric_names = ("win_rate", "kd", "kills_per_battle")
    weights = {
        "win_rate": config.statistics.win_rate_weight,
        "kd": config.statistics.kd_weight,
        "kills_per_battle": config.statistics.kills_per_battle_weight,
    }
    shrinkage = {
        "win_rate": config.statistics.win_rate_k,
        "kd": config.statistics.kd_k,
        "kills_per_battle": config.statistics.kills_per_battle_k,
    }
    evidence: dict[str, Any] = {}
    scores: list[float] = []
    any_observed = False
    any_missing = False
    for vehicle in sorted(vehicles, key=lambda item: item.vehicle_id):
        stats = observations.get(vehicle.vehicle_id)
        metrics: dict[str, Any] = {}
        weighted = 0.0
        observed = False
        if (
            stats is None
            or stats.mode_scope is not StatisticsScope.GROUND_REALISTIC_GROUND_VEHICLES
        ):
            for metric in metric_names:
                metrics[metric] = _empty_metric(None, None)
            evidence[vehicle.vehicle_id] = metrics
            scores.append(config.statistics.neutral_score)
            any_missing = True
            continue
        base = tuple(
            peer for peer in peers
            if peer.statistics.snapshot_id == stats.snapshot_id
            and peer.statistics.mode_scope is stats.mode_scope
        )
        class_peers = tuple(
            peer for peer in base if peer.vehicle.vehicle_class is vehicle.vehicle_class
        )
        near = tuple(
            peer for peer in class_peers
            if abs(
                BR_LADDER.index(peer.battle_rating)
                - BR_LADDER.index(resolved_brs[vehicle.vehicle_id])
            ) <= 1
        )
        if len(near) >= config.statistics.minimum_peers:
            compatible, fallback = near, "same_class_within_one_step"
        elif len(class_peers) >= config.statistics.minimum_peers:
            compatible, fallback = class_peers, "same_class_all_brs"
        else:
            compatible, fallback = base, "same_snapshot_scope_all_classes"
        for metric in metric_names:
            raw, sample_size = _metric_value(stats, metric)
            peer_values = [
                value for peer in compatible
                if (value := _metric_value(peer.statistics, metric)[0]) is not None
            ]
            if raw is None or sample_size is None or not peer_values:
                metrics[metric] = _empty_metric(raw, sample_size)
                weighted += config.statistics.neutral_score * weights[metric]
                any_missing = True
                continue
            result = performance_metric(
                raw, sample_size, peer_values, shrinkage_k=shrinkage[metric]
            )
            observed = True
            any_observed = True
            metrics[metric] = result.model_dump() | {
                "peer_group": fallback,
                "peer_count": len(peer_values),
                "scope": stats.mode_scope.value,
            }
            weighted += result.score * weights[metric]
        evidence[vehicle.vehicle_id] = metrics
        scores.append(weighted)
        if not observed:
            any_missing = True
    score = fmean(scores) if any_observed else None
    return RuleResult(
        rule="statistical_strength",
        score=score,
        effective_score=score if score is not None else config.statistics.neutral_score,
        status=_status(score, config) if score is not None else RuleStatus.UNKNOWN,
        evidence={"vehicles": evidence, "missing_is_neutral_only_in_composite": True},
        warnings=("statistics missing or incompatible",) if any_missing else (),
        explanation=(
            "Metrics are peer-relative after sample-size shrinkage; missing evidence stays null."
        ),
    )


def statistical_strength_rule(
    vehicles: Sequence[Vehicle],
    resolved_brs: Mapping[str, int],
    statistics_by_vehicle: Mapping[str, VehicleStatistics] | None = None,
    peer_statistics: Sequence[PeerStatistic] = (),
    ruleset: Ruleset | None = None,
    *,
    evaluation_date: date | None = None,
    source_providers: Mapping[str, str] | None = None,
    source_purposes: Mapping[str, str] | None = None,
) -> RuleResult:
    config = _ruleset(ruleset)
    observations = statistics_by_vehicle or {}
    if config.ruleset_id == "m1-baseline-v1":
        return _legacy_statistical_strength_rule(
            vehicles, resolved_brs, observations, peer_statistics, config
        )
    metric_names = ("win_rate", "kd", "kills_per_battle")
    metric_weights = {
        "win_rate": config.statistics.win_rate_weight,
        "kd": config.statistics.kd_weight,
        "kills_per_battle": config.statistics.kills_per_battle_weight,
    }
    shrinkage = {
        "win_rate": config.statistics.win_rate_k,
        "kd": config.statistics.kd_k,
        "kills_per_battle": config.statistics.kills_per_battle_k,
    }
    evidence: dict[str, Any] = {}
    vehicle_scores: list[float] = []
    any_observed_metric = False
    any_missing_evidence = False
    as_of = evaluation_date or date.today()
    for vehicle in sorted(vehicles, key=lambda item: item.vehicle_id):
        stats = observations.get(vehicle.vehicle_id)
        metric_evidence: dict[str, Any] = {}
        weighted = 0.0
        observed_metric = False
        purpose = None if stats is None else (source_purposes or {}).get(stats.snapshot_id)
        excluded = "missing_vehicle_row" if stats is None else None
        if (
            stats is not None
            and stats.mode_scope is StatisticsScope.REALISTIC_ALL_CONTEXTS
            and purpose == "acceptance"
        ):
            excluded = "synthetic_acceptance_fixture"
        compatible: tuple[PeerStatistic, ...] = ()
        fallback = "none"
        usable: tuple[str, ...] = ()
        if stats is not None and excluded is None:
            excluded, usable, compatible, fallback = statistics_eligibility(
                vehicle, resolved_brs[vehicle.vehicle_id], stats, peer_statistics,
                as_of=as_of, ruleset=config,
            )
        proxy = stats is not None and stats.mode_scope is StatisticsScope.REALISTIC_ALL_CONTEXTS
        metadata: dict[str, Any] = {
            "eligibility": "eligible" if excluded is None else "excluded",
            "eligible": excluded is None,
            "proxy_used": proxy and excluded is None,
            "evidence_label": (
                "Community RB ground-vehicle proxy" if proxy
                else "Verified Ground RB statistics" if stats is not None else None
            ),
            "source_scope": None if stats is None else stats.mode_scope.value,
            "source_provider": (
                None if stats is None else (source_providers or {}).get(stats.snapshot_id)
            ),
            "snapshot_id": None if stats is None else stats.snapshot_id,
            "exclusion_reason": excluded,
        }
        if excluded is not None:
            for metric in metric_names:
                metric_evidence[metric] = _empty_metric(None, None)
            evidence[vehicle.vehicle_id] = metadata | metric_evidence
            vehicle_scores.append(config.statistics.neutral_score)
            any_missing_evidence = True
            continue
        assert stats is not None
        for metric in metric_names:
            raw, sample_size = _metric_value(stats, metric)
            peer_values = _matching_peer_values(stats, metric, compatible)
            if metric not in usable or raw is None or sample_size is None:
                metric_evidence[metric] = _empty_metric(raw, sample_size)
                weighted += config.statistics.neutral_score * metric_weights[metric]
                any_missing_evidence = True
                continue
            result = performance_metric(
                raw, sample_size, peer_values, shrinkage_k=shrinkage[metric]
            )
            observed_metric = True
            any_observed_metric = True
            metric_score = (
                config.statistics.neutral_score
                + config.statistics.community_proxy_factor
                * (result.score - config.statistics.neutral_score)
                if proxy else result.score
            )
            metric_evidence[metric] = result.model_dump() | {
                "score": metric_score,
                "unattenuated_score": result.score,
                "peer_group": fallback,
                "peer_count": len(peer_values),
                "scope": stats.mode_scope.value,
                "metric_definition": _metric_definition(stats, metric),
                "source_field": (
                    {"win_rate": "rb_win_rate", "kd": "rb_ground_frags_per_death",
                     "kills_per_battle": "rb_ground_frags_per_battle"}[metric]
                    if proxy else None
                ),
                "sample_size_definition": (
                    "battles_confidence_proxy"
                    if proxy and metric == "kd"
                    and stats.kill_target_definition is KillTargetDefinition.GROUND_TARGETS
                    else "observed_denominator"
                ),
            }
            weighted += metric_score * metric_weights[metric]
        evidence[vehicle.vehicle_id] = metadata | metric_evidence
        vehicle_scores.append(weighted)
        if not observed_metric:
            any_missing_evidence = True
    score = fmean(vehicle_scores) if any_observed_metric else None
    effective = score if score is not None else config.statistics.neutral_score
    return RuleResult(
        rule="statistical_strength",
        score=score,
        effective_score=effective,
        status=_status(score, config) if score is not None else RuleStatus.UNKNOWN,
        evidence={"vehicles": evidence, "missing_is_neutral_only_in_composite": True},
        warnings=("statistics missing or incompatible",) if any_missing_evidence else (),
        explanation=(
            "Metrics are peer-relative after sample-size shrinkage; missing evidence stays null."
        ),
    )


def _readiness_failures(
    vehicles: Sequence[Vehicle],
    resolved_brs: Mapping[str, int],
    lineup_br: int,
    backup_score: float,
    aa_result: RuleResult,
    config: Ruleset,
) -> tuple[str, ...]:
    distances = [
        br_step_distance(lineup_br, battle_rating) for battle_rating in resolved_brs.values()
    ]
    failures: list[str] = []
    if sum(distance <= 1 for distance in distances) < config.readiness.minimum_within_one_step:
        failures.append("at_least_two_within_one_step")
    if sum(distance <= 2 for distance in distances) < config.readiness.minimum_within_two_steps:
        failures.append("at_least_three_within_two_steps")
    if backup_score < config.readiness.minimum_backup_depth:
        failures.append("backup_depth_at_least_60")
    combat = sum(vehicle.vehicle_class is not VehicleClass.SPAA for vehicle in vehicles)
    if combat < config.readiness.minimum_non_spaa_combat:
        failures.append("at_least_two_non_spaa_combat_vehicles")
    if aa_result.status is RuleStatus.CRITICAL:
        failures.append("critical_owned_spaa_omission")
    severe = (
        sum(distance > 2 for distance in distances)
        >= config.readiness.severe_count_more_than_two_steps
        or any(distance > config.readiness.severe_any_more_than_steps for distance in distances)
    )
    if severe:
        failures.append("no_severe_cohesion_warning")
    return tuple(failures)


def evaluate_lineup_rules(
    vehicles: Sequence[Vehicle],
    resolved_brs: Mapping[str, int],
    *,
    statistics_by_vehicle: Mapping[str, VehicleStatistics] | None = None,
    peer_statistics: Sequence[PeerStatistic] = (),
    nearby_spaa: Sequence[tuple[Vehicle, VehicleStatus]] = (),
    eligible_alternatives: Sequence[tuple[Vehicle, int]] = (),
    capability_resolutions: CapabilityResolutionMap | None = None,
    heavy_aa_requested: bool = False,
    ruleset: Ruleset | None = None,
    evaluation_date: date | None = None,
    source_providers: Mapping[str, str] | None = None,
    source_purposes: Mapping[str, str] | None = None,
) -> RulesEvaluation:
    """Evaluate each transparent component, composite score, and readiness gates."""

    config = _ruleset(ruleset)
    vehicle_ids = {vehicle.vehicle_id for vehicle in vehicles}
    if vehicle_ids != set(resolved_brs):
        raise ValueError("vehicles and resolved BR keys must match exactly")
    if not vehicles:
        raise ValueError("a lineup must contain at least one vehicle")
    lineup_br = max(resolved_brs.values())
    base_rules: tuple[RuleResult, ...] = (
        br_cohesion_rule(resolved_brs, lineup_br, config),
        backup_depth_rule(resolved_brs, lineup_br, config),
        role_coverage_rule(
            vehicles, config, capability_resolutions=capability_resolutions
        ),
        anti_air_rule(vehicles, nearby_spaa, config),
        scouting_rule(
            vehicles, config, capability_resolutions=capability_resolutions
        ),
        uptier_resilience_rule(
            vehicles, config, capability_resolutions=capability_resolutions
        ),
        statistical_strength_rule(
            vehicles, resolved_brs, statistics_by_vehicle, peer_statistics, config,
            evaluation_date=evaluation_date,
            source_providers=source_providers,
            source_purposes=source_purposes,
        ),
    )
    rules: tuple[RuleResult, ...] = base_rules
    if config.ruleset_id == "m2-capability-aware-v1":
        rules += (
            role_redundancy_rule(
                vehicles,
                resolved_brs,
                eligible_alternatives=eligible_alternatives,
                capability_resolutions=capability_resolutions,
                statistics_by_vehicle=statistics_by_vehicle,
                heavy_aa_requested=heavy_aa_requested,
                ruleset=config,
            ),
        )
    weights = config.weights.model_dump()
    overall = sum(rule.effective_score * weights[rule.rule] for rule in rules)
    backup = next(rule for rule in rules if rule.rule == "backup_depth")
    aa = next(rule for rule in rules if rule.rule == "anti_air")
    failures = _readiness_failures(
        vehicles, resolved_brs, lineup_br, backup.effective_score, aa, config
    )
    return RulesEvaluation(
        rules=rules,
        overall_score=overall,
        readiness_passed=not failures,
        readiness_failures=failures,
    )
