from __future__ import annotations

import json
from time import monotonic, sleep
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from mcp.client import Client
from typer.testing import CliRunner

from wt_advisor.cli.main import create_app as create_cli
from wt_advisor.domain.models import CandidateLineup, Role, VehicleStatus
from wt_advisor.mcp.server import create_server
from wt_advisor.rules import evaluate_lineup_rules, statistical_strength_rule
from wt_advisor.services import recommendations
from wt_advisor.services.advisor import AdvisorService
from wt_advisor.services.explanations import (
    build_alternative_explanation,
    build_tradeoffs,
)
from wt_advisor.services.recommendations import (
    DUPLICATE_CAP,
    MAX_PREFERENCE_SWING,
    PREFERENCE_CAP,
    ROLE_CAP,
    _duplicate_role_pairs,
    _lineup_key,
    _lineup_payload,
    _preference_factors,
)
from wt_advisor.web.app import create_app


def test_advisor_snapshot_has_ready_lineup_research_and_reasons(tmp_path) -> None:
    service = AdvisorService.from_database(tmp_path / "advisor.sqlite")

    snapshot = service.compute_advisor_snapshot("acceptance")

    assert snapshot["primary_lineup"]["analysis"]["readiness_passed"] is True
    assert snapshot["recommended_br"] == snapshot["primary_lineup"]["analysis"]["lineup_br"]
    assert snapshot["research_priorities"]
    assert snapshot["primary_lineup"]["reasons"]
    assert snapshot["source_fingerprint"]


def test_advisor_api_refresh_is_nonblocking_and_mcp_independent(tmp_path) -> None:
    service = AdvisorService.from_database(tmp_path / "advisor.sqlite")
    client = TestClient(create_app(service))

    initial = client.get("/api/profiles/acceptance/advisor")
    assert initial.status_code == 200
    assert initial.json()["data"]["status"] == "missing"

    started = client.post("/api/profiles/acceptance/advisor/refresh")
    assert started.status_code == 200
    assert started.json()["data"]["status"] in {"computing", "current"}
    until = monotonic() + 20
    while monotonic() < until:
        state = client.get("/api/profiles/acceptance/advisor").json()["data"]
        if state["status"] != "computing":
            break
        sleep(0.05)
    assert state["status"] == "current"
    assert state["snapshot"]["primary_lineup"] is not None


def test_advisor_snapshot_stales_after_garage_change(tmp_path) -> None:
    service = AdvisorService.from_database(tmp_path / "advisor.sqlite")
    service.refresh_advisor_snapshot("acceptance")
    assert service.get_advisor_snapshot("acceptance")["status"] == "current"

    service.set_user_vehicle_status("acceptance", "us_m3_lee", "owned")

    state = service.get_advisor_snapshot("acceptance")
    assert state["status"] == "stale"
    assert state["snapshot"] is not None


@pytest.mark.anyio
async def test_cli_and_mcp_read_same_snapshot(tmp_path) -> None:
    service = AdvisorService.from_database(tmp_path / "advisor.sqlite")
    service.refresh_advisor_snapshot("acceptance")

    cli = CliRunner().invoke(create_cli(service), ["advisor", "show", "--json"])
    assert cli.exit_code == 0, cli.output
    async with Client(create_server(service)) as client:
        mcp = await client.call_tool("get_advisor_snapshot", {"profile_id": "acceptance"})
    assert json.loads(cli.output) == mcp.structured_content


def test_no_ready_lineup_labels_research_as_recovery_targets() -> None:
    service = AdvisorService.from_acceptance_fixture()
    service._statuses = {
        vehicle_id: (VehicleStatus.OWNED if vehicle_id == "us_m3_stuart" else status)
        for vehicle_id, status in service._statuses.items()
        if status is not VehicleStatus.OWNED or vehicle_id == "us_m3_stuart"
    }

    snapshot = service.compute_advisor_snapshot("acceptance")

    assert snapshot["primary_lineup"] is None
    assert "no_readiness_passing_lineup" in snapshot["blockers"]
    assert all(item["recovery_target"] for item in snapshot["research_priorities"])


