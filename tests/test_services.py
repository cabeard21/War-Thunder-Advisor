import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from wt_advisor.data.models import (
    RawDatasetMetadata,
    RawStatisticsDataset,
    RawVehicleDataset,
    RawVehicleRecord,
    canonical_bytes,
)
from wt_advisor.data.providers.fixture import FixtureProvider
from wt_advisor.domain.models import (
    AcquisitionType,
    CandidateGroup,
    DatasetType,
    GameMode,
    Nation,
    PrerequisiteSemantics,
    Researchability,
    ResearchDomain,
    ResearchEdge,
    ResearchEdgeType,
    ResolvedAvailability,
    SnapshotPurpose,
    StatisticsScope,
    TreeMembership,
    VehicleStatistics,
    VehicleStatus,
    Visibility,
)
from wt_advisor.services.advisor import AdvisorService, _lower_alternative
from wt_advisor.storage import EvidenceRepository, create_database


def test_generate_lineups_is_deterministic_and_grouped_by_br() -> None:
    service = AdvisorService.from_acceptance_fixture()

    first = service.generate_lineups(profile_id="acceptance", top_n=3)
    second = service.generate_lineups(profile_id="acceptance", top_n=3)

    assert first.analysis_id == second.analysis_id
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.groups
    assert all(group.candidates for group in first.groups)
    assert first.recommended is not None


def test_targeted_generation_does_not_return_failed_target_as_lower_alternative() -> None:
    service = AdvisorService.from_acceptance_fixture()
    service._battle_ratings["us_m24"] = 40

    result = service.generate_lineups(
        profile_id="acceptance",
        target_br=40,
        top_n=3,
        hypothetical_owned=frozenset({"us_m24"}),
    )

    assert [group.lineup_br for group in result.groups] == [40]
    assert result.groups[0].candidates
    assert all(
        not candidate.analysis.readiness_passed
        for candidate in result.groups[0].candidates
    )
    assert result.recommended is None
    assert result.lower_br_alternative is None


def test_lower_alternative_uses_valid_lower_frontier_after_failed_highest_frontier() -> None:
    service = AdvisorService.from_acceptance_fixture()
    candidate = service.generate_lineups(profile_id="acceptance", top_n=1).recommended
    assert candidate is not None
    valid_lower = candidate.model_copy(
        update={"analysis": candidate.analysis.model_copy(update={"lineup_br": 37})}
    )
    failed_highest = candidate.model_copy(
        update={
            "analysis": candidate.analysis.model_copy(
                update={"lineup_br": 40, "readiness_passed": False, "readiness_failures": ("test",)}
            )
        }
    )
    groups = (
        CandidateGroup(lineup_br=37, candidates=(valid_lower,)),
        CandidateGroup(lineup_br=40, candidates=(failed_highest,)),
    )

    assert _lower_alternative(groups, recommended=None, target_br=None) == groups[0]


def test_lower_alternative_is_strictly_below_valid_recommendation_and_ready() -> None:
    service = AdvisorService.from_acceptance_fixture()
    candidate = service.generate_lineups(profile_id="acceptance", top_n=1).recommended
    assert candidate is not None
    lower = candidate.model_copy(
        update={"analysis": candidate.analysis.model_copy(update={"lineup_br": 37})}
    )
    recommendation = candidate.model_copy(
        update={"analysis": candidate.analysis.model_copy(update={"lineup_br": 40})}
    )
    groups = (
        CandidateGroup(lineup_br=37, candidates=(lower,)),
        CandidateGroup(lineup_br=40, candidates=(recommendation,)),
    )

    assert _lower_alternative(groups, recommended=recommendation, target_br=None) == groups[0]


def test_lower_alternative_is_none_without_a_qualifying_lower_frontier() -> None:
    service = AdvisorService.from_acceptance_fixture()
    candidate = service.generate_lineups(profile_id="acceptance", top_n=1).recommended
    assert candidate is not None
    failed_lower = candidate.model_copy(
        update={
            "analysis": candidate.analysis.model_copy(
                update={"lineup_br": 37, "readiness_passed": False, "readiness_failures": ("test",)}
            )
        }
    )
    recommendation = candidate.model_copy(
        update={"analysis": candidate.analysis.model_copy(update={"lineup_br": 40})}
    )
    groups = (
        CandidateGroup(lineup_br=37, candidates=(failed_lower,)),
        CandidateGroup(lineup_br=40, candidates=(recommendation,)),
    )

    assert _lower_alternative(groups, recommended=recommendation, target_br=None) is None


