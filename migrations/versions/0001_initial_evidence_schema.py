"""Initial Milestone 1 evidence and user-state schema.

This revision is deliberately independent of the evolving ORM metadata.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "data_snapshots",
        sa.Column("snapshot_id", sa.String(160), primary_key=True),
        sa.Column("dataset_type", sa.String(40), nullable=False),
        sa.Column("provider", sa.String(120), nullable=False),
        sa.Column("provider_version", sa.String(120)),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_revision", sa.String(160)),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("freshness", sa.String(20), nullable=False),
        sa.Column("sample_start", sa.Date()),
        sa.Column("sample_end", sa.Date()),
        sa.Column("notes", sa.Text()),
        sa.UniqueConstraint(
            "dataset_type",
            "provider",
            "source_revision",
            "checksum",
            name="uq_snapshot_source_content",
        ),
    )
    op.create_index("ix_data_snapshots_dataset_type", "data_snapshots", ["dataset_type"])
    op.create_table("vehicles", sa.Column("vehicle_id", sa.String(160), primary_key=True))
    op.create_table(
        "override_revisions",
        sa.Column("revision", sa.String(160), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("notes", sa.Text()),
    )
    op.create_table(
        "user_profiles",
        sa.Column("profile_id", sa.String(160), primary_key=True),
        sa.Column("nation", sa.String(40), nullable=False),
        sa.Column("preferred_mode", sa.String(50), nullable=False),
        sa.Column("crew_slots", sa.Integer(), nullable=False),
        sa.Column("include_premiums", sa.Boolean(), nullable=False),
        sa.Column("include_event_vehicles", sa.Boolean(), nullable=False),
        sa.Column("include_pack_vehicles", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
    )
    op.create_table(
        "raw_artifacts",
        sa.Column("artifact_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("snapshot_id", sa.String(160), nullable=False),
        sa.Column("media_type", sa.String(120), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["data_snapshots.snapshot_id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("snapshot_id", "checksum", name="uq_raw_artifact_snapshot_checksum"),
    )
    op.create_index("ix_raw_artifacts_snapshot_id", "raw_artifacts", ["snapshot_id"])
    op.create_table(
        "vehicle_source_ids",
        sa.Column("snapshot_id", sa.String(160), primary_key=True),
        sa.Column("vehicle_id", sa.String(160), primary_key=True),
        sa.Column("provider", sa.String(120), nullable=False),
        sa.Column("source_vehicle_id", sa.String(200), nullable=False),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["data_snapshots.snapshot_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["vehicle_id"], ["vehicles.vehicle_id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "snapshot_id", "provider", "source_vehicle_id", name="uq_source_vehicle_snapshot"
        ),
    )
    op.create_table(
        "vehicle_metadata",
        sa.Column("snapshot_id", sa.String(160), primary_key=True),
        sa.Column("vehicle_id", sa.String(160), primary_key=True),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("nation", sa.String(40), nullable=False),
        sa.Column("vehicle_class", sa.String(60), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("research_cost", sa.Integer()),
        sa.Column("purchase_cost", sa.Integer()),
        sa.Column("availability_type", sa.String(40), nullable=False),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["data_snapshots.snapshot_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["vehicle_id"], ["vehicles.vehicle_id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_vehicle_metadata_nation", "vehicle_metadata", ["nation"])
    op.create_table(
        "vehicle_battle_ratings",
        sa.Column("snapshot_id", sa.String(160), primary_key=True),
        sa.Column("vehicle_id", sa.String(160), primary_key=True),
        sa.Column("mode", sa.String(50), primary_key=True),
        sa.Column("battle_rating", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(200), nullable=False),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["data_snapshots.snapshot_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["vehicle_id"], ["vehicles.vehicle_id"], ondelete="RESTRICT"),
    )
    op.create_table(
        "vehicle_capabilities",
        sa.Column("snapshot_id", sa.String(160), primary_key=True),
        sa.Column("vehicle_id", sa.String(160), primary_key=True),
        sa.Column("capability", sa.String(60), primary_key=True),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["data_snapshots.snapshot_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["vehicle_id"], ["vehicles.vehicle_id"], ondelete="RESTRICT"),
    )
    op.create_table(
        "research_edges",
        sa.Column("snapshot_id", sa.String(160), primary_key=True),
        sa.Column("parent_vehicle_id", sa.String(160), primary_key=True),
        sa.Column("child_vehicle_id", sa.String(160), primary_key=True),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["data_snapshots.snapshot_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["parent_vehicle_id"], ["vehicles.vehicle_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["child_vehicle_id"], ["vehicles.vehicle_id"], ondelete="RESTRICT"),
    )
    op.create_table(
        "vehicle_statistics",
        sa.Column("snapshot_id", sa.String(160), primary_key=True),
        sa.Column("vehicle_id", sa.String(160), primary_key=True),
        sa.Column("mode_scope", sa.String(80), primary_key=True),
        sa.Column("sample_start", sa.Date()),
        sa.Column("sample_end", sa.Date()),
        sa.Column("battles", sa.Integer()),
        sa.Column("wins", sa.Integer()),
        sa.Column("losses", sa.Integer()),
        sa.Column("win_rate", sa.Float()),
        sa.Column("kills", sa.Integer()),
        sa.Column("ground_kills", sa.Integer()),
        sa.Column("air_kills", sa.Integer()),
        sa.Column("deaths", sa.Integer()),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["data_snapshots.snapshot_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["vehicle_id"], ["vehicles.vehicle_id"], ondelete="RESTRICT"),
    )
    op.create_table(
        "override_entries",
        sa.Column("entry_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("revision", sa.String(160), nullable=False),
        sa.Column("vehicle_id", sa.String(160), nullable=False),
        sa.Column("field", sa.String(160), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("reference", sa.Text(), nullable=False),
        sa.Column("added_at", sa.Date(), nullable=False),
        sa.ForeignKeyConstraint(["revision"], ["override_revisions.revision"], ondelete="RESTRICT"),
        sa.UniqueConstraint("revision", "vehicle_id", "field", name="uq_override_field"),
    )
    op.create_index("ix_override_entries_revision", "override_entries", ["revision"])
    op.create_index("ix_override_entries_vehicle_id", "override_entries", ["vehicle_id"])
    op.create_table(
        "user_vehicle_states",
        sa.Column("profile_id", sa.String(160), primary_key=True),
        sa.Column("vehicle_id", sa.String(160), primary_key=True),
        sa.Column("status", sa.String(60), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["user_profiles.profile_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["vehicle_id"], ["vehicles.vehicle_id"], ondelete="RESTRICT"),
    )


def downgrade() -> None:
    for table_name in (
        "user_vehicle_states",
        "override_entries",
        "vehicle_statistics",
        "research_edges",
        "vehicle_capabilities",
        "vehicle_battle_ratings",
        "vehicle_metadata",
        "vehicle_source_ids",
        "raw_artifacts",
        "user_profiles",
        "override_revisions",
        "vehicles",
        "data_snapshots",
    ):
        op.drop_table(table_name)
