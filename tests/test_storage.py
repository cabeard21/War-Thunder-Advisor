from __future__ import annotations

from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import Engine, func, inspect, select

from wt_advisor.data.models import RawCapabilityDataset
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
from wt_advisor.storage.db import (
    _alembic_config,
    _upgrade_packaged_0005_to_0006,
    _upgrade_packaged_0006_to_0007,
    build_engine,
    create_database,
)
from wt_advisor.storage.models import (
    CapabilityObservationRow,
    DataSnapshotRow,
    ImportAttemptRow,
    ProfileReconciliationAuditRow,
    RawArtifactRow,
    UserVehicleStateRow,
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


def test_operational_bundle_rolls_back_when_component_is_invalid(tmp_path: Path) -> None:
    engine = create_database(tmp_path / "atomic.sqlite")
    repository = EvidenceRepository(engine)
    fixture = FixtureProvider().load()
    bad_capabilities = RawCapabilityDataset(
        snapshot=snapshot("bad-capabilities", DatasetType.CAPABILITIES, b"[]"),
        records=(),
        raw_content=b"wrong checksum",
    )

    with pytest.raises(ValueError, match="checksum"):
        repository.import_operational_bundle(
            vehicles=fixture.vehicles, capabilities=bad_capabilities
        )

    with repository.session() as session:
        assert session.scalar(select(func.count()).select_from(DataSnapshotRow)) == 0


def test_raw_artifact_content_reads_retained_snapshot_bytes(tmp_path: Path) -> None:
    repository = EvidenceRepository(create_database(tmp_path / "artifact.sqlite"))
    fixture = FixtureProvider().load()
    repository.import_vehicle_dataset(fixture.vehicles)

    assert repository.raw_artifact_content(fixture.vehicles.snapshot.snapshot_id) == (
        fixture.vehicles.raw_content
    )
    assert repository.raw_artifact_content("missing") is None


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
        "capability_observations",
        "availability_observations",
        "vehicle_identity_aliases",
        "research_graph_edges",
        "import_attempts",
        "profile_reconciliation_audits",
        "profile_reconciliation_items",
        "statistics_import_diagnostics",
    }


def test_milestone_one_0002_database_upgrades_without_losing_rows(tmp_path: Path) -> None:
    engine = build_engine(tmp_path / "upgrade.sqlite")
    config = _alembic_config(engine)
    command.upgrade(config, "0002")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO user_profiles "
            "(profile_id, nation, preferred_mode, crew_slots, include_premiums, "
            "include_event_vehicles, include_pack_vehicles, revision) "
            "VALUES ('kept', 'usa', 'ground_realistic', 5, 0, 0, 0, 0)"
        )
    assert "capability_observations" not in inspect(engine).get_table_names()

    command.upgrade(config, "head")

    assert "capability_observations" in inspect(engine).get_table_names()
    with engine.connect() as connection:
        assert (
            connection.exec_driver_sql("SELECT profile_id FROM user_profiles").scalar_one()
            == "kept"
        )
    engine.dispose()


def test_vehicle_snapshot_import_is_idempotent_and_preserves_raw_artifact(
    database: tuple[Engine, EvidenceRepository],
) -> None:
    engine, repository = database
    metadata_snapshot = snapshot("vehicles-r1", DatasetType.VEHICLE_METADATA, b'{"revision":"r1"}')
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
    assert (
        repository.resolve_battle_rating(
            "us_m3_lee", GameMode.GROUND_REALISTIC, snapshot_id="vehicles-r1"
        ).value
        == 27
    )
    assert (
        repository.resolve_battle_rating(
            "us_m3_lee", GameMode.GROUND_REALISTIC, snapshot_id="vehicles-r2"
        ).value
        == 30
    )
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


