from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from wt_advisor.domain.models import (
    DatasetType,
    EvidenceContext,
    Freshness,
    GameMode,
    Lineup,
    Nation,
    SnapshotRef,
    StatisticsScope,
    VehicleStatistics,
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
