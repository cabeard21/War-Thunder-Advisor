"""Transparent lineup scoring from immutable domain facts."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping, Sequence
from importlib.resources import files
from pathlib import Path
from statistics import fmean, pstdev
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from wt_advisor.domain.models import (
    BR_LADDER,
    Capability,
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
        return stable_hash(self.model_dump(exclude={"content_hash"}))


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

    if path is None:
        resource = files("wt_advisor").joinpath("config/defaults.toml")
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

    if Capability.SCOUTING in vehicle.capabilities:
        roles.add(Role.SCOUT)
    if vehicle.capabilities & {Capability.ATGM, Capability.HIGH_CALIBER_HE}:
        roles.add(Role.ANTI_ARMOR_SPECIALIST)
    return frozenset(roles)


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
    vehicles: Sequence[Vehicle], ruleset: Ruleset | None = None
) -> RuleResult:
    config = _ruleset(ruleset)
    role_map = {vehicle.vehicle_id: infer_roles(vehicle) for vehicle in vehicles}
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
    vehicles: Sequence[Vehicle], ruleset: Ruleset | None = None
) -> RuleResult:
    config = _ruleset(ruleset)
    scouts = sorted(
        vehicle.vehicle_id for vehicle in vehicles if Capability.SCOUTING in vehicle.capabilities
    )
    score = 100.0 if scouts else 0.0
    return RuleResult(
        rule="scouting",
        score=score,
        effective_score=score,
        status=_status(score, config),
        evidence={"scouting_vehicle_ids": scouts},
        warnings=() if scouts else ("lineup has no scouting capability",),
        explanation="Scouting is present when at least one vehicle has the factual capability.",
    )


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
    vehicles: Sequence[Vehicle], ruleset: Ruleset | None = None
) -> RuleResult:
    config = _ruleset(ruleset)
    utility = {vehicle.vehicle_id: _utility(vehicle) for vehicle in vehicles}
    scores = config.uptier.model_dump()
    score = fmean(scores[value] for value in utility.values()) if utility else config.uptier.unknown
    unknown = sorted(key for key, value in utility.items() if value == "unknown")
    return RuleResult(
        rule="uptier_resilience",
        score=score,
        effective_score=score,
        status=_status(score, config),
        evidence={"utility_by_vehicle": dict(sorted(utility.items())), "proxy": True},
        warnings=("uptier utility unknown: " + ", ".join(unknown),) if unknown else (),
        explanation=(
            "Proxy grades utility/scouting as high, specialists as medium, "
            "armor reliance as low."
        ),
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


def statistical_strength_rule(
    vehicles: Sequence[Vehicle],
    resolved_brs: Mapping[str, int],
    statistics_by_vehicle: Mapping[str, VehicleStatistics] | None = None,
    peer_statistics: Sequence[PeerStatistic] = (),
    ruleset: Ruleset | None = None,
) -> RuleResult:
    config = _ruleset(ruleset)
    observations = statistics_by_vehicle or {}
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
    for vehicle in sorted(vehicles, key=lambda item: item.vehicle_id):
        stats = observations.get(vehicle.vehicle_id)
        metric_evidence: dict[str, Any] = {}
        weighted = 0.0
        observed_metric = False
        if (
            stats is None
            or stats.mode_scope is not StatisticsScope.GROUND_REALISTIC_GROUND_VEHICLES
        ):
            for metric in metric_names:
                metric_evidence[metric] = _empty_metric(None, None)
            evidence[vehicle.vehicle_id] = metric_evidence
            vehicle_scores.append(config.statistics.neutral_score)
            any_missing_evidence = True
            continue
        compatible, fallback = _compatible_peers(
            vehicle,
            resolved_brs[vehicle.vehicle_id],
            stats,
            peer_statistics,
            config.statistics.minimum_peers,
        )
        for metric in metric_names:
            raw, sample_size = _metric_value(stats, metric)
            peer_values = [
                value
                for peer in compatible
                if (value := _metric_value(peer.statistics, metric)[0]) is not None
            ]
            if raw is None or sample_size is None or not peer_values:
                metric_evidence[metric] = _empty_metric(raw, sample_size)
                weighted += config.statistics.neutral_score * metric_weights[metric]
                any_missing_evidence = True
                continue
            result = performance_metric(
                raw, sample_size, peer_values, shrinkage_k=shrinkage[metric]
            )
            observed_metric = True
            any_observed_metric = True
            metric_evidence[metric] = result.model_dump() | {
                "peer_group": fallback,
                "peer_count": len(peer_values),
                "scope": stats.mode_scope.value,
            }
            weighted += result.score * metric_weights[metric]
        evidence[vehicle.vehicle_id] = metric_evidence
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
    ruleset: Ruleset | None = None,
) -> RulesEvaluation:
    """Evaluate each transparent component, composite score, and readiness gates."""

    config = _ruleset(ruleset)
    vehicle_ids = {vehicle.vehicle_id for vehicle in vehicles}
    if vehicle_ids != set(resolved_brs):
        raise ValueError("vehicles and resolved BR keys must match exactly")
    if not vehicles:
        raise ValueError("a lineup must contain at least one vehicle")
    lineup_br = max(resolved_brs.values())
    rules = (
        br_cohesion_rule(resolved_brs, lineup_br, config),
        backup_depth_rule(resolved_brs, lineup_br, config),
        role_coverage_rule(vehicles, config),
        anti_air_rule(vehicles, nearby_spaa, config),
        scouting_rule(vehicles, config),
        uptier_resilience_rule(vehicles, config),
        statistical_strength_rule(
            vehicles, resolved_brs, statistics_by_vehicle, peer_statistics, config
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