def test_capability_observations_preserve_negative_unknown_and_conflict(
    database: tuple[Engine, EvidenceRepository],
) -> None:
    engine, repository = database
    repository.import_vehicle_snapshot(
        snapshot("vehicles-r1", DatasetType.VEHICLE_METADATA, b"vehicles"),
        [m3_lee()],
        raw_content=b"vehicles",
    )
    capability_snapshot = snapshot("capabilities-r1", DatasetType.CAPABILITIES, b"capabilities")

    repository.import_capability_snapshot(
        capability_snapshot,
        [
            {
                "vehicle_id": "us_m3_lee",
                "capability": "scouting",
                "value": False,
                "source_reference": "manual:one",
                "confidence": 0.9,
            },
            {
                "vehicle_id": "us_m3_lee",
                "capability": "scouting",
                "value": True,
                "source_reference": "manual:two",
                "confidence": 0.8,
            },
        ],
        raw_content=b"capabilities",
    )

    resolution = repository.resolve_capabilities("us_m3_lee", snapshot_id="capabilities-r1")

    assert resolution[Capability.SCOUTING].state == "conflicted"
    assert Capability.SMOKE not in resolution
    with repository.session() as session:
        observations = tuple(session.scalars(select(CapabilityObservationRow)))
        assert {item.value for item in observations} == {True, False}
    engine.dispose()


def test_capability_import_rejects_duplicate_claims_and_rolls_back(
    database: tuple[Engine, EvidenceRepository],
) -> None:
    engine, repository = database
    repository.import_vehicle_snapshot(
        snapshot("vehicles-r1", DatasetType.VEHICLE_METADATA, b"vehicles"),
        [m3_lee()],
        raw_content=b"vehicles",
    )
    item = {
        "vehicle_id": "us_m3_lee",
        "capability": "scouting",
        "value": True,
        "source_reference": "reference",
    }
    with pytest.raises(ValueError, match="duplicate capability observation"):
        repository.import_capability_snapshot(
            snapshot("capabilities-duplicate", DatasetType.CAPABILITIES, b"duplicate"),
            [item, item],
            raw_content=b"duplicate",
        )
    assert repository.get_snapshot("capabilities-duplicate") is None
    engine.dispose()


def test_capability_import_rejects_non_boolean_and_unknown_vehicle(
    database: tuple[Engine, EvidenceRepository],
) -> None:
    engine, repository = database
    repository.import_vehicle_snapshot(
        snapshot("vehicles-r1", DatasetType.VEHICLE_METADATA, b"vehicles"),
        [m3_lee()],
        raw_content=b"vehicles",
    )
    for vehicle_id, value, expected in (
        ("us_m3_lee", "false", "boolean"),
        ("unknown_vehicle", False, "unknown vehicle"),
    ):
        with pytest.raises(ValueError, match=expected):
            repository.import_capability_snapshot(
                snapshot(f"bad-{vehicle_id}", DatasetType.CAPABILITIES, b"bad"),
                [{
                    "vehicle_id": vehicle_id,
                    "capability": "scouting",
                    "value": value,
                    "source_reference": "reference",
                }],
                raw_content=b"bad",
            )
    engine.dispose()


def test_capability_revisions_keep_distinct_claims_and_supersede_same_claim(
    database: tuple[Engine, EvidenceRepository],
) -> None:
    engine, repository = database
    vehicle_snapshot = snapshot("vehicles-r1", DatasetType.VEHICLE_METADATA, b"vehicles")
    repository.import_vehicle_snapshot(vehicle_snapshot, [m3_lee()], raw_content=b"vehicles")
    revisions = (
        ("api-r0", b"api0", True, "api:scouting", "community_api"),
        ("api-r1", b"api1", False, "api:scouting", "community_api"),
        ("curated-r1", b"curated1", False, "manual:scouting", "curated_import"),
        ("curated-r2", b"curated2", True, "manual:scouting", "curated_import"),
    )
    for snapshot_id, content, value, reference, source_type in revisions:
        repository.import_capability_snapshot(
            snapshot(snapshot_id, DatasetType.CAPABILITIES, content),
            [{
                "vehicle_id": "us_m3_lee",
                "capability": "scouting",
                "value": value,
                "source_provider": "curated" if source_type == "curated_import" else "api",
                "source_type": source_type,
                "source_reference": reference,
                "verified_at": "2026-09-18",
                "source_revision": snapshot_id,
            }],
            raw_content=content,
        )
    selected = repository.list_compatible_capability_snapshots(vehicle_snapshot)
    assert "api-r0" not in {item.snapshot_id for item in selected}
    resolution = repository.resolve_capabilities(
        "us_m3_lee", snapshot_ids=tuple(item.snapshot_id for item in selected)
    )[Capability.SCOUTING]
    assert resolution.state == "conflicted"
    assert {item.source_snapshot_id for item in resolution.observations} == {
        "api-r1", "curated-r2"
    }
    assert (
        next(item for item in resolution.observations if item.value).source_revision
        == "curated-r2"
    )
    engine.dispose()


