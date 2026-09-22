"""Deterministic orchestration over fixture/provider evidence and pure rules."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from copy import copy
from datetime import UTC, date, datetime
from hashlib import sha256
from itertools import combinations
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from wt_advisor.data.freshness import evaluate_freshness
from wt_advisor.data.imports.capabilities import (
    MAX_CAPABILITY_IMPORT_BYTES,
    import_capabilities_json,
)
from wt_advisor.data.imports.statistics import (
    MAX_IMPORT_BYTES,
    import_statistics_csv,
    import_statistics_json,
    inspect_statistics_csv,
    inspect_statistics_json,
)
from wt_advisor.data.models import (
    RawDatasetMetadata,
    RawResearchGraphDataset,
    RawVehicleRecord,
    canonical_bytes,
)
from wt_advisor.data.providers.community_statistics import (
    PROVIDER as COMMUNITY_STATISTICS_PROVIDER,
)
from wt_advisor.data.providers.community_statistics import (
    CommunityStatisticsProvider,
)
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
    CapabilityState,
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
    RatioProvenance,
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
    statistics_eligibility,
)
from wt_advisor.storage import EvidenceRepository, create_database
from wt_advisor.storage.repository import ResolvedAnalysisBundle, SnapshotCompatibility

MAX_COMBINATIONS = 500_000
FROZEN_ACCEPTANCE_AS_OF = date(2026, 9, 19)
SCORED_CAPABILITIES = (
    Capability.SCOUTING,
    Capability.STABILIZER,
    Capability.VERTICAL_STABILIZER,
    Capability.SMOKE,
    Capability.ARTILLERY,
    Capability.HIGH_CALIBER_HE,
    Capability.ATGM,
)
MAX_COVERAGE_GAPS = 100


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
                row.vehicle_id for row in bundle.user_states if row.status is VehicleStatus.OWNED
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
                # Frozen M2 fixtures deliberately retain their historical identity.
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
            vehicle_id: resolved.value for vehicle_id, resolved in resolved_battle_ratings.items()
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
        capability_snapshots = repository.list_compatible_capability_snapshots(vehicle_snapshot)
        capability_resolutions = (
            {}
            if not capability_snapshots
            else {
                vehicle.vehicle_id: tuple(
                    repository.resolve_capabilities(
                        vehicle.vehicle_id,
                        snapshot_ids=tuple(item.snapshot_id for item in capability_snapshots),
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
                *capability_snapshots,
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
                schema_revision="0006",
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
        capability_snapshot_ids = [
            snapshot.snapshot_id
            for snapshot in self._evidence_context.snapshots
            if snapshot.dataset_type is DatasetType.CAPABILITIES
        ]
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
                covered_count=len({_research_edge_parts(edge)[1] for edge in self._research_edges}),
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
        priority_ids = self._priority_vehicle_ids()
        catalog_coverage = self._capability_coverage(tuple(sorted(self._vehicles)))
        profile_coverage = self._capability_coverage(priority_ids)
        unavailable_reason = (
            "incompatible_snapshot"
            if statistics_snapshot is None and newest_statistics is not None
            and compatibility.status == "incompatible"
            else "absent_source"
        )
        statistics_catalog = self._statistics_coverage(
            tuple(sorted(self._vehicles)), unavailable_reason=unavailable_reason
        )
        statistics_profile = self._statistics_coverage(
            priority_ids, unavailable_reason=unavailable_reason
        )
        components[DatasetType.CAPABILITIES.value].update(
            {
                "selected_snapshot_ids": capability_snapshot_ids,
                "field_coverage": catalog_coverage["field_coverage"],
                "field_complete_count": catalog_coverage["field_complete_count"],
                "missing_pair_count": catalog_coverage["missing_pair_count"],
                "missing_pairs": catalog_coverage["missing_pairs"],
                "missing_pairs_truncated": catalog_coverage["missing_pairs_truncated"],
            }
        )
        statistics_gaps = [
            vehicle_id
            for vehicle_id in sorted(self._vehicles)
            if vehicle_id not in self._statistics
        ]
        components[DatasetType.GLOBAL_STATISTICS.value].update(
            {
                "missing_vehicle_count": len(statistics_gaps),
                "missing_vehicle_ids": statistics_gaps[:MAX_COVERAGE_GAPS],
                "missing_vehicle_ids_truncated": len(statistics_gaps) > MAX_COVERAGE_GAPS,
                "missing_reason": (
                    "no_compatible_statistics_snapshot"
                    if statistics_snapshot is None
                    else "vehicle_row_not_in_selected_snapshot"
                ),
            }
        )
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
                    None if newest_statistics is None else newest_statistics.model_dump(mode="json")
                ),
            },
            "compatibility": {
                "status": compatibility.status,
                "compatible": compatibility.compatible,
                "reason": compatibility.reason,
                "missing_vehicle_ids": list(compatibility.missing_vehicle_ids),
            },
            "components": components,
            "capability_coverage": {
                "catalog": catalog_coverage,
                "profile": {"scope": "owned_and_immediate_research", **profile_coverage},
            },
            "statistics_coverage": {
                "catalog": statistics_catalog,
                "profile": {"scope": "owned_and_immediate_research", **statistics_profile},
            },
            "active": {
                "bundle_id": stable_hash(
                    {
                        "component_snapshot_ids": active_component_ids,
                        "capability_snapshot_ids": capability_snapshot_ids,
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
                "statistics_scoring_status": (
                    "community_rb_proxy" if statistics_catalog["proxy_vehicle_count"] > 0
                    else "eligible" if statistics_catalog["eligible_vehicle_count"] > 0
                    else "context_only" if statistics_snapshot is not None else "unavailable"
                ),
                "statistics_period": {
                    "start": (
                        None
                        if statistics_snapshot is None or statistics_snapshot.sample_start is None
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
                "capability_snapshot_ids": capability_snapshot_ids,
                "availability_snapshot_id": (
                    None if availability_snapshot is None else availability_snapshot.snapshot_id
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

    def _priority_vehicle_ids(self) -> tuple[str, ...]:
        owned = {
            vehicle_id for vehicle_id, status in self._statuses.items()
            if status is VehicleStatus.OWNED and vehicle_id in self._vehicles
        }
        active_research = {
            vehicle_id for vehicle_id, status in self._statuses.items()
            if status in {VehicleStatus.RESEARCHING, VehicleStatus.AVAILABLE_TO_RESEARCH}
            and vehicle_id in self._vehicles
        }
        immediate = {
            _research_edge_parts(edge)[1]
            for edge in self._research_edges
            if _research_edge_parts(edge)[0] in owned
        }
        return tuple(sorted(owned | active_research | (immediate & self._vehicles.keys())))

    def _capability_coverage(self, vehicle_ids: Sequence[str]) -> dict[str, Any]:
        states_by_vehicle = {
            vehicle_id: {
                resolution.capability: resolution.state
                for resolution in self._capability_resolutions.get(vehicle_id, ())
            }
            for vehicle_id in vehicle_ids
        }
        field_coverage: dict[str, dict[str, int]] = {}
        missing_pairs: list[dict[str, str]] = []
        complete = 0
        for capability in SCORED_CAPABILITIES:
            counts = {state.value: 0 for state in CapabilityState}
            for vehicle_id in vehicle_ids:
                state = states_by_vehicle[vehicle_id].get(capability, CapabilityState.UNKNOWN)
                counts[state.value] += 1
                if state in {CapabilityState.UNKNOWN, CapabilityState.CONFLICTED}:
                    missing_pairs.append({
                        "vehicle_id": vehicle_id,
                        "capability": capability.value,
                        "reason": (
                            "conflicting_observations"
                            if state is CapabilityState.CONFLICTED
                            else "no_selected_observation"
                        ),
                    })
            field_coverage[capability.value] = {"denominator": len(vehicle_ids), **counts}
        for states in states_by_vehicle.values():
            if all(
                states.get(capability, CapabilityState.UNKNOWN)
                in {CapabilityState.PRESENT, CapabilityState.VERIFIED_ABSENT}
                for capability in SCORED_CAPABILITIES
            ):
                complete += 1
        return {
            "vehicle_count": len(vehicle_ids),
            "field_complete_count": complete,
            "field_coverage": field_coverage,
            "missing_pair_count": len(missing_pairs),
            "missing_pairs": missing_pairs[:MAX_COVERAGE_GAPS],
            "missing_pairs_truncated": len(missing_pairs) > MAX_COVERAGE_GAPS,
        }

    def _statistics_coverage(
        self, vehicle_ids: Sequence[str], *, unavailable_reason: str
    ) -> dict[str, Any]:
        reasons: dict[str, int] = defaultdict(int)
        details: list[dict[str, str]] = []
        metric_names = ("win_rate", "kd", "kills_per_battle")
        selected = self._scoring_statistics()
        peers = self._peer_statistics()
        eligible_count = 0
        proxy_count = 0
        has_selected = any(
            snapshot.dataset_type is DatasetType.GLOBAL_STATISTICS
            for snapshot in self._evidence_context.snapshots
        )
        for vehicle_id in vehicle_ids:
            rows = self._statistics.get(vehicle_id, ())
            stats = selected.get(vehicle_id)
            exclusion: str | None = None
            usable: tuple[str, ...] = ()
            if stats is not None and has_selected:
                if self._synthetic_proxy(stats):
                    exclusion = "synthetic_acceptance_fixture"
                else:
                    exclusion, usable, _, _ = statistics_eligibility(
                        self._vehicles[vehicle_id], self._battle_ratings[vehicle_id], stats,
                        peers, as_of=self._evaluation_date, ruleset=self._ruleset,
                    )
                if exclusion is None:
                    eligible_count += 1
                    if stats.mode_scope is StatisticsScope.REALISTIC_ALL_CONTEXTS:
                        proxy_count += 1
            for metric in metric_names:
                if not has_selected:
                    reason = unavailable_reason
                elif stats is None:
                    reason = "missing_vehicle_row" if not rows else "wrong_mode"
                elif exclusion is not None:
                    reason = (
                        "missing_sample_metadata"
                        if exclusion == "nonpositive_battles"
                        and stats.battles is None
                        and stats.ratio_source(metric) is RatioProvenance.REPORTED
                        else exclusion
                    )
                elif metric in usable:
                    reason = (
                        "usable_reported_metric"
                        if stats.mode_scope is StatisticsScope.REALISTIC_ALL_CONTEXTS
                        else "usable_count_metric"
                    )
                else:
                    reason = (
                        "insufficient_compatible_peers"
                        if stats.ratio_source(metric) is not RatioProvenance.UNAVAILABLE
                        else "missing_metric"
                    )
                reasons[reason] += 1
                if reason not in ("usable_count_metric", "usable_reported_metric"):
                    details.append({
                        "vehicle_id": vehicle_id, "metric": metric, "reason": reason,
                    })
        return {
            "vehicle_count": len(vehicle_ids),
            "eligible_vehicle_count": eligible_count,
            "proxy_vehicle_count": proxy_count,
            "metric_denominator": len(vehicle_ids) * len(metric_names),
            "reason_counts": dict(sorted(reasons.items())),
            "gaps": details[:MAX_COVERAGE_GAPS],
            "gap_count": len(details),
            "gaps_truncated": len(details) > MAX_COVERAGE_GAPS,
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

    def vehicle_statistics_status(self, vehicle_id: str) -> list[dict[str, Any]]:
        """Return source observations with current scoring eligibility diagnostics."""
        rows = self.get_vehicle_statistics(vehicle_id)
        selected = self._scoring_statistics().get(vehicle_id)
        snapshots = {
            item.snapshot_id: item for item in self._evidence_context.snapshots
        }
        details = []
        for row in rows:
            reason = self._statistics_eligibility(vehicle_id, row)
            if reason is None and row != selected:
                reason = "superseded_by_verified_ground_rb"
            proxy = row.mode_scope is StatisticsScope.REALISTIC_ALL_CONTEXTS
            snapshot = snapshots.get(row.snapshot_id)
            details.append(row.model_dump(mode="json") | {
                "eligible": reason is None,
                "proxy_used": proxy and reason is None,
                "evidence_label": (
                    "Community RB ground-vehicle proxy" if proxy
                    else "Verified Ground RB statistics"
                ),
                "source_scope": row.mode_scope.value,
                "source_provider": None if snapshot is None else snapshot.provider,
                "exclusion_reason": reason,
            })
        return details

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

    def reprocess_retained_community_statistics(self) -> dict[str, Any]:
        """Normalize retained joined CSV against the active vehicle catalog, offline."""

        if self._repository is None or self._bundle_resolution is None:
            raise ValueError("retained community statistics require a database-backed service")
        vehicle_snapshot = self._bundle_resolution.vehicle_snapshot
        if vehicle_snapshot.purpose is not SnapshotPurpose.OPERATIONAL:
            raise ValueError("retained community statistics require operational vehicle evidence")
        sources = (
            snapshot
            for snapshot in self._repository.list_snapshots(DatasetType.GLOBAL_STATISTICS)
            if snapshot.provider == COMMUNITY_STATISTICS_PROVIDER
            and snapshot.sample_end is not None
            and self._repository.raw_artifact_content(snapshot.snapshot_id) is not None
        )
        candidates = tuple(sources)
        latest_date = max(
            (item.sample_end for item in candidates if item.sample_end is not None),
            default=None,
        )
        # A normalized successor retains the original CSV and source date. Keep
        # the first captured artifact as the stable input on every repeat run.
        source = min(
            (item for item in candidates if item.sample_end == latest_date),
            key=lambda item: (item.retrieved_at, item.snapshot_id),
            default=None,
        )
        if source is None:
            raise ValueError("no retained community statistics artifact is available")
        retained = self._repository.raw_artifact_content(source.snapshot_id)
        if retained is None:
            raise ValueError("retained community statistics artifact is unavailable")
        before = self.data_status()
        result = CommunityStatisticsProvider().fetch_statistics(
            identity_aliases=self._statistics_identity_aliases(COMMUNITY_STATISTICS_PROVIDER),
            canonical_vehicle_ids=self._vehicles.keys(),
            compatibility_key=vehicle_snapshot.compatibility_key,
            retained_content=retained,
            observation_date=source.sample_end,
            source_revision=source.source_revision,
        )
        if result.dataset is None:
            raise ValueError("retained community statistics have no accepted records")
        imported = self._repository.import_statistics_dataset(result.dataset)
        database_path = self._repository.engine.url.database
        if not database_path:
            raise ValueError("database path is unavailable after reprocess")
        reloaded = type(self).from_database(database_path)
        selected = reloaded.data_status()["active"]["statistics_snapshot_id"]
        if selected != result.dataset.snapshot.snapshot_id:
            raise RuntimeError("reprocessed statistics were stored but not selected")
        self.__dict__ = reloaded.__dict__
        return {
            "outcome": "updated" if imported.created else "unchanged",
            "previous_bundle_id": before["active"]["bundle_id"],
            "bundle_id": self.data_status()["active"]["bundle_id"],
            "source_snapshot_id": source.snapshot_id,
            "statistics_snapshot_id": selected,
            "accepted_statistics_rows": result.accepted_count,
            "quarantined_statistics_rows": sum(result.quarantine_counts.values()),
            "quarantine_reasons": result.quarantine_counts,
            "before_coverage": before["statistics_coverage"],
            "after_coverage": self.data_status()["statistics_coverage"],
        }

    def refresh_community_evidence(self) -> dict[str, Any]:
        """Fetch and atomically publish bounded community evidence for new evaluations."""

        before = self.data_status()
        previous_bundle_id = before["active"]["bundle_id"]
        result: dict[str, Any] = {
            "outcome": "failed",
            "previous_bundle_id": previous_bundle_id,
            "bundle_id": previous_bundle_id,
            "accepted_statistics_rows": 0,
            "quarantined_statistics_rows": 0,
            "quarantine_reasons": {},
            "statistics_status": "unavailable",
            "before_coverage": before["capability_coverage"],
            "after_coverage": before["capability_coverage"],
            "message": "Existing evidence was retained.",
        }
        if self._repository is None:
            return {**result, "message": "Community refresh requires a database-backed service."}

        published = False
        try:
            vehicle_bundle = WarThunderVehiclesApiProvider().fetch_operational_components(
                max_br=143,
                max_details=25,
                priority_vehicle_ids=self._priority_vehicle_ids(),
                previously_fetched_ids=self._previously_fetched_detail_ids(),
            )
            canonical_ids = {row.vehicle_id for row in vehicle_bundle.vehicles.records}
            aliases = {
                row.source_vehicle_id: row.vehicle_id
                for row in vehicle_bundle.vehicles.records
            }
            confirmed = self._statistics_identity_aliases(COMMUNITY_STATISTICS_PROVIDER)
            retained_aliases = {
                alias: vehicle_id
                for alias, vehicle_id in confirmed.items()
                if vehicle_id in canonical_ids and alias not in aliases
            }
            aliases = {**retained_aliases, **aliases}
            aliases = {
                **aliases,
                **_joined_community_aliases(vehicle_bundle.vehicles.records, aliases),
            }
            statistics_result = None
            statistics_error = None
            try:
                statistics_result = CommunityStatisticsProvider().fetch_statistics(
                    identity_aliases=aliases,
                    canonical_vehicle_ids=canonical_ids,
                    compatibility_key=vehicle_bundle.vehicles.snapshot.compatibility_key,
                )
            except (ValueError, OSError) as error:
                statistics_error = type(error).__name__

            graph: RawResearchGraphDataset | None = vehicle_bundle.research_graph
            previous_graph = (
                None if self._bundle_resolution is None
                else self._bundle_resolution.research_graph_snapshot
            )
            if previous_graph is not None and self._repository.component_compatibility(
                vehicle_bundle.vehicles.snapshot, previous_graph
            ).compatible:
                prior_edges = {
                    (edge.parent_vehicle_id, edge.child_vehicle_id)
                    for edge in self._research_edges
                }
                new_edges = {
                    (edge.parent_vehicle_id, edge.child_vehicle_id)
                    for edge in vehicle_bundle.research_graph.records
                }
                if not prior_edges.issubset(new_edges):
                    graph = None
            self._repository.import_operational_bundle(
                vehicles=vehicle_bundle.vehicles,
                capabilities=vehicle_bundle.capabilities,
                availability=vehicle_bundle.availability,
                research_graph=graph,
                statistics=(
                    None if statistics_result is None else statistics_result.dataset
                ),
            )
            published = True
            database_path = self._repository.engine.url.database
            if not database_path:
                raise ValueError("database path is unavailable after refresh")
            reloaded = type(self).from_database(database_path)
        except (httpx.HTTPError, ValueError, OSError, RuntimeError) as error:
            if published:
                return {
                    **result,
                    "outcome": "restart_required",
                    "bundle_id": None,
                    "message": (
                        "Evidence was published, but the service could not reload it; "
                        "restart the service."
                    ),
                }
            reason = (
                "source rate limited"
                if isinstance(error, httpx.HTTPStatusError)
                and error.response.status_code == 429
                else type(error).__name__
            )
            message = f"Refresh failed ({reason}); existing evidence was retained."
            return {**result, "message": message}

        # Swap the service view in one assignment so all three interfaces use the
        # newly selected bundle without requiring a dashboard/MCP restart.
        self.__dict__ = reloaded.__dict__
        after = self.data_status()
        accepted = 0 if statistics_result is None else statistics_result.accepted_count
        reasons = {} if statistics_result is None else statistics_result.quarantine_counts
        eligible_proxy = after["statistics_coverage"]["catalog"]["proxy_vehicle_count"]
        statistics_status = (
            "source_unavailable" if statistics_error is not None else
            "community_rb_proxy" if eligible_proxy else
            "context_only" if statistics_result is not None and accepted else
            "no_usable_rows"
        )
        return {
            **result,
            "outcome": (
                "unchanged" if after["active"]["bundle_id"] == previous_bundle_id
                else "updated"
            ),
            "bundle_id": after["active"]["bundle_id"],
            "accepted_statistics_rows": accepted,
            "quarantined_statistics_rows": sum(reasons.values()),
            "quarantine_reasons": reasons,
            "statistics_status": statistics_status,
            "eligible_statistics_rows": eligible_proxy,
            "statistics_source_url": (
                None if statistics_result is None else statistics_result.source_url
            ),
            "statistics_source_revision": (
                None if statistics_result is None else statistics_result.source_revision
            ),
            "statistics_age_days": (
                None if statistics_result is None else statistics_result.age_days
            ),
            "statistics_limitations": (
                None if statistics_result is None else statistics_result.reason
            ),
            "source_observation_date": (
                None if statistics_result is None
                else statistics_result.observation_date.isoformat()
            ),
            "after_coverage": after["capability_coverage"],
            "message": (
                "Vehicle evidence refreshed; eligible community RB proxies are available."
                if eligible_proxy else
                "Vehicle evidence refreshed; community RB statistics remain context only."
                if statistics_error is None else
                "Vehicle evidence refreshed; statistics source was unavailable."
            ),
        }

    def _previously_fetched_detail_ids(self) -> tuple[str, ...]:
        if self._repository is None:
            return ()
        source_to_canonical = {
            vehicle.source_vehicle_id: vehicle.vehicle_id
            for vehicle in self._vehicles.values()
        }
        fetched: set[str] = set()
        for snapshot in self._evidence_context.snapshots:
            if (
                snapshot.dataset_type is not DatasetType.CAPABILITIES
                or snapshot.provider != "war_thunder_vehicles_community_api"
            ):
                continue
            raw = self._repository.raw_artifact_content(snapshot.snapshot_id)
            if raw is None:
                continue
            try:
                rows = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                continue
            if not isinstance(rows, list):
                continue
            fetched.update(
                source_to_canonical[source_id]
                for row in rows
                if isinstance(row, dict)
                and isinstance((source_id := row.get("identifier")), str)
                and source_id in source_to_canonical
            )
        return tuple(sorted(fetched))

    def import_capabilities(
        self,
        source: Path,
        *,
        provider: str,
        source_revision: str,
        vehicle_snapshot_id: str,
    ) -> SnapshotRef:
        """Import provenance-stamped curated observations for the selected vehicle scope."""

        if self._repository is None:
            raise ValueError("capability import requires a database-backed service")
        if not provider.strip() or not source_revision.strip():
            raise ValueError("provider and source revision are required")
        selected = self._bundle_resolution
        if selected is None or selected.vehicle_snapshot.snapshot_id != vehicle_snapshot_id:
            raise ValueError("vehicle snapshot ID does not match the selected bundle")
        current_snapshot_id = (
            self._repository.resolve_analysis_bundle().vehicle_snapshot.snapshot_id
        )
        if current_snapshot_id != vehicle_snapshot_id:
            raise ValueError("vehicle evidence changed; reload before importing capabilities")
        if selected.vehicle_snapshot.purpose is not SnapshotPurpose.OPERATIONAL:
            raise ValueError("curated operational capabilities require operational vehicles")
        payload = _read_bounded_file(source, MAX_CAPABILITY_IMPORT_BYTES, kind="capability")
        snapshot_id = "capabilities-curated-" + sha256(
            provider.encode() + b"\0" + source_revision.encode() + b"\0" + payload
        ).hexdigest()[:16]
        metadata = RawDatasetMetadata(
            snapshot_id=snapshot_id,
            dataset_type=DatasetType.CAPABILITIES,
            provider=provider,
            source_revision=source_revision,
            retrieved_at=datetime.now(UTC),
            purpose=SnapshotPurpose.OPERATIONAL,
            compatibility_key=selected.vehicle_snapshot.compatibility_key,
        )
        dataset = import_capabilities_json(payload, metadata)
        unknown = sorted({row.vehicle_id for row in dataset.records} - self._vehicles.keys())
        if unknown:
            raise ValueError(
                "capability import contains unknown canonical IDs: " + ", ".join(unknown)
            )
        if any(
            row.source_type is not CapabilitySourceType.CURATED_IMPORT
            for row in dataset.records
        ):
            raise ValueError("local capability import requires source_type curated_import")
        if any(
            row.source_provider != provider or row.source_revision != source_revision
            for row in dataset.records
        ):
            raise ValueError("capability record provider/revision differs from import metadata")
        self._repository.import_capability_snapshot(
            dataset.snapshot, dataset.records, raw_content=dataset.raw_content
        )
        return dataset.snapshot

    def inspect_statistics(
        self,
        source: Path,
        *,
        provider: str,
        purpose: SnapshotPurpose = SnapshotPurpose.OPERATIONAL,
    ) -> StatisticsInspection:
        """Inspect a local statistics export without mutating evidence storage."""

        payload = _read_bounded_file(source, MAX_IMPORT_BYTES)
        return self._inspect_statistics_payload(source, payload, provider=provider, purpose=purpose)

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
            vehicle.source_vehicle_id: vehicle.vehicle_id for vehicle in self._vehicles.values()
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
        if provider == COMMUNITY_STATISTICS_PROVIDER:
            aliases = {
                **aliases,
                **_joined_community_aliases(self._vehicles.values(), aliases),
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
        self,
        profile_id: str,
        vehicle_id: str,
        status: VehicleStatus,
        *,
        expected_revision: str | None = None,
        expected_write_revision: str | None = None,
    ) -> VehicleStatusChange:
        if expected_revision is not None and expected_write_revision is not None:
            raise ValueError("expected_revision and expected_write_revision are mutually exclusive")
        self._resolve_profile_id(profile_id)
        self._require_vehicle(vehicle_id)
        before = self._statuses.get(vehicle_id, VehicleStatus.UNKNOWN)
        if self._repository is not None:
            self._repository.set_vehicle_status(
                self._profile.profile_id,
                vehicle_id,
                status,
                expected_revision=expected_revision,
                expected_write_revision=expected_write_revision,
            )
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

    def get_profile_write_revision(self, profile_id: str) -> str:
        """Return the persisted monotonic token for an optional guarded write."""

        stored = self._repository_required().get_profile(self._resolve_profile_id(profile_id))
        if stored is None:
            raise LookupError(f"unknown profile {profile_id!r}")
        return f"write-rev-{stored.write_revision}"

    def evaluate_and_store(
        self,
        *,
        profile_id: str,
        target_br: int | None = None,
        top_n: int = 10,
        hypothetical_owned: frozenset[str] = frozenset(),
        required_vehicle_ids: frozenset[str] | None = None,
        excluded_vehicle_ids: frozenset[str] = frozenset(),
        allow_partial: bool = False,
        preset_id: str | None = None,
    ) -> dict[str, Any]:
        """Calculate from one captured profile snapshot, then retain the result."""

        resolved_profile = self._resolve_profile_id(profile_id)
        repository = self._repository_required()
        captured = repository.capture_evaluation_snapshot(
            resolved_profile, preset_id=preset_id
        )
        captured_statuses = dict(sorted(captured.vehicle_statuses.items()))
        progress = UserProgress(
            profile=captured.profile.to_domain(),
            vehicle_statuses=captured_statuses,
            revision=stable_hash(captured_statuses),
        )
        frozen = copy(self)
        frozen._profile = progress.profile
        frozen._statuses = dict(captured_statuses)
        frozen._repository = None
        frozen._bundle_resolution = self._bundle_resolution
        preset_slots = () if captured.preset is None else captured.preset.slots
        preset_required = (
            () if captured.preset is None else captured.preset.required_vehicle_ids
        )
        preset_excluded = (
            () if captured.preset is None else captured.preset.excluded_vehicle_ids
        )
        requested_required = tuple(sorted(required_vehicle_ids or ()))
        requested_excluded = tuple(sorted(excluded_vehicle_ids))
        effective_target_br = target_br if target_br is not None else captured.context.target_br
        effective_required = frozenset(
            (
                *captured.context.required_vehicle_ids,
                *preset_slots,
                *preset_required,
                *requested_required,
            )
        )
        effective_excluded = frozenset(
            (*captured.context.excluded_vehicle_ids, *preset_excluded, *requested_excluded)
        )
        effective_inputs = {
            "target_br": effective_target_br,
            "top_n": top_n,
            "hypothetical_owned": sorted(hypothetical_owned),
            "required_vehicle_ids": sorted(effective_required),
            "excluded_vehicle_ids": sorted(effective_excluded),
            "allow_partial": allow_partial,
            "preset_slots": list(preset_slots),
            "requested_target_br": target_br,
            "requested_required_vehicle_ids": list(requested_required),
            "requested_excluded_vehicle_ids": list(requested_excluded),
            "context_revision": f"rev-{captured.context.revision}",
            "source_context_constraints_hash": stable_hash(
                {
                    "target_br": captured.context.target_br,
                    "required_vehicle_ids": sorted(captured.context.required_vehicle_ids),
                    "excluded_vehicle_ids": sorted(captured.context.excluded_vehicle_ids),
                }
            ),
            "source_preset_id": None if captured.preset is None else captured.preset.preset_id,
            "source_preset_revision": (
                None if captured.preset is None else f"rev-{captured.preset.revision}"
            ),
        }
        result = frozen.generate_lineups(
            profile_id=resolved_profile,
            target_br=effective_target_br,
            top_n=top_n,
            hypothetical_owned=hypothetical_owned,
            required_vehicle_ids=(
                None if not effective_required and not effective_excluded else effective_required
            ),
            excluded_vehicle_ids=effective_excluded,
            allow_partial=allow_partial,
        )
        payload = result.model_dump(mode="json")
        # Each explicit run is an immutable historical record. The deterministic
        # calculation identity remains result.analysis_id.
        evaluation_id = uuid4().hex
        stored = repository.store_evaluation(
            evaluation_id=evaluation_id,
            profile_id=resolved_profile,
            kind="generation",
            profile_revision=progress.revision,
            profile_state=progress.model_dump(mode="json"),
            effective_inputs=effective_inputs,
            evidence_context=self._evidence_context.model_dump(mode="json"),
            ruleset_hash=self._evidence_context.ruleset_hash,
            schema_revision=self._evidence_context.schema_revision,
            result_id=result.analysis_id,
            result_payload=payload,
        )
        return self._stored_evaluation_payload(stored, stale=False, stale_reasons=())

    def reevaluate_stored_evaluation(
        self, profile_id: str, evaluation_id: str
    ) -> dict[str, Any]:
        """Explicitly rerun a stored generation using its saved effective inputs."""

        previous = self._repository_required().get_stored_evaluation(
            self._resolve_profile_id(profile_id), evaluation_id
        )
        if previous is None:
            raise LookupError("unknown stored evaluation")
        inputs = previous.effective_inputs
        return self.evaluate_and_store(
            profile_id=profile_id,
            target_br=inputs.get("requested_target_br", inputs.get("target_br")),
            top_n=int(inputs.get("top_n", 10)),
            hypothetical_owned=frozenset(inputs.get("hypothetical_owned", ())),
            required_vehicle_ids=frozenset(
                inputs.get("requested_required_vehicle_ids", inputs.get("required_vehicle_ids", ()))
            ),
            excluded_vehicle_ids=frozenset(
                inputs.get("requested_excluded_vehicle_ids", inputs.get("excluded_vehicle_ids", ()))
            ),
            allow_partial=bool(inputs.get("allow_partial", False)),
            preset_id=inputs.get("source_preset_id"),
        )

    def get_stored_evaluation(self, profile_id: str, evaluation_id: str) -> dict[str, Any]:
        stored = self._repository_required().get_stored_evaluation(
            self._resolve_profile_id(profile_id), evaluation_id
        )
        if stored is None:
            raise LookupError("unknown stored evaluation")
        current_progress = self.get_user_progress(profile_id).revision
        reasons = []
        if stored.profile_revision != current_progress:
            reasons.append("profile_garage_state_changed")
        if stored.ruleset_hash != self._evidence_context.ruleset_hash:
            reasons.append("ruleset_changed")
        if stored.evidence_context != self._evidence_context.model_dump(mode="json"):
            reasons.append("evidence_changed")
        saved_context_constraints_hash = stored.effective_inputs.get(
            "source_context_constraints_hash"
        )
        if saved_context_constraints_hash is not None:
            current_context = self._repository_required().get_advisor_context(stored.profile_id)
            current_context_constraints_hash = stable_hash(
                {
                    "target_br": current_context.target_br,
                    "required_vehicle_ids": sorted(current_context.required_vehicle_ids),
                    "excluded_vehicle_ids": sorted(current_context.excluded_vehicle_ids),
                }
            )
            if current_context_constraints_hash != saved_context_constraints_hash:
                reasons.append("effective_constraints_changed")
        source_preset_id = stored.effective_inputs.get("source_preset_id")
        source_preset_revision = stored.effective_inputs.get("source_preset_revision")
        if source_preset_id is not None:
            current = next(
                (
                    preset
                    for preset in self._repository_required().list_presets(stored.profile_id)
                    if preset.preset_id == source_preset_id
                ),
                None,
            )
            current_revision = None if current is None else f"rev-{current.revision}"
            if current_revision != source_preset_revision:
                reasons.append("source_preset_changed")
        return self._stored_evaluation_payload(
            stored, stale=bool(reasons), stale_reasons=tuple(reasons)
        )

    @staticmethod
    def _stored_evaluation_payload(
        stored: Any, *, stale: bool, stale_reasons: tuple[str, ...]
    ) -> dict[str, Any]:
        return {
            "evaluation_id": stored.evaluation_id,
            "kind": stored.kind,
            "profile_revision": stored.profile_revision,
            "effective_inputs": stored.effective_inputs,
            "evidence_context": stored.evidence_context,
            "result_id": stored.result_id,
            "result": stored.result_payload,
            "created_at": stored.created_at.isoformat(),
            "stale": stale,
            "stale_reasons": stale_reasons,
        }

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
            self._statuses = self._repository.get_user_vehicle_states(self._profile.profile_id)
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

    def _repository_required(self) -> EvidenceRepository:
        if self._repository is None:
            raise ValueError("durable presets require a database-backed service")
        return self._repository

    def _validate_preset(
        self, slots: Sequence[str], required: Sequence[str], excluded: Sequence[str]
    ) -> None:
        for vehicle_id in (*slots, *required, *excluded):
            self._require_vehicle(vehicle_id)
        if len(set(slots)) != len(slots):
            raise ValueError("duplicate vehicle selections")
        if len(slots) > self._profile.crew_slots:
            raise ValueError("preset exceeds configured crew slots")
        if set(required) & set(excluded):
            raise ValueError("required and excluded vehicle IDs overlap")

    def create_preset(
        self,
        profile_id: str,
        *,
        name: str,
        slots: Sequence[str],
        required_vehicle_ids: Sequence[str] = (),
        excluded_vehicle_ids: Sequence[str] = (),
    ) -> Any:
        resolved = self._resolve_profile_id(profile_id)
        self._validate_preset(slots, required_vehicle_ids, excluded_vehicle_ids)
        return self._repository_required().create_preset(
            resolved,
            preset_id=str(uuid4()),
            name=name,
            slots=tuple(slots),
            required_vehicle_ids=tuple(sorted(required_vehicle_ids)),
            excluded_vehicle_ids=tuple(sorted(excluded_vehicle_ids)),
        )

    def list_presets(self, profile_id: str) -> Any:
        return self._repository_required().list_presets(self._resolve_profile_id(profile_id))

    def update_preset(
        self,
        profile_id: str,
        preset_id: str,
        *,
        name: str,
        slots: Sequence[str],
        required_vehicle_ids: Sequence[str],
        excluded_vehicle_ids: Sequence[str],
        expected_revision: str,
    ) -> Any:
        self._validate_preset(slots, required_vehicle_ids, excluded_vehicle_ids)
        return self._repository_required().update_preset(
            self._resolve_profile_id(profile_id),
            preset_id,
            name=name,
            slots=tuple(slots),
            required_vehicle_ids=tuple(sorted(required_vehicle_ids)),
            excluded_vehicle_ids=tuple(sorted(excluded_vehicle_ids)),
            expected_revision=expected_revision,
        )

    def delete_preset(self, profile_id: str, preset_id: str, *, expected_revision: str) -> None:
        self._repository_required().delete_preset(
            self._resolve_profile_id(profile_id), preset_id, expected_revision=expected_revision
        )

    def get_advisor_context(self, profile_id: str) -> Any:
        return self._repository_required().get_advisor_context(self._resolve_profile_id(profile_id))

    def update_advisor_context(
        self,
        profile_id: str,
        *,
        selected_preset_id: str | None,
        target_br: int | None = None,
        required_vehicle_ids: Sequence[str] = (),
        excluded_vehicle_ids: Sequence[str] = (),
        expected_revision: str,
    ) -> Any:
        self._validate_preset((), required_vehicle_ids, excluded_vehicle_ids)
        return self._repository_required().update_advisor_context(
            self._resolve_profile_id(profile_id),
            selected_preset_id=selected_preset_id,
            target_br=target_br,
            required_vehicle_ids=tuple(sorted(required_vehicle_ids)),
            excluded_vehicle_ids=tuple(sorted(excluded_vehicle_ids)),
            expected_revision=expected_revision,
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
            vehicle.vehicle_id: self._battle_ratings[vehicle.vehicle_id] for vehicle in vehicles
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
            evaluation_date=self._evaluation_date,
            source_providers={
                item.snapshot_id: item.provider for item in self._evidence_context.snapshots
            },
            source_purposes={
                item.snapshot_id: item.purpose.value for item in self._evidence_context.snapshots
            },
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
        required_vehicle_ids: frozenset[str] | None = None,
        excluded_vehicle_ids: frozenset[str] = frozenset(),
        allow_partial: bool = False,
    ) -> GenerationResult:
        self._resolve_profile_id(profile_id)
        if required_vehicle_id is not None and required_vehicle_ids is not None:
            raise ValueError("singular and plural required vehicle inputs are ambiguous")
        required = (
            frozenset({required_vehicle_id})
            if required_vehicle_ids is None and required_vehicle_id
            else (required_vehicle_ids or frozenset())
        )
        if required & excluded_vehicle_ids:
            raise ValueError("required and excluded vehicle IDs overlap")
        if top_n < 1 or top_n > 100:
            raise ValueError("top_n must be between 1 and 100")
        eligible = self._eligible_owned(hypothetical_owned)
        for vehicle_id in required:
            self._require_vehicle(vehicle_id)
            if vehicle_id not in eligible:
                raise ValueError("required vehicle is not eligible under profile ownership policy")
        eligible = tuple(item for item in eligible if item not in excluded_vehicle_ids)
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
            if required and not required.issubset(vehicle_ids):
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
            **(
                {"required_vehicle_id": required_vehicle_id}
                if required_vehicle_ids is None
                else {
                    "required_vehicle_ids": sorted(required),
                    "excluded_vehicle_ids": sorted(excluded_vehicle_ids),
                }
            ),
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

    def compare_lineups(self, lineup_a: Sequence[str], lineup_b: Sequence[str]) -> LineupComparison:
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
            role_changes = _role_changes(current.analysis, best.analysis, self._vehicles)
            opens_new_frontier = (
                best.analysis.readiness_passed
                and best.analysis.lineup_br > current.analysis.lineup_br
            )
            improves_frontier = best.analysis.lineup_br == current.analysis.lineup_br and (
                (not current.analysis.readiness_passed and best.analysis.readiness_passed)
                or best.analysis.overall_score > current.analysis.overall_score
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
                    fills_missing_role=any(change.startswith("added:") for change in role_changes),
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
            acquisition_type = availability.acquisition_type if availability is not None else None
            if (
                acquisition_type is AcquisitionType.PREMIUM
                or (
                    acquisition_type is None
                    and vehicle.availability_type is AvailabilityType.PREMIUM
                )
            ) and not self._profile.include_premiums:
                continue
            if (
                acquisition_type is AcquisitionType.EVENT
                or (
                    acquisition_type is None and vehicle.availability_type is AvailabilityType.EVENT
                )
            ) and not self._profile.include_event_vehicles:
                continue
            if (
                acquisition_type is AcquisitionType.PACK
                or (acquisition_type is None and vehicle.availability_type is AvailabilityType.PACK)
            ) and not self._profile.include_pack_vehicles:
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
                if _prerequisites_satisfied(prerequisites.get(vehicle_id, ()), self._statuses)
            )
        )

    def _is_normally_researchable(self, vehicle_id: str) -> bool:
        if self._resolved_availability:
            resolved = self._resolved_availability.get(vehicle_id)
            return (
                resolved is not None
                and resolved.researchability is Researchability.NORMALLY_RESEARCHABLE
            )
        return self._vehicles[vehicle_id].availability_type is AvailabilityType.RESEARCH_TREE

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
            if statistics.mode_scope in (
                StatisticsScope.GROUND_REALISTIC_GROUND_VEHICLES,
                StatisticsScope.REALISTIC_ALL_CONTEXTS,
            )
            and not self._synthetic_proxy(statistics)
        )

    def _synthetic_proxy(self, statistics: VehicleStatistics) -> bool:
        return statistics.mode_scope is StatisticsScope.REALISTIC_ALL_CONTEXTS and any(
            snapshot.snapshot_id == statistics.snapshot_id
            and snapshot.purpose is SnapshotPurpose.ACCEPTANCE
            for snapshot in self._evidence_context.snapshots
        )

    def _statistics_eligibility(self, vehicle_id: str, statistics: VehicleStatistics) -> str | None:
        if self._synthetic_proxy(statistics):
            return "synthetic_acceptance_fixture"
        reason, _, _, _ = statistics_eligibility(
            self._vehicles[vehicle_id], self._battle_ratings[vehicle_id], statistics,
            self._peer_statistics(), as_of=self._evaluation_date, ruleset=self._ruleset,
        )
        return reason

    def _scoring_statistics(self) -> dict[str, VehicleStatistics]:
        chosen: dict[str, VehicleStatistics] = {}
        for vehicle_id, observations in self._statistics.items():
            candidates = sorted(
                observations,
                key=lambda item: (
                    item.mode_scope is not StatisticsScope.GROUND_REALISTIC_GROUND_VEHICLES,
                    item.snapshot_id,
                ),
            )
            chosen[vehicle_id] = next(
                (
                    item for item in candidates
                    if self._statistics_eligibility(vehicle_id, item) is None
                ),
                candidates[0],
            )
        return chosen

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
            "capabilities_contract": "resolved_capability_resolutions",
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


def _read_bounded_file(source: Path, maximum_bytes: int, *, kind: str = "statistics") -> bytes:
    """Read one local import safely, including a size check resilient to file changes."""

    try:
        if source.stat().st_size > maximum_bytes:
            raise ValueError(f"{kind} import exceeds configured byte limit")
        with source.open("rb") as stream:
            payload = stream.read(maximum_bytes + 1)
    except OSError as exc:
        raise ValueError(f"unable to read {kind} source: {exc}") from exc
    if len(payload) > maximum_bytes:
        raise ValueError(f"{kind} import exceeds configured byte limit")
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


def _joined_community_aliases(
    vehicles: Iterable[Vehicle | RawVehicleRecord], native: Mapping[str, str]
) -> dict[str, str]:
    """Admit only source-confirmed variants with no competing native identity."""

    by_id = {vehicle.vehicle_id: vehicle for vehicle in vehicles}
    candidates = (
        ("us_halftrack_m3_75mm_gmc", "us_m3_gmc", "M3 GMC"),
        ("us_m2a4_1st_armor_div", "us_m2a4_first_tank_div", "M2A4 (1st Arm.Div.)"),
    )
    return {
        source: canonical
        for source, canonical, expected_name in candidates
        if canonical in by_id
        and by_id[canonical].name == expected_name
        and source not in native
    }


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
        "newest_snapshot_id": (None if newest_selected is None else newest_selected.snapshot_id),
        "provider": None if snapshot is None else snapshot.provider,
        "purpose": None if snapshot is None else snapshot.purpose.value,
        "freshness": ("unknown" if refreshed is None else refreshed.freshness.value),
        "compatible": is_available,
        "reason": reason,
        "total_count": total_count,
        "covered_count": covered_count,
        "coverage_percent": (
            0.0 if total_count == 0 else round(covered_count / total_count * 100, 2)
        ),
        "gaps": [],
    }


def _snapshot_of_type(context: EvidenceContext, dataset_type: DatasetType) -> SnapshotRef | None:
    return next(
        (snapshot for snapshot in context.snapshots if snapshot.dataset_type is dataset_type),
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
            else groups[-1].lineup_br
            if groups
            else None
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


def _readiness_changes(before: LineupAnalysis, after: LineupAnalysis) -> tuple[str, ...]:
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
