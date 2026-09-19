from __future__ import annotations

from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path

import pytest
from sqlalchemy import Engine, func, inspect, select

from wt_advisor.data.providers.fixture import FixtureProvider
from wt_advisor.domain.models import (
    Capability,
    DatasetType,
    Freshness,
    GameMode,
    Nation,
    SnapshotRef,
    StatisticsScope,
    UserProfile,
    Vehicle,
    VehicleClass,
    VehicleStatus,
)
from wt_advisor.storage.db import create_database
from wt_advisor.storage.models import (
    DataSnapshotRow,
    RawArtifactRow,
    VehicleBattleRatingRow,
    VehicleMetadataRow,
    VehicleStatisticsRow,
)
from wt_advisor.storage.repository import MAX_RAW_ARTIFACT_BYTES, EvidenceRepository


def snapshot(
    snapshot_id: str,
    dataset_type: DatasetType,
    raw_content: bytes,
    *,
    source_revision: str = "r1",
) -> SnapshotRef:
    return SnapshotRef(
        snapshot_id=snapshot_id,
        dataset_type=dataset_type,
        provider="fixture",
        retrieved_at=datetime(2026, 9, 18, tzinfo=UTC),
        source_revision=source_revision,
        checksum=sha256(raw_content).hexdigest(),
        freshness=Freshness.FRESH,
    )


def m3_lee(*, name: str = "M3 Lee") -> Vehicle:
    return Vehicle(
        vehicle_id="us_m3_lee",
        source_vehicle_id="us_m3_lee_source",
        name=name,
        nation=Nation.USA,
        vehicle_class=VehicleClass.MEDIUM_TANK,
        rank=2,
        research_parent_id=None,
        research_cost=14_000,
        purchase_cost=55_000,
        capabilities=frozenset({Capability.ARTILLERY, Capability.SMOKE}),
    )


def test_create_database_runs_baseline_migration(tmp_path: Path) -> None:
    engine = create_database(tmp_path / "advisor.sqlite")

    assert set(inspect(engine).get_table_names()) >= {
        "alembic_version",
        "data_snapshots",
        "raw_artifacts",
        "vehicles",
        "vehicle_source_ids",
        "vehicle_metadata",
        "vehicle_battle_ratings",
        "vehicle_capabilities",
        "research_edges",
        "vehicle_statistics",
        "override_revisions",
        "override_entries",
        "user_profiles",
        "user_vehicle_states",
    }


def test_vehicle_snapshot_import_is_idempotent_and_preserves_raw_artifact(
    database: tuple[Engine, EvidenceRepository],
) -> None:
    engine, repository = database
    metadata_snapshot = snapshot(
        "vehicles-r1", DatasetType.VEHICLE_METADATA, b'{"revision":"r1"}'
    )
    result_one = repository.import_vehicle_snapshot(
        metadata_snapshot,
        [m3_lee()],
        battle_ratings=[
            {
                "vehicle_id": "us_m3_lee",
                "mode": GameMode.GROUND_REALISTIC,
                "battle_rating": 27,
                "source": "fixture",
            }
        ],
        raw_content=b'{"revision":"r1"}',
        media_type="application/json",
    )
    result_two = repository.import_vehicle_snapshot(
        metadata_snapshot,
        [m3_lee()],
        battle_ratings=[
            {
                "vehicle_id": "us_m3_lee",
                "mode": "ground_realistic",
                "battle_rating": 27,
                "source": "fixture",
            }
        ],
        raw_content=b'{"revision":"r1"}',
        media_type="application/json",
    )

    assert result_one.created is True
    assert result_two.created is False
    assert result_one.snapshot_id == result_two.snapshot_id == "vehicles-r1"
    with repository.session() as session:
        assert session.scalar(select(func.count()).select_from(DataSnapshotRow)) == 1
        assert session.scalar(select(func.count()).select_from(RawArtifactRow)) == 1
        assert session.scalar(select(func.count()).select_from(VehicleMetadataRow)) == 1
        assert session.scalar(select(func.count()).select_from(VehicleBattleRatingRow)) == 1
        artifact = session.scalar(select(RawArtifactRow))
        assert artifact is not None
        assert artifact.content == b'{"revision":"r1"}'
        assert artifact.checksum == metadata_snapshot.checksum
    engine.dispose()


