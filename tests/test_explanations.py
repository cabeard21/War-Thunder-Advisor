"""Unit coverage for the deterministic explanation wording.

These are pure functions over an already-computed snapshot, so they are tested directly
rather than through a service: the point is that identical input always yields identical
text, and that the three categories never leak into one another.
"""

from __future__ import annotations

from wt_advisor.services.explanations import (
    _join,
    _role_name,
    build_alternative_explanation,
    build_evidence_summary,
    build_lineup_explanation,
    build_strengths,
    build_tradeoffs,
    build_warnings,
)


def _stats(**vehicles):
    return {"rules": [{"rule": "statistical_strength", "evidence": {"vehicles": vehicles}}]}


def test_join_reads_naturally_for_one_two_and_many() -> None:
    assert _join([]) == ""
    assert _join(["a"]) == "a"
    assert _join(["a", "b"]) == "a and b"
    assert _join(["a", "b", "c"]) == "a, b and c"


def test_unknown_role_falls_back_to_a_readable_form() -> None:
    assert _role_name("tank_destroyer") == "tank destroyer"
    assert _role_name("some_future_role") == "some future role"


def test_policy_reason_codes_are_not_presented_as_strengths() -> None:
    """OBJECTIVE_SCORE_RANK documents the ranking policy, not a property of the lineup."""
    strengths = build_strengths(
        ["HIGHEST_READY_BR", "OBJECTIVE_SCORE_RANK", "STRENGTH_ANTI_AIR", "UNKNOWN_CODE"]
    )

    assert any("highest battle rating" in line for line in strengths)
    assert any("anti-air" in line for line in strengths)
    assert len(strengths) == 2


def test_satisfied_preference_is_reported_without_a_tradeoff_complaint() -> None:
    lines = build_tradeoffs(
        {"preferred_roles_covered": ["tank_destroyer"], "duplicate_role_pairs": 0},
        {
            "unsatisfiable_preferred_roles": [],
            "unmet_preferred_roles": [],
            "minimum_duplicate_role_pairs": 0,
            "preference_changed_selection": True,
        },
        [],
    )

    assert any("Includes your preferred tank destroyer" in line for line in lines)
    assert any("changed which lineup is recommended" in line for line in lines)


def test_avoidable_duplicates_are_worded_differently_from_unavoidable_ones() -> None:
    avoidable = build_tradeoffs(
        {"preferred_roles_covered": [], "duplicate_role_pairs": 2},
        {
            "unsatisfiable_preferred_roles": [],
            "unmet_preferred_roles": [],
            "minimum_duplicate_role_pairs": 0,
            "duplicate_roles_unavoidable": False,
        },
        [],
    )
    unavoidable = build_tradeoffs(
        {"preferred_roles_covered": [], "duplicate_role_pairs": 1},
        {
            "unsatisfiable_preferred_roles": [],
            "unmet_preferred_roles": [],
            "minimum_duplicate_role_pairs": 1,
            "duplicate_roles_unavoidable": True,
        },
        [],
    )

    assert any(line == "Carries 2 duplicated roles." for line in avoidable)
    assert any(
        "no ready lineup at this battle rating avoids that" in line for line in unavoidable
    )
    assert any("duplicated role;" in line for line in unavoidable)


def test_no_preferences_configured_produces_no_tradeoff_noise() -> None:
    assert build_tradeoffs(
        {"preferred_roles_covered": [], "duplicate_role_pairs": 0},
        {
            "unsatisfiable_preferred_roles": [],
            "unmet_preferred_roles": [],
            "minimum_duplicate_role_pairs": 0,
        },
        [],
    ) == []


def test_research_pointer_prefers_the_target_that_adds_the_preferred_role() -> None:
    lines = build_tradeoffs(
        {"preferred_roles_covered": [], "duplicate_role_pairs": 0},
        {
            "unsatisfiable_preferred_roles": ["tank_destroyer"],
            "unmet_preferred_roles": [],
            "minimum_duplicate_role_pairs": 0,
        },
        [
            {"vehicle_id": "us_top_ranked", "reasons": ["OPENS_HIGHER_READY_BR"]},
            {"vehicle_id": "us_m10_gmc", "reasons": ["ADDS_PREFERRED_ROLE"]},
        ],
        {"us_m10_gmc": "M10 GMC"},
    )

    assert any("Researching M10 GMC" in line for line in lines)


