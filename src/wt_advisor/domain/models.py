"""Transport-independent, immutable domain contracts."""

from __future__ import annotations

import json
from datetime import date, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any, Generic, Self, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FrozenModel(BaseModel):
    """Base model for immutable validated domain values."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class Nation(StrEnum):
    USA = "usa"


class GameMode(StrEnum):
    GROUND_REALISTIC = "ground_realistic"


class VehicleClass(StrEnum):
    LIGHT_TANK = "light_tank"
    MEDIUM_TANK = "medium_tank"
    HEAVY_TANK = "heavy_tank"
    TANK_DESTROYER = "tank_destroyer"
    SPAA = "spaa"


class AvailabilityType(StrEnum):
    RESEARCH_TREE = "research_tree"
    PREMIUM = "premium"
    EVENT = "event"
    SQUADRON = "squadron"
    PACK = "pack"


class AcquisitionType(StrEnum):
    RESEARCH = "research"
    RESERVE = "reserve"
    PREMIUM = "premium"
    PACK = "pack"
    EVENT = "event"
    GIFT = "gift"
    SQUADRON = "squadron"
    MARKETPLACE = "marketplace"
    UNKNOWN = "unknown"


class Researchability(StrEnum):
    NORMALLY_RESEARCHABLE = "normally_researchable"
    NON_RESEARCHABLE = "non_researchable"
    UNKNOWN = "unknown"


class TreeMembership(StrEnum):
    MAIN_TREE = "main_tree"
    FOLDERED = "foldered"
    NONE = "none"
    UNKNOWN = "unknown"


class Visibility(StrEnum):
    VISIBLE = "visible"
    HIDDEN = "hidden"
    LEGACY = "legacy"
    UNKNOWN = "unknown"


class Capability(StrEnum):
    SCOUTING = "scouting"
    ARTILLERY = "artillery"
    STABILIZER = "stabilizer"
    VERTICAL_STABILIZER = "vertical_stabilizer"
    RADAR = "radar"
    IRST = "irst"
    SAM = "sam"
    AUTOCANNON_AA = "autocannon_aa"
    OPEN_TOP = "open_top"
    SMOKE = "smoke"
    RECON_DRONE = "recon_drone"
    THERMALS = "thermals"
    ATGM = "atgm"
    HIGH_CALIBER_HE = "high_caliber_he"


class CapabilityState(StrEnum):
    PRESENT = "present"
    VERIFIED_ABSENT = "verified_absent"
    UNKNOWN = "unknown"
    CONFLICTED = "conflicted"


class CapabilitySourceType(StrEnum):
    COMMUNITY_API = "community_api"
    OFFICIAL_REFERENCE = "official_reference"
    CURATED_IMPORT = "curated_import"
    OTHER = "other"


class Role(StrEnum):
    BRAWLER = "brawler"
    GENERAL_MEDIUM = "general_medium"
    FLANKER = "flanker"
    SCOUT = "scout"
    SNIPER = "sniper"
    TANK_DESTROYER = "tank_destroyer"
    HEAVY_ANCHOR = "heavy_anchor"
    SPAA = "spaa"
    ANTI_ARMOR_SPECIALIST = "anti_armor_specialist"


class VehicleStatus(StrEnum):
    OWNED = "owned"
    RESEARCHING = "researching"
    UNLOCKED_NOT_PURCHASED = "unlocked_not_purchased"
    AVAILABLE_TO_RESEARCH = "available_to_research"
    LOCKED = "locked"
    UNKNOWN = "unknown"


class Freshness(StrEnum):
    FRESH = "fresh"
    AGING = "aging"
    STALE = "stale"
    UNKNOWN = "unknown"


class RuleStatus(StrEnum):
    GOOD = "good"
    WARNING = "warning"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


class DatasetType(StrEnum):
    VEHICLE_METADATA = "vehicle_metadata"
    BATTLE_RATINGS = "battle_ratings"
    TECH_TREE = "tech_tree"
    GLOBAL_STATISTICS = "global_statistics"
    CURATED_OVERRIDES = "curated_overrides"
    CAPABILITIES = "capabilities"
    AVAILABILITY = "availability"
    IDENTITY_ALIASES = "identity_aliases"
    RESEARCH_GRAPH = "research_graph"


class SnapshotPurpose(StrEnum):
    ACCEPTANCE = "acceptance"
    OPERATIONAL = "operational"


class StatisticsScope(StrEnum):
    GROUND_REALISTIC_GROUND_VEHICLES = "ground_realistic_ground_vehicles"
    REALISTIC_ALL_CONTEXTS = "realistic_all_contexts"
    AIR_REALISTIC = "air_realistic"
    UNKNOWN_REALISTIC_SCOPE = "unknown_realistic_scope"


class RatioProvenance(StrEnum):
    DERIVED_FROM_COUNTS = "derived_from_counts"
    PROVIDER_REPORTED = "provider_reported"
    REPORTED = "provider_reported"
    UNAVAILABLE = "unavailable"


class ResearchDomain(StrEnum):
    GROUND = "ground"


class ResearchEdgeType(StrEnum):
    NORMAL = "normal"
    FOLDER = "folder"
    BRANCH_UNLOCK = "branch_unlock"
    REQUIRED_PREDECESSOR = "required_predecessor"


class PrerequisiteSemantics(StrEnum):
    ALL = "all"
    ANY = "any"


class ComponentStatus(StrEnum):
    AVAILABLE = "available"
    STALE = "stale"
    INCOMPATIBLE = "incompatible"
    UNAVAILABLE = "unavailable"


BR_LADDER: tuple[int, ...] = tuple(
    value for whole in range(1, 15) for value in (whole * 10, whole * 10 + 3, whole * 10 + 7)
)


def br_step_distance(high_br: int, low_br: int) -> int:
    """Return ladder steps between BR values represented as integer tenths."""

    if high_br not in BR_LADDER or low_br not in BR_LADDER:
        raise ValueError("battle rating is not on the supported War Thunder BR ladder")
    if high_br < low_br:
        raise ValueError("high_br must be greater than or equal to low_br")
    return BR_LADDER.index(high_br) - BR_LADDER.index(low_br)


class Vehicle(FrozenModel):
    vehicle_id: str = Field(min_length=1, pattern=r"^[a-z0-9_]+$")
    source_vehicle_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    nation: Nation
    vehicle_class: VehicleClass
    rank: int = Field(ge=1)
    research_parent_id: str | None = None
    research_cost: int | None = Field(default=None, ge=0)
    purchase_cost: int | None = Field(default=None, ge=0)
    availability_type: AvailabilityType = AvailabilityType.RESEARCH_TREE
    capabilities: frozenset[Capability] = frozenset()


class CapabilityObservation(FrozenModel):
    vehicle_id: str = Field(min_length=1, pattern=r"^[a-z0-9_]+$")
    capability: Capability
    value: bool
    source_provider: str = Field(min_length=1)
    source_snapshot_id: str = Field(min_length=1)
    source_reference: str = Field(min_length=1)
    source_type: CapabilitySourceType
    confidence: float | None = Field(default=None, ge=0, le=1)


class CapabilityResolution(FrozenModel):
    vehicle_id: str = Field(min_length=1, pattern=r"^[a-z0-9_]+$")
    capability: Capability
    state: CapabilityState
    observations: tuple[CapabilityObservation, ...] = ()

    @property
    def status(self) -> CapabilityState:
        """Compatibility name used by storage and transport DTOs."""

        return self.state

    @classmethod
    def from_observations(
        cls,
        vehicle_id: str,
        capability: Capability,
        observations: tuple[CapabilityObservation, ...],
    ) -> Self:
        if any(
            row.vehicle_id != vehicle_id or row.capability is not capability
            for row in observations
        ):
            raise ValueError(
                "capability observations must match the resolved vehicle and capability"
            )
        values = {row.value for row in observations}
        if values == {True}:
            state = CapabilityState.PRESENT
        elif values == {False}:
            state = CapabilityState.VERIFIED_ABSENT
        elif values == {False, True}:
            state = CapabilityState.CONFLICTED
        else:
            state = CapabilityState.UNKNOWN
        return cls(
            vehicle_id=vehicle_id,
            capability=capability,
            state=state,
            observations=observations,
        )


class ResolvedAvailability(FrozenModel):
    vehicle_id: str = Field(min_length=1, pattern=r"^[a-z0-9_]+$")
    acquisition_type: AcquisitionType
    researchability: Researchability
    tree_membership: TreeMembership
    visibility: Visibility
    source_snapshot_id: str = Field(min_length=1)
    source_provider: str = Field(min_length=1)
    source_reference: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    provider_observation: str | None = None


class ResearchEdge(FrozenModel):
    nation: Nation
    domain: ResearchDomain
    parent_vehicle_id: str = Field(min_length=1, pattern=r"^[a-z0-9_]+$")
    child_vehicle_id: str = Field(min_length=1, pattern=r"^[a-z0-9_]+$")
    edge_type: ResearchEdgeType
    prerequisite_group: str = Field(min_length=1)
    prerequisite_semantics: PrerequisiteSemantics = PrerequisiteSemantics.ALL
    snapshot_id: str = Field(min_length=1)

    @property
    def group_semantics(self) -> PrerequisiteSemantics:
        """Compatibility name matching the persisted column."""

        return self.prerequisite_semantics

    @model_validator(mode="after")
    def valid_edge(self) -> ResearchEdge:
        if self.parent_vehicle_id == self.child_vehicle_id:
            raise ValueError("research edge cannot reference itself")
        return self


class ComponentDataStatus(FrozenModel):
    dataset_type: DatasetType
    status: ComponentStatus
    selected_snapshot_id: str | None = None
    newest_snapshot_id: str | None = None
    provider: str | None = None
    purpose: SnapshotPurpose
    freshness: Freshness
    compatible: bool
    reason: str | None = None
    total_count: int = Field(default=0, ge=0)
    covered_count: int = Field(default=0, ge=0)
    coverage_percent: float = Field(default=0, ge=0, le=100)
    gaps: tuple[str, ...] = ()

    @model_validator(mode="after")
    def valid_coverage(self) -> ComponentDataStatus:
        if self.covered_count > self.total_count:
            raise ValueError("covered_count must not exceed total_count")
        expected = 0.0 if self.total_count == 0 else self.covered_count / self.total_count * 100
        if abs(expected - self.coverage_percent) > 0.01:
            raise ValueError("coverage_percent must match covered_count and total_count")
        return self


class VehicleStatistics(FrozenModel):
    vehicle_id: str
    snapshot_id: str
    mode_scope: StatisticsScope
    sample_start: date | None = None
    sample_end: date | None = None
    battles: int | None = Field(default=None, ge=0)
    wins: int | None = Field(default=None, ge=0)
    losses: int | None = Field(default=None, ge=0)
    win_rate: float | None = Field(default=None, ge=0, le=1)
    kills: int | None = Field(default=None, ge=0)
    ground_kills: int | None = Field(default=None, ge=0)
    air_kills: int | None = Field(default=None, ge=0)
    deaths: int | None = Field(default=None, ge=0)
    reported_win_rate: float | None = Field(default=None, ge=0, le=1)
    reported_kd: float | None = Field(default=None, ge=0)
    reported_kills_per_battle: float | None = Field(default=None, ge=0)
    ratio_provenance: RatioProvenance | None = None

    @model_validator(mode="after")
    def consistent_observation(self) -> VehicleStatistics:
        if (
            self.sample_start is not None
            and self.sample_end is not None
            and self.sample_start > self.sample_end
        ):
            raise ValueError("sample_start must not be after sample_end")
        if self.battles is not None:
            for field_name, value in (("wins", self.wins), ("losses", self.losses)):
                if value is not None and value > self.battles:
                    raise ValueError(f"{field_name} must not exceed battles")
            if (
                self.wins is not None
                and self.losses is not None
                and self.wins + self.losses > self.battles
            ):
                raise ValueError("wins plus losses must not exceed battles")
            if self.battles > 0 and self.wins is not None and self.win_rate is not None:
                observed_rate = self.wins / self.battles
                if abs(observed_rate - self.win_rate) > 0.01:
                    raise ValueError("win_rate is inconsistent with wins and battles")
            if (
                self.battles > 0
                and self.wins is not None
                and self.reported_win_rate is not None
                and abs(self.wins / self.battles - self.reported_win_rate) > 0.01
            ):
                raise ValueError("reported_win_rate is inconsistent with wins and battles")
            if (
                self.battles > 0
                and self.kills is not None
                and self.reported_kills_per_battle is not None
                and abs(self.kills / self.battles - self.reported_kills_per_battle) > 0.01
            ):
                raise ValueError(
                    "reported_kills_per_battle is inconsistent with kills and battles"
                )
        if (
            self.deaths is not None
            and self.deaths > 0
            and self.kills is not None
            and self.reported_kd is not None
            and abs(self.kills / self.deaths - self.reported_kd) > 0.01
        ):
            raise ValueError("reported_kd is inconsistent with kills and deaths")
        return self

    @property
    def derived_win_rate(self) -> float | None:
        if self.battles and self.wins is not None:
            return self.wins / self.battles
        return None

    @property
    def derived_kd(self) -> float | None:
        if self.deaths and self.kills is not None:
            return self.kills / self.deaths
        return None

    @property
    def derived_kills_per_battle(self) -> float | None:
        if self.battles and self.kills is not None:
            return self.kills / self.battles
        return None

    def ratio_source(self, metric: str) -> RatioProvenance:
        derived = {
            "win_rate": self.derived_win_rate,
            "kd": self.derived_kd,
            "kills_per_battle": self.derived_kills_per_battle,
        }
        reported = {
            "win_rate": (
                self.reported_win_rate
                if self.reported_win_rate is not None
                else self.win_rate
            ),
            "kd": self.reported_kd,
            "kills_per_battle": self.reported_kills_per_battle,
        }
        if metric not in derived:
            raise ValueError(f"unsupported ratio metric: {metric}")
        if derived[metric] is not None:
            return RatioProvenance.DERIVED_FROM_COUNTS
        if reported[metric] is not None:
            return RatioProvenance.REPORTED
        return RatioProvenance.UNAVAILABLE


class StatisticsInspection(FrozenModel):
    valid: bool
    recognized_columns: tuple[str, ...]
    unknown_columns: tuple[str, ...] = ()
    total_rows: int = Field(ge=0)
    matched_rows: int = Field(ge=0)
    canonical_match_rate: float = Field(ge=0, le=1)
    unresolved_source_ids: tuple[str, ...] = ()
    duplicate_observations: tuple[str, ...] = ()
    scopes: tuple[StatisticsScope, ...] = ()
    sample_start: date | None = None
    sample_end: date | None = None
    metric_coverage: dict[str, float] = Field(default_factory=dict)
    prospective_snapshot_id: str
    errors: tuple[str, ...] = ()


class SnapshotRef(FrozenModel):
    snapshot_id: str
    dataset_type: DatasetType
    provider: str
    retrieved_at: datetime
    source_revision: str | None = None
    checksum: str = Field(pattern=r"^[a-f0-9]{64}$")
    freshness: Freshness
    sample_start: date | None = None
    sample_end: date | None = None
    purpose: SnapshotPurpose = SnapshotPurpose.OPERATIONAL
    compatibility_key: str | None = None


class EvidenceContext(FrozenModel):
    snapshots: tuple[SnapshotRef, ...]
    override_revision: str
    ruleset_hash: str
    schema_revision: str


class RuleResult(FrozenModel):
    rule: str
    score: float | None = Field(default=None, ge=0, le=100)
    effective_score: float = Field(ge=0, le=100)
    status: RuleStatus
    evidence: dict[str, Any]
    warnings: tuple[str, ...] = ()
    explanation: str


class Lineup(FrozenModel):
    nation: Nation
    mode: GameMode
    slots: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_slots(self) -> Lineup:
        if len(set(self.slots)) != len(self.slots):
            raise ValueError("lineup vehicle IDs must be unique")
        return self


class LineupAnalysis(FrozenModel):
    lineup: Lineup
    resolved_brs: dict[str, int]
    lineup_br: int
    rules: tuple[RuleResult, ...]
    overall_score: float = Field(ge=0, le=100)
    warnings: tuple[str, ...] = ()
    strengths: tuple[str, ...] = ()
    readiness_passed: bool
    readiness_failures: tuple[str, ...] = ()
    evidence_context: EvidenceContext
    analysis_id: str


class UserProfile(FrozenModel):
    profile_id: str
    nation: Nation = Nation.USA
    preferred_mode: GameMode = GameMode.GROUND_REALISTIC
    crew_slots: int = Field(default=5, ge=1, le=10)
    include_premiums: bool = False
    include_event_vehicles: bool = False
    include_pack_vehicles: bool = False


class VehicleView(FrozenModel):
    vehicle: Vehicle
    br: int
    roles: frozenset[Role] = frozenset()
    provenance: dict[str, Any] = Field(default_factory=dict)


class CandidateLineup(FrozenModel):
    analysis: LineupAnalysis
    dominated: bool = False


class CandidateGroup(FrozenModel):
    lineup_br: int
    candidates: tuple[CandidateLineup, ...]


class GenerationResult(FrozenModel):
    groups: tuple[CandidateGroup, ...]
    recommended: CandidateLineup | None
    lower_br_alternative: CandidateGroup | None = None
    analysis_id: str
    evidence_context: EvidenceContext


class LineupComparison(FrozenModel):
    lineup_a: LineupAnalysis
    lineup_b: LineupAnalysis
    component_deltas: dict[str, float]
    cross_br: bool
    overall_delta: float | None
    evidence_context: EvidenceContext
    analysis_id: str


class AdditionEvaluation(FrozenModel):
    vehicle_id: str
    resulting_analysis: LineupAnalysis
    component_deltas: dict[str, float]
    br_delta: int
    cross_br: bool
    overall_delta: float | None
    new_warnings: tuple[str, ...] = ()
    resolved_warnings: tuple[str, ...] = ()


class UnlockEvaluation(FrozenModel):
    vehicle_id: str
    research_cost: int
    before: CandidateLineup
    expanded_best: CandidateLineup
    forced_include: CandidateLineup
    adopted: bool
    br_delta: int
    cross_br: bool
    overall_delta: float | None
    component_deltas: dict[str, float]
    current_status: VehicleStatus = VehicleStatus.UNKNOWN
    research_prerequisites: tuple[str, ...] = ()
    prerequisite_statuses: dict[str, VehicleStatus] = Field(default_factory=dict)
    graph_snapshot_id: str | None = None
    resulting_br: int | None = None
    readiness_before: bool | None = None
    readiness_expanded: bool | None = None
    readiness_forced: bool | None = None
    readiness_changes: tuple[str, ...] = ()
    role_changes: tuple[str, ...] = ()
    adopted_immediately: bool = False
    opens_new_ready_frontier: bool = False
    improves_existing_frontier: bool = False
    fills_missing_role: bool = False
    provides_stronger_backup: bool = False


class UserProgress(FrozenModel):
    profile: UserProfile
    vehicle_statuses: dict[str, VehicleStatus]
    revision: str


class VehicleStatusChange(FrozenModel):
    profile_id: str
    vehicle_id: str
    before_status: VehicleStatus
    after_status: VehicleStatus
    revision: str


class ReconciliationItem(FrozenModel):
    deprecated_vehicle_id: str = Field(min_length=1)
    canonical_vehicle_id: str = Field(min_length=1)
    deprecated_status: VehicleStatus
    canonical_status: VehicleStatus
    chosen_status: VehicleStatus
    reason: str = Field(min_length=1)
    collision: bool


class ReconciliationPlan(FrozenModel):
    profile_id: str = Field(min_length=1)
    expected_revision: str = Field(min_length=1)
    alias_revision: str = Field(min_length=1)
    items: tuple[ReconciliationItem, ...]
    plan_id: str = Field(min_length=1)
    already_applied: bool = False


class ReconciliationResult(FrozenModel):
    plan: ReconciliationPlan
    applied: bool
    resulting_revision: str | None = None


T = TypeVar("T")


class ServiceResult(FrozenModel, Generic[T]):
    schema_version: str = "1.0"
    analysis_id: str | None = None
    evidence_context: EvidenceContext | None = None
    data: T
    warnings: tuple[str, ...] = ()


def stable_hash(value: BaseModel | dict[str, Any]) -> str:
    """Hash a model/dictionary using canonical JSON for repeatable analysis IDs."""

    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return sha256(encoded.encode("utf-8")).hexdigest()
