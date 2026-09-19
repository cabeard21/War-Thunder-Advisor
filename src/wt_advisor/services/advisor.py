"""Deterministic orchestration over fixture/provider evidence and pure rules."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import date
from itertools import combinations
from pathlib import Path
from typing import Any

from wt_advisor.data.freshness import evaluate_freshness
from wt_advisor.data.providers.fixture import FixtureProvider
from wt_advisor.data.providers.wt_vehicles_api import WarThunderVehiclesApiProvider
from wt_advisor.domain.models import (
    AdditionEvaluation,
    AvailabilityType,
    CandidateGroup,
    CandidateLineup,
    DatasetType,
    EvidenceContext,
    GameMode,
    GenerationResult,
    Lineup,
    LineupAnalysis,
    LineupComparison,
    Nation,
    RuleStatus,
    SnapshotRef,
    StatisticsScope,
    UnlockEvaluation,
    UserProfile,
    UserProgress,
    Vehicle,
    VehicleClass,
    VehicleStatistics,
    VehicleStatus,
    VehicleStatusChange,
    VehicleView,
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
        research_edges: Sequence[tuple[str, str]],
        evidence_context: EvidenceContext,
        battle_rating_provenance: dict[str, dict[str, Any]] | None = None,
        ruleset: Ruleset | None = None,
        repository: EvidenceRepository | None = None,
        bundle_resolution: ResolvedAnalysisBundle | None = None,
        service_warnings: tuple[str, ...] = (),
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
        self._evidence_context = evidence_context
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
        ruleset = load_ruleset()
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
        active_snapshots = tuple(
            _refresh_snapshot(snapshot)
            for snapshot in (vehicle_snapshot, statistics_snapshot)
            if snapshot is not None
        )
        service_warnings = tuple(
            dict.fromkeys((*resolution.warnings, *_freshness_warnings(active_snapshots)))
        )
        ruleset = load_ruleset()
        return cls(
            vehicles=vehicles,
            battle_ratings=battle_ratings,
            statistics=statistics,
            profile=bundle.user_profile,
            vehicle_statuses=repository.get_user_vehicle_states(bundle.user_profile.profile_id),
            research_edges=repository.list_research_edges(
                snapshot_id=vehicle_snapshot.snapshot_id
            ),
            evidence_context=EvidenceContext(
                snapshots=active_snapshots,
                override_revision=overrides.revision,
                ruleset_hash=ruleset.content_hash,
                schema_revision="0002",
            ),
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
                self._bundle_resolution.newest_vehicle_snapshot
            )
            newest_statistics = self._bundle_resolution.newest_statistics_snapshot
            if newest_statistics is not None:
                newest_statistics = _refresh_snapshot(newest_statistics)
            compatibility = self._bundle_resolution.newest_compatibility
        statistics_snapshot_id = (
            None if statistics_snapshot is None else statistics_snapshot.snapshot_id
        )
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
            "active": {
                "bundle_id": stable_hash(
                    {
                        "vehicle_snapshot_id": vehicle_snapshot_id,
                        "statistics_snapshot_id": statistics_snapshot_id,
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
        """Persist a bounded live metadata snapshot for activation on the next load."""

        if self._repository is None:
            raise ValueError("live import requires a database-backed service")
        dataset = WarThunderVehiclesApiProvider().fetch_vehicles()
        self._repository.import_vehicle_dataset(dataset)
        return dataset.snapshot

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
        evaluation = evaluate_lineup_rules(
            vehicles,
            resolved_brs,
            statistics_by_vehicle=self._scoring_statistics(),
            peer_statistics=self._peer_statistics(),
            nearby_spaa=nearby_spaa,
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
        lower = _lower_alternative(groups, recommended)
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
            output.append(
                UnlockEvaluation(
                    vehicle_id=vehicle_id,
                    research_cost=vehicle.research_cost or 0,
                    before=current,
                    expanded_best=best,
                    forced_include=included,
                    adopted=vehicle_id in best.analysis.lineup.slots,
                    br_delta=best.analysis.lineup_br - current.analysis.lineup_br,
                    cross_br=cross_br,
                    overall_delta=(
                        None
                        if cross_br
                        else best.analysis.overall_score - current.analysis.overall_score
                    ),
                    component_deltas=_component_deltas(current.analysis, best.analysis),
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
            if (
                vehicle.availability_type is AvailabilityType.PREMIUM
                and not self._profile.include_premiums
            ):
                continue
            if (
                vehicle.availability_type is AvailabilityType.EVENT
                and not self._profile.include_event_vehicles
            ):
                continue
            if (
                vehicle.availability_type is AvailabilityType.PACK
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
            and self._vehicles[vehicle_id].availability_type is AvailabilityType.RESEARCH_TREE
        }
        parents = {child: parent for parent, child in self._research_edges}
        return tuple(
            sorted(
                vehicle_id
                for vehicle_id in candidates
                if (parent := parents.get(vehicle_id)) is None
                or self._statuses.get(parent) is VehicleStatus.OWNED
            )
        )

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
        return {
            "vehicle_snapshot_id": self._vehicle_snapshot_id(),
            "battle_rating": self._battle_rating_provenance.get(vehicle_id),
        }


def _combination_count(pool: int, slots: int) -> int:
    if slots < 0 or slots > pool:
        return 0
    numerator = denominator = 1
    for value in range(1, slots + 1):
        numerator *= pool - slots + value
        denominator *= value
    return numerator // denominator


def _refresh_snapshot(snapshot: SnapshotRef) -> SnapshotRef:
    observed = snapshot.sample_end or snapshot.retrieved_at.date()
    return snapshot.model_copy(
        update={"freshness": evaluate_freshness(snapshot.dataset_type, observed, date.today())}
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
    groups: Sequence[CandidateGroup], recommended: CandidateLineup | None
) -> CandidateGroup | None:
    if recommended is None:
        least_deficient = _least_deficient(groups)
        if least_deficient is None:
            return None
        return next(
            group for group in groups if least_deficient in group.candidates
        )
    lower = [group for group in groups if group.lineup_br < recommended.analysis.lineup_br]
    ready = [
        group for group in lower if any(item.analysis.readiness_passed for item in group.candidates)
    ]
    return (ready or lower)[-1] if (ready or lower) else None


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


def _addition_sort_key(item: AdditionEvaluation) -> tuple[Any, ...]:
    return (
        not item.resulting_analysis.readiness_passed,
        item.resulting_analysis.lineup_br,
        -item.resulting_analysis.overall_score,
        item.vehicle_id,
    )
