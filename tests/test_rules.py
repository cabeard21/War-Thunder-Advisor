from __future__ import annotations

import pytest

from wt_advisor.domain.models import (
    Capability,
    CapabilityState,
    Nation,
    RuleStatus,
    StatisticsScope,
    Vehicle,
    VehicleClass,
    VehicleStatistics,
    VehicleStatus,
    stable_hash,
)
from wt_advisor.rules import (
    PeerStatistic,
    Ruleset,
    anti_air_rule,
    backup_depth_rule,
    br_cohesion_rule,
    evaluate_lineup_rules,
    infer_roles,
    load_ruleset,
    performance_metric,
    role_coverage_rule,
    role_redundancy_rule,
    scouting_rule,
    statistical_strength_rule,
    uptier_resilience_rule,
)


def vehicle(
    vehicle_id: str,
    vehicle_class: VehicleClass,
    *capabilities: Capability,
) -> Vehicle:
    return Vehicle(
        vehicle_id=vehicle_id,
        source_vehicle_id=vehicle_id,
        name=vehicle_id,
        nation=Nation.USA,
        vehicle_class=vehicle_class,
        rank=1,
        capabilities=frozenset(capabilities),
    )


def statistic(
    vehicle_id: str,
    *,
    battles: int | None = 1000,
    win_rate: float | None = 0.5,
    kills: int | None = 1000,
    deaths: int | None = 1000,
    snapshot_id: str = "stats-1",
    scope: StatisticsScope = StatisticsScope.GROUND_REALISTIC_GROUND_VEHICLES,
) -> VehicleStatistics:
    return VehicleStatistics(
        vehicle_id=vehicle_id,
        snapshot_id=snapshot_id,
        mode_scope=scope,
        battles=battles,
        win_rate=win_rate,
        kills=kills,
        deaths=deaths,
    )


def test_default_ruleset_is_validated_and_hash_is_deterministic() -> None:
    first = load_ruleset()
    second = load_ruleset()

    assert isinstance(first, Ruleset)
    assert first == second
    assert first.content_hash == second.content_hash
    assert sum(first.weights.model_dump().values()) == pytest.approx(1.0)


def test_named_m1_ruleset_preserves_frozen_hash_and_m2_only_adds_zero_weight() -> None:
    baseline = load_ruleset("m1-baseline-v1")
    capability_aware = load_ruleset("m2-capability-aware-v1")

    assert baseline == load_ruleset()
    assert baseline.content_hash == (
        "750cb92729e6cb63ade14083148fc256aacd0ba2dab7acc42f027fdd1611d7a8"
    )
    assert capability_aware.ruleset_id == "m2-capability-aware-v1"
    assert capability_aware.weights.role_redundancy == 0
    for rule_name, weight in baseline.weights.model_dump().items():
        assert getattr(capability_aware.weights, rule_name) == weight


def test_m1_representative_evaluation_serialization_is_frozen() -> None:
    lineup = (
        vehicle("medium", VehicleClass.MEDIUM_TANK),
        vehicle("scout", VehicleClass.LIGHT_TANK, Capability.SCOUTING),
        vehicle("aa", VehicleClass.SPAA, Capability.AUTOCANNON_AA),
    )

    evaluation = evaluate_lineup_rules(
        lineup,
        {"medium": 23, "scout": 20, "aa": 20},
        ruleset=load_ruleset("m1-baseline-v1"),
    )

    assert [rule.rule for rule in evaluation.rules] == [
        "br_cohesion",
        "backup_depth",
        "role_coverage",
        "anti_air",
        "scouting",
        "uptier_resilience",
        "statistical_strength",
    ]
    assert stable_hash(evaluation) == (
        "75b90d6f22e2b1efac9bf652310eb16ddedeefeeeb0d56ee69e9ec9c967d99ea"
    )


def test_role_inference_uses_class_and_capability_facts() -> None:
    light = vehicle("light", VehicleClass.LIGHT_TANK, Capability.SCOUTING)
    spaa = vehicle("aa", VehicleClass.SPAA, Capability.AUTOCANNON_AA)
    howitzer = vehicle("howitzer", VehicleClass.MEDIUM_TANK, Capability.HIGH_CALIBER_HE)

    assert {role.value for role in infer_roles(light)} == {"flanker", "scout"}
    assert {role.value for role in infer_roles(spaa)} == {"spaa"}
    assert "anti_armor_specialist" in {role.value for role in infer_roles(howitzer)}