def test_preferences_cannot_make_an_unready_lineup_primary(tmp_path) -> None:
    service = AdvisorService.from_database(tmp_path / "advisor.sqlite")
    context = service.get_advisor_context("acceptance")
    service.update_advisor_context(
        "acceptance", selected_preset_id=None, expected_revision=f"rev-{context.revision}",
        preferred_roles=["tank_destroyer", "spaa"], duplicate_role_penalty=10,
    )

    snapshot = service.compute_advisor_snapshot("acceptance")

    assert snapshot["primary_lineup"]["analysis"]["readiness_passed"] is True


def test_preferences_cannot_change_the_selected_battle_rating(tmp_path) -> None:
    service = AdvisorService.from_database(tmp_path / "advisor.sqlite")
    neutral = service.compute_advisor_snapshot("acceptance")["recommended_br"]
    context = service.get_advisor_context("acceptance")
    service.update_advisor_context(
        "acceptance", selected_preset_id=None, expected_revision=f"rev-{context.revision}",
        preferred_roles=["tank_destroyer"], duplicate_role_penalty=10,
    )

    assert service.compute_advisor_snapshot("acceptance")["recommended_br"] == neutral


def test_objective_score_is_unchanged_by_preferences(tmp_path) -> None:
    """Preferences add a separate term; they never rewrite the validated evaluation."""
    service = AdvisorService.from_database(tmp_path / "advisor.sqlite")
    baseline = service.generate_lineups(profile_id="acceptance", top_n=100)
    context = service.get_advisor_context("acceptance")
    service.update_advisor_context(
        "acceptance", selected_preset_id=None, expected_revision=f"rev-{context.revision}",
        preferred_roles=["tank_destroyer", "spaa"], duplicate_role_penalty=10,
    )

    primary = service.compute_advisor_snapshot("acceptance")["primary_lineup"]

    scores = {
        candidate.analysis.lineup.slots: candidate.analysis.overall_score
        for group in baseline.groups
        for candidate in group.candidates
    }
    slots = tuple(primary["analysis"]["lineup"]["slots"])
    assert primary["preference_factors"]["objective_score"] == primary["analysis"][
        "overall_score"
    ]
    if slots in scores:
        assert primary["analysis"]["overall_score"] == scores[slots]


def _gapped_pair(service, *, gap: float):
    """Two candidates whose objective scores differ by exactly `gap`.

    The tank destroyer lineup is the weaker one, so only a preference can promote it.
    """
    td = service.analyze_lineup(("us_m3_gmc", "us_m3_stuart"))
    duplicates = service.analyze_lineup(("us_m3_stuart", "us_m3a1_stuart"))
    duplicates = duplicates.model_copy(update={"overall_score": td.overall_score + gap})
    return CandidateLineup(analysis=td), CandidateLineup(analysis=duplicates)


def test_strong_preference_overcomes_a_small_objective_gap() -> None:
    service = AdvisorService.from_acceptance_fixture()
    favored, stronger = _gapped_pair(service, gap=MAX_PREFERENCE_SWING / 2)

    assert _lineup_key(favored, (Role.TANK_DESTROYER,), 10, service) < _lineup_key(
        stronger, (Role.TANK_DESTROYER,), 10, service
    )


def test_preference_cannot_overcome_a_gap_beyond_the_documented_bound() -> None:
    """The differential, not one candidate's adjustment, is the binding invariant."""
    service = AdvisorService.from_acceptance_fixture()
    favored, stronger = _gapped_pair(service, gap=MAX_PREFERENCE_SWING + 0.01)

    assert _lineup_key(stronger, (Role.TANK_DESTROYER,), 10, service) < _lineup_key(
        favored, (Role.TANK_DESTROYER,), 10, service
    )


def test_each_preference_term_stays_within_its_own_cap() -> None:
    service = AdvisorService.from_acceptance_fixture()
    td = CandidateLineup(analysis=service.analyze_lineup(("us_m3_gmc", "us_m3_stuart")))
    duplicates = CandidateLineup(
        analysis=service.analyze_lineup(("us_m3_stuart", "us_m3a1_stuart"))
    )

    best = _preference_factors(td, (Role.TANK_DESTROYER,), 0, service)
    worst = _preference_factors(duplicates, (), 10, service)

    assert best["preference_adjustment"] <= ROLE_CAP
    assert worst["preference_adjustment"] >= -DUPLICATE_CAP
    assert abs(best["preference_adjustment"]) <= PREFERENCE_CAP
    assert abs(worst["preference_adjustment"]) <= PREFERENCE_CAP
    assert best["preference_adjustment"] - worst["preference_adjustment"] <= (
        MAX_PREFERENCE_SWING
    )


