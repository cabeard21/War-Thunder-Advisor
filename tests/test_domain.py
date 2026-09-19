from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from wt_advisor.domain.models import (
    AcquisitionType,
    Capability,
    CapabilityObservation,
    CapabilityResolution,
    CapabilitySourceType,
    CapabilityState,
    ComponentDataStatus,
    ComponentStatus,
    DatasetType,
    EvidenceContext,
    Freshness,
    GameMode,
    Lineup,
    Nation,
    PrerequisiteSemantics,
    Researchability,
    ResearchDomain,
    ResearchEdge,
    ResearchEdgeType,
    ResolvedAvailability,
    SnapshotRef,
    StatisticsScope,
    TreeMembership,
    VehicleStatistics,
    Visibility,
    br_step_distance,
    stable_hash,
)


def test_br_step_distance_handles_point_three_and_point_four_labels() -> None:
    assert br_step_distance(27, 23) == 1
    assert br_step_distance(27, 20) == 2


def test_br_step_distance_rejects_non_ladder_value() -> None:
    with pytest.raises(ValueError, match="supported"):
        br_step_distance(25, 20)


def test_lineup_rejects_duplicate_vehicle_ids() -> None:
    with pytest.raises(ValidationError, match="unique"):
        Lineup(
            nation=Nation.USA,
            mode=GameMode.GROUND_REALISTIC,
            slots=("us_m3_lee", "us_m3_lee"),
        )


def test_stable_hash_is_independent_of_dictionary_order() -> None:
    assert stable_hash({"a": 1, "b": 2}) == stable_hash({"b": 2, "a": 1})


def test_evidence_context_is_immutable() -> None:
    context = EvidenceContext(
        snapshots=(
            SnapshotRef(
                snapshot_id="vehicle-fixture",
                dataset_type=DatasetType.VEHICLE_METADATA,
                provider="fixture",
                retrieved_at=datetime(2026, 9, 18, tzinfo=UTC),
                source_revision="m1",
                checksum="a" * 64,
                freshness=Freshness.FRESH,
            ),
        ),
        override_revision="none",
        ruleset_hash="rules",
        schema_revision="0001",
    )
    with pytest.raises(ValidationError, match="frozen"):
        context.schema_revision = "0002"  # type: ignore[misc]


def test_statistics_reject_inconsistent_dates_and_win_rate() -> None:
    with pytest.raises(ValidationError, match="sample_start"):
        VehicleStatistics(
            vehicle_id="us_test",
            snapshot_id="stats",
            mode_scope=StatisticsScope.GROUND_REALISTIC_GROUND_VEHICLES,
            sample_start=date(2026, 9, 2),
            sample_end=date(2026, 9, 1),
        )


def test_capability_observations_distinguish_absence_unknown_and_conflict() -> None:
    present = CapabilityObservation(
        vehicle_id="us_m24",
        capability=Capability.SCOUTING,
        value=True,
        source_provider="curated",
        source_snapshot_id="cap-v1",
        source_reference="https://wiki.warthunder.com/unit/us_m24_chaffee",
        source_type=CapabilitySourceType.OFFICIAL_REFERENCE,
    )
    absent = present.model_copy(update={"value": False, "source_provider": "community_api"})

    assert CapabilityResolution.from_observations(
        "us_m24", Capability.SCOUTING, (present,)
    ).state is CapabilityState.PRESENT
    assert CapabilityResolution.from_observations(
        "us_m24", Capability.SCOUTING, (absent,)
    ).state is CapabilityState.VERIFIED_ABSENT
    assert CapabilityResolution.from_observations(
        "us_m24", Capability.SCOUTING, ()
    ).state is CapabilityState.UNKNOWN
    assert CapabilityResolution.from_observations(
        "us_m24", Capability.SCOUTING, (present, absent)
    ).state is CapabilityState.CONFLICTED


def test_resolved_availability_keeps_provider_observation_and_resolution_evidence() -> None:
    availability = ResolvedAvailability(
        vehicle_id="us_m24",
        acquisition_type=AcquisitionType.RESEARCH,
        researchability=Researchability.NORMALLY_RESEARCHABLE,
        tree_membership=TreeMembership.MAIN_TREE,
        visibility=Visibility.VISIBLE,
        source_snapshot_id="availability-v1",
        source_provider="curated",
        source_reference="reference",
        reason="visible main-tree vehicle",
        provider_observation="research_tree",
    )

    assert availability.provider_observation == "research_tree"
    with pytest.raises(ValidationError, match="frozen"):
        availability.reason = "changed"  # type: ignore[misc]


def test_research_edge_requires_group_for_branching_semantics() -> None:
    edge = ResearchEdge(
        nation=Nation.USA,
        domain=ResearchDomain.GROUND,
        parent_vehicle_id="us_m3_lee",
        child_vehicle_id="us_m4a1",
        edge_type=ResearchEdgeType.REQUIRED_PREDECESSOR,
        prerequisite_group="sherman-entry",
        prerequisite_semantics=PrerequisiteSemantics.ALL,
        snapshot_id="graph-v1",
    )

    assert edge.prerequisite_semantics is PrerequisiteSemantics.ALL
    with pytest.raises(ValidationError, match="prerequisite_group"):
        ResearchEdge(
            nation=Nation.USA,
            domain=ResearchDomain.GROUND,
            parent_vehicle_id="us_m3_lee",
            child_vehicle_id="us_m4a1",
            edge_type=ResearchEdgeType.NORMAL,
            prerequisite_semantics=PrerequisiteSemantics.ANY,
            snapshot_id="graph-v1",
        )


def test_component_data_status_validates_coverage() -> None:
    status = ComponentDataStatus(
        dataset_type=DatasetType.CAPABILITIES,
        status=ComponentStatus.AVAILABLE,
        selected_snapshot_id="cap-v1",
        newest_snapshot_id="cap-v1",
        provider="curated",
        purpose="operational",
        freshness=Freshness.FRESH,
        compatible=True,
        total_count=10,
        covered_count=8,
        coverage_percent=80.0,
        gaps=("us_m4", "us_m4a1"),
    )

    assert status.coverage_percent == 80.0
    with pytest.raises(ValidationError, match="covered_count"):
        ComponentDataStatus.model_validate(
            {**status.model_dump(), "covered_count": 11}
        )


def test_statistics_preserve_reported_ratios_separately_from_counts() -> None:
    statistics = VehicleStatistics(
        vehicle_id="us_m24",
        snapshot_id="stats-v2",
        mode_scope=StatisticsScope.GROUND_REALISTIC_GROUND_VEHICLES,
        battles=100,
        wins=55,
        kills=120,
        deaths=80,
        reported_win_rate=0.55,
        reported_kd=1.5,
        reported_kills_per_battle=1.2,
    )

    assert statistics.derived_win_rate == 0.55
    assert statistics.derived_kd == 1.5
    assert statistics.derived_kills_per_battle == 1.2
    with pytest.raises(ValidationError, match="win_rate"):
        VehicleStatistics(
            vehicle_id="us_test",
            snapshot_id="stats",
            mode_scope=StatisticsScope.GROUND_REALISTIC_GROUND_VEHICLES,
            battles=100,
            wins=80,
            losses=20,
            win_rate=0.5,
        )