def test_br_cohesion_and_backup_depth_use_ladder_steps() -> None:
    resolved = {"top": 27, "one": 23, "two": 20, "far": 17}

    cohesion = br_cohesion_rule(resolved, 27)
    backups = backup_depth_rule(resolved, 27)

    assert cohesion.score == pytest.approx((100 + 80 + 50 + 0) / 4)
    assert backups.score == pytest.approx((1 + 0.75 + 0.25) / 3 * 100)
    assert backups.evidence["competitive_count"] == 3


def test_role_coverage_is_soft_and_exposes_missing_categories() -> None:
    lineup = (
        vehicle("medium", VehicleClass.MEDIUM_TANK),
        vehicle("light", VehicleClass.LIGHT_TANK, Capability.SCOUTING),
        vehicle("td", VehicleClass.TANK_DESTROYER),
    )

    result = role_coverage_rule(lineup)

    assert result.score == pytest.approx(80)
    assert result.status is RuleStatus.GOOD
    assert result.evidence["missing_categories"] == ["anti_air"]


def test_m2_role_coverage_uses_resolved_capabilities_not_stale_legacy_absence() -> None:
    ruleset = load_ruleset("m2-capability-aware-v1")
    light = vehicle("light", VehicleClass.LIGHT_TANK)
    medium = vehicle("medium", VehicleClass.MEDIUM_TANK)
    stale = vehicle("stale", VehicleClass.MEDIUM_TANK, Capability.HIGH_CALIBER_HE)

    result = role_coverage_rule(
        (light, medium, stale),
        ruleset,
        capability_resolutions={
            "medium": {Capability.HIGH_CALIBER_HE: CapabilityState.PRESENT},
            "stale": {Capability.HIGH_CALIBER_HE: CapabilityState.VERIFIED_ABSENT},
        },
    )

    assert "anti_armor_specialist" in result.evidence["roles_by_vehicle"]["medium"]
    assert "anti_armor_specialist" not in result.evidence["roles_by_vehicle"]["stale"]
    assert result.evidence["covered_categories"]["anti_armor"] is True


def test_anti_air_distinguishes_owned_omitted_unowned_and_absent() -> None:
    tank = vehicle("tank", VehicleClass.MEDIUM_TANK)
    aa = vehicle("aa", VehicleClass.SPAA)

    owned_omitted = anti_air_rule((tank,), ((aa, VehicleStatus.OWNED),))
    unowned = anti_air_rule((tank,), ((aa, VehicleStatus.AVAILABLE_TO_RESEARCH),))
    absent = anti_air_rule((tank,), ())

    assert owned_omitted.status is RuleStatus.CRITICAL
    assert owned_omitted.evidence["state"] == "owned_but_omitted"
    assert unowned.evidence["state"] == "available_but_unowned"
    assert absent.evidence["state"] == "absent_near_br"


def test_scouting_and_uptier_proxy_explain_utility() -> None:
    scout = vehicle("scout", VehicleClass.LIGHT_TANK, Capability.SCOUTING)
    td = vehicle("td", VehicleClass.TANK_DESTROYER)
    heavy = vehicle("heavy", VehicleClass.HEAVY_TANK)

    scouting = scouting_rule((scout, td, heavy))
    uptier = uptier_resilience_rule((scout, td, heavy))

    assert scouting.score == 100
    assert uptier.score == pytest.approx((100 + 60 + 25) / 3)
    assert uptier.evidence == {
        "utility_by_vehicle": {
            "scout": "high",
            "td": "medium",
            "heavy": "low",
        },
        "proxy": True,
    }


def test_m2_scouting_distinguishes_present_absent_and_incomplete_evidence() -> None:
    ruleset = load_ruleset("m2-capability-aware-v1")
    tank = vehicle("tank", VehicleClass.MEDIUM_TANK)
    scout = vehicle("scout", VehicleClass.LIGHT_TANK)

    present = scouting_rule(
        (tank, scout),
        ruleset,
        capability_resolutions={
            "tank": {Capability.SCOUTING: "verified_absent"},
            "scout": {Capability.SCOUTING: "present"},
        },
    )
    absent = scouting_rule(
        (tank, scout),
        ruleset,
        capability_resolutions={
            "tank": {Capability.SCOUTING: "verified_absent"},
            "scout": {Capability.SCOUTING: "verified_absent"},
        },
    )
    incomplete = scouting_rule(
        (tank, scout),
        ruleset,
        capability_resolutions={
            "tank": {Capability.SCOUTING: "verified_absent"},
            "scout": {Capability.SCOUTING: "conflicted"},
        },
    )

    assert (present.score, present.effective_score, present.status) == (
        100,
        100,
        RuleStatus.GOOD,
    )
    assert present.evidence["state"] == "present"
    assert (absent.score, absent.effective_score, absent.status) == (
        0,
        0,
        RuleStatus.CRITICAL,
    )
    assert absent.evidence["state"] == "verified_absent"
    assert incomplete.score is None
    assert incomplete.effective_score == 50
    assert incomplete.status is RuleStatus.UNKNOWN
    assert incomplete.evidence["state"] == "unknown"
    assert incomplete.warnings == ("capability_evidence_incomplete",)


