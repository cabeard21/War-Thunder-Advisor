"""Immutable contracts at the boundary between providers and normalization."""

from __future__ import annotations

import json
from datetime import date, datetime
from hashlib import sha256
from typing import Any

from pydantic import Field, model_validator

from wt_advisor.domain.models import (
    AvailabilityType,
    Capability,
    DatasetType,
    Freshness,
    FrozenModel,
    Nation,
    SnapshotPurpose,
    SnapshotRef,
    UserProfile,
    VehicleClass,
    VehicleStatistics,
    VehicleStatus,
)


class RawDatasetMetadata(FrozenModel):
    """Provider-supplied metadata before a content checksum is calculated."""

    snapshot_id: str = Field(min_length=1)
    dataset_type: DatasetType
    provider: str = Field(min_length=1)
    retrieved_at: datetime
    source_revision: str | None = None
    sample_start: date | None = None
    sample_end: date | None = None
    purpose: SnapshotPurpose = SnapshotPurpose.OPERATIONAL
    compatibility_key: str | None = None

    def snapshot(self, content: bytes, freshness: Freshness = Freshness.UNKNOWN) -> SnapshotRef:
        return SnapshotRef(
            snapshot_id=self.snapshot_id,
            dataset_type=self.dataset_type,
            provider=self.provider,
            retrieved_at=self.retrieved_at,
            source_revision=self.source_revision,
            checksum=sha256(content).hexdigest(),
            freshness=freshness,
            sample_start=self.sample_start,
            sample_end=self.sample_end,
            purpose=self.purpose,
            compatibility_key=self.compatibility_key,
        )


class RawVehicleRecord(FrozenModel):
    """A minimally normalized vehicle observation from any provider."""

    vehicle_id: str = Field(min_length=1, pattern=r"^[a-z0-9_]+$")
    source_vehicle_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    nation: Nation
    vehicle_class: VehicleClass
    rank: int = Field(ge=1)
    ground_realistic_br: int = Field(ge=10)
    research_parent_id: str | None = None
    research_cost: int | None = Field(default=None, ge=0)
    purchase_cost: int | None = Field(default=None, ge=0)
    availability_type: AvailabilityType = AvailabilityType.RESEARCH_TREE
    capabilities: frozenset[Capability] = frozenset()
    initially_owned: bool = False
    initial_status: VehicleStatus = VehicleStatus.LOCKED


class RawVehicleDataset(FrozenModel):
    snapshot: SnapshotRef
    records: tuple[RawVehicleRecord, ...]
    raw_content: bytes = Field(repr=False)

    @model_validator(mode="after")
    def unique_ids(self) -> RawVehicleDataset:
        ids = [row.vehicle_id for row in self.records]
        if len(ids) != len(set(ids)):
            raise ValueError("raw vehicle dataset contains duplicate vehicle IDs")
        return self


class RawStatisticsDataset(FrozenModel):
    snapshot: SnapshotRef
    records: tuple[VehicleStatistics, ...]
    raw_content: bytes = Field(repr=False)


class FixtureUserState(FrozenModel):
    vehicle_id: str
    status: VehicleStatus


class FixtureBundle(FrozenModel):
    """All deterministic inputs needed by the Milestone 1 acceptance scenario."""

    vehicles: RawVehicleDataset
    statistics: RawStatisticsDataset
    user_profile: UserProfile
    user_states: tuple[FixtureUserState, ...]
    research_edges: tuple[tuple[str, str], ...]

    @property
    def battle_ratings(self) -> dict[str, int]:
        return {row.vehicle_id: row.ground_realistic_br for row in self.vehicles.records}


def canonical_bytes(value: Any) -> bytes:
    """Serialize provider content canonically for stable snapshot checksums."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