def test_capability_resolution_uses_verification_date_and_separates_source_types(
    database: tuple[Engine, EvidenceRepository],
) -> None:
    engine, repository = database
    vehicle_snapshot = snapshot("vehicles-r1", DatasetType.VEHICLE_METADATA, b"vehicles")
    repository.import_vehicle_snapshot(vehicle_snapshot, [m3_lee()], raw_content=b"vehicles")
    claims = (
        ("api", True, "community_api", None, 18),
        ("curated-new", False, "curated_import", "2026-09-20", 19),
        ("curated-old-late", True, "curated_import", "2025-09-20", 21),
    )
    for snapshot_id, value, source_type, verified_at, day in claims:
        evidence = snapshot(
            snapshot_id, DatasetType.CAPABILITIES, snapshot_id.encode()
        ).model_copy(update={"retrieved_at": datetime(2026, 9, day, tzinfo=UTC)})
        repository.import_capability_snapshot(
            evidence,
            [{
                "vehicle_id": "us_m3_lee", "capability": "scouting", "value": value,
                "source_provider": "shared-provider", "source_reference": "shared:reference",
                "source_type": source_type, "verified_at": verified_at,
                "source_revision": snapshot_id,
            }],
            raw_content=snapshot_id.encode(),
        )
    selected = repository.list_compatible_capability_snapshots(vehicle_snapshot)
    resolved = repository.resolve_capabilities(
        "us_m3_lee", snapshot_ids=tuple(item.snapshot_id for item in selected)
    )[Capability.SCOUTING]

    assert resolved.state == "conflicted"
    assert {row.source_snapshot_id for row in resolved.observations} == {
        "api", "curated-new",
    }
    engine.dispose()


def test_independent_evidence_imports_validate_identity_and_preserve_graph_semantics(
    database: tuple[Engine, EvidenceRepository],
) -> None:
    engine, repository = database
    parent = m3_lee()
    child = parent.model_copy(
        update={
            "vehicle_id": "us_m4a1",
            "source_vehicle_id": "us_m4a1_source",
            "name": "M4A1",
        }
    )
    repository.import_vehicle_snapshot(
        snapshot("vehicles-r1", DatasetType.VEHICLE_METADATA, b"vehicles"),
        [parent, child],
        raw_content=b"vehicles",
    )
    repository.import_availability_snapshot(
        snapshot("availability-r1", DatasetType.AVAILABILITY, b"availability"),
        [
            {
                "vehicle_id": "us_m4a1",
                "acquisition_type": "research",
                "researchability": "normally_researchable",
                "tree_membership": "main_tree",
                "visibility": "visible",
                "source_reference": "manual:tree",
                "confidence": 1.0,
            }
        ],
        raw_content=b"availability",
    )
    repository.import_research_graph_snapshot(
        snapshot("graph-r1", DatasetType.RESEARCH_GRAPH, b"graph"),
        [
            {
                "nation": "usa",
                "domain": "ground",
                "parent_vehicle_id": "us_m3_lee",
                "child_vehicle_id": "us_m4a1",
                "edge_type": "branch_unlock",
                "prerequisite_group": "main",
                "group_semantics": "all",
            }
        ],
        raw_content=b"graph",
    )

    availability = repository.get_resolved_availability("us_m4a1", snapshot_id="availability-r1")
    edges = repository.list_research_graph_edges(snapshot_id="graph-r1")

    assert availability is not None
    assert availability.researchability == "normally_researchable"
    assert edges[0].edge_type == "branch_unlock"
    assert edges[0].prerequisite_semantics == "all"
    with pytest.raises(ValueError, match="unknown vehicle"):
        repository.import_research_graph_snapshot(
            snapshot("graph-bad", DatasetType.RESEARCH_GRAPH, b"bad"),
            [
                {
                    "nation": "usa",
                    "domain": "ground",
                    "parent_vehicle_id": "missing",
                    "child_vehicle_id": "us_m4a1",
                    "edge_type": "normal",
                    "prerequisite_group": "main",
                    "group_semantics": "all",
                }
            ],
            raw_content=b"bad",
        )
    engine.dispose()


