"""Committed, network-free Milestone 1 acceptance evidence."""

from __future__ import annotations

import json
from datetime import date, datetime
from importlib.resources import files
from typing import Any

from wt_advisor.data.models import (
    FixtureBundle,
    FixtureUserState,
    RawDatasetMetadata,
    RawStatisticsDataset,
    RawVehicleDataset,
    RawVehicleRecord,
    canonical_bytes,
)
from wt_advisor.domain.models import (
    DatasetType,
    Freshness,
    SnapshotPurpose,
    UserProfile,
    VehicleStatistics,
)


class FixtureProvider:
    """Read the reproducible early-USA fixture shipped with the package."""

    def __init__(self, fixture_name: str = "usa_ground_rb_m1.json") -> None:
        self._fixture_name = fixture_name

    def _read(self) -> dict[str, Any]:
        fixture = files("wt_advisor.data.fixtures").joinpath(self._fixture_name)
        decoded = json.loads(fixture.read_text(encoding="utf-8"))
        if not isinstance(decoded, dict):
            raise ValueError("fixture root must be an object")
        return decoded

    def fetch_vehicles(self) -> RawVehicleDataset:
        raw = self._read()
        rows = tuple(RawVehicleRecord.model_validate(row) for row in raw["vehicles"])
        metadata = RawDatasetMetadata(
            snapshot_id=raw["vehicle_snapshot"]["snapshot_id"],
            dataset_type=DatasetType.VEHICLE_METADATA,
            provider=raw["provider"],
            retrieved_at=datetime.fromisoformat(raw["retrieved_at"]),
            source_revision=raw["revision"],
            purpose=SnapshotPurpose.ACCEPTANCE,
            compatibility_key="usa-ground-rb-m1-acceptance",
        )
        content = canonical_bytes(raw["vehicles"])
        snapshot = metadata.snapshot(content, Freshness.AGING)
        return RawVehicleDataset(snapshot=snapshot, records=rows, raw_content=content)

    def fetch_vehicle_statistics(self) -> RawStatisticsDataset:
        raw = self._read()
        sample_start = date.fromisoformat(raw["statistics_snapshot"]["sample_start"])
        sample_end = date.fromisoformat(raw["statistics_snapshot"]["sample_end"])
        metadata = RawDatasetMetadata(
            snapshot_id=raw["statistics_snapshot"]["snapshot_id"],
            dataset_type=DatasetType.GLOBAL_STATISTICS,
            provider=raw["provider"],
            retrieved_at=datetime.fromisoformat(raw["retrieved_at"]),
            source_revision=raw["revision"],
            sample_start=sample_start,
            sample_end=sample_end,
            purpose=SnapshotPurpose.ACCEPTANCE,
            compatibility_key="usa-ground-rb-m1-acceptance",
        )
        rows = tuple(
            VehicleStatistics(snapshot_id=metadata.snapshot_id, **row)
            for row in raw["statistics"]
        )
        content = canonical_bytes(raw["statistics"])
        snapshot = metadata.snapshot(content, Freshness.FRESH)
        return RawStatisticsDataset(snapshot=snapshot, records=rows, raw_content=content)

    def load(self) -> FixtureBundle:
        """Return the complete acceptance bundle used by services and tests."""

        raw = self._read()
        states = tuple(FixtureUserState.model_validate(row) for row in raw["user_states"])
        edges = tuple((row["parent_id"], row["child_id"]) for row in raw["research_edges"])
        return FixtureBundle(
            vehicles=self.fetch_vehicles(),
            statistics=self.fetch_vehicle_statistics(),
            user_profile=UserProfile.model_validate(raw["user_profile"]),
            user_states=states,
            research_edges=edges,
        )
