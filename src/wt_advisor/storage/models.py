"""SQLAlchemy persistence models.

The rows deliberately mirror evidence as observed. Resolution (for example applying a
curated override) happens in the repository and never rewrites an imported row.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class DataSnapshotRow(Base):
    __tablename__ = "data_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "dataset_type",
            "provider",
            "source_revision",
            "checksum",
            "provider_version",
            name="uq_snapshot_source_content",
        ),
    )

    snapshot_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    dataset_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(120), nullable=False)
    provider_version: Mapped[str | None] = mapped_column(String(120))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_revision: Mapped[str | None] = mapped_column(String(160))
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    freshness: Mapped[str] = mapped_column(String(20), nullable=False)
    sample_start: Mapped[date | None] = mapped_column(Date)
    sample_end: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    purpose: Mapped[str] = mapped_column(String(30), nullable=False, default="operational")
    compatibility_key: Mapped[str | None] = mapped_column(String(160))


class RawArtifactRow(Base):
    __tablename__ = "raw_artifacts"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "checksum", name="uq_raw_artifact_snapshot_checksum"),
    )

    artifact_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("data_snapshots.snapshot_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    media_type: Mapped[str] = mapped_column(String(120), nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


class VehicleRow(Base):
    __tablename__ = "vehicles"

    vehicle_id: Mapped[str] = mapped_column(String(160), primary_key=True)


class VehicleSourceIdRow(Base):
    __tablename__ = "vehicle_source_ids"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id", "provider", "source_vehicle_id", name="uq_source_vehicle_snapshot"
        ),
        ForeignKeyConstraint(["snapshot_id"], ["data_snapshots.snapshot_id"], ondelete="RESTRICT"),
    )

    snapshot_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    vehicle_id: Mapped[str] = mapped_column(
        ForeignKey("vehicles.vehicle_id", ondelete="RESTRICT"), primary_key=True
    )
    provider: Mapped[str] = mapped_column(String(120), nullable=False)
    source_vehicle_id: Mapped[str] = mapped_column(String(200), nullable=False)


class VehicleMetadataRow(Base):
    __tablename__ = "vehicle_metadata"
    __table_args__ = (
        ForeignKeyConstraint(["snapshot_id"], ["data_snapshots.snapshot_id"], ondelete="RESTRICT"),
    )

    snapshot_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    vehicle_id: Mapped[str] = mapped_column(
        ForeignKey("vehicles.vehicle_id", ondelete="RESTRICT"), primary_key=True
    )
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    nation: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    vehicle_class: Mapped[str] = mapped_column(String(60), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    research_cost: Mapped[int | None] = mapped_column(Integer)
    purchase_cost: Mapped[int | None] = mapped_column(Integer)
    availability_type: Mapped[str] = mapped_column(String(40), nullable=False)


class VehicleBattleRatingRow(Base):
    __tablename__ = "vehicle_battle_ratings"

    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("data_snapshots.snapshot_id", ondelete="RESTRICT"), primary_key=True
    )
    vehicle_id: Mapped[str] = mapped_column(
        ForeignKey("vehicles.vehicle_id", ondelete="RESTRICT"), primary_key=True
    )
    mode: Mapped[str] = mapped_column(String(50), primary_key=True)
    battle_rating: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(200), nullable=False)


class VehicleCapabilityRow(Base):
    __tablename__ = "vehicle_capabilities"

    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("data_snapshots.snapshot_id", ondelete="RESTRICT"), primary_key=True
    )
    vehicle_id: Mapped[str] = mapped_column(
        ForeignKey("vehicles.vehicle_id", ondelete="RESTRICT"), primary_key=True
    )
    capability: Mapped[str] = mapped_column(String(60), primary_key=True)


class ResearchEdgeRow(Base):
    __tablename__ = "research_edges"

    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("data_snapshots.snapshot_id", ondelete="RESTRICT"), primary_key=True
    )
    parent_vehicle_id: Mapped[str] = mapped_column(
        ForeignKey("vehicles.vehicle_id", ondelete="RESTRICT"), primary_key=True
    )
    child_vehicle_id: Mapped[str] = mapped_column(
        ForeignKey("vehicles.vehicle_id", ondelete="RESTRICT"), primary_key=True
    )


class VehicleStatisticsRow(Base):
    __tablename__ = "vehicle_statistics"

    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("data_snapshots.snapshot_id", ondelete="RESTRICT"), primary_key=True
    )
    vehicle_id: Mapped[str] = mapped_column(
        ForeignKey("vehicles.vehicle_id", ondelete="RESTRICT"), primary_key=True
    )
    mode_scope: Mapped[str] = mapped_column(String(80), primary_key=True)
    sample_start: Mapped[date | None] = mapped_column(Date)
    sample_end: Mapped[date | None] = mapped_column(Date)
    battles: Mapped[int | None] = mapped_column(Integer)
    wins: Mapped[int | None] = mapped_column(Integer)
    losses: Mapped[int | None] = mapped_column(Integer)
    win_rate: Mapped[float | None] = mapped_column(Float)
    kills: Mapped[int | None] = mapped_column(Integer)
    ground_kills: Mapped[int | None] = mapped_column(Integer)
    air_kills: Mapped[int | None] = mapped_column(Integer)
    deaths: Mapped[int | None] = mapped_column(Integer)
    reported_win_rate: Mapped[float | None] = mapped_column(Float)
    reported_kd: Mapped[float | None] = mapped_column(Float)
    reported_kills_per_battle: Mapped[float | None] = mapped_column(Float)
    ratio_provenance: Mapped[str | None] = mapped_column(String(60))
    kill_target_definition: Mapped[str] = mapped_column(
        String(40), nullable=False, default="all_targets"
    )


class CapabilityObservationRow(Base):
    __tablename__ = "capability_observations"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id",
            "vehicle_id",
            "capability",
            "source_reference",
            name="uq_capability_observation_source",
        ),
    )

    observation_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("data_snapshots.snapshot_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    vehicle_id: Mapped[str] = mapped_column(
        ForeignKey("vehicles.vehicle_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    capability: Mapped[str] = mapped_column(String(60), nullable=False)
    value: Mapped[bool] = mapped_column(Boolean, nullable=False)
    source_provider: Mapped[str] = mapped_column(String(120), nullable=False)
    source_type: Mapped[str] = mapped_column(String(60), nullable=False)
    source_reference: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    verified_at: Mapped[date | None] = mapped_column(Date)
    source_revision: Mapped[str | None] = mapped_column(String(160))


class AvailabilityObservationRow(Base):
    __tablename__ = "availability_observations"

    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("data_snapshots.snapshot_id", ondelete="RESTRICT"), primary_key=True
    )
    vehicle_id: Mapped[str] = mapped_column(
        ForeignKey("vehicles.vehicle_id", ondelete="RESTRICT"), primary_key=True
    )
    acquisition_type: Mapped[str] = mapped_column(String(60), nullable=False)
    researchability: Mapped[str] = mapped_column(String(60), nullable=False)
    tree_membership: Mapped[str] = mapped_column(String(60), nullable=False)
    visibility: Mapped[str] = mapped_column(String(60), nullable=False)
    source_provider: Mapped[str] = mapped_column(String(120), nullable=False)
    source_reference: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)


class VehicleIdentityAliasRow(Base):
    __tablename__ = "vehicle_identity_aliases"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id", "provider", "source_vehicle_id", name="uq_identity_alias_source"
        ),
    )

    alias_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("data_snapshots.snapshot_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(120), nullable=False)
    source_vehicle_id: Mapped[str] = mapped_column(String(200), nullable=False)
    canonical_vehicle_id: Mapped[str] = mapped_column(
        ForeignKey("vehicles.vehicle_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    deprecated_vehicle_id: Mapped[str | None] = mapped_column(String(160))
    confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    source_reference: Mapped[str] = mapped_column(Text, nullable=False)


class ResearchGraphEdgeRow(Base):
    __tablename__ = "research_graph_edges"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id",
            "parent_vehicle_id",
            "child_vehicle_id",
            "edge_type",
            "prerequisite_group",
            name="uq_research_graph_edge",
        ),
    )

    edge_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("data_snapshots.snapshot_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    nation: Mapped[str] = mapped_column(String(40), nullable=False)
    domain: Mapped[str] = mapped_column(String(40), nullable=False)
    parent_vehicle_id: Mapped[str] = mapped_column(
        ForeignKey("vehicles.vehicle_id", ondelete="RESTRICT"), nullable=False
    )
    child_vehicle_id: Mapped[str] = mapped_column(
        ForeignKey("vehicles.vehicle_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    edge_type: Mapped[str] = mapped_column(String(60), nullable=False)
    prerequisite_group: Mapped[str] = mapped_column(String(120), nullable=False)
    group_semantics: Mapped[str] = mapped_column(String(10), nullable=False)


class ImportAttemptRow(Base):
    __tablename__ = "import_attempts"
    __table_args__ = (
        UniqueConstraint(
            "dataset_type", "provider", "raw_checksum", "status", name="uq_import_attempt"
        ),
    )

    attempt_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dataset_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(120), nullable=False)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    raw_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    media_type: Mapped[str] = mapped_column(String(120), nullable=False)
    raw_content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


class StatisticsImportDiagnosticRow(Base):
    __tablename__ = "statistics_import_diagnostics"

    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("data_snapshots.snapshot_id", ondelete="RESTRICT"), primary_key=True
    )
    recognized_columns: Mapped[Any] = mapped_column(JSON, nullable=False)
    unknown_columns: Mapped[Any] = mapped_column(JSON, nullable=False)
    matched_count: Mapped[int] = mapped_column(Integer, nullable=False)
    unresolved_source_ids: Mapped[Any] = mapped_column(JSON, nullable=False)
    duplicate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class OverrideRevisionRow(Base):
    __tablename__ = "override_revisions"

    revision: Mapped[str] = mapped_column(String(160), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)


class OverrideEntryRow(Base):
    __tablename__ = "override_entries"
    __table_args__ = (
        UniqueConstraint("revision", "vehicle_id", "field", name="uq_override_field"),
    )

    entry_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    revision: Mapped[str] = mapped_column(
        ForeignKey("override_revisions.revision", ondelete="RESTRICT"), nullable=False, index=True
    )
    vehicle_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    field: Mapped[str] = mapped_column(String(160), nullable=False)
    value: Mapped[Any] = mapped_column(JSON, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    reference: Mapped[str] = mapped_column(Text, nullable=False)
    added_at: Mapped[date] = mapped_column(Date, nullable=False)


class UserProfileRow(Base):
    __tablename__ = "user_profiles"

    profile_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    nation: Mapped[str] = mapped_column(String(40), nullable=False)
    preferred_mode: Mapped[str] = mapped_column(String(50), nullable=False)
    crew_slots: Mapped[int] = mapped_column(Integer, nullable=False)
    include_premiums: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    include_event_vehicles: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    include_pack_vehicles: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    write_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class UserVehicleStateRow(Base):
    __tablename__ = "user_vehicle_states"

    profile_id: Mapped[str] = mapped_column(
        ForeignKey("user_profiles.profile_id", ondelete="CASCADE"), primary_key=True
    )
    vehicle_id: Mapped[str] = mapped_column(
        ForeignKey("vehicles.vehicle_id", ondelete="RESTRICT"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(60), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    superseded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_by_vehicle_id: Mapped[str | None] = mapped_column(String(160))


class LineupPresetRow(Base):
    __tablename__ = "lineup_presets"
    __table_args__ = (
        UniqueConstraint("profile_id", "normalized_name", name="uq_preset_profile_name"),
    )
    preset_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    profile_id: Mapped[str] = mapped_column(
        ForeignKey("user_profiles.profile_id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(160), nullable=False)
    slots: Mapped[Any] = mapped_column(JSON, nullable=False)
    required_vehicle_ids: Mapped[Any] = mapped_column(JSON, nullable=False)
    excluded_vehicle_ids: Mapped[Any] = mapped_column(JSON, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AdvisorContextRow(Base):
    __tablename__ = "advisor_contexts"
    profile_id: Mapped[str] = mapped_column(
        ForeignKey("user_profiles.profile_id", ondelete="CASCADE"), primary_key=True
    )
    selected_preset_id: Mapped[str | None] = mapped_column(
        ForeignKey("lineup_presets.preset_id", ondelete="SET NULL")
    )
    target_br: Mapped[int | None] = mapped_column(Integer)
    required_vehicle_ids: Mapped[Any] = mapped_column(JSON, nullable=False)
    excluded_vehicle_ids: Mapped[Any] = mapped_column(JSON, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class StoredEvaluationRow(Base):
    """Immutable, fully captured result of an explicit M3 evaluation."""

    __tablename__ = "stored_evaluations"
    evaluation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    profile_id: Mapped[str] = mapped_column(
        ForeignKey("user_profiles.profile_id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    profile_revision: Mapped[str] = mapped_column(String(80), nullable=False)
    profile_state: Mapped[Any] = mapped_column(JSON, nullable=False)
    effective_inputs: Mapped[Any] = mapped_column(JSON, nullable=False)
    evidence_context: Mapped[Any] = mapped_column(JSON, nullable=False)
    ruleset_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_revision: Mapped[str] = mapped_column(String(20), nullable=False)
    result_id: Mapped[str] = mapped_column(String(64), nullable=False)
    result_payload: Mapped[Any] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProfileReconciliationAuditRow(Base):
    __tablename__ = "profile_reconciliation_audits"
    __table_args__ = (
        UniqueConstraint(
            "profile_id", "identity_snapshot_id", "source_revision", name="uq_reconciliation_run"
        ),
    )

    audit_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[str] = mapped_column(
        ForeignKey("user_profiles.profile_id", ondelete="CASCADE"), nullable=False, index=True
    )
    identity_snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("data_snapshots.snapshot_id", ondelete="RESTRICT"), nullable=False
    )
    source_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    resulting_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProfileReconciliationItemRow(Base):
    __tablename__ = "profile_reconciliation_items"

    item_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    audit_id: Mapped[int] = mapped_column(
        ForeignKey("profile_reconciliation_audits.audit_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    deprecated_vehicle_id: Mapped[str] = mapped_column(String(160), nullable=False)
    canonical_vehicle_id: Mapped[str] = mapped_column(String(160), nullable=False)
    deprecated_status: Mapped[str] = mapped_column(String(60), nullable=False)
    canonical_status: Mapped[str | None] = mapped_column(String(60))
    chosen_status: Mapped[str] = mapped_column(String(60), nullable=False)
    conflict: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