def test_zero_penalty_produces_no_duplicate_role_adjustment() -> None:
    service = AdvisorService.from_acceptance_fixture()
    duplicates = CandidateLineup(
        analysis=service.analyze_lineup(("us_m3_stuart", "us_m3a1_stuart"))
    )

    factors = _preference_factors(duplicates, (), 0, service)

    assert factors["duplicate_role_pairs"] == 1
    assert factors["duplicate_component"] == 0.0
    assert factors["preference_adjustment"] == 0.0


def test_duplicate_penalty_influence_is_monotonic_up_to_its_cap() -> None:
    service = AdvisorService.from_acceptance_fixture()
    duplicates = CandidateLineup(
        analysis=service.analyze_lineup(("us_m3_stuart", "us_m3a1_stuart"))
    )

    components = [
        _preference_factors(duplicates, (), penalty, service)["duplicate_component"]
        for penalty in range(0, 11)
    ]

    assert components == sorted(components, reverse=True)
    assert components[0] == 0.0
    assert components[-1] >= -DUPLICATE_CAP
    # The duplicate term never touches the preferred-role term.
    assert all(
        _preference_factors(duplicates, (), penalty, service)["role_component"] == 0.0
        for penalty in range(0, 11)
    )


def test_canonical_duplicate_counting_semantics() -> None:
    """One canonical role per vehicle, SPAA exempt, capability roles never duplicate."""
    service = AdvisorService.from_acceptance_fixture()

    def pairs(slots):
        return _duplicate_role_pairs(
            CandidateLineup(analysis=service.analyze_lineup(slots)), service._vehicles
        )

    # Two light tanks are one duplicate pair, not two via flanker plus scout.
    assert pairs(("us_m3_stuart", "us_m3a1_stuart")) == 1
    # Two tank destroyers are one pair, not two via tank_destroyer plus sniper.
    assert pairs(("us_m3_gmc", "us_m10_gmc")) == 1
    # SPAA is exempt: the anti-air and diagnostic rules already govern it.
    assert pairs(("us_m13_mgmc", "us_m16_mgmc")) == 0
    # Different canonical roles are not duplicates at all.
    assert pairs(("us_m3_gmc", "us_m3_stuart")) == 0


def test_cached_statistical_rule_preserves_exact_evaluation() -> None:
    service = AdvisorService.from_m2_acceptance_fixture()
    vehicle_ids = ("us_m3_stuart", "us_m3a1_stuart", "us_m22")
    vehicles = tuple(service._vehicles[vehicle_id] for vehicle_id in vehicle_ids)
    brs = {vehicle_id: service._battle_ratings[vehicle_id] for vehicle_id in vehicle_ids}
    statistics = service._scoring_statistics()
    peers = service._peer_statistics()
    cached = {
        vehicle.vehicle_id: statistical_strength_rule(
            (vehicle,), {vehicle.vehicle_id: brs[vehicle.vehicle_id]},
            statistics, peers, service._ruleset,
            evaluation_date=service._evaluation_date,
        )
        for vehicle in vehicles
    }

    ordinary = evaluate_lineup_rules(
        vehicles, brs, statistics_by_vehicle=statistics, peer_statistics=peers,
        ruleset=service._ruleset, evaluation_date=service._evaluation_date,
    )
    reused = evaluate_lineup_rules(
        vehicles, brs, statistics_by_vehicle=statistics, peer_statistics=peers,
        ruleset=service._ruleset, evaluation_date=service._evaluation_date,
        statistical_rule_cache=cached,
    )

    assert reused == ordinary


def test_advisor_cache_preserves_frozen_m1_lineup_analysis() -> None:
    service = AdvisorService.from_acceptance_fixture()
    expected = service.generate_lineups(profile_id="acceptance", top_n=100).recommended
    assert expected is not None

    snapshot = service.compute_advisor_snapshot("acceptance")

    assert snapshot["primary_lineup"]["analysis"]["overall_score"] == (
        expected.analysis.overall_score
    )
    assert snapshot["primary_lineup"]["analysis"]["rules"] == [
        rule.model_dump(mode="json") for rule in expected.analysis.rules
    ]


