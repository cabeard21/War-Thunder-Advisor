"""Repository for evidence imports, resolution, and user progression."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from hashlib import sha256
from typing import Any, Protocol, cast

from sqlalchemy import Engine, select, update
from sqlalchemy.orm import Session

from wt_advisor.data.models import (
    RawAvailabilityDataset,
    RawCapabilityDataset,
    RawResearchGraphDataset,
    RawStatisticsDataset,
    RawVehicleDataset,
)
from wt_advisor.domain.models import (
    BR_LADDER,
    AcquisitionType,
    AvailabilityType,
    Capability,
    CapabilityObservation,
    CapabilityResolution,
    CapabilitySourceType,
    DatasetType,
    Freshness,
    GameMode,
    KillTargetDefinition,
    Nation,
    PrerequisiteSemantics,
    ReconciliationItem,
    ReconciliationPlan,
    Researchability,
    ResearchDomain,
    ResearchEdge,
    ResearchEdgeType,
    ResolvedAvailability,
    SnapshotPurpose,
    SnapshotRef,
    StatisticsScope,
    TreeMembership,
    UserProfile,
    Vehicle,
    VehicleClass,
    VehicleStatistics,
    VehicleStatus,
    Visibility,
    stable_hash,
)
from wt_advisor.storage.db import database_session
from wt_advisor.storage.models import (
    AdvisorContextRow,
    AvailabilityObservationRow,
    CapabilityObservationRow,
    DataSnapshotRow,
    ImportAttemptRow,
    LineupPresetRow,
    OverrideEntryRow,
    OverrideRevisionRow,
    ProfileReconciliationAuditRow,
    ProfileReconciliationItemRow,
    RawArtifactRow,
    ResearchEdgeRow,
    ResearchGraphEdgeRow,
    StatisticsImportDiagnosticRow,
    StoredEvaluationRow,
    UserProfileRow,
    UserVehicleStateRow,
    VehicleBattleRatingRow,
    VehicleCapabilityRow,
    VehicleIdentityAliasRow,
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
class ImportAttemptResult:
    attempt_id: int
    created: bool


@dataclass(frozen=True, slots=True)
class StatisticsImportDiagnostics:
    snapshot_id: str
    recognized_columns: tuple[str, ...]
    unknown_columns: tuple[str, ...]
    matched_count: int
    unresolved_source_ids: tuple[str, ...]
    duplicate_count: int


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
    capability_snapshot: SnapshotRef | None = None
    availability_snapshot: SnapshotRef | None = None
    identity_snapshot: SnapshotRef | None = None
    research_graph_snapshot: SnapshotRef | None = None


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
    write_revision: int

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


@dataclass(frozen=True, slots=True)
class StoredPreset:
    preset_id: str
    profile_id: str
    name: str
    slots: tuple[str, ...]
    required_vehicle_ids: tuple[str, ...]
    excluded_vehicle_ids: tuple[str, ...]
    revision: int


@dataclass(frozen=True, slots=True)
class StoredAdvisorContext:
    profile_id: str
    selected_preset_id: str | None
    target_br: int | None
    required_vehicle_ids: tuple[str, ...]
    excluded_vehicle_ids: tuple[str, ...]
    revision: int


@dataclass(frozen=True, slots=True)
class StoredEvaluation:
    evaluation_id: str
    profile_id: str
    kind: str
    profile_revision: str
    profile_state: dict[str, Any]
    effective_inputs: dict[str, Any]
    evidence_context: dict[str, Any]
    ruleset_hash: str
    schema_revision: str
    result_id: str
    result_payload: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class EvaluationSnapshot:
    """All mutable profile inputs captured by one read transaction."""

    profile: StoredProfile
    vehicle_statuses: dict[str, VehicleStatus]
    context: StoredAdvisorContext
    preset: StoredPreset | None


@dataclass(frozen=True, slots=True)
class ProfileReconciliationOutcome:
    plan: ReconciliationPlan
    applied: bool
    changed: bool
    resulting_revision: int

    @property
    def items(self) -> tuple[ReconciliationItem, ...]:
        return self.plan.items


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

    @property
    def normalization_revision(self) -> str | None: ...


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
    canonical_fields = {field: values[field] for field in Vehicle.model_fields if field in values}
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

    def raw_artifact_content(self, snapshot_id: str) -> bytes | None:
        """Return retained source bytes for a known immutable snapshot."""

        with self.session() as session:
            artifact = session.scalar(
                select(RawArtifactRow).where(RawArtifactRow.snapshot_id == snapshot_id)
            )
            return None if artifact is None else bytes(artifact.content)

    def import_operational_bundle(
        self,
        *,
        vehicles: RawVehicleDataset,
        capabilities: RawCapabilityDataset,
        availability: RawAvailabilityDataset | None = None,
        research_graph: RawResearchGraphDataset | None = None,
        statistics: RawStatisticsDataset | None = None,
    ) -> None:
        """Publish related evidence in one database transaction or none at all."""

        with self.engine.begin() as connection:
            transactional = EvidenceRepository(cast(Engine, connection))
            transactional.import_vehicle_dataset(vehicles)
            transactional.import_capability_snapshot(
                capabilities.snapshot,
                capabilities.records,
                raw_content=capabilities.raw_content,
            )
            if availability is not None:
                transactional.import_availability_snapshot(
                    availability.snapshot,
                    availability.records,
                    raw_content=availability.raw_content,
                )
            if research_graph is not None:
                transactional.import_research_graph_snapshot(
                    research_graph.snapshot,
                    research_graph.records,
                    raw_content=research_graph.raw_content,
                )
            if statistics is not None:
                transactional.import_statistics_dataset(statistics)

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
                DataSnapshotRow.provider_version == provider_version,
            )
        )
        if same_identity is not None:
            if same_identity.compatibility_key is None and snapshot.compatibility_key is not None:
                same_identity.compatibility_key = snapshot.compatibility_key
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
                session.scalars(
                    select(VehicleRow.vehicle_id).where(VehicleRow.vehicle_id.in_(known_ids))
                )
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

    def _begin_component_import(
        self,
        session: Session,
        snapshot: SnapshotRef,
        *,
        expected_type: DatasetType,
        raw_content: bytes | str,
        media_type: str,
        provider_version: str | None,
        notes: str | None,
    ) -> ImportResult:
        if snapshot.dataset_type is not expected_type:
            raise ValueError(f"component import requires a {expected_type.value} snapshot")
        content = self._validated_raw_content(snapshot, raw_content)
        return self._add_snapshot(
            session,
            snapshot,
            raw_content=content,
            media_type=media_type,
            provider_version=provider_version,
            notes=notes,
        )

    @staticmethod
    def _known_vehicle_ids(session: Session) -> set[str]:
        return set(session.scalars(select(VehicleRow.vehicle_id)))

    @staticmethod
    def _validate_known_vehicle(known_ids: set[str], vehicle_id: str) -> None:
        if vehicle_id not in known_ids:
            raise ValueError(f"component references unknown vehicle {vehicle_id!r}")

    def import_capability_snapshot(
        self,
        snapshot: SnapshotRef,
        observations: Iterable[Mapping[str, Any] | object],
        *,
        raw_content: bytes | str,
        media_type: str = "application/json",
        provider_version: str | None = None,
        notes: str | None = None,
    ) -> ImportResult:
        normalized = tuple(
            CapabilityObservation.model_validate(
                {
                    **_mapping(item),
                    "source_provider": _mapping(item).get("source_provider", snapshot.provider),
                    "source_snapshot_id": snapshot.snapshot_id,
                    "source_type": _mapping(item).get(
                        "source_type", CapabilitySourceType.CURATED_IMPORT
                    ),
                }
            )
            for item in observations
        )
        keys = [(item.vehicle_id, item.capability, item.source_reference) for item in normalized]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate capability observation")
        with self.session() as session:
            result = self._begin_component_import(
                session,
                snapshot,
                expected_type=DatasetType.CAPABILITIES,
                raw_content=raw_content,
                media_type=media_type,
                provider_version=provider_version,
                notes=notes,
            )
            if not result.created:
                return result
            known_ids = self._known_vehicle_ids(session)
            for item in normalized:
                vehicle_id = item.vehicle_id
                self._validate_known_vehicle(known_ids, vehicle_id)
                session.add(
                    CapabilityObservationRow(
                        snapshot_id=snapshot.snapshot_id,
                        vehicle_id=vehicle_id,
                        capability=item.capability.value,
                        value=item.value,
                        source_provider=item.source_provider,
                        source_type=item.source_type.value,
                        source_reference=item.source_reference,
                        confidence=1.0 if item.confidence is None else item.confidence,
                        verified_at=item.verified_at,
                        source_revision=item.source_revision,
                    )
                )
            return result

    def resolve_capabilities(
        self,
        vehicle_id: str,
        *,
        snapshot_id: str | None = None,
        snapshot_ids: Sequence[str] | None = None,
    ) -> dict[Capability, CapabilityResolution]:
        if (snapshot_id is None) == (snapshot_ids is None):
            raise ValueError("provide exactly one of snapshot_id or snapshot_ids")
        selected_ids = (snapshot_id,) if snapshot_id is not None else tuple(snapshot_ids or ())
        if not selected_ids:
            return {}
        if len(selected_ids) != len(set(selected_ids)):
            raise ValueError("duplicate capability snapshot ID")
        with self.session() as session:
            snapshot_rows = tuple(
                session.scalars(
                    select(DataSnapshotRow).where(DataSnapshotRow.snapshot_id.in_(selected_ids))
                )
            )
            if len(snapshot_rows) != len(selected_ids) or any(
                row.dataset_type != DatasetType.CAPABILITIES.value for row in snapshot_rows
            ):
                raise ValueError("unknown or non-capability snapshot ID")
            chronology = {
                row.snapshot_id: (row.retrieved_at, row.snapshot_id) for row in snapshot_rows
            }
            rows = tuple(
                session.scalars(
                    select(CapabilityObservationRow)
                    .where(
                        CapabilityObservationRow.snapshot_id.in_(selected_ids),
                        CapabilityObservationRow.vehicle_id == vehicle_id,
                    )
                    .order_by(
                        CapabilityObservationRow.capability,
                        CapabilityObservationRow.observation_id,
                    )
                )
            )
        latest_claims: dict[tuple[str, str, str, str], CapabilityObservationRow] = {}
        for row in rows:
            claim = (
                row.capability, row.source_provider, row.source_reference, row.source_type
            )
            previous = latest_claims.get(claim)
            priority = (
                row.verified_at or chronology[row.snapshot_id][0].date(),
                chronology[row.snapshot_id],
            )
            previous_priority = (
                None
                if previous is None
                else (
                    previous.verified_at or chronology[previous.snapshot_id][0].date(),
                    chronology[previous.snapshot_id],
                )
            )
            if previous_priority is None or priority > previous_priority:
                latest_claims[claim] = row
        grouped: dict[Capability, list[CapabilityObservation]] = {}
        for row in sorted(
            latest_claims.values(),
            key=lambda item: (
                item.capability, item.source_provider, item.source_reference, item.source_type
            ),
        ):
            capability = Capability(row.capability)
            grouped.setdefault(capability, []).append(
                CapabilityObservation(
                    vehicle_id=row.vehicle_id,
                    capability=capability,
                    value=row.value,
                    source_provider=row.source_provider,
                    source_snapshot_id=row.snapshot_id,
                    source_reference=row.source_reference,
                    source_type=CapabilitySourceType(row.source_type),
                    confidence=row.confidence,
                    verified_at=row.verified_at,
                    source_revision=row.source_revision,
                )
            )
        return {
            capability: CapabilityResolution.from_observations(
                vehicle_id, capability, tuple(observations)
            )
            for capability, observations in grouped.items()
        }

    def list_compatible_capability_snapshots(
        self, vehicle_snapshot: SnapshotRef
    ) -> tuple[SnapshotRef, ...]:
        """Return newest API evidence plus compatible curated revisions."""

        compatible = tuple(
            candidate
            for candidate in self.list_snapshots(DatasetType.CAPABILITIES)
            if self.component_compatibility(vehicle_snapshot, candidate).compatible
        )
        with self.session() as session:
            api_ids = set(
                session.scalars(
                    select(CapabilityObservationRow.snapshot_id)
                    .where(
                        CapabilityObservationRow.snapshot_id.in_(
                            tuple(item.snapshot_id for item in compatible)
                        ),
                        CapabilityObservationRow.source_type
                        == CapabilitySourceType.COMMUNITY_API.value,
                    )
                )
            )
        newest_api_id = next(
            (
                item.snapshot_id
                for item in sorted(
                    compatible,
                    key=lambda candidate: (candidate.retrieved_at, candidate.snapshot_id),
                    reverse=True,
                )
                if item.snapshot_id in api_ids
            ),
            None,
        )
        return tuple(
            item
            for item in compatible
            if item.snapshot_id not in api_ids or item.snapshot_id == newest_api_id
        )

    def import_availability_snapshot(
        self,
        snapshot: SnapshotRef,
        observations: Iterable[Mapping[str, Any] | object],
        *,
        raw_content: bytes | str,
        media_type: str = "application/json",
        provider_version: str | None = None,
        notes: str | None = None,
    ) -> ImportResult:
        normalized = tuple(_mapping(item) for item in observations)
        with self.session() as session:
            result = self._begin_component_import(
                session,
                snapshot,
                expected_type=DatasetType.AVAILABILITY,
                raw_content=raw_content,
                media_type=media_type,
                provider_version=provider_version,
                notes=notes,
            )
            if not result.created:
                return result
            known_ids = self._known_vehicle_ids(session)
            for item in normalized:
                vehicle_id = str(item["vehicle_id"])
                self._validate_known_vehicle(known_ids, vehicle_id)
                confidence = item.get("confidence")
                session.add(
                    AvailabilityObservationRow(
                        snapshot_id=snapshot.snapshot_id,
                        vehicle_id=vehicle_id,
                        acquisition_type=_enum_value(item["acquisition_type"]),
                        researchability=_enum_value(item["researchability"]),
                        tree_membership=_enum_value(item["tree_membership"]),
                        visibility=_enum_value(item["visibility"]),
                        source_provider=str(item.get("source_provider", snapshot.provider)),
                        source_reference=str(item["source_reference"]),
                        confidence=1.0 if confidence is None else float(confidence),
                    )
                )
            return result

    def get_resolved_availability(
        self, vehicle_id: str, *, snapshot_id: str
    ) -> ResolvedAvailability | None:
        with self.session() as session:
            row = session.get(AvailabilityObservationRow, (snapshot_id, vehicle_id))
            if row is None:
                return None
            return ResolvedAvailability(
                vehicle_id=row.vehicle_id,
                acquisition_type=AcquisitionType(row.acquisition_type),
                researchability=Researchability(row.researchability),
                tree_membership=TreeMembership(row.tree_membership),
                visibility=Visibility(row.visibility),
                source_snapshot_id=row.snapshot_id,
                source_provider=row.source_provider,
                source_reference=row.source_reference,
                reason="resolved from versioned availability observation",
            )

    def import_identity_snapshot(
        self,
        snapshot: SnapshotRef,
        aliases: Iterable[Mapping[str, Any] | object],
        *,
        raw_content: bytes | str,
        media_type: str = "application/json",
        provider_version: str | None = None,
        notes: str | None = None,
    ) -> ImportResult:
        normalized = tuple(_mapping(item) for item in aliases)
        with self.session() as session:
            result = self._begin_component_import(
                session,
                snapshot,
                expected_type=DatasetType.IDENTITY_ALIASES,
                raw_content=raw_content,
                media_type=media_type,
                provider_version=provider_version,
                notes=notes,
            )
            if not result.created:
                return result
            known_ids = self._known_vehicle_ids(session)
            for item in normalized:
                canonical_id = str(item["canonical_vehicle_id"])
                self._validate_known_vehicle(known_ids, canonical_id)
                session.add(
                    VehicleIdentityAliasRow(
                        snapshot_id=snapshot.snapshot_id,
                        provider=str(item.get("provider", snapshot.provider)),
                        source_vehicle_id=str(item["source_vehicle_id"]),
                        canonical_vehicle_id=canonical_id,
                        deprecated_vehicle_id=(
                            None
                            if item.get("deprecated_vehicle_id") is None
                            else str(item["deprecated_vehicle_id"])
                        ),
                        confirmed=bool(item.get("confirmed", False)),
                        source_reference=str(item["source_reference"]),
                    )
                )
            return result

    def resolve_canonical_vehicle_id(
        self, provider: str, source_vehicle_id: str, *, snapshot_id: str
    ) -> str | None:
        with self.session() as session:
            row = session.scalar(
                select(VehicleIdentityAliasRow).where(
                    VehicleIdentityAliasRow.snapshot_id == snapshot_id,
                    VehicleIdentityAliasRow.provider == provider,
                    VehicleIdentityAliasRow.source_vehicle_id == source_vehicle_id,
                    VehicleIdentityAliasRow.confirmed.is_(True),
                )
            )
            return None if row is None else row.canonical_vehicle_id

    def list_confirmed_identity_aliases(self, *, snapshot_id: str, provider: str) -> dict[str, str]:
        """Return active, provider-scoped aliases for import-time identity resolution."""

        with self.session() as session:
            rows = tuple(
                session.scalars(
                    select(VehicleIdentityAliasRow)
                    .where(
                        VehicleIdentityAliasRow.snapshot_id == snapshot_id,
                        VehicleIdentityAliasRow.provider == provider,
                        VehicleIdentityAliasRow.confirmed.is_(True),
                    )
                    .order_by(VehicleIdentityAliasRow.source_vehicle_id)
                )
            )
        return {row.source_vehicle_id: row.canonical_vehicle_id for row in rows}

    def import_research_graph_snapshot(
        self,
        snapshot: SnapshotRef,
        edges: Iterable[ResearchEdge | Mapping[str, Any] | object],
        *,
        raw_content: bytes | str,
        media_type: str = "application/json",
        provider_version: str | None = None,
        notes: str | None = None,
    ) -> ImportResult:
        normalized = tuple(_mapping(item) for item in edges)
        with self.session() as session:
            result = self._begin_component_import(
                session,
                snapshot,
                expected_type=DatasetType.RESEARCH_GRAPH,
                raw_content=raw_content,
                media_type=media_type,
                provider_version=provider_version,
                notes=notes,
            )
            if not result.created:
                return result
            known_ids = self._known_vehicle_ids(session)
            for item in normalized:
                parent_id = str(item["parent_vehicle_id"])
                child_id = str(item["child_vehicle_id"])
                self._validate_known_vehicle(known_ids, parent_id)
                self._validate_known_vehicle(known_ids, child_id)
                semantics = item.get("prerequisite_semantics", item.get("group_semantics", "all"))
                session.add(
                    ResearchGraphEdgeRow(
                        snapshot_id=snapshot.snapshot_id,
                        nation=_enum_value(item["nation"]),
                        domain=_enum_value(item["domain"]),
                        parent_vehicle_id=parent_id,
                        child_vehicle_id=child_id,
                        edge_type=_enum_value(item["edge_type"]),
                        prerequisite_group=str(item["prerequisite_group"]),
                        group_semantics=_enum_value(semantics),
                    )
                )
            return result

    def list_research_graph_edges(self, *, snapshot_id: str) -> tuple[ResearchEdge, ...]:
        with self.session() as session:
            rows = tuple(
                session.scalars(
                    select(ResearchGraphEdgeRow)
                    .where(ResearchGraphEdgeRow.snapshot_id == snapshot_id)
                    .order_by(
                        ResearchGraphEdgeRow.child_vehicle_id,
                        ResearchGraphEdgeRow.prerequisite_group,
                        ResearchGraphEdgeRow.parent_vehicle_id,
                    )
                )
            )
        return tuple(
            ResearchEdge(
                nation=Nation(row.nation),
                domain=ResearchDomain(row.domain),
                parent_vehicle_id=row.parent_vehicle_id,
                child_vehicle_id=row.child_vehicle_id,
                edge_type=ResearchEdgeType(row.edge_type),
                prerequisite_group=row.prerequisite_group,
                prerequisite_semantics=PrerequisiteSemantics(row.group_semantics),
                snapshot_id=row.snapshot_id,
            )
            for row in rows
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
        diagnostics: Mapping[str, Any] | object | None = None,
    ) -> ImportResult:
        if snapshot.dataset_type is not DatasetType.GLOBAL_STATISTICS:
            raise ValueError("statistics import requires a global-statistics snapshot")
        content = self._validated_raw_content(snapshot, raw_content)
        normalized: list[tuple[VehicleStatistics, str | None]] = []
        for item in statistics:
            values = _mapping(item)
            ratio_provenance = values.pop("ratio_provenance", None)
            values["snapshot_id"] = snapshot.snapshot_id
            normalized.append(
                (
                    VehicleStatistics.model_validate(values),
                    None if ratio_provenance is None else _enum_value(ratio_provenance),
                )
            )

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
            for item, ratio_provenance in normalized:
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
                        reported_win_rate=item.reported_win_rate,
                        reported_kd=item.reported_kd,
                        reported_kills_per_battle=item.reported_kills_per_battle,
                        ratio_provenance=ratio_provenance,
                        kill_target_definition=item.kill_target_definition.value,
                    )
                )
            if diagnostics is not None:
                diagnostic = _mapping(diagnostics)
                session.add(
                    StatisticsImportDiagnosticRow(
                        snapshot_id=snapshot.snapshot_id,
                        recognized_columns=list(diagnostic.get("recognized_columns", ())),
                        unknown_columns=list(diagnostic.get("unknown_columns", ())),
                        matched_count=int(diagnostic.get("matched_count", len(normalized))),
                        unresolved_source_ids=list(diagnostic.get("unresolved_source_ids", ())),
                        duplicate_count=int(diagnostic.get("duplicate_count", 0)),
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
            provider_version=dataset.normalization_revision,
        )

    def get_statistics_import_diagnostics(
        self, snapshot_id: str
    ) -> StatisticsImportDiagnostics | None:
        with self.session() as session:
            row = session.get(StatisticsImportDiagnosticRow, snapshot_id)
            if row is None:
                return None
            return StatisticsImportDiagnostics(
                snapshot_id=row.snapshot_id,
                recognized_columns=tuple(row.recognized_columns),
                unknown_columns=tuple(row.unknown_columns),
                matched_count=row.matched_count,
                unresolved_source_ids=tuple(row.unresolved_source_ids),
                duplicate_count=row.duplicate_count,
            )

    def record_import_attempt(
        self,
        *,
        dataset_type: DatasetType | str,
        provider: str,
        raw_content: bytes | str,
        status: str,
        error: str | None,
        attempted_at: datetime | None = None,
        media_type: str = "application/json",
    ) -> ImportAttemptResult:
        content = raw_content.encode("utf-8") if isinstance(raw_content, str) else raw_content
        if len(content) > MAX_RAW_ARTIFACT_BYTES:
            raise ValueError("raw artifact exceeds configured byte limit")
        checksum = sha256(content).hexdigest()
        dataset_value = _enum_value(dataset_type)
        with self.session() as session:
            existing = session.scalar(
                select(ImportAttemptRow).where(
                    ImportAttemptRow.dataset_type == dataset_value,
                    ImportAttemptRow.provider == provider,
                    ImportAttemptRow.raw_checksum == checksum,
                    ImportAttemptRow.status == status,
                )
            )
            if existing is not None:
                return ImportAttemptResult(attempt_id=existing.attempt_id, created=False)
            row = ImportAttemptRow(
                dataset_type=dataset_value,
                provider=provider,
                attempted_at=attempted_at or datetime.now(UTC),
                status=status,
                error=error,
                raw_checksum=checksum,
                media_type=media_type,
                raw_content=content,
            )
            session.add(row)
            session.flush()
            return ImportAttemptResult(attempt_id=row.attempt_id, created=True)

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

    def component_compatibility(
        self, vehicle_snapshot: SnapshotRef, component_snapshot: SnapshotRef
    ) -> SnapshotCompatibility:
        """Validate purpose/key and canonical-ID subset for an optional component."""

        if component_snapshot.purpose is not vehicle_snapshot.purpose:
            return SnapshotCompatibility(status="incompatible", reason="snapshot purpose mismatch")
        if vehicle_snapshot.purpose is SnapshotPurpose.ACCEPTANCE:
            if (
                vehicle_snapshot.compatibility_key is None
                or component_snapshot.compatibility_key != vehicle_snapshot.compatibility_key
            ):
                return SnapshotCompatibility(
                    status="incompatible",
                    reason="acceptance snapshots are not members of the same frozen bundle",
                )
        elif (
            vehicle_snapshot.compatibility_key is not None
            or component_snapshot.compatibility_key is not None
        ) and component_snapshot.compatibility_key != vehicle_snapshot.compatibility_key:
            return SnapshotCompatibility(
                status="incompatible", reason="snapshot compatibility keys do not match"
            )
        vehicle_ids = {
            vehicle.vehicle_id
            for vehicle in self.list_vehicles(snapshot_id=vehicle_snapshot.snapshot_id)
        }
        with self.session() as session:
            model_and_columns: dict[DatasetType, tuple[type[Any], tuple[Any, ...]]] = {
                DatasetType.CAPABILITIES: (
                    CapabilityObservationRow,
                    (CapabilityObservationRow.vehicle_id,),
                ),
                DatasetType.AVAILABILITY: (
                    AvailabilityObservationRow,
                    (AvailabilityObservationRow.vehicle_id,),
                ),
                DatasetType.IDENTITY_ALIASES: (
                    VehicleIdentityAliasRow,
                    (VehicleIdentityAliasRow.canonical_vehicle_id,),
                ),
                DatasetType.RESEARCH_GRAPH: (
                    ResearchGraphEdgeRow,
                    (
                        ResearchGraphEdgeRow.parent_vehicle_id,
                        ResearchGraphEdgeRow.child_vehicle_id,
                    ),
                ),
            }
            definition = model_and_columns.get(component_snapshot.dataset_type)
            if definition is None:
                raise ValueError("unsupported component snapshot type")
            model, columns = definition
            referenced: set[str] = set()
            for column in columns:
                referenced.update(
                    session.scalars(
                        select(column).where(model.snapshot_id == component_snapshot.snapshot_id)
                    )
                )
        missing = tuple(sorted(referenced - vehicle_ids))
        if missing:
            return SnapshotCompatibility(
                status="incompatible",
                reason="component references canonical IDs absent from vehicle evidence",
                missing_vehicle_ids=missing,
            )
        return SnapshotCompatibility(status="compatible", reason="canonical identities compatible")

    def latest_compatible_component(
        self, vehicle_snapshot: SnapshotRef, dataset_type: DatasetType
    ) -> SnapshotRef | None:
        if dataset_type not in {
            DatasetType.CAPABILITIES,
            DatasetType.AVAILABILITY,
            DatasetType.IDENTITY_ALIASES,
            DatasetType.RESEARCH_GRAPH,
        }:
            raise ValueError("dataset type is not an optional evidence component")
        return next(
            (
                candidate
                for candidate in self.list_snapshots(dataset_type)
                if self.component_compatibility(vehicle_snapshot, candidate).compatible
            ),
            None,
        )

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
            capability_snapshot=self.latest_compatible_component(
                vehicle_snapshot, DatasetType.CAPABILITIES
            ),
            availability_snapshot=self.latest_compatible_component(
                vehicle_snapshot, DatasetType.AVAILABILITY
            ),
            identity_snapshot=self.latest_compatible_component(
                vehicle_snapshot, DatasetType.IDENTITY_ALIASES
            ),
            research_graph_snapshot=self.latest_compatible_component(
                vehicle_snapshot, DatasetType.RESEARCH_GRAPH
            ),
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
                    reported_win_rate=row.reported_win_rate,
                    reported_kd=row.reported_kd,
                    reported_kills_per_battle=row.reported_kills_per_battle,
                    kill_target_definition=KillTargetDefinition(row.kill_target_definition),
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
                    write_revision=0,
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
            write_revision=row.write_revision,
        )

    def set_vehicle_status(
        self,
        profile_id: str,
        vehicle_id: str,
        status: VehicleStatus | str,
        expected_revision: int | str | None = None,
        expected_write_revision: int | str | None = None,
    ) -> VehicleStatusChange:
        if expected_revision is not None and expected_write_revision is not None:
            raise ValueError("expected_revision and expected_write_revision are mutually exclusive")
        normalized_status = status if isinstance(status, VehicleStatus) else VehicleStatus(status)
        with self.session() as session:
            profile = session.get(UserProfileRow, profile_id)
            if profile is None:
                raise LookupError(f"unknown profile {profile_id!r}")
            if expected_write_revision is not None and profile.write_revision != int(
                str(expected_write_revision).removeprefix("write-rev-")
            ):
                raise ValueError("profile write revision changed")
            if session.get(VehicleRow, vehicle_id) is None:
                raise LookupError(f"unknown vehicle {vehicle_id!r}")
            current_statuses = {
                row.vehicle_id: VehicleStatus(row.status)
                for row in session.scalars(
                    select(UserVehicleStateRow).where(
                        UserVehicleStateRow.profile_id == profile_id,
                        UserVehicleStateRow.superseded.is_(False),
                    )
                )
            }
            if expected_revision is not None and stable_hash(
                dict(sorted(current_statuses.items()))
            ) != str(expected_revision):
                raise ValueError("profile revision changed")
            state = session.get(UserVehicleStateRow, (profile_id, vehicle_id))
            before = None if state is None else VehicleStatus(state.status)
            if before is normalized_status:
                return VehicleStatusChange(
                    before=before,
                    after=normalized_status,
                    profile_revision=profile.revision,
                    changed=False,
                )
            current_revision = profile.revision
            guarded_update = (
                update(UserProfileRow)
                .where(
                    UserProfileRow.profile_id == profile_id,
                    UserProfileRow.revision == current_revision,
                )
                .values(
                    revision=current_revision + 1,
                    write_revision=profile.write_revision + 1,
                )
            )
            update_result = session.execute(
                guarded_update.execution_options(synchronize_session=False)
            )
            if getattr(update_result, "rowcount", 0) != 1:
                raise ValueError("profile revision changed")
            profile.revision = current_revision + 1
            profile.write_revision += 1
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
                state.superseded = False
                state.superseded_at = None
                state.superseded_by_vehicle_id = None
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
                .where(
                    UserVehicleStateRow.profile_id == profile_id,
                    UserVehicleStateRow.superseded.is_(False),
                )
                .order_by(UserVehicleStateRow.vehicle_id)
            )
            return {row.vehicle_id: VehicleStatus(row.status) for row in rows}

    @staticmethod
    def _stored_evaluation(row: StoredEvaluationRow) -> StoredEvaluation:
        return StoredEvaluation(
            evaluation_id=row.evaluation_id,
            profile_id=row.profile_id,
            kind=row.kind,
            profile_revision=row.profile_revision,
            profile_state=dict(row.profile_state),
            effective_inputs=dict(row.effective_inputs),
            evidence_context=dict(row.evidence_context),
            ruleset_hash=row.ruleset_hash,
            schema_revision=row.schema_revision,
            result_id=row.result_id,
            result_payload=dict(row.result_payload),
            created_at=row.created_at,
        )

    def store_evaluation(
        self,
        *,
        evaluation_id: str,
        profile_id: str,
        kind: str,
        profile_revision: str,
        profile_state: dict[str, Any],
        effective_inputs: dict[str, Any],
        evidence_context: dict[str, Any],
        ruleset_hash: str,
        schema_revision: str,
        result_id: str,
        result_payload: dict[str, Any],
    ) -> StoredEvaluation:
        with self.session() as session:
            if session.get(UserProfileRow, profile_id) is None:
                raise LookupError(f"unknown profile {profile_id!r}")
            row = StoredEvaluationRow(
                evaluation_id=evaluation_id,
                profile_id=profile_id,
                kind=kind,
                profile_revision=profile_revision,
                profile_state=profile_state,
                effective_inputs=effective_inputs,
                evidence_context=evidence_context,
                ruleset_hash=ruleset_hash,
                schema_revision=schema_revision,
                result_id=result_id,
                result_payload=result_payload,
                created_at=datetime.now(UTC),
            )
            session.add(row)
            session.flush()
            return self._stored_evaluation(row)

    def get_stored_evaluation(self, profile_id: str, evaluation_id: str) -> StoredEvaluation | None:
        with self.session() as session:
            row = session.get(StoredEvaluationRow, evaluation_id)
            if row is None or row.profile_id != profile_id:
                return None
            return self._stored_evaluation(row)

    def capture_evaluation_snapshot(
        self, profile_id: str, *, preset_id: str | None = None
    ) -> EvaluationSnapshot:
        """Materialize mutable calculation inputs without crossing transactions."""

        with self.session() as session:
            profile_row = session.get(UserProfileRow, profile_id)
            if profile_row is None:
                raise LookupError(f"unknown profile {profile_id!r}")
            statuses = {
                row.vehicle_id: VehicleStatus(row.status)
                for row in session.scalars(
                    select(UserVehicleStateRow)
                    .where(
                        UserVehicleStateRow.profile_id == profile_id,
                        UserVehicleStateRow.superseded.is_(False),
                    )
                    .order_by(UserVehicleStateRow.vehicle_id)
                )
            }
            context_row = session.get(AdvisorContextRow, profile_id)
            context = (
                StoredAdvisorContext(profile_id, None, None, (), (), 0)
                if context_row is None
                else StoredAdvisorContext(
                    profile_id,
                    context_row.selected_preset_id,
                    context_row.target_br,
                    tuple(context_row.required_vehicle_ids),
                    tuple(context_row.excluded_vehicle_ids),
                    context_row.revision,
                )
            )
            selected_preset_id = preset_id or context.selected_preset_id
            preset_row = (
                None
                if selected_preset_id is None
                else session.get(LineupPresetRow, selected_preset_id)
            )
            if preset_row is not None and preset_row.profile_id != profile_id:
                preset_row = None
            if preset_id is not None and preset_row is None:
                raise LookupError("unknown preset")
            return EvaluationSnapshot(
                profile=self._stored_profile(profile_row),
                vehicle_statuses=statuses,
                context=context,
                preset=None if preset_row is None else self._preset(preset_row),
            )

    @staticmethod
    def _preset(row: LineupPresetRow) -> StoredPreset:
        return StoredPreset(
            row.preset_id,
            row.profile_id,
            row.name,
            tuple(row.slots),
            tuple(row.required_vehicle_ids),
            tuple(row.excluded_vehicle_ids),
            row.revision,
        )

    def create_preset(
        self,
        profile_id: str,
        *,
        preset_id: str,
        name: str,
        slots: tuple[str, ...],
        required_vehicle_ids: tuple[str, ...],
        excluded_vehicle_ids: tuple[str, ...],
    ) -> StoredPreset:
        normalized = " ".join(name.split()).casefold()
        if not normalized:
            raise ValueError("preset name must not be empty")
        with self.session() as session:
            if session.get(UserProfileRow, profile_id) is None:
                raise LookupError(f"unknown profile {profile_id!r}")
            now = datetime.now(UTC)
            row = LineupPresetRow(
                preset_id=preset_id,
                profile_id=profile_id,
                name=" ".join(name.split()),
                normalized_name=normalized,
                slots=list(slots),
                required_vehicle_ids=list(required_vehicle_ids),
                excluded_vehicle_ids=list(excluded_vehicle_ids),
                revision=1,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.flush()
            return self._preset(row)

    def list_presets(self, profile_id: str) -> tuple[StoredPreset, ...]:
        with self.session() as session:
            return tuple(
                self._preset(row)
                for row in session.scalars(
                    select(LineupPresetRow)
                    .where(LineupPresetRow.profile_id == profile_id)
                    .order_by(LineupPresetRow.name)
                )
            )

    def update_preset(
        self,
        profile_id: str,
        preset_id: str,
        *,
        name: str,
        slots: tuple[str, ...],
        required_vehicle_ids: tuple[str, ...],
        excluded_vehicle_ids: tuple[str, ...],
        expected_revision: int | str,
    ) -> StoredPreset:
        with self.session() as session:
            row = session.get(LineupPresetRow, preset_id)
            if row is None or row.profile_id != profile_id:
                raise LookupError("unknown preset")
            if row.revision != int(str(expected_revision).removeprefix("rev-")):
                raise ValueError("preset revision changed")
            normalized = " ".join(name.split()).casefold()
            if not normalized:
                raise ValueError("preset name must not be empty")
            row.name, row.normalized_name, row.slots = (
                " ".join(name.split()),
                normalized,
                list(slots),
            )
            row.required_vehicle_ids, row.excluded_vehicle_ids, row.revision, row.updated_at = (
                list(required_vehicle_ids),
                list(excluded_vehicle_ids),
                row.revision + 1,
                datetime.now(UTC),
            )
            return self._preset(row)

    def delete_preset(
        self, profile_id: str, preset_id: str, *, expected_revision: int | str
    ) -> None:
        with self.session() as session:
            row = session.get(LineupPresetRow, preset_id)
            if row is None or row.profile_id != profile_id:
                raise LookupError("unknown preset")
            if row.revision != int(str(expected_revision).removeprefix("rev-")):
                raise ValueError("preset revision changed")
            context = session.get(AdvisorContextRow, profile_id)
            if context is not None and context.selected_preset_id == preset_id:
                context.selected_preset_id = None
                context.revision += 1
            session.delete(row)

    def get_advisor_context(self, profile_id: str) -> StoredAdvisorContext:
        with self.session() as session:
            if session.get(UserProfileRow, profile_id) is None:
                raise LookupError(f"unknown profile {profile_id!r}")
            row = session.get(AdvisorContextRow, profile_id)
            if row is None:
                return StoredAdvisorContext(profile_id, None, None, (), (), 0)
            return StoredAdvisorContext(
                profile_id,
                row.selected_preset_id,
                row.target_br,
                tuple(row.required_vehicle_ids),
                tuple(row.excluded_vehicle_ids),
                row.revision,
            )

    def update_advisor_context(
        self,
        profile_id: str,
        *,
        selected_preset_id: str | None,
        target_br: int | None,
        required_vehicle_ids: tuple[str, ...],
        excluded_vehicle_ids: tuple[str, ...],
        expected_revision: int | str,
    ) -> StoredAdvisorContext:
        with self.session() as session:
            if session.get(UserProfileRow, profile_id) is None:
                raise LookupError(f"unknown profile {profile_id!r}")
            if selected_preset_id is not None:
                preset = session.get(LineupPresetRow, selected_preset_id)
                if preset is None or preset.profile_id != profile_id:
                    raise LookupError("unknown preset")
            row = session.get(AdvisorContextRow, profile_id)
            current = 0 if row is None else row.revision
            if current != int(str(expected_revision).removeprefix("rev-")):
                raise ValueError("advisor context revision changed")
            if row is None:
                row = AdvisorContextRow(
                    profile_id=profile_id,
                    selected_preset_id=selected_preset_id,
                    target_br=target_br,
                    required_vehicle_ids=list(required_vehicle_ids),
                    excluded_vehicle_ids=list(excluded_vehicle_ids),
                    revision=1,
                )
                session.add(row)
            else:
                (
                    row.selected_preset_id,
                    row.target_br,
                    row.required_vehicle_ids,
                    row.excluded_vehicle_ids,
                    row.revision,
                ) = (
                    selected_preset_id,
                    target_br,
                    list(required_vehicle_ids),
                    list(excluded_vehicle_ids),
                    current + 1,
                )
            return StoredAdvisorContext(
                profile_id,
                row.selected_preset_id,
                row.target_br,
                tuple(row.required_vehicle_ids),
                tuple(row.excluded_vehicle_ids),
                row.revision,
            )

    def reconcile_profile_aliases(
        self,
        profile_id: str,
        *,
        identity_snapshot_id: str,
        expected_revision: int | str,
        apply: bool = False,
    ) -> ProfileReconciliationOutcome:
        """Plan or atomically apply confirmed canonical-alias profile merges."""

        expected = int(str(expected_revision).removeprefix("rev-"))
        progression = {
            VehicleStatus.UNKNOWN: 0,
            VehicleStatus.LOCKED: 1,
            VehicleStatus.AVAILABLE_TO_RESEARCH: 2,
            VehicleStatus.RESEARCHING: 3,
            VehicleStatus.UNLOCKED_NOT_PURCHASED: 4,
            VehicleStatus.OWNED: 5,
        }
        with self.session() as session:
            profile = session.get(UserProfileRow, profile_id)
            if profile is None:
                raise LookupError(f"unknown profile {profile_id!r}")
            if profile.revision != expected:
                raise ValueError(
                    f"profile revision changed: expected {expected}, found {profile.revision}"
                )
            snapshot = session.get(DataSnapshotRow, identity_snapshot_id)
            if snapshot is None or snapshot.dataset_type != DatasetType.IDENTITY_ALIASES.value:
                raise LookupError(f"unknown identity snapshot {identity_snapshot_id!r}")
            aliases = tuple(
                session.scalars(
                    select(VehicleIdentityAliasRow)
                    .where(
                        VehicleIdentityAliasRow.snapshot_id == identity_snapshot_id,
                        VehicleIdentityAliasRow.confirmed.is_(True),
                        VehicleIdentityAliasRow.deprecated_vehicle_id.is_not(None),
                    )
                    .order_by(VehicleIdentityAliasRow.deprecated_vehicle_id)
                )
            )
            planned: list[
                tuple[ReconciliationItem, UserVehicleStateRow, UserVehicleStateRow | None]
            ] = []
            for alias in aliases:
                deprecated_id = alias.deprecated_vehicle_id
                if deprecated_id is None:
                    continue
                old_state = session.get(UserVehicleStateRow, (profile_id, deprecated_id))
                if old_state is None or old_state.superseded:
                    continue
                canonical_state = session.get(
                    UserVehicleStateRow, (profile_id, alias.canonical_vehicle_id)
                )
                old_status = VehicleStatus(old_state.status)
                current_status = (
                    VehicleStatus.UNKNOWN
                    if canonical_state is None or canonical_state.superseded
                    else VehicleStatus(canonical_state.status)
                )
                chosen = max((old_status, current_status), key=progression.__getitem__)
                item = ReconciliationItem(
                    deprecated_vehicle_id=deprecated_id,
                    canonical_vehicle_id=alias.canonical_vehicle_id,
                    deprecated_status=old_status,
                    canonical_status=current_status,
                    chosen_status=chosen,
                    reason="confirmed alias merged using most-progressed status",
                    collision=canonical_state is not None and old_status is not current_status,
                )
                planned.append((item, old_state, canonical_state))
            items = tuple(item for item, _, _ in planned)
            plan_payload = {
                "profile_id": profile_id,
                "expected_revision": str(expected),
                "alias_revision": identity_snapshot_id,
                "items": [item.model_dump(mode="json") for item in items],
            }
            plan = ReconciliationPlan(
                profile_id=profile_id,
                expected_revision=str(expected),
                alias_revision=identity_snapshot_id,
                items=items,
                plan_id=stable_hash(plan_payload),
                already_applied=not items,
            )
            if not apply or not planned:
                return ProfileReconciliationOutcome(
                    plan=plan,
                    applied=apply,
                    changed=False,
                    resulting_revision=profile.revision,
                )

            profile.revision += 1
            now = datetime.now(UTC)
            audit = ProfileReconciliationAuditRow(
                profile_id=profile_id,
                identity_snapshot_id=identity_snapshot_id,
                source_revision=expected,
                resulting_revision=profile.revision,
                applied_at=now,
            )
            session.add(audit)
            session.flush()
            for item, old_state, canonical_state in planned:
                if canonical_state is None:
                    canonical_state = UserVehicleStateRow(
                        profile_id=profile_id,
                        vehicle_id=item.canonical_vehicle_id,
                        status=item.chosen_status.value,
                        revision=profile.revision,
                        superseded=False,
                    )
                    session.add(canonical_state)
                else:
                    canonical_state.status = item.chosen_status.value
                    canonical_state.revision = profile.revision
                    canonical_state.superseded = False
                    canonical_state.superseded_at = None
                    canonical_state.superseded_by_vehicle_id = None
                old_state.superseded = True
                old_state.superseded_at = now
                old_state.superseded_by_vehicle_id = item.canonical_vehicle_id
                session.add(
                    ProfileReconciliationItemRow(
                        audit_id=audit.audit_id,
                        deprecated_vehicle_id=item.deprecated_vehicle_id,
                        canonical_vehicle_id=item.canonical_vehicle_id,
                        deprecated_status=item.deprecated_status.value,
                        canonical_status=item.canonical_status.value,
                        chosen_status=item.chosen_status.value,
                        conflict=item.collision,
                        reason=item.reason,
                    )
                )
            return ProfileReconciliationOutcome(
                plan=plan,
                applied=True,
                changed=True,
                resulting_revision=profile.revision,
            )