def test_research_pointer_falls_back_to_the_top_ranked_target() -> None:
    lines = build_tradeoffs(
        {"preferred_roles_covered": [], "duplicate_role_pairs": 0},
        {
            "unsatisfiable_preferred_roles": ["tank_destroyer"],
            "unmet_preferred_roles": [],
            "minimum_duplicate_role_pairs": 0,
        },
        [{"vehicle_id": "us_top_ranked", "reasons": ["OPENS_HIGHER_READY_BR"]}],
    )

    assert any("Researching us_top_ranked" in line for line in lines)


def test_warnings_deduplicate_and_ignore_unknown_codes() -> None:
    lines = build_warnings(
        {"warnings": ["missing_effective_spaa", "some_internal_code"]},
        ["missing_effective_spaa", "no_readiness_passing_lineup"],
    )

    assert len(lines) == 2
    assert any("no effective anti-air" in line for line in lines)
    assert any("passes the readiness checks" in line for line in lines)


def test_evidence_summary_covers_every_coverage_shape() -> None:
    assert "No community performance data is attached" in build_evidence_summary({})["summary"]
    full = build_evidence_summary(_stats(a={"eligible": True}, b={"eligible": True}))
    assert "available for all 2 vehicles." in full["summary"]
    proxied = build_evidence_summary(
        _stats(a={"eligible": True, "proxy_used": True}, b={"eligible": True})
    )
    assert "1 rely on proxy statistics" in proxied["summary"]
    partial = build_evidence_summary(_stats(a={"eligible": True}, b={"eligible": False}))
    assert "covers 1 of 2 vehicles." in partial["summary"]
    partial_proxy = build_evidence_summary(
        _stats(a={"eligible": True, "proxy_used": True}, b={"eligible": False})
    )
    assert "through proxy statistics" in partial_proxy["summary"]
    none_usable = build_evidence_summary(_stats(a={"eligible": False}))
    assert "No usable community performance data" in none_usable["summary"]
    assert none_usable["eligible_count"] == 0


def test_lineup_explanation_keeps_the_three_categories_separate() -> None:
    explanation = build_lineup_explanation(
        {
            "reasons": ["STRENGTH_ANTI_AIR"],
            "analysis": {
                "warnings": ["missing_effective_spaa"],
                **_stats(a={"eligible": True}),
            },
            "preference_factors": {
                "preferred_roles_covered": [],
                "duplicate_role_pairs": 1,
            },
        },
        transparency={
            "unsatisfiable_preferred_roles": ["tank_destroyer"],
            "unmet_preferred_roles": [],
            "minimum_duplicate_role_pairs": 1,
            "duplicate_roles_unavoidable": True,
        },
        blockers=[],
        research_priorities=[{"vehicle_id": "us_m10_gmc", "reasons": ["ADDS_PREFERRED_ROLE"]}],
    )

    assert explanation["research_pointer"] == "us_m10_gmc"
    assert all("tank destroyer" not in line for line in explanation["strengths"])
    assert all("tank destroyer" not in line for line in explanation["warnings"])
    assert any("tank destroyer" in line for line in explanation["tradeoffs"])


def test_alternative_at_a_higher_battle_rating_is_described_as_raising_it() -> None:
    raised = build_alternative_explanation(
        {"analysis": {"lineup_br": 37}, "preference_factors": {}},
        {"analysis": {"lineup_br": 30}, "preference_factors": {}},
    )

    assert any("Raises the battle rating to 3.7." in line for line in raised["differences"])
    assert raised["same_battle_rating"] is False


def test_alternative_reports_gained_and_lost_preferred_roles() -> None:
    swapped = build_alternative_explanation(
        {
            "analysis": {"lineup_br": 30},
            "preference_factors": {
                "preferred_roles_covered": ["tank_destroyer"],
                "duplicate_role_pairs": 0,
                "objective_score": 80.0,
            },
        },
        {
            "analysis": {"lineup_br": 30},
            "preference_factors": {
                "preferred_roles_covered": ["spaa"],
                "duplicate_role_pairs": 2,
                "objective_score": 80.0,
            },
        },
    )

    assert any("Adds your preferred tank destroyer" in line for line in swapped["differences"])
    assert any("Gives up your preferred anti-air" in line for line in swapped["differences"])
    assert any("Fewer duplicated roles (0 against 2)" in line for line in swapped["differences"])


def test_indistinguishable_alternative_says_so_rather_than_inventing_a_difference() -> None:
    same = build_alternative_explanation(
        {"analysis": {"lineup_br": 30}, "preference_factors": {"objective_score": 80.0}},
        {"analysis": {"lineup_br": 30}, "preference_factors": {"objective_score": 80.0}},
    )

    assert same["differences"] == ["Closely comparable to the primary lineup."]