def test_role_preference_and_duplicate_penalty_break_equal_score_ties() -> None:
    service = AdvisorService.from_acceptance_fixture()
    td = service.analyze_lineup(("us_m3_gmc", "us_m3_stuart"))
    duplicates = service.analyze_lineup(("us_m3_stuart", "us_m3a1_stuart"))
    duplicates = duplicates.model_copy(update={"overall_score": td.overall_score})
    favored = CandidateLineup(analysis=td)
    repeated = CandidateLineup(analysis=duplicates)

    assert _lineup_key(
        favored, (Role.TANK_DESTROYER,), 0, service
    ) < _lineup_key(repeated, (Role.TANK_DESTROYER,), 0, service)
    assert _lineup_key(favored, (), 2, service) < _lineup_key(repeated, (), 2, service)


def test_older_completion_cannot_hide_matching_current_snapshot(tmp_path) -> None:
    service = AdvisorService.from_database(tmp_path / "advisor.sqlite")
    current = service.refresh_advisor_snapshot("acceptance")["snapshot"]
    assert current is not None
    service._repository.store_evaluation(
        evaluation_id=uuid4().hex, profile_id=current["profile_id"], kind="advisor_snapshot",
        profile_revision="outdated", profile_state={}, effective_inputs={},
        evidence_context=current["evidence_context"],
        ruleset_hash=service.evidence_context.ruleset_hash,
        schema_revision=service.evidence_context.schema_revision,
        result_id="outdated", result_payload={**current, "source_fingerprint": "outdated"},
    )

    state = service.get_advisor_snapshot("acceptance")

    assert state["status"] == "current"
    assert state["snapshot"]["source_fingerprint"] == current["source_fingerprint"]


def test_failed_refresh_retains_prior_snapshot_and_allows_retry(tmp_path, monkeypatch) -> None:
    service = AdvisorService.from_database(tmp_path / "advisor.sqlite")
    service.refresh_advisor_snapshot("acceptance")
    original = service.refresh_advisor_snapshot
    monkeypatch.setattr(service, "refresh_advisor_snapshot", lambda _: (_ for _ in ()).throw(
        RuntimeError("test failure")
    ))
    client = TestClient(create_app(service))
    client.post("/api/profiles/acceptance/advisor/refresh")
    until = monotonic() + 5
    while monotonic() < until:
        state = client.get("/api/profiles/acceptance/advisor").json()["data"]
        if state["status"] == "failed":
            break
        sleep(0.02)
    assert state["status"] == "failed"
    assert state["snapshot"] is not None
    monkeypatch.setattr(service, "refresh_advisor_snapshot", original)
    client.post("/api/profiles/acceptance/advisor/refresh")


def test_two_light_tanks_emit_no_redundancy_derived_strength() -> None:
    """The observed BR 3.0 case: role_redundancy is SPAA-only and must not read as a strength.

    Its GOOD status only means no near-equivalent SPAA pair was found, which is vacuous
    when the lineup holds fewer than two SPAA, and it never inspects light tanks at all.
    """
    service = AdvisorService.from_m2_acceptance_fixture()
    candidate = CandidateLineup(
        analysis=service.analyze_lineup(("us_m16_mgmc", "us_m3_stuart", "us_m3a1_stuart"))
    )

    payload = _lineup_payload(candidate, (), 0, service)

    assert "role_redundancy" in candidate.analysis.strengths
    assert not any("REDUNDANCY" in reason for reason in payload["reasons"])
    assert "NO_SPECIALIST_OVERLAP" not in payload["reasons"]


def test_specialist_overlap_reported_only_when_a_pair_was_compared() -> None:
    service = AdvisorService.from_m2_acceptance_fixture()
    two_spaa = CandidateLineup(
        analysis=service.analyze_lineup(("us_m13_mgmc", "us_m16_mgmc", "us_m3_stuart"))
    )

    reasons = _lineup_payload(two_spaa, (), 0, service)["reasons"]
    rule = next(
        item for item in two_spaa.analysis.rules if item.rule == "role_redundancy"
    )

    assert rule.evidence["pair_evidence"]
    assert ("NO_SPECIALIST_OVERLAP" in reasons) is not bool(rule.evidence["redundant_pairs"])