def test_invalid_import_attempt_is_retained_without_activating_snapshot(
    database: tuple[Engine, EvidenceRepository],
) -> None:
    engine, repository = database

    result = repository.record_import_attempt(
        dataset_type=DatasetType.CAPABILITIES,
        provider="community",
        raw_content=b'{"vehicles":[]}',
        status="rejected",
        error="empty vehicle collection",
        attempted_at=datetime(2026, 9, 18, tzinfo=UTC),
    )

    assert result.created is True
    assert repository.list_snapshots(DatasetType.CAPABILITIES) == ()
    with repository.session() as session:
        attempt = session.get(ImportAttemptRow, result.attempt_id)
        assert attempt is not None
        assert attempt.raw_checksum == sha256(b'{"vehicles":[]}').hexdigest()
        assert attempt.error == "empty vehicle collection"
    engine.dispose()


def test_confirmed_alias_reconciliation_is_dry_run_transactional_and_idempotent(
    database: tuple[Engine, EvidenceRepository],
) -> None:
    engine, repository = database
    canonical = m3_lee()
    deprecated = canonical.model_copy(
        update={
            "vehicle_id": "us_m3_lee_old",
            "source_vehicle_id": "us_m3_lee_old_source",
            "name": "M3 Lee Legacy",
        }
    )
    repository.import_vehicle_snapshot(
        snapshot("vehicles-r1", DatasetType.VEHICLE_METADATA, b"vehicles"),
        [canonical, deprecated],
        raw_content=b"vehicles",
    )
    repository.import_identity_snapshot(
        snapshot("identity-r1", DatasetType.IDENTITY_ALIASES, b"identity"),
        [
            {
                "provider": "fixture",
                "source_vehicle_id": "us_m3_lee_old",
                "canonical_vehicle_id": "us_m3_lee",
                "deprecated_vehicle_id": "us_m3_lee_old",
                "confirmed": True,
                "source_reference": "manual:alias",
            }
        ],
        raw_content=b"identity",
    )
    repository.create_profile(UserProfile(profile_id="default"))
    repository.set_vehicle_status("default", "us_m3_lee", VehicleStatus.LOCKED)
    repository.set_vehicle_status("default", "us_m3_lee_old", VehicleStatus.OWNED)
    revision = repository.get_profile("default").revision  # type: ignore[union-attr]

    preview = repository.reconcile_profile_aliases(
        "default", identity_snapshot_id="identity-r1", expected_revision=revision, apply=False
    )
    assert preview.applied is False
    assert preview.items[0].chosen_status is VehicleStatus.OWNED
    assert repository.get_user_vehicle_states("default")["us_m3_lee"] is VehicleStatus.LOCKED

    applied = repository.reconcile_profile_aliases(
        "default", identity_snapshot_id="identity-r1", expected_revision=revision, apply=True
    )
    assert applied.applied is True
    assert repository.get_user_vehicle_states("default") == {"us_m3_lee": VehicleStatus.OWNED}
    repeated = repository.reconcile_profile_aliases(
        "default",
        identity_snapshot_id="identity-r1",
        expected_revision=applied.resulting_revision,
        apply=True,
    )
    assert repeated.changed is False
    with repository.session() as session:
        audits = tuple(session.scalars(select(ProfileReconciliationAuditRow)))
        old_state = session.get(UserVehicleStateRow, ("default", "us_m3_lee_old"))
        assert len(audits) == 1
        assert old_state is not None and old_state.superseded is True
    engine.dispose()