def test_data_status_exposes_independent_milestone_two_components() -> None:
    status = AdvisorService.from_acceptance_fixture().data_status()

    assert set(status["components"]) == {
        "vehicle_metadata",
        "capabilities",
        "availability",
        "identity_aliases",
        "research_graph",
        "global_statistics",
    }
    assert status["components"]["vehicle_metadata"]["status"] == "available"
    assert status["components"]["research_graph"]["status"] == "available"
    assert status["components"]["global_statistics"]["status"] == "available"
    assert status["active"]["bundle_id"]


def test_statistics_inspection_is_read_only_and_resolves_provider_ids(
    tmp_path: Path,
) -> None:
    source = tmp_path / "statistics.json"
    source.write_text(
        json.dumps(
            [
                {
                    "source_vehicle_id": "us_m3_lee",
                    "mode_scope": "ground_realistic_ground_vehicles",
                    "sample_start": "2026-09-01",
                    "sample_end": "2026-09-18",
                    "battles": 10,
                    "wins": 5,
                    "losses": 5,
                }
            ]
        ),
        encoding="utf-8",
    )
    service = AdvisorService.from_acceptance_fixture()

    inspection = service.inspect_statistics(
        source, provider="manual-test", purpose=SnapshotPurpose.OPERATIONAL
    )

    assert inspection.valid is True
    assert inspection.matched_rows == 1
    assert inspection.canonical_match_rate == 1.0
    assert inspection.prospective_snapshot_id.startswith("statistics-")
    assert service.get_vehicle_statistics("us_m3_lee")[0].snapshot_id == (
        "fixture-usa-ground-rb-statistics-m1"
    )


def test_generation_excludes_premium_event_and_unowned_vehicles() -> None:
    service = AdvisorService.from_acceptance_fixture()

    result = service.generate_lineups(profile_id="acceptance", top_n=10)
    used_ids = {
        vehicle_id
        for group in result.groups
        for candidate in group.candidates
        for vehicle_id in candidate.analysis.lineup.slots
    }

    assert "us_m2a4_first_tank_div" not in used_ids
    assert "us_m3_lee" not in used_ids

    with pytest.raises(ValueError, match="ownership policy"):
        service.generate_lineups(
            profile_id="acceptance",
            hypothetical_owned=frozenset({"us_m2a4_first_tank_div"}),
            required_vehicle_id="us_m2a4_first_tank_div",
        )


def test_partial_generation_requires_explicit_opt_in() -> None:
    service = AdvisorService.from_acceptance_fixture()
    statuses = service.get_user_progress("acceptance").vehicle_statuses
    owned = [vehicle_id for vehicle_id, status in statuses.items() if status is VehicleStatus.OWNED]
    for vehicle_id in owned[2:]:
        service.set_user_vehicle_status("acceptance", vehicle_id, VehicleStatus.LOCKED)

    with pytest.raises(ValueError, match="allow partial"):
        service.generate_lineups(profile_id="acceptance")

    result = service.generate_lineups(profile_id="acceptance", allow_partial=True)
    assert result.groups
    assert all(
        len(item.analysis.lineup.slots) == 2
        for group in result.groups
        for item in group.candidates
    )


def test_hypothetical_m3_lee_unlock_does_not_mutate_profile() -> None:
    service = AdvisorService.from_acceptance_fixture()

    before = service.get_user_progress("acceptance")
    result = service.generate_lineups(
        profile_id="acceptance",
        hypothetical_owned=frozenset({"us_m3_lee"}),
        top_n=3,
    )
    after = service.get_user_progress("acceptance")

    assert before == after
    assert any(
        "us_m3_lee" in candidate.analysis.lineup.slots
        for group in result.groups
        for candidate in group.candidates
    )