def test_explanation_separates_strengths_tradeoffs_and_warnings(tmp_path) -> None:
    service = AdvisorService.from_database(tmp_path / "advisor.sqlite")
    context = service.get_advisor_context("acceptance")
    service.update_advisor_context(
        "acceptance", selected_preset_id=None, expected_revision=f"rev-{context.revision}",
        preferred_roles=["tank_destroyer"], duplicate_role_penalty=10,
    )

    snapshot = service.compute_advisor_snapshot("acceptance")
    explanation = snapshot["primary_lineup"]["explanation"]
    factors = snapshot["primary_lineup"]["preference_factors"]

    assert explanation["strengths"]
    if factors["preferred_roles_missing"]:
        # An unmet preference is a tradeoff, never a strength and never a blocker.
        assert any("tank destroyer" in line for line in explanation["tradeoffs"])
        assert not any("tank destroyer" in line for line in explanation["strengths"])
        assert not any("tank destroyer" in line for line in explanation["warnings"])
    assert explanation["evidence_summary"]["summary"]


def test_unsatisfiable_preference_is_distinguished_from_being_outranked() -> None:
    service = AdvisorService.from_acceptance_fixture()
    unsatisfiable = build_tradeoffs(
        {"preferred_roles_covered": [], "duplicate_role_pairs": 0},
        {
            "unsatisfiable_preferred_roles": ["tank_destroyer"],
            "unmet_preferred_roles": [],
            "minimum_duplicate_role_pairs": 0,
        },
        [{"vehicle_id": "us_m10_gmc", "reasons": ["ADDS_PREFERRED_ROLE"]}],
    )
    outranked = build_tradeoffs(
        {"preferred_roles_covered": [], "duplicate_role_pairs": 0},
        {
            "unsatisfiable_preferred_roles": [],
            "unmet_preferred_roles": ["tank_destroyer"],
            "minimum_duplicate_role_pairs": 0,
        },
        [],
    )

    assert any("No ready lineup at this battle rating" in line for line in unsatisfiable)
    assert any("us_m10_gmc" in line for line in unsatisfiable)
    assert any("strong enough to stay the primary choice" in line for line in outranked)
    assert not any("No ready lineup" in line for line in outranked)
    assert service is not None


def test_alternatives_never_compare_scores_across_battle_ratings() -> None:
    lower = build_alternative_explanation(
        {"analysis": {"lineup_br": 27}, "preference_factors": {"objective_score": 86.0}},
        {"analysis": {"lineup_br": 30}, "preference_factors": {"objective_score": 79.7}},
    )
    same = build_alternative_explanation(
        {"analysis": {"lineup_br": 30}, "preference_factors": {"objective_score": 77.2}},
        {"analysis": {"lineup_br": 30}, "preference_factors": {"objective_score": 79.7}},
    )

    assert lower["objective_delta"] is None
    assert not any("points" in line for line in lower["differences"])
    assert any("not directly comparable" in line for line in lower["differences"])
    assert same["objective_delta"] is not None
    assert any("points lower" in line for line in same["differences"])


def test_raw_reason_codes_and_provenance_survive_the_explanation_layer(tmp_path) -> None:
    service = AdvisorService.from_database(tmp_path / "advisor.sqlite")

    primary = service.compute_advisor_snapshot("acceptance")["primary_lineup"]

    assert "HIGHEST_READY_BR" in primary["reasons"]
    assert "OBJECTIVE_SCORE_RANK" in primary["reasons"]
    statistical = next(
        rule for rule in primary["analysis"]["rules"]
        if rule["rule"] == "statistical_strength"
    )
    vehicles = statistical["evidence"]["vehicles"]
    assert vehicles
    assert any("source_scope" in item for item in vehicles.values())
    assert primary["preference_factors"]["preference_cap"] == PREFERENCE_CAP


def test_snapshot_stales_when_the_recommendation_policy_changes(tmp_path, monkeypatch) -> None:
    service = AdvisorService.from_database(tmp_path / "advisor.sqlite")
    service.refresh_advisor_snapshot("acceptance")
    assert service.get_advisor_snapshot("acceptance")["status"] == "current"

    monkeypatch.setattr(
        recommendations, "RECOMMENDATION_POLICY_VERSION", "a-later-ranking-policy"
    )

    state = service.get_advisor_snapshot("acceptance")
    assert state["status"] == "stale"
    assert "policy_changed" in state["stale_reasons"]
    assert state["snapshot"] is not None