def test_repository_rejects_mismatched_or_oversized_raw_artifacts(
    database: tuple[Engine, EvidenceRepository],
) -> None:
    _, repository = database
    valid_snapshot = snapshot("vehicles-r1", DatasetType.VEHICLE_METADATA, b"expected")

    with pytest.raises(ValueError, match="checksum"):
        repository.import_vehicle_snapshot(
            valid_snapshot,
            [m3_lee()],
            raw_content=b"different",
        )
    oversized = b"x" * (MAX_RAW_ARTIFACT_BYTES + 1)
    with pytest.raises(ValueError, match="byte limit"):
        repository.import_vehicle_snapshot(
            snapshot("vehicles-large", DatasetType.VEHICLE_METADATA, oversized),
            [m3_lee()],
            raw_content=oversized,
        )


def test_explicit_snapshot_queries_do_not_mix_versions(
    database: tuple[Engine, EvidenceRepository],
) -> None:
    engine, repository = database
    repository.import_vehicle_snapshot(
        snapshot("vehicles-r1", DatasetType.VEHICLE_METADATA, b"r1"),
        [m3_lee(name="M3 Lee")],
        battle_ratings=[
            {"vehicle_id": "us_m3_lee", "mode": "ground_realistic", "battle_rating": 27}
        ],
        raw_content=b"r1",
    )
    repository.import_vehicle_snapshot(
        snapshot(
            "vehicles-r2",
            DatasetType.VEHICLE_METADATA,
            b"r2",
            source_revision="r2",
        ),
        [m3_lee(name="M3 Medium Tank")],
        battle_ratings=[
            {"vehicle_id": "us_m3_lee", "mode": "ground_realistic", "battle_rating": 30}
        ],
        raw_content=b"r2",
    )

    first = repository.get_vehicle("us_m3_lee", snapshot_id="vehicles-r1")
    second = repository.get_vehicle("us_m3_lee", snapshot_id="vehicles-r2")

    assert first is not None and first.name == "M3 Lee"
    assert second is not None and second.name == "M3 Medium Tank"
    assert repository.resolve_battle_rating(
        "us_m3_lee", GameMode.GROUND_REALISTIC, snapshot_id="vehicles-r1"
    ).value == 27
    assert repository.resolve_battle_rating(
        "us_m3_lee", GameMode.GROUND_REALISTIC, snapshot_id="vehicles-r2"
    ).value == 30
    engine.dispose()


def test_override_resolution_retains_imported_battle_rating_and_provenance(
    database: tuple[Engine, EvidenceRepository],
) -> None:
    engine, repository = database
    repository.import_vehicle_snapshot(
        snapshot("vehicles-r1", DatasetType.VEHICLE_METADATA, b"r1"),
        [m3_lee()],
        battle_ratings=[
            {"vehicle_id": "us_m3_lee", "mode": "ground_realistic", "battle_rating": 27}
        ],
        raw_content=b"r1",
    )
    repository.import_override_revision(
        revision="override-1",
        entries=[
            {
                "vehicle_id": "us_m3_lee",
                "field": "battle_rating.ground_realistic",
                "value": 30,
                "reason": "Official BR update",
                "reference": "https://example.invalid/br-update",
                "added_at": date(2026, 9, 18),
            }
        ],
    )

    resolved = repository.resolve_battle_rating(
        "us_m3_lee",
        GameMode.GROUND_REALISTIC,
        snapshot_id="vehicles-r1",
        override_revision="override-1",
    )

    assert resolved.value == 30
    assert resolved.imported_value == 27
    assert resolved.source == "override"
    assert resolved.snapshot_id == "vehicles-r1"
    assert resolved.override_revision == "override-1"
    with repository.session() as session:
        stored = session.scalar(select(VehicleBattleRatingRow))
        assert stored is not None and stored.battle_rating == 27
    engine.dispose()