def test_compare_cross_br_marks_overall_delta_non_comparable() -> None:
    service = AdvisorService.from_acceptance_fixture()

    comparison = service.compare_lineups(
        ("us_m2a4", "us_m2a4_1st", "us_m13_mgmc"),
        ("us_m3_lee", "us_m5a1", "us_m22", "us_m16_mgmc"),
    )

    assert comparison.cross_br is True
    assert comparison.overall_delta is None
    assert comparison.component_deltas


def test_suggest_addition_reports_resolved_anti_air_warning() -> None:
    service = AdvisorService.from_acceptance_fixture()

    suggestions = service.suggest_lineup_additions(
        profile_id="acceptance",
        lineup=("us_m2a4", "us_m2a4_1st", "us_m3_gmc", "us_m22"),
        hypothetical_owned=frozenset({"us_m16_mgmc"}),
    )

    m16 = next(item for item in suggestions if item.vehicle_id == "us_m16_mgmc")
    assert m16.resulting_analysis.lineup_br >= 10
    assert "missing_effective_spaa" in m16.resolved_warnings


def test_evaluate_next_unlocks_includes_forced_lineup() -> None:
    service = AdvisorService.from_acceptance_fixture()

    unlocks = service.evaluate_next_unlocks("acceptance")
    m3_lee = next(item for item in unlocks if item.vehicle_id == "us_m3_lee")

    assert "us_m3_lee" in m3_lee.forced_include.analysis.lineup.slots
    assert m3_lee.research_cost > 0
    assert m3_lee.current_status is VehicleStatus.RESEARCHING
    assert m3_lee.research_prerequisites == ("us_m2a4_1st",)
    assert m3_lee.prerequisite_statuses == {"us_m2a4_1st": VehicleStatus.OWNED}
    assert m3_lee.resulting_br == m3_lee.expanded_best.analysis.lineup_br
    assert m3_lee.readiness_before is m3_lee.before.analysis.readiness_passed
    assert m3_lee.readiness_expanded is m3_lee.expanded_best.analysis.readiness_passed
    assert m3_lee.readiness_forced is m3_lee.forced_include.analysis.readiness_passed
    assert m3_lee.adopted_immediately is m3_lee.adopted
    assert m3_lee.graph_snapshot_id is not None


def test_directly_researchable_requires_every_incoming_prerequisite() -> None:
    service = AdvisorService.from_acceptance_fixture()
    service._research_edges = (
        ("us_m2a4_first_tank_div", "us_m3_lee"),
        ("us_m2a4_1st", "us_m3_lee"),
    )

    assert "us_m3_lee" not in service._directly_researchable()

    service.set_user_vehicle_status(
        "acceptance", "us_m2a4_first_tank_div", VehicleStatus.OWNED
    )

    assert "us_m3_lee" in service._directly_researchable()


def test_directly_researchable_supports_any_prerequisite_groups() -> None:
    service = AdvisorService.from_acceptance_fixture()
    service._research_edges = (
        ResearchEdge(
            nation=Nation.USA,
            domain=ResearchDomain.GROUND,
            parent_vehicle_id="us_m2a4_first_tank_div",
            child_vehicle_id="us_m3_lee",
            edge_type=ResearchEdgeType.BRANCH_UNLOCK,
            prerequisite_group="branch-choice",
            prerequisite_semantics=PrerequisiteSemantics.ANY,
            snapshot_id="graph-test",
        ),
        ResearchEdge(
            nation=Nation.USA,
            domain=ResearchDomain.GROUND,
            parent_vehicle_id="us_m2a4_1st",
            child_vehicle_id="us_m3_lee",
            edge_type=ResearchEdgeType.BRANCH_UNLOCK,
            prerequisite_group="branch-choice",
            prerequisite_semantics=PrerequisiteSemantics.ANY,
            snapshot_id="graph-test",
        ),
    )

    assert "us_m3_lee" in service._directly_researchable()


def test_directly_researchable_requires_resolved_normal_researchability() -> None:
    service = AdvisorService.from_acceptance_fixture()
    unresolved = ResolvedAvailability(
        vehicle_id="us_m3_lee",
        acquisition_type=AcquisitionType.UNKNOWN,
        researchability=Researchability.UNKNOWN,
        tree_membership=TreeMembership.UNKNOWN,
        visibility=Visibility.UNKNOWN,
        source_snapshot_id="availability-test",
        source_provider="test",
        source_reference="test-record",
        reason="no verified acquisition classification",
    )
    service._resolved_availability = {"us_m3_lee": unresolved}

    assert "us_m3_lee" not in service._directly_researchable()


