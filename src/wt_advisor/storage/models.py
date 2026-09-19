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
        ForeignKeyConstraint(
            ["snapshot_id"], ["data_snapshots.snapshot_id"], ondelete="RESTRICT"
        ),
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
        ForeignKeyConstraint(
            ["snapshot_id"], ["data_snapshots.snapshot_id"], ondelete="RESTRICT"
        ),
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