def test_statistics_import_preserves_nulls_and_scope(
    database: tuple[Engine, EvidenceRepository],
) -> None:
    engine, repository = database
    repository.import_vehicle_snapshot(
        snapshot("vehicles-r1", DatasetType.VEHICLE_METADATA, b"vehicles"),
        [m3_lee()],
        raw_content=b"vehicles",
    )
    stats_snapshot = snapshot("stats-r1", DatasetType.GLOBAL_STATISTICS, b"stats")

    repository.import_statistics_snapshot(
        stats_snapshot,
        [
            {
                "vehicle_id": "us_m3_lee",
                "mode_scope": StatisticsScope.GROUND_REALISTIC_GROUND_VEHICLES,
                "sample_start": date(2026, 8, 1),
                "sample_end": date(2026, 8, 31),
                "battles": 1200,
                "wins": None,
                "win_rate": 0.53,
                "kills": 2100,
                "deaths": 1000,
            }
        ],
        raw_content=b"stats",
    )

    statistics = repository.get_vehicle_statistics("us_m3_lee", snapshot_id="stats-r1")

    assert len(statistics) == 1
    assert statistics[0].mode_scope is StatisticsScope.GROUND_REALISTIC_GROUND_VEHICLES
    assert statistics[0].wins is None
    with repository.session() as session:
        row = session.scalar(select(VehicleStatisticsRow))
        assert row is not None and row.wins is None
    engine.dispose()


def test_profile_status_updates_increment_revision_but_idempotent_write_does_not(
    database: tuple[Engine, EvidenceRepository],
) -> None:
    engine, repository = database
    repository.import_vehicle_snapshot(
        snapshot("vehicles-r1", DatasetType.VEHICLE_METADATA, b"vehicles"),
        [m3_lee()],
        raw_content=b"vehicles",
    )
    repository.create_profile(UserProfile(profile_id="default"))

    first = repository.set_vehicle_status("default", "us_m3_lee", VehicleStatus.RESEARCHING)
    repeated = repository.set_vehicle_status(
        "default", "us_m3_lee", VehicleStatus.RESEARCHING
    )
    second = repository.set_vehicle_status("default", "us_m3_lee", VehicleStatus.OWNED)

    assert first.before is None
    assert first.after is VehicleStatus.RESEARCHING
    assert first.profile_revision == 1
    assert repeated.changed is False
    assert repeated.profile_revision == 1
    assert second.before is VehicleStatus.RESEARCHING
    assert second.after is VehicleStatus.OWNED
    assert second.profile_revision == 2
    stored_profile = repository.get_profile("default")
    assert stored_profile is not None and stored_profile.revision == 2
    assert repository.get_user_vehicle_states("default") == {
        "us_m3_lee": VehicleStatus.OWNED
    }
    engine.dispose()


def test_create_profile_is_idempotent_for_same_definition_and_rejects_drift(
    database: tuple[Engine, EvidenceRepository],
) -> None:
    engine, repository = database
    profile = UserProfile(profile_id="default", crew_slots=5)

    assert repository.create_profile(profile).created is True
    assert repository.create_profile(profile).created is False

    different = profile.model_copy(update={"crew_slots": 4})
    with pytest.raises(ValueError, match="already exists"):
        repository.create_profile(different)
    engine.dispose()


def test_provider_datasets_can_be_imported_without_provider_storage_coupling(
    database: tuple[Engine, EvidenceRepository],
) -> None:
    engine, repository = database
    bundle = FixtureProvider().load()

    vehicle_import = repository.import_vehicle_dataset(bundle.vehicles)
    statistics_import = repository.import_statistics_dataset(bundle.statistics)

    assert vehicle_import.snapshot_id == bundle.vehicles.snapshot.snapshot_id
    assert statistics_import.snapshot_id == bundle.statistics.snapshot.snapshot_id
    imported = repository.get_vehicle("us_m3_lee", snapshot_id=vehicle_import.snapshot_id)
    assert imported is not None and imported.name == "M3 Lee"
    resolved = repository.resolve_battle_rating(
        "us_m3_lee",
        GameMode.GROUND_REALISTIC,
        snapshot_id=vehicle_import.snapshot_id,
    )
    assert resolved.value == bundle.battle_ratings["us_m3_lee"]
    assert repository.get_vehicle_statistics(
        "us_m3_lee", snapshot_id=statistics_import.snapshot_id
    )
    engine.dispose()


@pytest.fixture
def database(tmp_path: Path) -> tuple[Engine, EvidenceRepository]:
    engine = create_database(tmp_path / "advisor.sqlite")
    return engine, EvidenceRepository(engine)