def test_profile_mutation_is_idempotent_and_revisioned() -> None:
    service = AdvisorService.from_acceptance_fixture()
    before = service.get_user_progress("acceptance")

    first = service.set_user_vehicle_status(
        "acceptance", "us_m3_lee", VehicleStatus.OWNED
    )
    second = service.set_user_vehicle_status(
        "acceptance", "us_m3_lee", VehicleStatus.OWNED
    )

    assert first.before_status is VehicleStatus.RESEARCHING
    assert first.after_status is VehicleStatus.OWNED
    assert first.revision != before.revision
    assert second.revision == first.revision


def test_vehicle_filters_validate_nation_and_mode() -> None:
    service = AdvisorService.from_acceptance_fixture()

    vehicles = service.list_vehicles(
        nation=Nation.USA,
        mode=GameMode.GROUND_REALISTIC,
        max_br=40,
    )

    assert vehicles
    assert all(item.vehicle.nation is Nation.USA and item.br <= 40 for item in vehicles)
    assert vehicles[0].provenance["battle_rating"]["source"] == "snapshot"


def test_database_factory_persists_profile_status(tmp_path: Path) -> None:
    database = tmp_path / "advisor.sqlite"
    first = AdvisorService.from_database(database)
    first.set_user_vehicle_status("acceptance", "us_m3_lee", VehicleStatus.OWNED)

    reopened = AdvisorService.from_database(database)

    status = reopened.get_user_progress("acceptance").vehicle_statuses["us_m3_lee"]
    assert status is VehicleStatus.OWNED


def test_profile_reconciliation_dry_run_then_apply_is_audited_and_idempotent(
    tmp_path: Path,
) -> None:
    database = tmp_path / "advisor.sqlite"
    AdvisorService.from_database(database)
    fixture = FixtureProvider().load()
    aliases = (
        {
            "provider": "test-aliases",
            "source_vehicle_id": "us_m2a4_first_tank_div",
            "deprecated_vehicle_id": "us_m2a4_first_tank_div",
            "canonical_vehicle_id": "us_m2a4_1st",
            "confirmed": True,
            "source_reference": "test-confirmed-alias",
        },
    )
    raw_content = canonical_bytes(aliases)
    snapshot = RawDatasetMetadata(
        snapshot_id="identity-test-v1",
        dataset_type=DatasetType.IDENTITY_ALIASES,
        provider="test-aliases",
        retrieved_at=datetime(2026, 9, 19, tzinfo=UTC),
        purpose=fixture.vehicles.snapshot.purpose,
        compatibility_key=fixture.vehicles.snapshot.compatibility_key,
    ).snapshot(raw_content)
    EvidenceRepository(create_database(database)).import_identity_snapshot(
        snapshot, aliases, raw_content=raw_content
    )
    service = AdvisorService.from_database(database)

    preview = service.reconcile_profile("acceptance")
    applied = service.reconcile_profile(
        "acceptance", apply=True, expected_revision=preview.plan.expected_revision
    )
    repeated = service.reconcile_profile("acceptance")

    assert preview.applied is False
    assert preview.plan.items[0].chosen_status is VehicleStatus.OWNED
    assert applied.applied is True
    assert "us_m2a4_first_tank_div" not in service.get_user_progress(
        "acceptance"
    ).vehicle_statuses
    assert repeated.plan.already_applied is True
    assert repeated.plan.items == ()


