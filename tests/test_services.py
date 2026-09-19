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
    DatasetType,
    GameMode,
    Nation,
    StatisticsScope,
    VehicleStatistics,
    VehicleStatus,
)
from wt_advisor.services.advisor import AdvisorService
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