def test_m2_uptier_proxy_exposes_capability_inputs_and_never_calls_unknown_low() -> None:
    ruleset = load_ruleset("m2-capability-aware-v1")
    unknown = vehicle("unknown", VehicleClass.HEAVY_TANK)
    supported = vehicle("supported", VehicleClass.HEAVY_TANK)
    verified_low = vehicle("verified_low", VehicleClass.HEAVY_TANK)
    absent = {
        capability: "verified_absent"
        for capability in (
            Capability.SCOUTING,
            Capability.STABILIZER,
            Capability.VERTICAL_STABILIZER,
            Capability.SMOKE,
            Capability.ARTILLERY,
            Capability.HIGH_CALIBER_HE,
        )
    }

    result = uptier_resilience_rule(
        (unknown, supported, verified_low),
        ruleset,
        capability_resolutions={
            "supported": absent | {Capability.SMOKE: "present"},
            "verified_low": absent,
        },
    )

    assert result.evidence["utility_by_vehicle"] == {
        "supported": "medium",
        "unknown": "unknown",
        "verified_low": "low",
    }
    assert result.evidence["inputs_by_vehicle"]["supported"]["smoke"] == "present"
    assert result.evidence["inputs_by_vehicle"]["unknown"]["scouting"] == "unknown"
    assert result.warnings == ("uptier utility unknown: unknown",)


def test_m2_role_redundancy_only_flags_near_equivalent_specialists_with_alternative() -> None:
    ruleset = load_ruleset("m2-capability-aware-v1")
    first = vehicle("aa_1", VehicleClass.SPAA, Capability.AUTOCANNON_AA)
    second = vehicle("aa_2", VehicleClass.SPAA, Capability.AUTOCANNON_AA)
    alternative = vehicle("medium", VehicleClass.MEDIUM_TANK)
    absent_specialist_facts = {
        Capability.RADAR: CapabilityState.VERIFIED_ABSENT,
        Capability.IRST: CapabilityState.VERIFIED_ABSENT,
        Capability.SAM: CapabilityState.VERIFIED_ABSENT,
    }

    result = role_redundancy_rule(
        (first, second),
        {"aa_1": 20, "aa_2": 23},
        eligible_alternatives=((alternative, 23),),
        capability_resolutions={
            "aa_1": absent_specialist_facts,
            "aa_2": absent_specialist_facts,
        },
        ruleset=ruleset,
    )
    general_purpose = role_redundancy_rule(
        (alternative, vehicle("medium_2", VehicleClass.MEDIUM_TANK)),
        {"medium": 23, "medium_2": 23},
        eligible_alternatives=((first, 23),),
        ruleset=ruleset,
    )

    assert result.score is None
    assert result.effective_score == 100
    assert result.status is RuleStatus.WARNING
    assert result.evidence["redundant_pairs"] == [["aa_1", "aa_2"]]
    assert result.warnings == ("near-equivalent specialist redundancy",)
    assert general_purpose.status is RuleStatus.GOOD
    assert general_purpose.evidence["redundant_pairs"] == []


def test_m2_role_redundancy_does_not_infer_equivalence_from_unknown_capabilities() -> None:
    ruleset = load_ruleset("m2-capability-aware-v1")
    first = vehicle("aa_1", VehicleClass.SPAA, Capability.RADAR)
    second = vehicle("aa_2", VehicleClass.SPAA)
    alternative = vehicle("medium", VehicleClass.MEDIUM_TANK)

    result = role_redundancy_rule(
        (first, second),
        {"aa_1": 20, "aa_2": 20},
        eligible_alternatives=((alternative, 20),),
        capability_resolutions={
            "aa_1": {Capability.RADAR: CapabilityState.CONFLICTED},
        },
        ruleset=ruleset,
    )

    assert result.evidence["redundant_pairs"] == []
    assert result.evidence["pair_evidence"][0]["capability_evidence_complete"] is False


