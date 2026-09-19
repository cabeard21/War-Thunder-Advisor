"""Repository for evidence imports, resolution, and user progression."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from hashlib import sha256
from typing import Any, Protocol

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from wt_advisor.domain.models import (
    BR_LADDER,
    AvailabilityType,
    Capability,
    DatasetType,
    Freshness,
    GameMode,
    Nation,
    SnapshotPurpose,
    SnapshotRef,
    StatisticsScope,
    UserProfile,
    Vehicle,
    VehicleClass,
    VehicleStatistics,
    VehicleStatus,
)
from wt_advisor.storage.db import database_session
from wt_advisor.storage.models import (
    DataSnapshotRow,
    OverrideEntryRow,
    OverrideRevisionRow,
    RawArtifactRow,
    ResearchEdgeRow,
    UserProfileRow,
    UserVehicleStateRow,
    VehicleBattleRatingRow,
    VehicleCapabilityRow,
    VehicleMetadataRow,
    VehicleRow,
    VehicleSourceIdRow,
    VehicleStatisticsRow,
)

MAX_RAW_ARTIFACT_BYTES = 10_000_000


@dataclass(frozen=True, slots=True)
class ImportResult:
    snapshot_id: str
    created: bool


@dataclass(frozen=True, slots=True)
class CreationResult:
    created: bool


@dataclass(frozen=True, slots=True)
class ResolvedBattleRating:
    value: int
    imported_value: int
    source: str
    snapshot_id: str
    imported_source: str
    override_revision: str | None = None
    override_reason: str | None = None
    override_reference: str | None = None


@dataclass(frozen=True, slots=True)
class SnapshotCompatibility:
    status: str
    reason: str
    missing_vehicle_ids: tuple[str, ...] = ()

    @property
    def compatible(self) -> bool:
        return self.status == "compatible"


@dataclass(frozen=True, slots=True)
class ResolvedAnalysisBundle:
    newest_vehicle_snapshot: SnapshotRef
    newest_statistics_snapshot: SnapshotRef | None
    vehicle_snapshot: SnapshotRef
    statistics_snapshot: SnapshotRef | None
    newest_compatibility: SnapshotCompatibility
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StoredProfile:
    profile_id: str
    nation: Nation
    preferred_mode: GameMode
    crew_slots: int
    include_premiums: bool
    include_event_vehicles: bool
    include_pack_vehicles: bool
    revision: int

    def to_domain(self) -> UserProfile:
        return UserProfile(
            profile_id=self.profile_id,
            nation=self.nation,
            preferred_mode=self.preferred_mode,
            crew_slots=self.crew_slots,
            include_premiums=self.include_premiums,
            include_event_vehicles=self.include_event_vehicles,
            include_pack_vehicles=self.include_pack_vehicles,
        )


@dataclass(frozen=True, slots=True)
class VehicleStatusChange:
    before: VehicleStatus | None
    after: VehicleStatus
    profile_revision: int
    changed: bool


class VehicleDataset(Protocol):
    @property
    def snapshot(self) -> SnapshotRef: ...

    @property
    def records(self) -> Sequence[object]: ...

    @property
    def raw_content(self) -> bytes: ...


class StatisticsDataset(Protocol):
    @property
    def snapshot(self) -> SnapshotRef: ...

    @property
    def records(self) -> Sequence[object]: ...

    @property
    def raw_content(self) -> bytes: ...


def _mapping(value: Mapping[str, Any] | object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return dict(model_dump(mode="python"))
    raise TypeError(f"expected a mapping or Pydantic model, got {type(value).__name__}")


def _enum_value(value: object) -> str:
    return str(getattr(value, "value", value))


def _vehicle_from_observation(item: Vehicle | Mapping[str, Any] | object) -> Vehicle:
    if isinstance(item, Vehicle):
        return item
    values = _mapping(item)
    canonical_fields = {
        field: values[field] for field in Vehicle.model_fields if field in values
    }
    return Vehicle.model_validate(canonical_fields)


def _battle_rating_tenths(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("battle rating must be a number")
    if isinstance(value, float):
        tenths = round(value * 10)
        if abs(value * 10 - tenths) > 1e-6:
            raise ValueError("battle rating must have one decimal place")
    elif isinstance(value, (int, str)):
        tenths = int(value)
    else:
        raise ValueError("battle rating must be a number")
    if tenths not in BR_LADDER:
        raise ValueError("battle rating is not on the supported War Thunder BR ladder")
    return tenths


class EvidenceRepository:
    """Transaction-oriented repository with explicit snapshot reads."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    @contextmanager
    def session(self) -> Iterator[Session]:
        with database_session(self.engine) as session:
            yield session

    def _add_snapshot(
        self,
        session: Session,
        snapshot: SnapshotRef,
        *,
        raw_content: bytes | str,
        media_type: str,
        provider_version: str | None,
        notes: str | None,
    ) -> ImportResult:
        same_identity = session.scalar(
            select(DataSnapshotRow).where(
                DataSnapshotRow.dataset_type == snapshot.dataset_type.value,
                DataSnapshotRow.provider == snapshot.provider,
                DataSnapshotRow.source_revision == snapshot.source_revision,
                DataSnapshotRow.checksum == snapshot.checksum,
            )
        )
        if same_identity is not None:
            return ImportResult(snapshot_id=same_identity.snapshot_id, created=False)

        same_id = session.get(DataSnapshotRow, snapshot.snapshot_id)
        if same_id is not None:
            raise ValueError(
                f"snapshot ID {snapshot.snapshot_id!r} already exists with different evidence"
            )

        content = raw_content.encode("utf-8") if isinstance(raw_content, str) else raw_content
        session.add(
            DataSnapshotRow(
                snapshot_id=snapshot.snapshot_id,
                dataset_type=snapshot.dataset_type.value,
                provider=snapshot.provider,
                provider_version=provider_version,
                retrieved_at=snapshot.retrieved_at,
                source_revision=snapshot.source_revision,
                checksum=snapshot.checksum,
                freshness=snapshot.freshness.value,
                sample_start=snapshot.sample_start,
                sample_end=snapshot.sample_end,
                notes=notes,
                purpose=snapshot.purpose.value,
                compatibility_key=snapshot.compatibility_key,
            )
        )
        session.add(
            RawArtifactRow(
                snapshot_id=snapshot.snapshot_id,
                media_type=media_type,
                checksum=sha256(content).hexdigest(),
                content=content,
            )
        )
        return ImportResult(snapshot_id=snapshot.snapshot_id, created=True)

    @staticmethod
    def _validated_raw_content(snapshot: SnapshotRef, raw_content: bytes | str) -> bytes:
        content = raw_content.encode("utf-8") if isinstance(raw_content, str) else raw_content
        if len(content) > MAX_RAW_ARTIFACT_BYTES:
            raise ValueError("raw artifact exceeds configured byte limit")
        if sha256(content).hexdigest() != snapshot.checksum:
            raise ValueError("raw artifact checksum does not match snapshot checksum")
        return content

    def import_vehicle_snapshot(
        self,
        snapshot: SnapshotRef,
        vehicles: Iterable[Vehicle | Mapping[str, Any] | object],
        *,
        battle_ratings: Iterable[Mapping[str, Any] | object] = (),
        raw_content: bytes | str,
        media_type: str = "application/json",
        provider_version: str | None = None,
        notes: str | None = None,
    ) -> ImportResult:
        """Atomically import versioned vehicle facts and their raw source artifact."""

        if snapshot.dataset_type not in {
            DatasetType.VEHICLE_METADATA,
            DatasetType.BATTLE_RATINGS,
            DatasetType.TECH_TREE,
        }:
            raise ValueError("vehicle import requires a vehicle, BR, or tech-tree snapshot")
        content = self._validated_raw_content(snapshot, raw_content)
        normalized_vehicles = tuple(_vehicle_from_observation(item) for item in vehicles)
        normalized_ratings = tuple(_mapping(item) for item in battle_ratings)

        with self.session() as session:
            result = self._add_snapshot(
                session,
                snapshot,
                raw_content=content,
                media_type=media_type,
                provider_version=provider_version,
                notes=notes,
            )
            if not result.created:
                return result

            known_ids = {vehicle.vehicle_id for vehicle in normalized_vehicles}
            existing_ids = set(
                session.scalars(select(VehicleRow.vehicle_id).where(VehicleRow.vehicle_id.in_(known_ids)))
            )
            session.add_all(
                VehicleRow(vehicle_id=vehicle_id) for vehicle_id in known_ids - existing_ids
            )
            session.flush()

            for vehicle in normalized_vehicles:
                session.add(
                    VehicleSourceIdRow(
                        snapshot_id=snapshot.snapshot_id,
                        vehicle_id=vehicle.vehicle_id,
                        provider=snapshot.provider,
                        source_vehicle_id=vehicle.source_vehicle_id,
                    )
                )
                session.add(
                    VehicleMetadataRow(
                        snapshot_id=snapshot.snapshot_id,
                        vehicle_id=vehicle.vehicle_id,
                        name=vehicle.name,
                        nation=vehicle.nation.value,
                        vehicle_class=vehicle.vehicle_class.value,
                        rank=vehicle.rank,
                        research_cost=vehicle.research_cost,
                        purchase_cost=vehicle.purchase_cost,
                        availability_type=vehicle.availability_type.value,
                    )
                )
                session.add_all(
                    VehicleCapabilityRow(
                        snapshot_id=snapshot.snapshot_id,
                        vehicle_id=vehicle.vehicle_id,
                        capability=capability.value,
                    )
                    for capability in vehicle.capabilities
                )

            all_database_ids = set(session.scalars(select(VehicleRow.vehicle_id)))
            for vehicle in normalized_vehicles:
                if vehicle.research_parent_id is None:
                    continue
                if vehicle.research_parent_id not in all_database_ids:
                    raise ValueError(
                        f"research parent {vehicle.research_parent_id!r} is not a known vehicle"
                    )
                session.add(
                    ResearchEdgeRow(
                        snapshot_id=snapshot.snapshot_id,
                        parent_vehicle_id=vehicle.research_parent_id,
                        child_vehicle_id=vehicle.vehicle_id,
                    )
                )

            for record in normalized_ratings:
                vehicle_id = str(record["vehicle_id"])
                if vehicle_id not in all_database_ids:
                    raise ValueError(f"battle rating references unknown vehicle {vehicle_id!r}")
                session.add(
                    VehicleBattleRatingRow(
                        snapshot_id=snapshot.snapshot_id,
                        vehicle_id=vehicle_id,
                        mode=_enum_value(record["mode"]),
                        battle_rating=_battle_rating_tenths(record["battle_rating"]),
                        source=str(record.get("source", snapshot.provider)),
                    )
                )
            return result

    def import_vehicle_dataset(self, dataset: VehicleDataset) -> ImportResult:
        """Import a provider dataset via its snapshot/records boundary contract."""

        records = tuple(dataset.records)
        if not records:
            raise ValueError("vehicle dataset must contain at least one record")
        ratings: list[dict[str, Any]] = []
        for record in records:
            values = _mapping(record)
            if "ground_realistic_br" in values:
                ratings.append(
                    {
                        "vehicle_id": values["vehicle_id"],
                        "mode": GameMode.GROUND_REALISTIC,
                        "battle_rating": values["ground_realistic_br"],
                        "source": dataset.snapshot.provider,
                    }
                )
        return self.import_vehicle_snapshot(
            dataset.snapshot,
            records,
            battle_ratings=ratings,
            raw_content=dataset.raw_content,
        )

    def import_statistics_snapshot(
        self,
        snapshot: SnapshotRef,
        statistics: Iterable[VehicleStatistics | Mapping[str, Any] | object],
        *,
        raw_content: bytes | str,
        media_type: str = "application/json",
        provider_version: str | None = None,
        notes: str | None = None,
    ) -> ImportResult:
        if snapshot.dataset_type is not DatasetType.GLOBAL_STATISTICS:
            raise ValueError("statistics import requires a global-statistics snapshot")
        content = self._validated_raw_content(snapshot, raw_content)
        normalized: list[VehicleStatistics] = []
        for item in statistics:
            values = _mapping(item)
            values["snapshot_id"] = snapshot.snapshot_id
            normalized.append(VehicleStatistics.model_validate(values))

        with self.session() as session:
            result = self._add_snapshot(
                session,
                snapshot,
                raw_content=content,
                media_type=media_type,
                provider_version=provider_version,
                notes=notes,
            )
            if not result.created:
                return result
            known_ids = set(session.scalars(select(VehicleRow.vehicle_id)))
            for item in normalized:
                if item.vehicle_id not in known_ids:
                    raise ValueError(f"statistics reference unknown vehicle {item.vehicle_id!r}")
                session.add(
                    VehicleStatisticsRow(
                        snapshot_id=snapshot.snapshot_id,
                        vehicle_id=item.vehicle_id,
                        mode_scope=item.mode_scope.value,
                        sample_start=item.sample_start,
                        sample_end=item.sample_end,
                        battles=item.battles,
                        wins=item.wins,
                        losses=item.losses,
                        win_rate=item.win_rate,
                        kills=item.kills,
                        ground_kills=item.ground_kills,
                        air_kills=item.air_kills,
                        deaths=item.deaths,
                    )
                )
            return result

    def import_statistics_dataset(self, dataset: StatisticsDataset) -> ImportResult:
        """Import a statistics provider dataset without depending on its concrete type."""

        records = tuple(dataset.records)
        return self.import_statistics_snapshot(
            dataset.snapshot,
            records,
            raw_content=dataset.raw_content,
        )

    def import_override_revision(
        self,
        *,
        revision: str,
        entries: Iterable[Mapping[str, Any] | object],
        created_at: datetime | None = None,
        notes: str | None = None,
    ) -> CreationResult:
        normalized = tuple(_mapping(entry) for entry in entries)
        with self.session() as session:
            existing = session.get(OverrideRevisionRow, revision)
            if existing is not None:
                stored = tuple(
                    session.scalars(
                        select(OverrideEntryRow)
                        .where(OverrideEntryRow.revision == revision)
                        .order_by(OverrideEntryRow.vehicle_id, OverrideEntryRow.field)
                    )
                )
                incoming_identity = sorted(
                    (str(item["vehicle_id"]), str(item["field"]), item["value"])
                    for item in normalized
                )
                stored_identity = sorted(
                    (item.vehicle_id, item.field, item.value) for item in stored
                )
                if incoming_identity != stored_identity:
                    raise ValueError(
                        f"override revision {revision!r} already exists with different entries"
                    )
                return CreationResult(created=False)

            session.add(
                OverrideRevisionRow(
                    revision=revision,
                    created_at=created_at or datetime.now(UTC),
                    notes=notes,
                )
            )
            session.flush()
            session.add_all(
                OverrideEntryRow(
                    revision=revision,
                    vehicle_id=str(item["vehicle_id"]),
                    field=str(item["field"]),
                    value=item["value"],
                    reason=str(item["reason"]),
                    reference=str(item.get("reference", item.get("source", ""))),
                    added_at=(
                        item["added_at"]
                        if isinstance(item["added_at"], date)
                        else date.fromisoformat(str(item["added_at"]))
                    ),
                )
                for item in normalized
            )
            return CreationResult(created=True)

    def get_snapshot(self, snapshot_id: str) -> SnapshotRef | None:
        with self.session() as session:
            row = session.get(DataSnapshotRow, snapshot_id)
            return None if row is None else self._snapshot_domain(row)

    def list_snapshots(self, dataset_type: DatasetType | None = None) -> tuple[SnapshotRef, ...]:
        with self.session() as session:
            statement = select(DataSnapshotRow)
            if dataset_type is not None:
                statement = statement.where(DataSnapshotRow.dataset_type == dataset_type.value)
            rows = session.scalars(
                statement.order_by(DataSnapshotRow.retrieved_at.desc(), DataSnapshotRow.snapshot_id)
            )
            return tuple(self._snapshot_domain(row) for row in rows)

    def snapshot_compatibility(
        self, vehicle_snapshot: SnapshotRef, statistics_snapshot: SnapshotRef
    ) -> SnapshotCompatibility:
        """Evaluate whether statistics can be used with one vehicle snapshot."""

        if statistics_snapshot.dataset_type is not DatasetType.GLOBAL_STATISTICS:
            raise ValueError("statistics compatibility requires a statistics snapshot")
        if vehicle_snapshot.purpose is not statistics_snapshot.purpose:
            return SnapshotCompatibility(
                status="incompatible",
                reason=(
                    f"snapshot purpose mismatch: {vehicle_snapshot.purpose.value} vehicle "
                    f"evidence cannot use {statistics_snapshot.purpose.value} statistics"
                ),
            )
        if vehicle_snapshot.purpose is SnapshotPurpose.ACCEPTANCE:
            if (
                vehicle_snapshot.compatibility_key is None
                or vehicle_snapshot.compatibility_key != statistics_snapshot.compatibility_key
            ):
                return SnapshotCompatibility(
                    status="incompatible",
                    reason="acceptance snapshots are not members of the same frozen bundle",
                )
        elif (
            vehicle_snapshot.compatibility_key is not None
            or statistics_snapshot.compatibility_key is not None
        ) and vehicle_snapshot.compatibility_key != statistics_snapshot.compatibility_key:
            return SnapshotCompatibility(
                status="incompatible",
                reason="snapshot compatibility keys do not match",
            )

        vehicle_ids = {
            vehicle.vehicle_id
            for vehicle in self.list_vehicles(snapshot_id=vehicle_snapshot.snapshot_id)
        }
        statistics_ids = set(
            self.list_statistics_vehicle_ids(snapshot_id=statistics_snapshot.snapshot_id)
        )
        missing = tuple(sorted(statistics_ids - vehicle_ids))
        if missing:
            return SnapshotCompatibility(
                status="incompatible",
                reason="statistics reference canonical IDs absent from vehicle evidence",
                missing_vehicle_ids=missing,
            )
        return SnapshotCompatibility(status="compatible", reason="canonical identities compatible")

    def resolve_analysis_bundle(self) -> ResolvedAnalysisBundle:
        """Resolve current vehicle evidence first and optional compatible statistics second."""

        vehicles = self.list_snapshots(DatasetType.VEHICLE_METADATA)
        if not vehicles:
            raise LookupError("no vehicle snapshots are stored")
        operational = tuple(
            snapshot for snapshot in vehicles if snapshot.purpose is SnapshotPurpose.OPERATIONAL
        )
        vehicle_snapshot = operational[0] if operational else vehicles[0]
        statistics = self.list_snapshots(DatasetType.GLOBAL_STATISTICS)
        newest_statistics = statistics[0] if statistics else None
        newest_compatibility = (
            SnapshotCompatibility(status="unavailable", reason="no statistics snapshots stored")
            if newest_statistics is None
            else self.snapshot_compatibility(vehicle_snapshot, newest_statistics)
        )
        selected_statistics = next(
            (
                snapshot
                for snapshot in statistics
                if self.snapshot_compatibility(vehicle_snapshot, snapshot).compatible
            ),
            None,
        )
        warnings = () if selected_statistics is not None else ("statistics_unavailable",)
        return ResolvedAnalysisBundle(
            newest_vehicle_snapshot=vehicles[0],
            newest_statistics_snapshot=newest_statistics,
            vehicle_snapshot=vehicle_snapshot,
            statistics_snapshot=selected_statistics,
            newest_compatibility=newest_compatibility,
            warnings=warnings,
        )

    @staticmethod
    def _snapshot_domain(row: DataSnapshotRow) -> SnapshotRef:
        return SnapshotRef(
            snapshot_id=row.snapshot_id,
            dataset_type=DatasetType(row.dataset_type),
            provider=row.provider,
            retrieved_at=row.retrieved_at,
            source_revision=row.source_revision,
            checksum=row.checksum,
            freshness=Freshness(row.freshness),
            sample_start=row.sample_start,
            sample_end=row.sample_end,
            purpose=SnapshotPurpose(row.purpose),
            compatibility_key=row.compatibility_key,
        )

    def get_vehicle(self, vehicle_id: str, *, snapshot_id: str) -> Vehicle | None:
        with self.session() as session:
            metadata = session.get(VehicleMetadataRow, (snapshot_id, vehicle_id))
            if metadata is None:
                return None
            source = session.get(VehicleSourceIdRow, (snapshot_id, vehicle_id))
            capabilities = frozenset(
                Capability(item)
                for item in session.scalars(
                    select(VehicleCapabilityRow.capability).where(
                        VehicleCapabilityRow.snapshot_id == snapshot_id,
                        VehicleCapabilityRow.vehicle_id == vehicle_id,
                    )
                )
            )
            parent_id = session.scalar(
                select(ResearchEdgeRow.parent_vehicle_id).where(
                    ResearchEdgeRow.snapshot_id == snapshot_id,
                    ResearchEdgeRow.child_vehicle_id == vehicle_id,
                )
            )
            if source is None:
                raise RuntimeError("vehicle metadata is missing its source identity")
            return Vehicle(
                vehicle_id=metadata.vehicle_id,
                source_vehicle_id=source.source_vehicle_id,
                name=metadata.name,
                nation=Nation(metadata.nation),
                vehicle_class=VehicleClass(metadata.vehicle_class),
                rank=metadata.rank,
                research_parent_id=parent_id,
                research_cost=metadata.research_cost,
                purchase_cost=metadata.purchase_cost,
                availability_type=AvailabilityType(metadata.availability_type),
                capabilities=capabilities,
            )

    def list_vehicles(self, *, snapshot_id: str) -> tuple[Vehicle, ...]:
        with self.session() as session:
            vehicle_ids = tuple(
                session.scalars(
                    select(VehicleMetadataRow.vehicle_id)
                    .where(VehicleMetadataRow.snapshot_id == snapshot_id)
                    .order_by(VehicleMetadataRow.vehicle_id)
                )
            )
        return tuple(
            vehicle
            for vehicle_id in vehicle_ids
            if (vehicle := self.get_vehicle(vehicle_id, snapshot_id=snapshot_id)) is not None
        )

    def list_research_edges(self, *, snapshot_id: str) -> tuple[tuple[str, str], ...]:
        with self.session() as session:
            rows = session.execute(
                select(ResearchEdgeRow.parent_vehicle_id, ResearchEdgeRow.child_vehicle_id)
                .where(ResearchEdgeRow.snapshot_id == snapshot_id)
                .order_by(ResearchEdgeRow.parent_vehicle_id, ResearchEdgeRow.child_vehicle_id)
            )
            return tuple((parent, child) for parent, child in rows)

    def list_statistics_vehicle_ids(self, *, snapshot_id: str) -> tuple[str, ...]:
        with self.session() as session:
            return tuple(
                session.scalars(
                    select(VehicleStatisticsRow.vehicle_id)
                    .where(VehicleStatisticsRow.snapshot_id == snapshot_id)
                    .distinct()
                    .order_by(VehicleStatisticsRow.vehicle_id)
                )
            )

    def resolve_battle_rating(
        self,
        vehicle_id: str,
        mode: GameMode | str,
        *,
        snapshot_id: str,
        override_revision: str | None = None,
    ) -> ResolvedBattleRating:
        mode_value = _enum_value(mode)
        with self.session() as session:
            imported = session.get(VehicleBattleRatingRow, (snapshot_id, vehicle_id, mode_value))
            if imported is None:
                raise LookupError(
                    f"no {mode_value} battle rating for {vehicle_id!r} in {snapshot_id!r}"
                )
            if override_revision is None:
                return ResolvedBattleRating(
                    value=imported.battle_rating,
                    imported_value=imported.battle_rating,
                    source="snapshot",
                    snapshot_id=snapshot_id,
                    imported_source=imported.source,
                )
            field_aliases = (
                f"battle_rating.{mode_value}",
                "ground_realistic_br" if mode_value == GameMode.GROUND_REALISTIC.value else "",
            )
            override = session.scalar(
                select(OverrideEntryRow).where(
                    OverrideEntryRow.revision == override_revision,
                    OverrideEntryRow.vehicle_id == vehicle_id,
                    OverrideEntryRow.field.in_(field_aliases),
                )
            )
            if override is None:
                return ResolvedBattleRating(
                    value=imported.battle_rating,
                    imported_value=imported.battle_rating,
                    source="snapshot",
                    snapshot_id=snapshot_id,
                    imported_source=imported.source,
                    override_revision=override_revision,
                )
            value = _battle_rating_tenths(override.value)
            return ResolvedBattleRating(
                value=value,
                imported_value=imported.battle_rating,
                source="override",
                snapshot_id=snapshot_id,
                imported_source=imported.source,
                override_revision=override_revision,
                override_reason=override.reason,
                override_reference=override.reference,
            )

    def get_vehicle_statistics(
        self, vehicle_id: str, *, snapshot_id: str
    ) -> tuple[VehicleStatistics, ...]:
        with self.session() as session:
            rows = session.scalars(
                select(VehicleStatisticsRow)
                .where(
                    VehicleStatisticsRow.snapshot_id == snapshot_id,
                    VehicleStatisticsRow.vehicle_id == vehicle_id,
                )
                .order_by(VehicleStatisticsRow.mode_scope)
            )
            return tuple(
                VehicleStatistics(
                    vehicle_id=row.vehicle_id,
                    snapshot_id=row.snapshot_id,
                    mode_scope=StatisticsScope(row.mode_scope),
                    sample_start=row.sample_start,
                    sample_end=row.sample_end,
                    battles=row.battles,
                    wins=row.wins,
                    losses=row.losses,
                    win_rate=row.win_rate,
                    kills=row.kills,
                    ground_kills=row.ground_kills,
                    air_kills=row.air_kills,
                    deaths=row.deaths,
                )
                for row in rows
            )

    def create_profile(self, profile: UserProfile) -> CreationResult:
        with self.session() as session:
            existing = session.get(UserProfileRow, profile.profile_id)
            if existing is not None:
                if self._stored_profile(existing).to_domain() != profile:
                    raise ValueError(
                        f"profile {profile.profile_id!r} already exists with a different definition"
                    )
                return CreationResult(created=False)
            session.add(
                UserProfileRow(
                    profile_id=profile.profile_id,
                    nation=profile.nation.value,
                    preferred_mode=profile.preferred_mode.value,
                    crew_slots=profile.crew_slots,
                    include_premiums=profile.include_premiums,
                    include_event_vehicles=profile.include_event_vehicles,
                    include_pack_vehicles=profile.include_pack_vehicles,
                    revision=0,
                )
            )
            return CreationResult(created=True)

    def get_profile(self, profile_id: str) -> StoredProfile | None:
        with self.session() as session:
            row = session.get(UserProfileRow, profile_id)
            return None if row is None else self._stored_profile(row)

    @staticmethod
    def _stored_profile(row: UserProfileRow) -> StoredProfile:
        return StoredProfile(
            profile_id=row.profile_id,
            nation=Nation(row.nation),
            preferred_mode=GameMode(row.preferred_mode),
            crew_slots=row.crew_slots,
            include_premiums=row.include_premiums,
            include_event_vehicles=row.include_event_vehicles,
            include_pack_vehicles=row.include_pack_vehicles,
            revision=row.revision,
        )

    def set_vehicle_status(
        self,
        profile_id: str,
        vehicle_id: str,
        status: VehicleStatus | str,
    ) -> VehicleStatusChange:
        normalized_status = status if isinstance(status, VehicleStatus) else VehicleStatus(status)
        with self.session() as session:
            profile = session.get(UserProfileRow, profile_id)
            if profile is None:
                raise LookupError(f"unknown profile {profile_id!r}")
            if session.get(VehicleRow, vehicle_id) is None:
                raise LookupError(f"unknown vehicle {vehicle_id!r}")
            state = session.get(UserVehicleStateRow, (profile_id, vehicle_id))
            before = None if state is None else VehicleStatus(state.status)
            if before is normalized_status:
                return VehicleStatusChange(
                    before=before,
                    after=normalized_status,
                    profile_revision=profile.revision,
                    changed=False,
                )
            profile.revision += 1
            if state is None:
                session.add(
                    UserVehicleStateRow(
                        profile_id=profile_id,
                        vehicle_id=vehicle_id,
                        status=normalized_status.value,
                        revision=profile.revision,
                    )
                )
            else:
                state.status = normalized_status.value
                state.revision = profile.revision
            return VehicleStatusChange(
                before=before,
                after=normalized_status,
                profile_revision=profile.revision,
                changed=True,
            )

    def get_user_vehicle_states(self, profile_id: str) -> dict[str, VehicleStatus]:
        with self.session() as session:
            if session.get(UserProfileRow, profile_id) is None:
                raise LookupError(f"unknown profile {profile_id!r}")
            rows = session.scalars(
                select(UserVehicleStateRow)
                .where(UserVehicleStateRow.profile_id == profile_id)
                .order_by(UserVehicleStateRow.vehicle_id)
            )
            return {row.vehicle_id: VehicleStatus(row.status) for row in rows}
