from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy.engine import URL

import wt_advisor.data.imports.availability as availability_import
import wt_advisor.data.imports.capabilities as capability_import
import wt_advisor.data.imports.research_graph as graph_import
from wt_advisor.data.models import (
    RawAvailabilityDataset,
    RawCapabilityDataset,
    RawDatasetMetadata,
    RawResearchGraphDataset,
    RawVehicleDataset,
)
from wt_advisor.data.providers.fixture import FixtureProvider
from wt_advisor.domain.models import (
    AcquisitionType,
    Capability,
    CapabilityObservation,
    CapabilityResolution,
    CapabilitySourceType,
    ComponentDataStatus,
    ComponentStatus,
    DatasetType,
    Freshness,
    Nation,
    PrerequisiteSemantics,
    RatioProvenance,
    Researchability,
    ResearchDomain,
    ResearchEdge,
    ResearchEdgeType,
    ResolvedAvailability,
    SnapshotPurpose,
    StatisticsScope,
    TreeMembership,
    VehicleStatistics,
    Visibility,
    br_step_distance,
)
from wt_advisor.services.advisor import AdvisorService, _read_bounded_file
from wt_advisor.storage.db import build_engine


def _metadata(dataset_type: DatasetType) -> RawDatasetMetadata:
    return RawDatasetMetadata(
        snapshot_id=f"test-{dataset_type.value}",
        dataset_type=dataset_type,
        provider="test",
        retrieved_at=datetime(2026, 9, 19, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    ("module", "function_name", "limit_name", "dataset_type", "invalid"),
    [
        (
            capability_import,
            "import_capabilities_json",
            "MAX_CAPABILITY_IMPORT_BYTES",
            DatasetType.CAPABILITIES,
            "not-json",
        ),
        (
            availability_import,
            "import_availability_yaml",
            "MAX_AVAILABILITY_IMPORT_BYTES",
            DatasetType.AVAILABILITY,
            "[",
        ),
        (
            graph_import,
            "import_research_graph_json",
            "MAX_RESEARCH_GRAPH_IMPORT_BYTES",
            DatasetType.RESEARCH_GRAPH,
            "not-json",
        ),
    ],
)
def test_component_importers_fail_closed_at_each_boundary(
    monkeypatch: pytest.MonkeyPatch,
    module: object,
    function_name: str,
    limit_name: str,
    dataset_type: DatasetType,
    invalid: str,
) -> None:
    function = getattr(module, function_name)
    metadata = _metadata(dataset_type)
    monkeypatch.setattr(module, limit_name, 1)
    with pytest.raises(ValueError, match="byte limit"):
        function("[]", metadata)
    monkeypatch.setattr(module, limit_name, 2_000_000)
    with pytest.raises(ValueError, match="dataset type"):
        function("[]", _metadata(DatasetType.VEHICLE_METADATA))
    with pytest.raises(ValueError, match="invalid"):
        function(invalid, metadata)
    with pytest.raises(ValueError, match="array of objects"):
        function("{}", metadata)
    with pytest.raises(ValueError, match="invalid"):
        function("[{}]", metadata)


def test_domain_validators_cover_conflicts_and_invalid_ranges() -> None:
    with pytest.raises(ValueError, match="greater"):
        br_step_distance(10, 13)

    observation = CapabilityObservation(
        vehicle_id="us_m24",
        capability=Capability.SCOUTING,
        value=True,
        source_provider="test",
        source_snapshot_id="snapshot",
        source_reference="test:m24",
        source_type=CapabilitySourceType.CURATED_IMPORT,
    )
    with pytest.raises(ValueError, match="match"):
        CapabilityResolution.from_observations(
            "us_other", Capability.SCOUTING, (observation,)
        )
    with pytest.raises(ValidationError, match="itself"):
        ResearchEdge(
            nation=Nation.USA,
            domain=ResearchDomain.GROUND,
            parent_vehicle_id="us_m24",
            child_vehicle_id="us_m24",
            edge_type=ResearchEdgeType.NORMAL,
            prerequisite_group="g",
            prerequisite_semantics=PrerequisiteSemantics.ALL,
            snapshot_id="snapshot",
        )
    with pytest.raises(ValidationError, match="covered_count"):
        ComponentDataStatus(
            dataset_type=DatasetType.CAPABILITIES,
            status=ComponentStatus.AVAILABLE,
            purpose=SnapshotPurpose.OPERATIONAL,
            freshness=Freshness.FRESH,
            compatible=True,
            total_count=1,
            covered_count=2,
            coverage_percent=100,
        )
    with pytest.raises(ValidationError, match="coverage_percent"):
        ComponentDataStatus(
            dataset_type=DatasetType.CAPABILITIES,
            status=ComponentStatus.AVAILABLE,
            purpose=SnapshotPurpose.OPERATIONAL,
            freshness=Freshness.FRESH,
            compatible=True,
            total_count=2,
            covered_count=1,
            coverage_percent=10,
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"sample_start": date(2026, 2, 1), "sample_end": date(2026, 1, 1)},
        {"battles": 2, "wins": 3},
        {"battles": 2, "losses": 3},
        {"battles": 2, "wins": 2, "losses": 1},
        {"battles": 10, "wins": 5, "win_rate": 0.9},
        {"battles": 10, "wins": 5, "reported_win_rate": 0.9},
        {"battles": 10, "kills": 5, "reported_kills_per_battle": 0.9},
        {"kills": 5, "deaths": 5, "reported_kd": 2.0},
    ],
)
def test_statistics_reject_internally_inconsistent_observations(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        VehicleStatistics(
            vehicle_id="us_m24",
            snapshot_id="stats",
            mode_scope=StatisticsScope.GROUND_REALISTIC_GROUND_VEHICLES,
            **overrides,
        )


def test_statistics_ratio_provenance_prefers_counts_then_reported_values() -> None:
    counted = VehicleStatistics(
        vehicle_id="us_m24",
        snapshot_id="stats",
        mode_scope=StatisticsScope.GROUND_REALISTIC_GROUND_VEHICLES,
        battles=10,
        wins=5,
        kills=20,
        deaths=10,
    )
    assert counted.ratio_source("win_rate") is RatioProvenance.DERIVED_FROM_COUNTS
    assert counted.ratio_source("kd") is RatioProvenance.DERIVED_FROM_COUNTS
    assert counted.ratio_source("kills_per_battle") is RatioProvenance.DERIVED_FROM_COUNTS

    reported = VehicleStatistics(
        vehicle_id="us_m24",
        snapshot_id="stats",
        mode_scope=StatisticsScope.UNKNOWN_REALISTIC_SCOPE,
        reported_win_rate=0.5,
        reported_kd=1.2,
        reported_kills_per_battle=0.8,
    )
    assert reported.ratio_source("win_rate") is RatioProvenance.REPORTED
    assert reported.ratio_source("kd") is RatioProvenance.REPORTED
    assert reported.ratio_source("kills_per_battle") is RatioProvenance.REPORTED
    assert reported.derived_win_rate is None
    assert reported.derived_kd is None
    assert reported.derived_kills_per_battle is None
    with pytest.raises(ValueError, match="unsupported"):
        reported.ratio_source("bogus")


def test_component_datasets_reject_duplicate_natural_keys() -> None:
    fixture = FixtureProvider().load()
    with pytest.raises(ValidationError, match="duplicate vehicle"):
        RawVehicleDataset(
            snapshot=fixture.vehicles.snapshot,
            records=(*fixture.vehicles.records, fixture.vehicles.records[0]),
            raw_content=fixture.vehicles.raw_content,
        )

    observation = CapabilityObservation(
        vehicle_id="us_m24",
        capability=Capability.SCOUTING,
        value=True,
        source_provider="test",
        source_snapshot_id="capabilities",
        source_reference="test:m24",
        source_type=CapabilitySourceType.CURATED_IMPORT,
    )
    with pytest.raises(ValidationError, match="duplicate observations"):
        RawCapabilityDataset(
            snapshot=_metadata(DatasetType.CAPABILITIES).snapshot(b"[]"),
            records=(observation, observation),
            raw_content=b"[]",
        )

    availability = ResolvedAvailability(
        vehicle_id="us_m24",
        acquisition_type=AcquisitionType.RESEARCH,
        researchability=Researchability.NORMALLY_RESEARCHABLE,
        tree_membership=TreeMembership.MAIN_TREE,
        visibility=Visibility.VISIBLE,
        source_snapshot_id="availability",
        source_provider="test",
        source_reference="test:m24",
        reason="test",
    )
    with pytest.raises(ValidationError, match="duplicate vehicle"):
        RawAvailabilityDataset(
            snapshot=_metadata(DatasetType.AVAILABILITY).snapshot(b"[]"),
            records=(availability, availability),
            raw_content=b"[]",
        )

    edge = ResearchEdge(
        nation=Nation.USA,
        domain=ResearchDomain.GROUND,
        parent_vehicle_id="us_m5a1",
        child_vehicle_id="us_m24",
        edge_type=ResearchEdgeType.REQUIRED_PREDECESSOR,
        prerequisite_group="required:us_m24",
        snapshot_id="graph",
    )
    with pytest.raises(ValidationError, match="duplicate edges"):
        RawResearchGraphDataset(
            snapshot=_metadata(DatasetType.RESEARCH_GRAPH).snapshot(b"[]"),
            records=(edge, edge),
            raw_content=b"[]",
        )


def test_database_engine_accepts_each_supported_location_shape(tmp_path: Path) -> None:
    databases: tuple[str | Path | URL, ...] = (
        URL.create("sqlite", database=":memory:"),
        tmp_path / "path.sqlite",
        "sqlite:///:memory:",
        str(tmp_path / "string.sqlite"),
    )
    for database in databases:
        engine = build_engine(database)
        try:
            assert engine.url.drivername == "sqlite"
        finally:
            engine.dispose()


def test_database_service_reports_head_schema_revision(tmp_path: Path) -> None:
    service = AdvisorService.from_database(tmp_path / "advisor.sqlite")

    assert service.data_status()["schema_revision"] == "0006"


def test_bounded_statistics_reader_rejects_oversized_or_unreadable_files(tmp_path: Path) -> None:
    source = tmp_path / "statistics.csv"
    source.write_bytes(b"123")

    with pytest.raises(ValueError, match="byte limit"):
        _read_bounded_file(source, 2)
    with pytest.raises(ValueError, match="unable to read"):
        _read_bounded_file(tmp_path / "missing.csv", 10)