def test_database_factory_activates_latest_imported_vehicle_snapshot(tmp_path: Path) -> None:
    database = tmp_path / "advisor.sqlite"
    AdvisorService.from_database(database)
    bundle = FixtureProvider().load()
    records = tuple(
        row.model_copy(update={"name": "M3 Lee Updated"})
        if row.vehicle_id == "us_m3_lee"
        else row
        for row in bundle.vehicles.records
    )
    raw_content = canonical_bytes([row.model_dump(mode="json") for row in records])
    snapshot = RawDatasetMetadata(
        snapshot_id="vehicles-newer",
        dataset_type=DatasetType.VEHICLE_METADATA,
        provider="test-refresh",
        retrieved_at=datetime(2026, 9, 18, 23, 59, tzinfo=UTC),
        source_revision="newer",
    ).snapshot(raw_content)
    repository = EvidenceRepository(create_database(database))
    repository.import_vehicle_dataset(
        RawVehicleDataset(snapshot=snapshot, records=records, raw_content=raw_content)
    )

    reopened = AdvisorService.from_database(database)

    assert reopened.get_vehicle("us_m3_lee").vehicle.name == "M3 Lee Updated"
    assert reopened.data_status()["active"]["vehicle_snapshot_id"] == "vehicles-newer"


def _import_newer_operational_vehicles(
    database: Path,
    *,
    records: tuple[RawVehicleRecord, ...] | None = None,
    snapshot_id: str = "vehicles-live",
) -> None:
    bundle = FixtureProvider().load()
    selected = records or tuple(
        row.model_copy(update={"research_parent_id": None})
        for row in bundle.vehicles.records
    )
    raw_content = canonical_bytes([row.model_dump(mode="json") for row in selected])
    snapshot = RawDatasetMetadata(
        snapshot_id=snapshot_id,
        dataset_type=DatasetType.VEHICLE_METADATA,
        provider="war_thunder_vehicles_community_api",
        retrieved_at=datetime(2026, 9, 19, tzinfo=UTC),
        source_revision="live-r1",
    ).snapshot(raw_content)
    EvidenceRepository(create_database(database)).import_vehicle_dataset(
        RawVehicleDataset(snapshot=snapshot, records=selected, raw_content=raw_content)
    )


def test_live_vehicle_snapshot_runs_without_attaching_acceptance_statistics(
    tmp_path: Path,
) -> None:
    database = tmp_path / "advisor.sqlite"
    AdvisorService.from_database(database)
    _import_newer_operational_vehicles(database)

    service = AdvisorService.from_database(database)
    status = service.data_status()
    generation = service.generate_lineups(profile_id="acceptance", top_n=2)
    recommended = generation.recommended

    assert status["newest_stored"]["vehicle_snapshot"]["snapshot_id"] == "vehicles-live"
    assert status["newest_stored"]["statistics_snapshot"]["provider"] == (
        "committed_acceptance_fixture"
    )
    assert status["active"]["vehicle_snapshot_id"] == "vehicles-live"
    assert status["active"]["statistics_snapshot_id"] is None
    assert status["active"]["statistics_status"] == "unavailable"
    assert "statistics_unavailable" in status["active"]["warnings"]
    assert status["active"]["research_graph_available"] is False
    assert recommended is not None
    stats_rule = next(
        rule
        for rule in recommended.analysis.rules
        if rule.rule == "statistical_strength"
    )
    assert stats_rule.score is None
    assert stats_rule.evidence.get("raw") is None
    assert stats_rule.effective_score == 50
    assert "statistics_unavailable" in recommended.analysis.warnings
    assert service.get_vehicle_statistics("us_m3_lee") == ()
    with pytest.raises(ValueError, match=r"no research graph|progression unavailable"):
        service.evaluate_next_unlocks("acceptance")