def test_m2_redundancy_only_accepts_compatible_confident_performance_difference() -> None:
    ruleset = load_ruleset("m2-capability-aware-v1")
    first = vehicle("aa_1", VehicleClass.SPAA, Capability.AUTOCANNON_AA)
    second = vehicle("aa_2", VehicleClass.SPAA, Capability.AUTOCANNON_AA)
    alternative = vehicle("medium", VehicleClass.MEDIUM_TANK)
    absent = {
        capability: CapabilityState.VERIFIED_ABSENT
        for capability in (Capability.RADAR, Capability.IRST, Capability.SAM)
    }
    common = {
        "eligible_alternatives": ((alternative, 20),),
        "capability_resolutions": {"aa_1": absent, "aa_2": absent},
        "ruleset": ruleset,
    }

    incompatible = role_redundancy_rule(
        (first, second),
        {"aa_1": 20, "aa_2": 20},
        statistics_by_vehicle={
            "aa_1": statistic("aa_1", win_rate=0.4, scope=StatisticsScope.AIR_REALISTIC),
            "aa_2": statistic("aa_2", win_rate=0.6, scope=StatisticsScope.AIR_REALISTIC),
        },
        **common,
    )
    compatible = role_redundancy_rule(
        (first, second),
        {"aa_1": 20, "aa_2": 20},
        statistics_by_vehicle={
            "aa_1": statistic("aa_1", win_rate=0.4),
            "aa_2": statistic("aa_2", win_rate=0.6),
        },
        **common,
    )

    assert incompatible.status is RuleStatus.WARNING
    assert compatible.status is RuleStatus.GOOD


def test_m2_redundancy_is_diagnostic_only_for_composite_and_readiness() -> None:
    ruleset = load_ruleset("m2-capability-aware-v1")
    lineup = (
        vehicle("medium", VehicleClass.MEDIUM_TANK),
        vehicle("heavy", VehicleClass.HEAVY_TANK),
        vehicle("aa_1", VehicleClass.SPAA, Capability.AUTOCANNON_AA),
        vehicle("aa_2", VehicleClass.SPAA, Capability.AUTOCANNON_AA),
    )
    resolved = {item.vehicle_id: 20 for item in lineup}
    alternative = vehicle("alternative", VehicleClass.LIGHT_TANK)
    specialist_absences = {
        capability: CapabilityState.VERIFIED_ABSENT
        for capability in (Capability.RADAR, Capability.IRST, Capability.SAM)
    }
    resolutions = {"aa_1": specialist_absences, "aa_2": specialist_absences}

    without_alternative = evaluate_lineup_rules(
        lineup, resolved, capability_resolutions=resolutions, ruleset=ruleset
    )
    with_alternative = evaluate_lineup_rules(
        lineup,
        resolved,
        eligible_alternatives=((alternative, 20),),
        capability_resolutions=resolutions,
        ruleset=ruleset,
    )

    assert with_alternative.overall_score == without_alternative.overall_score
    assert with_alternative.readiness_passed == without_alternative.readiness_passed
    assert next(
        rule for rule in with_alternative.rules if rule.rule == "role_redundancy"
    ).status is RuleStatus.WARNING


def test_performance_metric_shrinks_small_samples_more_than_large_samples() -> None:
    peers = tuple(0.5 + offset for offset in (-0.04, -0.02, 0, 0.02, 0.04))

    small = performance_metric(0.61, 180, peers, shrinkage_k=1000)
    large = performance_metric(0.61, 500_000, peers, shrinkage_k=1000)

    assert small.confidence_weight < large.confidence_weight
    assert abs(small.adjusted_value - small.peer_mean) < abs(large.adjusted_value - large.peer_mean)
    assert small.score < large.score


def test_performance_metric_zero_variance_peers_is_neutral() -> None:
    result = performance_metric(0.75, 100_000, (0.5,) * 5, shrinkage_k=1000)

    assert result.adjusted_value > result.peer_mean
    assert result.peer_deviation == 0
    assert result.score == 50


def test_statistical_strength_exposes_peer_scope_confidence_and_fallback() -> None:
    tank = vehicle("tank", VehicleClass.MEDIUM_TANK)
    peers = tuple(
        PeerStatistic(
            vehicle=vehicle(f"peer_{index}", VehicleClass.MEDIUM_TANK),
            battle_rating=20,
            statistics=statistic(
                f"peer_{index}",
                win_rate=0.45 + index * 0.02,
                kills=800 + index * 100,
            ),
        )
        for index in range(5)
    )

    result = statistical_strength_rule(
        (tank,),
        {"tank": 20},
        {"tank": statistic("tank", battles=5000, win_rate=0.6, kills=7000, deaths=4000)},
        peers,
    )
    evidence = result.evidence["vehicles"]["tank"]["win_rate"]

    assert result.score is not None
    assert 0 <= result.score <= 100
    assert evidence["peer_group"] == "same_class_within_one_step"
    assert evidence["peer_count"] == 5
    assert evidence["scope"] == "ground_realistic_ground_vehicles"
    assert 0 < evidence["confidence_weight"] < 1