def test_statistics_store_reported_ratios_and_import_diagnostics(
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
                "mode_scope": "ground_realistic_ground_vehicles",
                "battles": 10,
                "wins": 5,
                "kills": 15,
                "deaths": 10,
                "reported_win_rate": 0.5,
                "reported_kd": 1.5,
                "reported_kills_per_battle": 1.5,
                "ratio_provenance": "provider_reported",
                "kill_target_definition": "ground_targets",
            }
        ],
        raw_content=b"stats",
        diagnostics={
            "recognized_columns": ["vehicle_id", "wins"],
            "unknown_columns": [],
            "matched_count": 1,
            "unresolved_source_ids": [],
        },
    )

    stored = repository.get_vehicle_statistics("us_m3_lee", snapshot_id="stats-r1")[0]
    diagnostics = repository.get_statistics_import_diagnostics("stats-r1")

    assert stored.reported_kd == 1.5
    assert stored.kill_target_definition.value == "ground_targets"
    assert diagnostics is not None and diagnostics.matched_count == 1
    engine.dispose()


def test_statistics_normalization_revision_creates_distinct_idempotent_snapshot(
    database: tuple[Engine, EvidenceRepository],
) -> None:
    engine, repository = database
    repository.import_vehicle_snapshot(
        snapshot("vehicles-r1", DatasetType.VEHICLE_METADATA, b"vehicles"),
        [m3_lee()],
        raw_content=b"vehicles",
    )
    old = snapshot("stats-old", DatasetType.GLOBAL_STATISTICS, b"same-source")
    revised = snapshot("stats-v2", DatasetType.GLOBAL_STATISTICS, b"same-source")
    records = [{"vehicle_id": "us_m3_lee", "mode_scope": "ground_realistic_ground_vehicles"}]

    assert repository.import_statistics_snapshot(old, records, raw_content=b"same-source").created
    first = repository.import_statistics_snapshot(
        revised, records, raw_content=b"same-source", provider_version="ground-targets-v2"
    )
    repeated = repository.import_statistics_snapshot(
        revised, records, raw_content=b"same-source", provider_version="ground-targets-v2"
    )

    assert first.created is True
    assert first.snapshot_id == "stats-v2"
    assert repeated.created is False
    assert repeated.snapshot_id == "stats-v2"
    with repository.session() as session:
        rows = tuple(
            session.scalars(
                select(DataSnapshotRow).where(
                    DataSnapshotRow.dataset_type == DatasetType.GLOBAL_STATISTICS.value
                )
            )
        )
        assert len(rows) == 2
        assert {row.provider_version for row in rows} == {None, "ground-targets-v2"}
        assert {row.source_revision for row in rows} == {"r1"}
        assert len({row.checksum for row in rows}) == 1
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
    repeated = repository.set_vehicle_status("default", "us_m3_lee", VehicleStatus.RESEARCHING)
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
    assert repository.get_user_vehicle_states("default") == {"us_m3_lee": VehicleStatus.OWNED}
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
    assert repository.get_vehicle_statistics("us_m3_lee", snapshot_id=statistics_import.snapshot_id)
    engine.dispose()


@pytest.fixture
def database(tmp_path: Path) -> tuple[Engine, EvidenceRepository]:
    engine = create_database(tmp_path / "advisor.sqlite")
    return engine, EvidenceRepository(engine)


def test_populated_0004_upgrades_to_0007_without_losing_profile(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy.sqlite"
    engine = build_engine(database_path)
    command.upgrade(_alembic_config(engine), "0004")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO user_profiles "
            "(profile_id, nation, preferred_mode, crew_slots, include_premiums, "
            "include_event_vehicles, include_pack_vehicles, revision) "
            "VALUES ('legacy', 'usa', 'ground_realistic', 5, 0, 0, 0, 0)"
        )

    command.upgrade(_alembic_config(engine), "head")

    repository = EvidenceRepository(engine)
    assert repository.get_profile("legacy") is not None
    assert inspect(engine).has_table("stored_evaluations")
    with engine.connect() as connection:
        revision = connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()
        assert revision == "0007"
    engine.dispose()