def test_incompatible_operational_statistics_are_inspectable_but_not_activated(
    tmp_path: Path,
) -> None:
    database = tmp_path / "advisor.sqlite"
    AdvisorService.from_database(database)
    fixture = FixtureProvider().load()
    live_records = tuple(
        row.model_copy(update={"research_parent_id": None})
        for row in fixture.vehicles.records
        if row.vehicle_id != "us_m24"
    )
    _import_newer_operational_vehicles(database, records=live_records)
    observations = (
        VehicleStatistics(
            vehicle_id="us_m24",
            snapshot_id="stats-operational-incompatible",
            mode_scope=StatisticsScope.GROUND_REALISTIC_GROUND_VEHICLES,
            sample_start=date(2026, 9, 1),
            sample_end=date(2026, 9, 18),
            battles=10,
            wins=5,
            losses=5,
            win_rate=0.5,
        ),
    )
    raw_content = canonical_bytes([row.model_dump(mode="json") for row in observations])
    snapshot = RawDatasetMetadata(
        snapshot_id="stats-operational-incompatible",
        dataset_type=DatasetType.GLOBAL_STATISTICS,
        provider="manual_operational_import",
        retrieved_at=datetime(2026, 9, 19, 1, tzinfo=UTC),
        source_revision="stats-r1",
        sample_start=date(2026, 9, 1),
        sample_end=date(2026, 9, 18),
    ).snapshot(raw_content)
    EvidenceRepository(create_database(database)).import_statistics_dataset(
        RawStatisticsDataset(
            snapshot=snapshot,
            records=observations,
            raw_content=raw_content,
        )
    )

    service = AdvisorService.from_database(database)
    status = service.data_status()

    assert status["newest_stored"]["statistics_snapshot"]["snapshot_id"] == (
        "stats-operational-incompatible"
    )
    assert status["compatibility"]["status"] == "incompatible"
    assert "us_m24" in status["compatibility"]["missing_vehicle_ids"]
    assert status["compatibility"]["reason"]
    assert status["active"]["statistics_snapshot_id"] is None


def test_frozen_acceptance_result_remains_unchanged() -> None:
    result = AdvisorService.from_acceptance_fixture().generate_lineups(
        profile_id="acceptance", top_n=10
    )

    assert result.recommended is not None
    assert result.recommended.analysis.lineup.slots == (
        "us_m15_cgmc",
        "us_m22",
        "us_m3_gmc",
        "us_m3_stuart",
        "us_m3a1_stuart",
    )
    assert result.recommended.analysis.lineup_br == 20
    assert result.recommended.analysis.overall_score == pytest.approx(
        73.92332079227364, abs=1e-12
    )


def test_compatible_stale_statistics_remain_active_with_warning(tmp_path: Path) -> None:
    database = tmp_path / "advisor.sqlite"
    AdvisorService.from_database(database)
    fixture = FixtureProvider().load()
    live_records = tuple(
        row.model_copy(update={"research_parent_id": None}) for row in fixture.vehicles.records
    )
    _import_newer_operational_vehicles(database, records=live_records)
    observations = tuple(
        row.model_copy(
            update={
                "snapshot_id": "stats-operational-stale",
                "sample_start": date(2025, 1, 1),
                "sample_end": date(2025, 1, 31),
            }
        )
        for row in fixture.statistics.records
    )
    raw_content = canonical_bytes([row.model_dump(mode="json") for row in observations])
    snapshot = RawDatasetMetadata(
        snapshot_id="stats-operational-stale",
        dataset_type=DatasetType.GLOBAL_STATISTICS,
        provider="manual_operational_import",
        retrieved_at=datetime(2026, 9, 19, 1, tzinfo=UTC),
        source_revision="stats-stale-r1",
        sample_start=date(2025, 1, 1),
        sample_end=date(2025, 1, 31),
    ).snapshot(raw_content)
    EvidenceRepository(create_database(database)).import_statistics_dataset(
        RawStatisticsDataset(
            snapshot=snapshot,
            records=observations,
            raw_content=raw_content,
        )
    )

    service = AdvisorService.from_database(database)
    status = service.data_status()

    assert status["active"]["statistics_snapshot_id"] == "stats-operational-stale"
    assert "statistics_stale" in status["active"]["warnings"]
    analysis = service.analyze_lineup(
        ("us_m15_cgmc", "us_m22", "us_m3_gmc", "us_m3_stuart", "us_m3a1_stuart")
    )
    assert "statistics_stale" in analysis.warnings


def test_partial_live_research_graph_ignores_absent_profile_ids(tmp_path: Path) -> None:
    database = tmp_path / "advisor.sqlite"
    AdvisorService.from_database(database)
    fixture = FixtureProvider().load()
    kept_ids = {"us_m2a4", "us_m3_stuart", "us_m3_lee", "us_m3_gmc", "us_m13_mgmc"}
    records = tuple(row for row in fixture.vehicles.records if row.vehicle_id in kept_ids)
    _import_newer_operational_vehicles(database, records=records, snapshot_id="partial-graph")

    service = AdvisorService.from_database(database)

    assert service.data_status()["active"]["research_graph_available"] is True
    assert service._directly_researchable() == ("us_m3_lee",)