def test_statistics_missing_or_incompatible_stay_visible_and_use_neutral_effective_score() -> None:
    tank = vehicle("tank", VehicleClass.MEDIUM_TANK)
    incompatible_peers = tuple(
        PeerStatistic(
            vehicle=vehicle(f"peer_{index}", VehicleClass.MEDIUM_TANK),
            battle_rating=20,
            statistics=statistic(
                f"peer_{index}",
                snapshot_id="other",
                scope=StatisticsScope.AIR_REALISTIC,
            ),
        )
        for index in range(5)
    )

    evaluation = evaluate_lineup_rules(
        (tank,),
        {"tank": 20},
        statistics_by_vehicle={"tank": statistic("tank", win_rate=None, kills=None, deaths=None)},
        peer_statistics=incompatible_peers,
    )
    stats_rule = next(rule for rule in evaluation.rules if rule.rule == "statistical_strength")

    assert stats_rule.score is None
    assert stats_rule.effective_score == 50
    assert stats_rule.status is RuleStatus.UNKNOWN
    assert stats_rule.evidence["vehicles"]["tank"]["win_rate"]["raw_value"] is None


def test_missing_vehicle_statistics_contribute_neutral_to_partial_lineup_score() -> None:
    strong = vehicle("strong", VehicleClass.MEDIUM_TANK)
    missing = vehicle("missing", VehicleClass.LIGHT_TANK)
    peers = tuple(
        PeerStatistic(
            vehicle=vehicle(f"peer_{index}", VehicleClass.MEDIUM_TANK),
            battle_rating=20,
            statistics=statistic(f"peer_{index}", win_rate=0.45 + index * 0.01),
        )
        for index in range(5)
    )

    result = statistical_strength_rule(
        (strong, missing),
        {"strong": 20, "missing": 20},
        {"strong": statistic("strong", battles=500_000, win_rate=0.8)},
        peers,
    )

    assert result.score is not None
    assert result.score < 75
    assert result.warnings == ("statistics missing or incompatible",)


def test_composite_and_readiness_are_deterministic_and_structural_rules_dominate() -> None:
    lineup = (
        vehicle("medium", VehicleClass.MEDIUM_TANK),
        vehicle("light", VehicleClass.LIGHT_TANK, Capability.SCOUTING),
        vehicle("td", VehicleClass.TANK_DESTROYER),
        vehicle("aa", VehicleClass.SPAA),
    )
    resolved = {"medium": 27, "light": 23, "td": 20, "aa": 20}

    first = evaluate_lineup_rules(lineup, resolved)
    second = evaluate_lineup_rules(tuple(lineup), dict(reversed(tuple(resolved.items()))))

    assert first == second
    assert first.readiness_passed
    assert first.overall_score < 100


def test_role_coverage_surfaces_overlap_without_making_it_invalid() -> None:
    lineup = tuple(
        vehicle(f"light_{index}", VehicleClass.LIGHT_TANK) for index in range(3)
    )

    result = role_coverage_rule(lineup)

    assert result.evidence["role_counts"]["flanker"] == 3
    assert result.evidence["excessive_overlap"] == ["flanker"]


def test_readiness_reports_each_failed_gate_and_owned_spaa_omission() -> None:
    top = vehicle("top", VehicleClass.MEDIUM_TANK)
    low = vehicle("low", VehicleClass.HEAVY_TANK)
    aa = vehicle("aa", VehicleClass.SPAA)

    evaluation = evaluate_lineup_rules(
        (top, low),
        {"top": 40, "low": 27},
        nearby_spaa=((aa, VehicleStatus.OWNED),),
    )

    assert not evaluation.readiness_passed
    assert set(evaluation.readiness_failures) >= {
        "at_least_two_within_one_step",
        "at_least_three_within_two_steps",
        "backup_depth_at_least_60",
        "critical_owned_spaa_omission",
        "no_severe_cohesion_warning",
    }


def test_ruleset_models_are_immutable() -> None:
    ruleset = load_ruleset()

    with pytest.raises(Exception, match="frozen"):
        ruleset.weights = ruleset.weights
