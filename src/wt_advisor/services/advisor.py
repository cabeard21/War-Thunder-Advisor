"""Deterministic orchestration over fixture/provider evidence and pure rules."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime
from itertools import combinations
from pathlib import Path
from typing import Any

from wt_advisor.data.freshness import evaluate_freshness
from wt_advisor.data.imports.statistics import (
    MAX_IMPORT_BYTES,
    import_statistics_csv,
    import_statistics_json,
    inspect_statistics_csv,
    inspect_statistics_json,
)
from wt_advisor.data.models import RawDatasetMetadata, canonical_bytes
from wt_advisor.data.providers.fixture import FixtureProvider
from wt_advisor.data.providers.wt_vehicles_api import WarThunderVehiclesApiProvider
from wt_advisor.domain.models import (
    AcquisitionType,
    AdditionEvaluation,
    AvailabilityType,
    CandidateGroup,
    CandidateLineup,
    Capability,
    CapabilityObservation,
    CapabilityResolution,
    CapabilitySourceType,
    DatasetType,
    EvidenceContext,
    Freshness,
    GameMode,
    GenerationResult,
    Lineup,
    LineupAnalysis,
    LineupComparison,
    Nation,
    PrerequisiteSemantics,
    ReconciliationResult,
    Researchability,
    ResearchDomain,
    ResearchEdge,
    ResearchEdgeType,
    ResolvedAvailability,
    RuleStatus,
    SnapshotPurpose,
    SnapshotRef,
    StatisticsInspection,
    StatisticsScope,
    TreeMembership,
    UnlockEvaluation,
    UserProfile,
    UserProgress,
    Vehicle,
    VehicleClass,
    VehicleStatistics,
    VehicleStatus,
    VehicleStatusChange,
    VehicleView,
    Visibility,
    stable_hash,
)
from wt_advisor.overrides.loader import load_overrides
from wt_advisor.rules import (
    PeerStatistic,
    Ruleset,
    evaluate_lineup_rules,
    infer_roles,
    load_ruleset,
)
from wt_advisor.storage import EvidenceRepository, create_database
from wt_advisor.storage.repository import ResolvedAnalysisBundle, SnapshotCompatibility

MAX_COMBINATIONS = 500_000
FROZEN_ACCEPTANCE_AS_OF = date(2026, 9, 19)


class AdvisorService:
    """In-process application boundary used by both transports."""

    def __init__(
        self,
        *,
        vehicles: Sequence[Vehicle],
        battle_ratings: dict[str, int],
        statistics: Sequence[VehicleStatistics],
        profile: UserProfile,
        vehicle_statuses: dict[str, VehicleStatus],
        research_edges: Sequence[Any],
        evidence_context: EvidenceContext,
        resolved_availability: dict[str, ResolvedAvailability] | None = None,
        capability_resolutions: Mapping[str, Sequence[CapabilityResolution]] | None = None,
        battle_rating_provenance: dict[str, dict[str, Any]] | None = None,
        ruleset: Ruleset | None = None,
        repository: EvidenceRepository | None = None,
        bundle_resolution: ResolvedAnalysisBundle | None = None,
        service_warnings: tuple[str, ...] = (),
        evaluation_date: date | None = None,
    ) -> None:
        self._vehicles = {vehicle.vehicle_id: vehicle for vehicle in vehicles}
        self._battle_ratings = dict(battle_ratings)
        statistics_by_vehicle: dict[str, list[VehicleStatistics]] = defaultdict(list)
        for row in statistics:
            statistics_by_vehicle[row.vehicle_id].append(row)
        self._statistics = {
            vehicle_id: tuple(sorted(rows, key=lambda item: item.mode_scope.value))
            for vehicle_id, rows in statistics_by_vehicle.items()
        }
        self._profile = profile
        self._statuses = dict(vehicle_statuses)
        self._research_edges = tuple(research_edges)
        self._ruleset = ruleset or load_ruleset()
        self._evaluation_date = evaluation_date or date.today()
        self._evidence_context = evidence_context.model_copy(
            update={
                "snapshots": tuple(
                    _refresh_snapshot(snapshot, as_of=self._evaluation_date)
                    for snapshot in evidence_context.snapshots
                )
            }
        )
        self._resolved_availability = dict(resolved_availability or {})
        self._capability_resolutions = {
            vehicle_id: tuple(resolutions)
            for vehicle_id, resolutions in (capability_resolutions or {}).items()
        }
        self._battle_rating_provenance = battle_rating_provenance or {}
        self._repository = repository
        self._bundle_resolution = bundle_resolution
        self._service_warnings = service_warnings

    @classmethod
    def from_acceptance_fixture(cls) -> AdvisorService:
        bundle = FixtureProvider().load()
        vehicles = tuple(
            Vehicle(
                vehicle_id=row.vehicle_id,
                source_vehicle_id=row.source_vehicle_id,
                name=row.name,
                nation=row.nation,
                vehicle_class=row.vehicle_class,
                rank=row.rank,
                research_parent_id=row.research_parent_id,
                research_cost=row.research_cost,
                purchase_cost=row.purchase_cost,
                availability_type=row.availability_type,
                capabilities=row.capabilities,
            )
            for row in bundle.vehicles.records
        )
        ruleset = load_ruleset("m1-baseline-v1")
        evidence = EvidenceContext(
            snapshots=(bundle.vehicles.snapshot, bundle.statistics.snapshot),
            override_revision="m1-no-curated-corrections",
            ruleset_hash=ruleset.content_hash,
            schema_revision="0001",
        )
        return cls(
            vehicles=vehicles,
            battle_ratings=bundle.battle_ratings,
            statistics=bundle.statistics.records,
            profile=bundle.user_profile,
            vehicle_statuses={row.vehicle_id: row.status for row in bundle.user_states},
            research_edges=bundle.research_edges,
            evidence_context=evidence,
            battle_rating_provenance={
                vehicle.vehicle_id: {
                    "value": bundle.battle_ratings[vehicle.vehicle_id],
                    "imported_value": bundle.battle_ratings[vehicle.vehicle_id],
                    "source": "snapshot",
                    "snapshot_id": bundle.vehicles.snapshot.snapshot_id,
                    "imported_source": bundle.vehicles.snapshot.provider,
                    "override_revision": None,
                }
                for vehicle in vehicles
            },
            ruleset=ruleset,
            evaluation_date=FROZEN_ACCEPTANCE_AS_OF,
        )

    @classmethod
    def from_m2_acceptance_fixture(
        cls,
        *,
        owned: frozenset[str] | None = None,
        researching: frozenset[str] = frozenset(),
    ) -> AdvisorService:
        """Build a network-free M2 service with independently versioned evidence."""

        bundle = FixtureProvider().load()
        scenario_fixture = FixtureProvider.load_m2_scenarios()
        vehicles = tuple(
            Vehicle(
                vehicle_id=row.vehicle_id,
                source_vehicle_id=row.source_vehicle_id,
                name=row.name,
                nation=row.nation,
                vehicle_class=row.vehicle_class,
                rank=row.rank,
                research_parent_id=row.research_parent_id,
                research_cost=row.research_cost,
                purchase_cost=row.purchase_cost,
                availability_type=row.availability_type,
                capabilities=frozenset(),
            )
            for row in bundle.vehicles.records
        )
        retrieved_at = datetime(2026, 9, 1, 12, tzinfo=UTC)
        # These M2 components intentionally enrich the frozen M1 vehicle fixture.
        compatibility_key = "usa-ground-rb-m1-acceptance"
        capability_rows = tuple(scenario_fixture["capability_observations"])
        capability_content = canonical_bytes(capability_rows)
        capability_snapshot = RawDatasetMetadata(
            snapshot_id="fixture-usa-ground-rb-capabilities-m2",
            dataset_type=DatasetType.CAPABILITIES,
            provider="committed_acceptance_fixture",
            retrieved_at=retrieved_at,
            source_revision=str(scenario_fixture["revision"]),
            purpose=SnapshotPurpose.ACCEPTANCE,
            compatibility_key=compatibility_key,
        ).snapshot(capability_content, Freshness.FRESH)
        grouped_capabilities: dict[str, list[CapabilityObservation]] = defaultdict(list)
        for raw in capability_rows:
            observation = CapabilityObservation(
                vehicle_id=str(raw["vehicle_id"]),
                capability=Capability(str(raw["capability"])),
                value=True,
                source_provider="committed_acceptance_fixture",
                source_snapshot_id=capability_snapshot.snapshot_id,
                source_reference=str(raw["source_reference"]),
                source_type=CapabilitySourceType.CURATED_IMPORT,
                confidence=1.0,
            )
            grouped_capabilities[observation.vehicle_id].append(observation)
        capability_resolutions = {
            vehicle_id: tuple(
                CapabilityResolution.from_observations(
                    vehicle_id,
                    capability,
                    tuple(row for row in rows if row.capability is capability),
                )
                for capability in sorted(
                    {row.capability for row in rows}, key=lambda item: item.value
                )
            )
            for vehicle_id, rows in grouped_capabilities.items()
        }

        availability_content = canonical_bytes(
            [
                {
                    "vehicle_id": row.vehicle_id,
                    "availability_type": row.availability_type.value,
                }
                for row in bundle.vehicles.records
            ]
        )
        availability_snapshot = RawDatasetMetadata(
            snapshot_id="fixture-usa-ground-rb-availability-m2",
            dataset_type=DatasetType.AVAILABILITY,
            provider="committed_acceptance_fixture",
            retrieved_at=retrieved_at,
            source_revision=str(scenario_fixture["revision"]),
            purpose=SnapshotPurpose.ACCEPTANCE,
            compatibility_key=compatibility_key,
        ).snapshot(availability_content, Freshness.FRESH)
        resolved_availability = {
            row.vehicle_id: ResolvedAvailability(
                vehicle_id=row.vehicle_id,
                acquisition_type=(
                    AcquisitionType.RESEARCH
                    if row.availability_type is AvailabilityType.RESEARCH_TREE
                    else AcquisitionType(row.availability_type.value)
                ),
                researchability=(
                    Researchability.NORMALLY_RESEARCHABLE
                    if row.availability_type is AvailabilityType.RESEARCH_TREE
                    else Researchability.NON_RESEARCHABLE
                ),
                tree_membership=(
                    TreeMembership.MAIN_TREE
                    if row.availability_type is AvailabilityType.RESEARCH_TREE
                    else TreeMembership.NONE
                ),
                visibility=Visibility.VISIBLE,
                source_snapshot_id=availability_snapshot.snapshot_id,
                source_provider="committed_acceptance_fixture",
                source_reference=f"acceptance:{row.vehicle_id}#availability",
                reason="frozen advisor-quality acceptance classification",
                provider_observation=row.availability_type.value,
            )
            for row in bundle.vehicles.records
        }

        graph_content = canonical_bytes(bundle.research_edges)
        graph_snapshot = RawDatasetMetadata(
            snapshot_id="fixture-usa-ground-rb-research-graph-m2",
            dataset_type=DatasetType.RESEARCH_GRAPH,
            provider="committed_acceptance_fixture",
            retrieved_at=retrieved_at,
            source_revision=str(scenario_fixture["revision"]),
            purpose=SnapshotPurpose.ACCEPTANCE,
            compatibility_key=compatibility_key,
        ).snapshot(graph_content, Freshness.FRESH)
        research_edges = tuple(
            ResearchEdge(
                nation=Nation.USA,
                domain=ResearchDomain.GROUND,
                parent_vehicle_id=parent_id,
                child_vehicle_id=child_id,
                edge_type=ResearchEdgeType.REQUIRED_PREDECESSOR,
                prerequisite_group=f"required:{child_id}",
                prerequisite_semantics=PrerequisiteSemantics.ALL,
                snapshot_id=graph_snapshot.snapshot_id,
            )
            for parent_id, child_id in bundle.research_edges
        )
        selected_owned = (
            frozenset(
                row.vehicle_id
                for row in bundle.user_states
                if row.status is VehicleStatus.OWNED
            )
            if owned is None
            else owned
        )
        statuses = {
            vehicle.vehicle_id: (
                VehicleStatus.OWNED
                if vehicle.vehicle_id in selected_owned
                else (
                    VehicleStatus.RESEARCHING
                    if vehicle.vehicle_id in researching
                    else VehicleStatus.LOCKED
                )
            )
            for vehicle in vehicles
        }
        ruleset = load_ruleset("m2-capability-aware-v1")
        return cls(
            vehicles=vehicles,
            battle_ratings=bundle.battle_ratings,
            statistics=bundle.statistics.records,
            profile=bundle.user_profile,
            vehicle_statuses=statuses,
            research_edges=research_edges,
            evidence_context=EvidenceContext(
                snapshots=(
                    bundle.vehicles.snapshot,
                    capability_snapshot,
                    availability_snapshot,
                    graph_snapshot,
                    bundle.statistics.snapshot,
                ),
                override_revision="m2-no-curated-corrections",
                ruleset_hash=ruleset.content_hash,
                schema_revision="0004",
            ),
            resolved_availability=resolved_availability,
            capability_resolutions=capability_resolutions,
            ruleset=ruleset,
            evaluation_date=FROZEN_ACCEPTANCE_AS_OF,
        )

    @classmethod
    def from_database(cls, database: str | Path = "wt-advisor.sqlite") -> AdvisorService:
        """Create or open a durable local database seeded by the acceptance fixture."""

        bundle = FixtureProvider().load()
        repository = EvidenceRepository(create_database(database))
        repository.import_vehicle_dataset(bundle.vehicles)
        repository.import_statistics_dataset(bundle.statistics)
        overrides = load_overrides(
            Path(__file__).resolve().parents[1] / "overrides" / "acceptance.yaml"
        )
        repository.import_override_revision(
            revision=overrides.revision,
            entries=(entry.model_dump(mode="json") for entry in overrides.entries),
        )
        creation = repository.create_profile(bundle.user_profile)
        if creation.created:
            for item in bundle.user_states:
                repository.set_vehicle_status(
                    bundle.user_profile.profile_id, item.vehicle_id, item.status
                )
        resolution = repository.resolve_analysis_bundle()
        vehicle_snapshot = resolution.vehicle_snapshot
        statistics_snapshot = resolution.statistics_snapshot
        vehicles = repository.list_vehicles(snapshot_id=vehicle_snapshot.snapshot_id)
        resolved_battle_ratings = {
            vehicle.vehicle_id: repository.resolve_battle_rating(
                vehicle.vehicle_id,
                bundle.user_profile.preferred_mode,
                snapshot_id=vehicle_snapshot.snapshot_id,
                override_revision=overrides.revision,
            )
            for vehicle in vehicles
        }
        battle_ratings = {
            vehicle_id: resolved.value
            for vehicle_id, resolved in resolved_battle_ratings.items()
        }
        statistics = (
            ()
            if statistics_snapshot is None
            else tuple(
                row
                for vehicle in vehicles
                for row in repository.get_vehicle_statistics(
                    vehicle.vehicle_id, snapshot_id=statistics_snapshot.snapshot_id
                )
            )
        )
        capability_resolutions = (
            {}
            if resolution.capability_snapshot is None
            else {
                vehicle.vehicle_id: tuple(
                    repository.resolve_capabilities(
                        vehicle.vehicle_id,
                        snapshot_id=resolution.capability_snapshot.snapshot_id,
                    ).values()
                )
                for vehicle in vehicles
            }
        )
        resolved_availability = (
            {}
            if resolution.availability_snapshot is None
            else {
                vehicle.vehicle_id: availability
                for vehicle in vehicles
                if (
                    availability := repository.get_resolved_availability(
                        vehicle.vehicle_id,
                        snapshot_id=resolution.availability_snapshot.snapshot_id,
                    )
                )
                is not None
            }
        )
        research_edges: Sequence[Any] = (
            repository.list_research_edges(snapshot_id=vehicle_snapshot.snapshot_id)
            if resolution.research_graph_snapshot is None
            else repository.list_research_graph_edges(
                snapshot_id=resolution.research_graph_snapshot.snapshot_id
            )
        )
        active_snapshots = tuple(
            _refresh_snapshot(snapshot, as_of=date.today())
            for snapshot in (
                vehicle_snapshot,
                resolution.capability_snapshot,
                resolution.availability_snapshot,
                resolution.identity_snapshot,
                resolution.research_graph_snapshot,
                statistics_snapshot,
            )
            if snapshot is not None
        )
        service_warnings = tuple(
            dict.fromkeys((*resolution.warnings, *_freshness_warnings(active_snapshots)))
        )
        ruleset = load_ruleset("m2-capability-aware-v1")
        return cls(
            vehicles=vehicles,
            battle_ratings=battle_ratings,
            statistics=statistics,
            profile=bundle.user_profile,
            vehicle_statuses=repository.get_user_vehicle_states(bundle.user_profile.profile_id),
            research_edges=research_edges,
            evidence_context=EvidenceContext(
                snapshots=active_snapshots,
                override_revision=overrides.revision,
                ruleset_hash=ruleset.content_hash,
                schema_revision="0004",
            ),
            resolved_availability=resolved_availability,
            capability_resolutions=capability_resolutions,
            battle_rating_provenance={
                vehicle_id: {
                    "value": resolved.value,
                    "imported_value": resolved.imported_value,
                    "source": resolved.source,
                    "snapshot_id": resolved.snapshot_id,
                    "imported_source": resolved.imported_source,
                    "override_revision": resolved.override_revision,
                    "override_reason": resolved.override_reason,
                    "override_reference": resolved.override_reference,
                }
                for vehicle_id, resolved in resolved_battle_ratings.items()
            },
            ruleset=ruleset,
            repository=repository,
            bundle_resolution=resolution,
            service_warnings=service_warnings,
        )

    @property
    def evidence_context(self) -> EvidenceContext:
        return self._evidence_context

    def _resolve_profile_id(self, profile_id: str) -> str:
        if profile_id in {"acceptance", self._profile.profile_id}:
            return self._profile.profile_id
        raise ValueError(f"unknown profile: {profile_id}")

    def data_status(self) -> dict[str, Any]:
        vehicle_snapshot_id = self._vehicle_snapshot_id()
        vehicle_snapshot = next(
            snapshot
            for snapshot in self._evidence_context.snapshots
            if snapshot.dataset_type is DatasetType.VEHICLE_METADATA
        )
        statistics_snapshot = next(
            (
                snapshot
                for snapshot in self._evidence_context.snapshots
                if snapshot.dataset_type is DatasetType.GLOBAL_STATISTICS
            ),
            None,
        )
        if self._bundle_resolution is None:
            newest_vehicle = vehicle_snapshot
            newest_statistics = statistics_snapshot
            compatibility = SnapshotCompatibility(
                status="compatible" if statistics_snapshot is not None else "unavailable",
                reason=(
                    "frozen acceptance snapshots share an explicit bundle key"
                    if statistics_snapshot is not None
                    else "no statistics snapshot resolved"
                ),
            )
        else:
            newest_vehicle = _refresh_snapshot(
                self._bundle_resolution.newest_vehicle_snapshot,
                as_of=self._evaluation_date,
            )
            newest_statistics = self._bundle_resolution.newest_statistics_snapshot
            if newest_statistics is not None:
                newest_statistics = _refresh_snapshot(
                    newest_statistics, as_of=self._evaluation_date
                )
            compatibility = self._bundle_resolution.newest_compatibility
        statistics_snapshot_id = (
            None if statistics_snapshot is None else statistics_snapshot.snapshot_id
        )
        capability_snapshot = (
            _snapshot_of_type(self._evidence_context, DatasetType.CAPABILITIES)
            if self._bundle_resolution is None
            else self._bundle_resolution.capability_snapshot
        )
        availability_snapshot = (
            _snapshot_of_type(self._evidence_context, DatasetType.AVAILABILITY)
            if self._bundle_resolution is None
            else self._bundle_resolution.availability_snapshot
        )
        identity_snapshot = (
            _snapshot_of_type(self._evidence_context, DatasetType.IDENTITY_ALIASES)
            if self._bundle_resolution is None
            else self._bundle_resolution.identity_snapshot
        )
        graph_snapshot = (
            (
                _snapshot_of_type(self._evidence_context, DatasetType.RESEARCH_GRAPH)
                or vehicle_snapshot
            )
            if self._bundle_resolution is None and self._research_edges
            else (
                None
                if self._bundle_resolution is None
                else self._bundle_resolution.research_graph_snapshot
            )
        )
        components = {
            DatasetType.VEHICLE_METADATA.value: _component_status(
                DatasetType.VEHICLE_METADATA,
                vehicle_snapshot,
                total_count=len(self._vehicles),
                covered_count=len(self._vehicles),
                as_of=self._evaluation_date,
            ),
            DatasetType.CAPABILITIES.value: _component_status(
                DatasetType.CAPABILITIES,
                capability_snapshot,
                total_count=len(self._vehicles),
                covered_count=len(self._capability_resolutions),
                as_of=self._evaluation_date,
                reason=(
                    "capability evidence is embedded in the frozen Milestone 1 fixture"
                    if capability_snapshot is None and self._bundle_resolution is None
                    else None
                ),
            ),
            DatasetType.AVAILABILITY.value: _component_status(
                DatasetType.AVAILABILITY,
                availability_snapshot,
                total_count=len(self._vehicles),
                covered_count=len(self._resolved_availability),
                as_of=self._evaluation_date,
            ),
            DatasetType.IDENTITY_ALIASES.value: _component_status(
                DatasetType.IDENTITY_ALIASES,
                identity_snapshot,
                total_count=len(self._vehicles),
                covered_count=0,
                as_of=self._evaluation_date,
            ),
            DatasetType.RESEARCH_GRAPH.value: _component_status(
                DatasetType.RESEARCH_GRAPH,
                graph_snapshot,
                total_count=len(self._vehicles),
                covered_count=len(
                    {
                        _research_edge_parts(edge)[1]
                        for edge in self._research_edges
                    }
                ),
                available=bool(self._research_edges),
                as_of=self._evaluation_date,
            ),
            DatasetType.GLOBAL_STATISTICS.value: _component_status(
                DatasetType.GLOBAL_STATISTICS,
                statistics_snapshot,
                newest_snapshot=newest_statistics,
                total_count=len(self._vehicles),
                covered_count=len(self._statistics),
                reason=compatibility.reason,
                as_of=self._evaluation_date,
            ),
        }
        active_component_ids = {
            name: item["selected_snapshot_id"]
            for name, item in components.items()
            if item["selected_snapshot_id"] is not None
        }
        return {
            "schema_revision": self._evidence_context.schema_revision,
            "override_revision": self._evidence_context.override_revision,
            "ruleset_hash": self._evidence_context.ruleset_hash,
            "snapshots": [
                item.model_dump(mode="json") for item in self._evidence_context.snapshots
            ],
            "newest_stored": {
                "vehicle_snapshot": newest_vehicle.model_dump(mode="json"),
                "statistics_snapshot": (
                    None
                    if newest_statistics is None
                    else newest_statistics.model_dump(mode="json")
                ),
            },
            "compatibility": {
                "status": compatibility.status,
                "compatible": compatibility.compatible,
                "reason": compatibility.reason,
                "missing_vehicle_ids": list(compatibility.missing_vehicle_ids),
            },
            "components": components,
            "active": {
                "bundle_id": stable_hash(
                    {
                        "component_snapshot_ids": active_component_ids,
                        "override_revision": self._evidence_context.override_revision,
                        "ruleset_hash": self._evidence_context.ruleset_hash,
                    }
                ),
                "selection_policy": (
                    "newest operational vehicle evidence with optional compatible statistics"
                ),
                "vehicle_snapshot_id": vehicle_snapshot_id,
                "vehicle_purpose": vehicle_snapshot.purpose.value,
                "battle_rating_snapshot_id": vehicle_snapshot_id,
                "tech_tree_snapshot_id": vehicle_snapshot_id,
                "statistics_snapshot_id": statistics_snapshot_id,
                "statistics_status": (
                    "available" if statistics_snapshot is not None else "unavailable"
                ),
                "statistics_period": {
                    "start": (
                        None
                        if statistics_snapshot is None
                        or statistics_snapshot.sample_start is None
                        else statistics_snapshot.sample_start.isoformat()
                    ),
                    "end": (
                        None
                        if statistics_snapshot is None or statistics_snapshot.sample_end is None
                        else statistics_snapshot.sample_end.isoformat()
                    ),
                },
                "statistics_provider": (
                    None if statistics_snapshot is None else statistics_snapshot.provider
                ),
                "research_graph_available": bool(self._research_edges),
                "capability_snapshot_id": (
                    None if capability_snapshot is None else capability_snapshot.snapshot_id
                ),
                "availability_snapshot_id": (
                    None
                    if availability_snapshot is None
                    else availability_snapshot.snapshot_id
                ),
                "identity_snapshot_id": (
                    None if identity_snapshot is None else identity_snapshot.snapshot_id
                ),
                "research_graph_snapshot_id": (
                    None if graph_snapshot is None else graph_snapshot.snapshot_id
                ),
                "warnings": list(self._service_warnings),
            },
        }

    def list_vehicles(
        self,
        *,
        nation: Nation = Nation.USA,
        mode: GameMode = GameMode.GROUND_REALISTIC,
        max_br: int | None = None,
    ) -> tuple[VehicleView, ...]:
        if mode is not GameMode.GROUND_REALISTIC:
            raise ValueError("Milestone 1 supports Ground Realistic only")
        views = (
            VehicleView(
                vehicle=vehicle,
                br=self._battle_ratings[vehicle.vehicle_id],
                roles=infer_roles(vehicle),
                provenance=self._vehicle_provenance(vehicle.vehicle_id),
            )
            for vehicle in self._vehicles.values()
            if vehicle.nation is nation
            and (max_br is None or self._battle_ratings[vehicle.vehicle_id] <= max_br)
        )
        return tuple(sorted(views, key=lambda item: (item.br, item.vehicle.vehicle_id)))

    def get_vehicle(self, vehicle_id: str) -> VehicleView:
        vehicle = self._require_vehicle(vehicle_id)
        return VehicleView(
            vehicle=vehicle,
            br=self._battle_ratings[vehicle_id],
            roles=infer_roles(vehicle),
            provenance=self._vehicle_provenance(vehicle_id),
        )

    def get_vehicle_statistics(self, vehicle_id: str) -> tuple[VehicleStatistics, ...]:
        self._require_vehicle(vehicle_id)
        return self._statistics.get(vehicle_id, ())

    def import_live_vehicles(self) -> SnapshotRef:
        """Persist bounded live metadata, capability, and prerequisite evidence."""

        if self._repository is None:
            raise ValueError("live import requires a database-backed service")
        bundle = WarThunderVehiclesApiProvider().fetch_operational_components(max_br=40)
        self._repository.import_vehicle_dataset(bundle.vehicles)
        self._repository.import_capability_snapshot(
            bundle.capabilities.snapshot,
            bundle.capabilities.records,
            raw_content=bundle.capabilities.raw_content,
        )
        self._repository.import_availability_snapshot(
            bundle.availability.snapshot,
            bundle.availability.records,
            raw_content=bundle.availability.raw_content,
        )
        self._repository.import_research_graph_snapshot(
            bundle.research_graph.snapshot,
            bundle.research_graph.records,
            raw_content=bundle.research_graph.raw_content,
        )
        return bundle.vehicles.snapshot

    def inspect_statistics(
        self,
        source: Path,
        *,
        provider: str,
        purpose: SnapshotPurpose = SnapshotPurpose.OPERATIONAL,
    ) -> StatisticsInspection:
        """Inspect a local statistics export without mutating evidence storage."""

        payload = _read_bounded_file(source, MAX_IMPORT_BYTES)
        return self._inspect_statistics_payload(
            source, payload, provider=provider, purpose=purpose
        )

    def _inspect_statistics_payload(
        self,
        source: Path,
        payload: bytes,
        *,
        provider: str,
        purpose: SnapshotPurpose,
    ) -> StatisticsInspection:
        metadata = RawDatasetMetadata(
            snapshot_id="statistics-inspection",
            dataset_type=DatasetType.GLOBAL_STATISTICS,
            provider=provider,
            retrieved_at=datetime.now(UTC),
            purpose=purpose,
        )
        identity_aliases = self._statistics_identity_aliases(provider)
        canonical_vehicle_ids = self._vehicles.keys()
        if source.suffix.lower() == ".json":
            return inspect_statistics_json(
                payload,
                metadata,
                identity_aliases=identity_aliases,
                canonical_vehicle_ids=canonical_vehicle_ids,
            )
        if source.suffix.lower() == ".csv":
            return inspect_statistics_csv(
                payload,
                metadata,
                identity_aliases=identity_aliases,
                canonical_vehicle_ids=canonical_vehicle_ids,
            )
        raise ValueError("statistics source must be a .json or .csv file")

    def import_statistics(
        self,
        source: Path,
        *,
        provider: str,
        purpose: SnapshotPurpose = SnapshotPurpose.OPERATIONAL,
    ) -> SnapshotRef:
        """Validate and persist one local statistics export."""

        if self._repository is None:
            raise ValueError("statistics import requires a database-backed service")
        payload = _read_bounded_file(source, MAX_IMPORT_BYTES)
        inspection = self._inspect_statistics_payload(
            source, payload, provider=provider, purpose=purpose
        )
        if not inspection.valid:
            error = "; ".join(inspection.errors)
            self._repository.record_import_attempt(
                dataset_type=DatasetType.GLOBAL_STATISTICS,
                provider=provider,
                raw_content=payload,
                status="rejected",
                error=error,
                media_type=_statistics_media_type(source),
            )
            raise ValueError("statistics import failed: " + error)
        metadata = RawDatasetMetadata(
            snapshot_id=inspection.prospective_snapshot_id,
            dataset_type=DatasetType.GLOBAL_STATISTICS,
            provider=provider,
            retrieved_at=datetime.now(UTC),
            sample_start=inspection.sample_start,
            sample_end=inspection.sample_end,
            purpose=purpose,
        )
        identity_aliases = self._statistics_identity_aliases(provider)
        canonical_vehicle_ids = self._vehicles.keys()
        try:
            if source.suffix.lower() == ".json":
                dataset = import_statistics_json(
                    payload,
                    metadata,
                    identity_aliases=identity_aliases,
                    canonical_vehicle_ids=canonical_vehicle_ids,
                )
            elif source.suffix.lower() == ".csv":
                dataset = import_statistics_csv(
                    payload,
                    metadata,
                    identity_aliases=identity_aliases,
                    canonical_vehicle_ids=canonical_vehicle_ids,
                )
            else:
                raise ValueError("statistics source must be a .json or .csv file")
            self._repository.import_statistics_dataset(dataset)
        except (TypeError, ValueError) as exc:
            self._repository.record_import_attempt(
                dataset_type=DatasetType.GLOBAL_STATISTICS,
                provider=provider,
                raw_content=payload,
                status="rejected",
                error=str(exc),
                media_type=_statistics_media_type(source),
            )
            raise
        return dataset.snapshot

    def _statistics_identity_aliases(self, provider: str) -> dict[str, str]:
        aliases = {
            vehicle.source_vehicle_id: vehicle.vehicle_id
            for vehicle in self._vehicles.values()
        }
        if (
            self._repository is not None
            and self._bundle_resolution is not None
            and self._bundle_resolution.identity_snapshot is not None
        ):
            aliases = {
                **aliases,
                **self._repository.list_confirmed_identity_aliases(
                    snapshot_id=self._bundle_resolution.identity_snapshot.snapshot_id,
                    provider=provider,
                ),
            }
        return aliases

    def get_user_progress(self, profile_id: str) -> UserProgress:
        self._resolve_profile_id(profile_id)
        if self._repository is not None:
            self._statuses = self._repository.get_user_vehicle_states(self._profile.profile_id)
        statuses = dict(sorted(self._statuses.items()))
        return UserProgress(
            profile=self._profile,
            vehicle_statuses=statuses,
            revision=stable_hash(statuses),
        )

    def set_user_vehicle_status(
        self, profile_id: str, vehicle_id: str, status: VehicleStatus
    ) -> VehicleStatusChange:
        self._resolve_profile_id(profile_id)
        self._require_vehicle(vehicle_id)
        before = self._statuses.get(vehicle_id, VehicleStatus.UNKNOWN)
        if self._repository is not None:
            self._repository.set_vehicle_status(self._profile.profile_id, vehicle_id, status)
            self._statuses = self._repository.get_user_vehicle_states(self._profile.profile_id)
        elif before is not status:
            self._statuses = {**self._statuses, vehicle_id: status}
        return VehicleStatusChange(
            profile_id=self._profile.profile_id,
            vehicle_id=vehicle_id,
            before_status=before,
            after_status=status,
            revision=stable_hash(dict(sorted(self._statuses.items()))),
        )

    def reconcile_profile(
        self,
        profile_id: str,
        *,
        apply: bool = False,
        expected_revision: str | None = None,
    ) -> ReconciliationResult:
        """Plan or transactionally apply confirmed canonical-ID aliases."""

        self._resolve_profile_id(profile_id)
        if self._repository is None or self._bundle_resolution is None:
            raise ValueError("profile reconciliation requires a database-backed service")
        identity_snapshot = self._bundle_resolution.identity_snapshot
        if identity_snapshot is None:
            raise ValueError("profile reconciliation requires active identity alias evidence")
        stored_profile = self._repository.get_profile(self._profile.profile_id)
        if stored_profile is None:
            raise ValueError(f"unknown profile: {profile_id}")
        visible_revision = self.get_user_progress(profile_id).revision
        if expected_revision is not None and expected_revision != visible_revision:
            raise ValueError("profile revision changed before reconciliation")
        outcome = self._repository.reconcile_profile_aliases(
            self._profile.profile_id,
            identity_snapshot_id=identity_snapshot.snapshot_id,
            expected_revision=stored_profile.revision,
            apply=apply,
        )
        if apply and outcome.applied:
            self._statuses = self._repository.get_user_vehicle_states(
                self._profile.profile_id
            )
        resulting_revision = stable_hash(dict(sorted(self._statuses.items())))
        public_plan_payload = {
            "profile_id": outcome.plan.profile_id,
            "expected_revision": visible_revision,
            "alias_revision": outcome.plan.alias_revision,
            "items": [item.model_dump(mode="json") for item in outcome.plan.items],
        }
        public_plan = outcome.plan.model_copy(
            update={
                "expected_revision": visible_revision,
                "plan_id": stable_hash(public_plan_payload),
            }
        )
        return ReconciliationResult(
            plan=public_plan,
            applied=outcome.applied,
            resulting_revision=resulting_revision,
        )

    def analyze_lineup(self, vehicle_ids: Sequence[str]) -> LineupAnalysis:
        if not vehicle_ids:
            raise ValueError("lineup must contain at least one vehicle")
        if len(vehicle_ids) > self._profile.crew_slots:
            raise ValueError("lineup exceeds configured crew slots")
        vehicles = tuple(self._require_vehicle(vehicle_id) for vehicle_id in vehicle_ids)
        if len({vehicle.vehicle_id for vehicle in vehicles}) != len(vehicles):
            raise ValueError("lineup vehicle IDs must be unique")
        if {vehicle.nation for vehicle in vehicles} != {self._profile.nation}:
            raise ValueError("all lineup vehicles must match the profile nation")
        resolved_brs = {
            vehicle.vehicle_id: self._battle_ratings[vehicle.vehicle_id]
            for vehicle in vehicles
        }
        lineup_br = max(resolved_brs.values())
        nearby_spaa = self._nearby_spaa(lineup_br)
        capability_evidence = (
            {
                vehicle_id: {
                    resolution.capability: resolution
                    for resolution in self._capability_resolutions.get(vehicle_id, ())
                }
                for vehicle_id in resolved_brs
            }
            if self._capability_resolutions
            else None
        )
        evaluation = evaluate_lineup_rules(
            vehicles,
            resolved_brs,
            statistics_by_vehicle=self._scoring_statistics(),
            peer_statistics=self._peer_statistics(),
            nearby_spaa=nearby_spaa,
            capability_resolutions=capability_evidence,
            ruleset=self._ruleset,
        )
        warnings = tuple(
            dict.fromkeys(
                (*self._service_warnings, *(w for rule in evaluation.rules for w in rule.warnings))
            )
        )
        anti_air = next(rule for rule in evaluation.rules if rule.rule == "anti_air")
        if anti_air.evidence.get("state") != "included":
            warnings = ("missing_effective_spaa", *warnings)
        strengths = tuple(rule.rule for rule in evaluation.rules if rule.status is RuleStatus.GOOD)
        request = {
            "lineup": list(vehicle_ids),
            "evidence": self._evidence_context.model_dump(mode="json"),
            "profile_revision": self.get_user_progress(self._profile.profile_id).revision,
        }
        return LineupAnalysis(
            lineup=Lineup(
                nation=self._profile.nation,
                mode=self._profile.preferred_mode,
                slots=tuple(vehicle_ids),
            ),
            resolved_brs=resolved_brs,
            lineup_br=lineup_br,
            rules=evaluation.rules,
            overall_score=evaluation.overall_score,
            warnings=warnings,
            strengths=strengths,
            readiness_passed=evaluation.readiness_passed,
            readiness_failures=evaluation.readiness_failures,
            evidence_context=self._evidence_context,
            analysis_id=stable_hash(request),
        )

    def generate_lineups(
        self,
        *,
        profile_id: str,
        target_br: int | None = None,
        top_n: int = 10,
        hypothetical_owned: frozenset[str] = frozenset(),
        required_vehicle_id: str | None = None,
        allow_partial: bool = False,
    ) -> GenerationResult:
        self._resolve_profile_id(profile_id)
        if top_n < 1 or top_n > 100:
            raise ValueError("top_n must be between 1 and 100")
        eligible = self._eligible_owned(hypothetical_owned)
        if required_vehicle_id is not None:
            self._require_vehicle(required_vehicle_id)
            if required_vehicle_id not in eligible:
                raise ValueError("required vehicle is not eligible under profile ownership policy")
        if not eligible:
            raise ValueError("no eligible owned vehicles")
        if len(eligible) < self._profile.crew_slots and not allow_partial:
            raise ValueError(
                "not enough eligible vehicles to fill crew slots; allow partial explicitly"
            )
        slot_count = min(self._profile.crew_slots, len(eligible))
        candidates_count = _combination_count(len(eligible), slot_count)
        if candidates_count > MAX_COMBINATIONS:
            raise ValueError("candidate combination guard exceeded")
        grouped: dict[int, list[CandidateLineup]] = defaultdict(list)
        for vehicle_ids in combinations(eligible, slot_count):
            if required_vehicle_id is not None and required_vehicle_id not in vehicle_ids:
                continue
            analysis = self.analyze_lineup(vehicle_ids)
            if target_br is not None and analysis.lineup_br != target_br:
                continue
            grouped[analysis.lineup_br].append(CandidateLineup(analysis=analysis))
        groups = tuple(
            CandidateGroup(
                lineup_br=br,
                candidates=tuple(_pareto_frontier(items)[:top_n]),
            )
            for br, items in sorted(grouped.items())
            if items
        )
        recommended = _recommended(groups)
        lower = _lower_alternative(groups, recommended, target_br=target_br)
        request = {
            "profile_id": self._profile.profile_id,
            "profile_revision": self.get_user_progress(profile_id).revision,
            "target_br": target_br,
            "top_n": top_n,
            "hypothetical_owned": sorted(hypothetical_owned),
            "required_vehicle_id": required_vehicle_id,
            "allow_partial": allow_partial,
            "groups": [group.model_dump(mode="json") for group in groups],
            "evidence": self._evidence_context.model_dump(mode="json"),
        }
        return GenerationResult(
            groups=groups,
            recommended=recommended,
            lower_br_alternative=lower,
            analysis_id=stable_hash(request),
            evidence_context=self._evidence_context,
        )

    def compare_lineups(
        self, lineup_a: Sequence[str], lineup_b: Sequence[str]
    ) -> LineupComparison:
        analysis_a = self.analyze_lineup(lineup_a)
        analysis_b = self.analyze_lineup(lineup_b)
        deltas = _component_deltas(analysis_a, analysis_b)
        cross_br = analysis_a.lineup_br != analysis_b.lineup_br
        request = {
            "a": analysis_a.analysis_id,
            "b": analysis_b.analysis_id,
            "evidence": self._evidence_context.model_dump(mode="json"),
        }
        return LineupComparison(
            lineup_a=analysis_a,
            lineup_b=analysis_b,
            component_deltas=deltas,
            cross_br=cross_br,
            overall_delta=None if cross_br else analysis_b.overall_score - analysis_a.overall_score,
            evidence_context=self._evidence_context,
            analysis_id=stable_hash(request),
        )

    def suggest_lineup_additions(
        self,
        *,
        profile_id: str,
        lineup: Sequence[str],
        hypothetical_owned: frozenset[str] = frozenset(),
    ) -> tuple[AdditionEvaluation, ...]:
        self._resolve_profile_id(profile_id)
        if len(lineup) >= self._profile.crew_slots:
            raise ValueError("lineup has no empty crew slot")
        before = self.analyze_lineup(lineup)
        eligible = set(self._eligible_owned(hypothetical_owned)) - set(lineup)
        results = []
        for vehicle_id in sorted(eligible):
            after = self.analyze_lineup((*lineup, vehicle_id))
            cross_br = before.lineup_br != after.lineup_br
            results.append(
                AdditionEvaluation(
                    vehicle_id=vehicle_id,
                    resulting_analysis=after,
                    component_deltas=_component_deltas(before, after),
                    br_delta=after.lineup_br - before.lineup_br,
                    cross_br=cross_br,
                    overall_delta=None if cross_br else after.overall_score - before.overall_score,
                    new_warnings=tuple(sorted(set(after.warnings) - set(before.warnings))),
                    resolved_warnings=tuple(sorted(set(before.warnings) - set(after.warnings))),
                )
            )
        return tuple(sorted(results, key=_addition_sort_key))

    def evaluate_next_unlocks(self, profile_id: str) -> tuple[UnlockEvaluation, ...]:
        self._resolve_profile_id(profile_id)
        before_result = self.generate_lineups(profile_id=profile_id, top_n=1)
        before_candidate = _best_available(before_result)
        if before_candidate is None:
            raise ValueError("no current readiness-passing lineup")
        output = []
        for vehicle_id in self._directly_researchable():
            expanded = self.generate_lineups(
                profile_id=profile_id,
                hypothetical_owned=frozenset({vehicle_id}),
                top_n=1,
            )
            forced = self.generate_lineups(
                profile_id=profile_id,
                hypothetical_owned=frozenset({vehicle_id}),
                required_vehicle_id=vehicle_id,
                top_n=1,
            )
            expanded_candidate = _best_available(expanded)
            forced_candidate = _best_available(forced)
            if expanded_candidate is None or forced_candidate is None:
                continue
            current = before_candidate
            best = expanded_candidate
            included = forced_candidate
            cross_br = current.analysis.lineup_br != best.analysis.lineup_br
            vehicle = self._vehicles[vehicle_id]
            prerequisites = self._research_prerequisites(vehicle_id)
            adopted = vehicle_id in best.analysis.lineup.slots
            readiness_changes = _readiness_changes(current.analysis, best.analysis)
            role_changes = _role_changes(
                current.analysis, best.analysis, self._vehicles
            )
            opens_new_frontier = (
                best.analysis.readiness_passed
                and best.analysis.lineup_br > current.analysis.lineup_br
            )
            improves_frontier = (
                best.analysis.lineup_br == current.analysis.lineup_br
                and (
                    (not current.analysis.readiness_passed and best.analysis.readiness_passed)
                    or best.analysis.overall_score > current.analysis.overall_score
                )
            )
            output.append(
                UnlockEvaluation(
                    vehicle_id=vehicle_id,
                    research_cost=vehicle.research_cost or 0,
                    before=current,
                    expanded_best=best,
                    forced_include=included,
                    adopted=adopted,
                    br_delta=best.analysis.lineup_br - current.analysis.lineup_br,
                    cross_br=cross_br,
                    overall_delta=(
                        None
                        if cross_br
                        else best.analysis.overall_score - current.analysis.overall_score
                    ),
                    component_deltas=_component_deltas(current.analysis, best.analysis),
                    current_status=self._statuses.get(vehicle_id, VehicleStatus.UNKNOWN),
                    research_prerequisites=prerequisites,
                    prerequisite_statuses={
                        parent: self._statuses.get(parent, VehicleStatus.UNKNOWN)
                        for parent in prerequisites
                    },
                    graph_snapshot_id=self._research_graph_snapshot_id(),
                    resulting_br=best.analysis.lineup_br,
                    readiness_before=current.analysis.readiness_passed,
                    readiness_expanded=best.analysis.readiness_passed,
                    readiness_forced=included.analysis.readiness_passed,
                    readiness_changes=readiness_changes,
                    role_changes=role_changes,
                    adopted_immediately=adopted,
                    opens_new_ready_frontier=opens_new_frontier,
                    improves_existing_frontier=improves_frontier,
                    fills_missing_role=any(
                        change.startswith("added:") for change in role_changes
                    ),
                    provides_stronger_backup=(
                        adopted
                        and not opens_new_frontier
                        and best.analysis.lineup.slots != current.analysis.lineup.slots
                    ),
                )
            )
        return tuple(sorted(output, key=lambda item: (item.research_cost, item.vehicle_id)))

    def evaluate_next_unlocks_status(self, profile_id: str) -> dict[str, Any]:
        """Return progression evaluations or a structured unavailable result."""

        try:
            evaluations = self.evaluate_next_unlocks(profile_id)
        except ValueError as exc:
            if "research graph" not in str(exc):
                raise
            return {
                "status": "unavailable",
                "evaluations": [],
                "warnings": ["progression_unavailable"],
                "reason": "active vehicle snapshot has no research graph evidence",
                "evidence_context": self._evidence_context.model_dump(mode="json"),
            }
        return {
            "status": "available",
            "evaluations": [item.model_dump(mode="json") for item in evaluations],
            "warnings": [],
            "reason": None,
            "evidence_context": self._evidence_context.model_dump(mode="json"),
        }

    def _eligible_owned(self, hypothetical: Iterable[str]) -> tuple[str, ...]:
        owned = {
            vehicle_id
            for vehicle_id, status in self._statuses.items()
            if status is VehicleStatus.OWNED
        } | set(hypothetical)
        eligible = []
        for vehicle_id in sorted(owned):
            vehicle = self._vehicles.get(vehicle_id)
            if vehicle is None:
                continue
            if vehicle.nation is not self._profile.nation:
                continue
            availability = self._resolved_availability.get(vehicle_id)
            acquisition_type = (
                availability.acquisition_type if availability is not None else None
            )
            if (
                (
                    acquisition_type is AcquisitionType.PREMIUM
                    or (
                        acquisition_type is None
                        and vehicle.availability_type is AvailabilityType.PREMIUM
                    )
                )
                and not self._profile.include_premiums
            ):
                continue
            if (
                (
                    acquisition_type is AcquisitionType.EVENT
                    or (
                        acquisition_type is None
                        and vehicle.availability_type is AvailabilityType.EVENT
                    )
                )
                and not self._profile.include_event_vehicles
            ):
                continue
            if (
                (
                    acquisition_type is AcquisitionType.PACK
                    or (
                        acquisition_type is None
                        and vehicle.availability_type is AvailabilityType.PACK
                    )
                )
                and not self._profile.include_pack_vehicles
            ):
                continue
            eligible.append(vehicle_id)
        return tuple(eligible)

    def _directly_researchable(self) -> tuple[str, ...]:
        if not self._research_edges:
            raise ValueError("active vehicle snapshot has no research graph evidence")
        candidates = {
            vehicle_id
            for vehicle_id, status in self._statuses.items()
            if vehicle_id in self._vehicles
            and status in {VehicleStatus.RESEARCHING, VehicleStatus.AVAILABLE_TO_RESEARCH}
            and self._is_normally_researchable(vehicle_id)
        }
        prerequisites: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
        for edge in self._research_edges:
            parent, child, group, requirement = _research_edge_parts(edge)
            prerequisites[child].append((parent, group, requirement))
        return tuple(
            sorted(
                vehicle_id
                for vehicle_id in candidates
                if _prerequisites_satisfied(
                    prerequisites.get(vehicle_id, ()), self._statuses
                )
            )
        )

    def _is_normally_researchable(self, vehicle_id: str) -> bool:
        if self._resolved_availability:
            resolved = self._resolved_availability.get(vehicle_id)
            return (
                resolved is not None
                and resolved.researchability is Researchability.NORMALLY_RESEARCHABLE
            )
        return (
            self._vehicles[vehicle_id].availability_type
            is AvailabilityType.RESEARCH_TREE
        )

    def _research_prerequisites(self, vehicle_id: str) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    parent
                    for edge in self._research_edges
                    for parent, child, _, _ in (_research_edge_parts(edge),)
                    if child == vehicle_id
                }
            )
        )

    def _research_graph_snapshot_id(self) -> str | None:
        for edge in self._research_edges:
            snapshot_id = getattr(edge, "snapshot_id", None)
            if snapshot_id is not None:
                return str(snapshot_id)
        return self._vehicle_snapshot_id() if self._research_edges else None

    def _nearby_spaa(self, lineup_br: int) -> tuple[tuple[Vehicle, VehicleStatus], ...]:
        return tuple(
            (vehicle, self._statuses.get(vehicle.vehicle_id, VehicleStatus.UNKNOWN))
            for vehicle in self._vehicles.values()
            if vehicle.vehicle_class is VehicleClass.SPAA
            and abs(self._battle_ratings[vehicle.vehicle_id] - lineup_br) <= 7
        )

    def _peer_statistics(self) -> tuple[PeerStatistic, ...]:
        return tuple(
            PeerStatistic(
                vehicle=self._vehicles[vehicle_id],
                battle_rating=self._battle_ratings[vehicle_id],
                statistics=statistics,
            )
            for vehicle_id, observations in self._statistics.items()
            for statistics in observations
            if statistics.mode_scope is StatisticsScope.GROUND_REALISTIC_GROUND_VEHICLES
        )

    def _scoring_statistics(self) -> dict[str, VehicleStatistics]:
        return {
            vehicle_id: statistics
            for vehicle_id, observations in self._statistics.items()
            for statistics in observations
            if statistics.mode_scope is StatisticsScope.GROUND_REALISTIC_GROUND_VEHICLES
        }

    def _require_vehicle(self, vehicle_id: str) -> Vehicle:
        try:
            return self._vehicles[vehicle_id]
        except KeyError as exc:
            raise ValueError(f"unknown vehicle: {vehicle_id}") from exc

    def _vehicle_snapshot_id(self) -> str:
        return next(
            snapshot.snapshot_id
            for snapshot in self._evidence_context.snapshots
            if snapshot.dataset_type is DatasetType.VEHICLE_METADATA
        )

    def _vehicle_provenance(self, vehicle_id: str) -> dict[str, Any]:
        provenance: dict[str, Any] = {
            "vehicle_snapshot_id": self._vehicle_snapshot_id(),
            "battle_rating": self._battle_rating_provenance.get(vehicle_id),
        }
        availability = self._resolved_availability.get(vehicle_id)
        if availability is not None:
            provenance["resolved_availability"] = availability.model_dump(mode="json")
        capabilities = self._capability_resolutions.get(vehicle_id)
        if capabilities is not None:
            provenance["capability_resolutions"] = [
                item.model_dump(mode="json") for item in capabilities
            ]
        return provenance


def _combination_count(pool: int, slots: int) -> int:
    if slots < 0 or slots > pool:
        return 0
    numerator = denominator = 1
    for value in range(1, slots + 1):
        numerator *= pool - slots + value
        denominator *= value
    return numerator // denominator


def _read_bounded_file(source: Path, maximum_bytes: int) -> bytes:
    """Read one local import safely, including a size check resilient to file changes."""

    try:
        if source.stat().st_size > maximum_bytes:
            raise ValueError("statistics import exceeds configured byte limit")
        with source.open("rb") as stream:
            payload = stream.read(maximum_bytes + 1)
    except OSError as exc:
        raise ValueError(f"unable to read statistics source: {exc}") from exc
    if len(payload) > maximum_bytes:
        raise ValueError("statistics import exceeds configured byte limit")
    return payload


def _statistics_media_type(source: Path) -> str:
    if source.suffix.lower() == ".json":
        return "application/json"
    if source.suffix.lower() == ".csv":
        return "text/csv"
    return "application/octet-stream"


def _research_edge_parts(edge: Any) -> tuple[str, str, str, str]:
    """Normalize legacy tuple edges and Milestone 2 typed graph edges."""

    if isinstance(edge, tuple):
        if len(edge) != 2:
            raise ValueError("legacy research edges must contain parent and child IDs")
        return str(edge[0]), str(edge[1]), "default", "all"
    parent = getattr(edge, "parent_vehicle_id", getattr(edge, "parent_id", None))
    child = getattr(edge, "child_vehicle_id", getattr(edge, "child_id", None))
    if parent is None or child is None:
        raise ValueError("research edge is missing parent or child identity")
    group = getattr(edge, "prerequisite_group", None) or "default"
    raw_requirement = getattr(
        edge,
        "prerequisite_semantics",
        getattr(
            edge,
            "group_semantics",
            getattr(edge, "requirement", getattr(edge, "requirement_type", "all")),
        ),
    )
    requirement = getattr(raw_requirement, "value", raw_requirement)
    if requirement not in {"all", "any"}:
        raise ValueError(f"unsupported prerequisite group semantics: {requirement!r}")
    return str(parent), str(child), str(group), str(requirement)


def _prerequisites_satisfied(
    prerequisites: Sequence[tuple[str, str, str]],
    statuses: dict[str, VehicleStatus],
) -> bool:
    if not prerequisites:
        return True
    grouped: dict[tuple[str, str], list[str]] = defaultdict(list)
    for parent, group, requirement in prerequisites:
        grouped[(group, requirement)].append(parent)
    for (_, requirement), parents in grouped.items():
        ownership = [statuses.get(parent) is VehicleStatus.OWNED for parent in parents]
        if requirement == "all" and not all(ownership):
            return False
        if requirement == "any" and not any(ownership):
            return False
    return True


def _refresh_snapshot(snapshot: SnapshotRef, *, as_of: date) -> SnapshotRef:
    observed = snapshot.sample_end or snapshot.retrieved_at.date()
    return snapshot.model_copy(
        update={"freshness": evaluate_freshness(snapshot.dataset_type, observed, as_of)}
    )


def _freshness_warnings(snapshots: Sequence[SnapshotRef]) -> tuple[str, ...]:
    warnings: list[str] = []
    for snapshot in snapshots:
        evidence_type = (
            "statistics"
            if snapshot.dataset_type is DatasetType.GLOBAL_STATISTICS
            else "vehicle_evidence"
        )
        if snapshot.freshness.value == "stale":
            warnings.append(f"{evidence_type}_stale")
        elif snapshot.freshness.value == "unknown":
            warnings.append(f"{evidence_type}_freshness_unknown")
    return tuple(warnings)


def _component_status(
    dataset_type: DatasetType,
    snapshot: SnapshotRef | None,
    *,
    total_count: int,
    covered_count: int,
    newest_snapshot: SnapshotRef | None = None,
    reason: str | None = None,
    available: bool | None = None,
    as_of: date,
) -> dict[str, Any]:
    is_available = snapshot is not None if available is None else available
    refreshed = None if snapshot is None else _refresh_snapshot(snapshot, as_of=as_of)
    if not is_available:
        status = "unavailable"
    elif refreshed is not None and refreshed.freshness.value == "stale":
        status = "stale"
    else:
        status = "available"
    newest_selected = newest_snapshot or snapshot
    return {
        "dataset_type": dataset_type.value,
        "status": status,
        "selected_snapshot_id": None if snapshot is None else snapshot.snapshot_id,
        "newest_snapshot_id": (
            None if newest_selected is None else newest_selected.snapshot_id
        ),
        "provider": None if snapshot is None else snapshot.provider,
        "purpose": None if snapshot is None else snapshot.purpose.value,
        "freshness": (
            "unknown" if refreshed is None else refreshed.freshness.value
        ),
        "compatible": is_available,
        "reason": reason,
        "total_count": total_count,
        "covered_count": covered_count,
        "coverage_percent": (
            0.0 if total_count == 0 else round(covered_count / total_count * 100, 2)
        ),
        "gaps": [],
    }


def _snapshot_of_type(
    context: EvidenceContext, dataset_type: DatasetType
) -> SnapshotRef | None:
    return next(
        (
            snapshot
            for snapshot in context.snapshots
            if snapshot.dataset_type is dataset_type
        ),
        None,
    )


def _score_vector(candidate: CandidateLineup) -> tuple[float, ...]:
    return tuple(rule.effective_score for rule in candidate.analysis.rules)


def _dominates(left: CandidateLineup, right: CandidateLineup) -> bool:
    left_scores = _score_vector(left)
    right_scores = _score_vector(right)
    return all(a >= b for a, b in zip(left_scores, right_scores, strict=True)) and any(
        a > b for a, b in zip(left_scores, right_scores, strict=True)
    )


def _candidate_sort_key(candidate: CandidateLineup) -> tuple[Any, ...]:
    return (
        -candidate.analysis.overall_score,
        tuple(-score for score in _score_vector(candidate)),
        candidate.analysis.lineup.slots,
    )


def _pareto_frontier(candidates: Sequence[CandidateLineup]) -> list[CandidateLineup]:
    frontier = [
        candidate
        for candidate in candidates
        if not any(_dominates(other, candidate) for other in candidates if other is not candidate)
    ]
    return sorted(frontier, key=_candidate_sort_key)


def _recommended(groups: Sequence[CandidateGroup]) -> CandidateLineup | None:
    for group in reversed(groups):
        passing = [item for item in group.candidates if item.analysis.readiness_passed]
        if passing:
            return sorted(passing, key=_candidate_sort_key)[0]
    return None


def _lower_alternative(
    groups: Sequence[CandidateGroup],
    recommended: CandidateLineup | None,
    *,
    target_br: int | None,
) -> CandidateGroup | None:
    ceiling = (
        target_br
        if target_br is not None
        else (
            recommended.analysis.lineup_br
            if recommended is not None
            else groups[-1].lineup_br if groups else None
        )
    )
    if ceiling is None:
        return None
    ready_lower = [
        group
        for group in groups
        if group.lineup_br < ceiling
        and any(item.analysis.readiness_passed for item in group.candidates)
    ]
    return ready_lower[-1] if ready_lower else None


def _best_available(result: GenerationResult) -> CandidateLineup | None:
    if result.recommended is not None:
        return result.recommended
    return _least_deficient(result.groups)


def _least_deficient(groups: Sequence[CandidateGroup]) -> CandidateLineup | None:
    candidates = [candidate for group in groups for candidate in group.candidates]
    return min(
        candidates,
        key=lambda item: (
            len(item.analysis.readiness_failures),
            -item.analysis.overall_score,
            item.analysis.lineup.slots,
        ),
        default=None,
    )


def _component_deltas(before: LineupAnalysis, after: LineupAnalysis) -> dict[str, float]:
    before_scores = {rule.rule: rule.effective_score for rule in before.rules}
    after_scores = {rule.rule: rule.effective_score for rule in after.rules}
    return {
        rule: after_scores[rule] - before_scores[rule]
        for rule in sorted(before_scores.keys() & after_scores.keys())
    }


def _readiness_changes(
    before: LineupAnalysis, after: LineupAnalysis
) -> tuple[str, ...]:
    previous = set(before.readiness_failures)
    current = set(after.readiness_failures)
    return tuple(
        [f"resolved:{item}" for item in sorted(previous - current)]
        + [f"new:{item}" for item in sorted(current - previous)]
    )


def _role_changes(
    before: LineupAnalysis,
    after: LineupAnalysis,
    vehicles: dict[str, Vehicle],
) -> tuple[str, ...]:
    before_roles = {
        role.value
        for vehicle_id in before.lineup.slots
        for role in infer_roles(vehicles[vehicle_id])
    }
    after_roles = {
        role.value
        for vehicle_id in after.lineup.slots
        for role in infer_roles(vehicles[vehicle_id])
    }
    return tuple(
        [f"added:{role}" for role in sorted(after_roles - before_roles)]
        + [f"removed:{role}" for role in sorted(before_roles - after_roles)]
    )


def _addition_sort_key(item: AdditionEvaluation) -> tuple[Any, ...]:
    return (
        not item.resulting_analysis.readiness_passed,
        item.resulting_analysis.lineup_br,
        -item.resulting_analysis.overall_score,
        item.vehicle_id,
    )