def test_packaged_0005_upgrade_preserves_observations_and_reaches_0006(
    tmp_path: Path,
) -> None:
    engine = build_engine(tmp_path / "packaged.sqlite")
    command.upgrade(_alembic_config(engine), "0005")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO data_snapshots "
            "(snapshot_id, dataset_type, provider, retrieved_at, checksum, freshness, purpose) "
            "VALUES ('caps-old', 'capabilities', 'test', '2026-09-01', "
            "'checksum', 'fresh', 'operational')"
        )
        connection.exec_driver_sql("INSERT INTO vehicles (vehicle_id) VALUES ('us_m3_lee')")
        connection.exec_driver_sql(
            "INSERT INTO capability_observations "
            "(snapshot_id, vehicle_id, capability, value, source_provider, source_type, "
            "source_reference, confidence) VALUES "
            "('caps-old', 'us_m3_lee', 'scouting', 1, 'test', 'curated_import', 'ref', 1.0)"
        )
        _upgrade_packaged_0005_to_0006(connection)
        assert (
            connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar()
            == "0006"
        )
        assert connection.exec_driver_sql(
            "SELECT value, verified_at, source_revision FROM capability_observations"
        ).one() == (1, None, None)
    engine.dispose()


def test_packaged_0006_upgrade_preserves_existing_statistics_definition(
    tmp_path: Path,
) -> None:
    engine = build_engine(tmp_path / "packaged-metrics.sqlite")
    command.upgrade(_alembic_config(engine), "0006")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO data_snapshots "
            "(snapshot_id, dataset_type, provider, retrieved_at, source_revision, checksum, "
            "freshness, purpose) VALUES "
            "('stats-old', 'global_statistics', 'fixture', '2026-09-01', 'r1', "
            "'checksum', 'fresh', 'operational')"
        )
        connection.exec_driver_sql("INSERT INTO vehicles (vehicle_id) VALUES ('us_m3_lee')")
        connection.exec_driver_sql(
            "INSERT INTO vehicle_statistics (snapshot_id, vehicle_id, mode_scope, reported_kd) "
            "VALUES ('stats-old', 'us_m3_lee', 'ground_realistic_ground_vehicles', 1.5)"
        )
    with engine.begin() as connection:
        _upgrade_packaged_0006_to_0007(connection)
        assert connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar() == "0007"
        assert connection.exec_driver_sql(
            "SELECT kill_target_definition, reported_kd FROM vehicle_statistics"
        ).one() == ("all_targets", 1.5)
        assert connection.exec_driver_sql(
            "SELECT checksum, source_revision, provider_version FROM data_snapshots"
        ).one() == ("checksum", "r1", None)
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
    engine.dispose()


def test_alembic_0006_upgrade_preserves_populated_snapshot_references(
    tmp_path: Path,
) -> None:
    engine = build_engine(tmp_path / "alembic-metrics.sqlite")
    command.upgrade(_alembic_config(engine), "0006")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO data_snapshots "
            "(snapshot_id, dataset_type, provider, retrieved_at, source_revision, checksum, "
            "freshness, purpose) VALUES "
            "('stats-old', 'global_statistics', 'fixture', '2026-09-01', 'r1', "
            "'checksum', 'fresh', 'operational')"
        )
        connection.exec_driver_sql("INSERT INTO vehicles (vehicle_id) VALUES ('us_m3_lee')")
        connection.exec_driver_sql(
            "INSERT INTO vehicle_statistics (snapshot_id, vehicle_id, mode_scope, reported_kd) "
            "VALUES ('stats-old', 'us_m3_lee', 'ground_realistic_ground_vehicles', 1.5)"
        )
    command.upgrade(_alembic_config(engine), "head")
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        assert connection.exec_driver_sql(
            "SELECT kill_target_definition FROM vehicle_statistics"
        ).scalar() == "all_targets"
    engine.dispose()
