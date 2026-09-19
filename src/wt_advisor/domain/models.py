"""Transport-independent, immutable domain contracts."""

from __future__ import annotations

import json
from datetime import date, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any, Generic, TypeVar

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


class Capability(StrEnum):
    SCOUTING = "scouting"
    ARTILLERY = "artillery"
    STABILIZER = "stabilizer"
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


class SnapshotPurpose(StrEnum):
    ACCEPTANCE = "acceptance"
    OPERATIONAL = "operational"


class StatisticsScope(StrEnum):
    GROUND_REALISTIC_GROUND_VEHICLES = "ground_realistic_ground_vehicles"
    REALISTIC_ALL_CONTEXTS = "realistic_all_contexts"
    AIR_REALISTIC = "air_realistic"
    UNKNOWN_REALISTIC_SCOPE = "unknown_realistic_scope"


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
        return self


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
