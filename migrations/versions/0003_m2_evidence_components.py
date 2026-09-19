"""Add independently versioned Milestone 2 evidence components."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "capability_observations",
        sa.Column("observation_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("snapshot_id", sa.String(160), nullable=False),
        sa.Column("vehicle_id", sa.String(160), nullable=False),
        sa.Column("capability", sa.String(60), nullable=False),
        sa.Column("value", sa.Boolean(), nullable=False),
        sa.Column("source_provider", sa.String(120), nullable=False),
        sa.Column("source_type", sa.String(60), nullable=False),
        sa.Column("source_reference", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["data_snapshots.snapshot_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["vehicle_id"], ["vehicles.vehicle_id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "snapshot_id",
            "vehicle_id",
            "capability",
            "source_reference",
            name="uq_capability_observation_source",
        ),
    )
    op.create_index(
        "ix_capability_observations_snapshot_id", "capability_observations", ["snapshot_id"]
    )
    op.create_index(
        "ix_capability_observations_vehicle_id", "capability_observations", ["vehicle_id"]
    )
    op.create_table(
        "availability_observations",
        sa.Column("snapshot_id", sa.String(160), primary_key=True),
        sa.Column("vehicle_id", sa.String(160), primary_key=True),
        sa.Column("acquisition_type", sa.String(60), nullable=False),
        sa.Column("researchability", sa.String(60), nullable=False),
        sa.Column("tree_membership", sa.String(60), nullable=False),
        sa.Column("visibility", sa.String(60), nullable=False),
        sa.Column("source_provider", sa.String(120), nullable=False),
        sa.Column("source_reference", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["data_snapshots.snapshot_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["vehicle_id"], ["vehicles.vehicle_id"], ondelete="RESTRICT"),
    )
    op.create_table(
        "vehicle_identity_aliases",
        sa.Column("alias_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("snapshot_id", sa.String(160), nullable=False),
        sa.Column("provider", sa.String(120), nullable=False),
        sa.Column("source_vehicle_id", sa.String(200), nullable=False),
        sa.Column("canonical_vehicle_id", sa.String(160), nullable=False),
        sa.Column("deprecated_vehicle_id", sa.String(160)),
        sa.Column("confirmed", sa.Boolean(), nullable=False),
        sa.Column("source_reference", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["data_snapshots.snapshot_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["canonical_vehicle_id"], ["vehicles.vehicle_id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "snapshot_id", "provider", "source_vehicle_id", name="uq_identity_alias_source"
        ),
    )
    op.create_index(
        "ix_vehicle_identity_aliases_snapshot_id", "vehicle_identity_aliases", ["snapshot_id"]
    )
    op.create_index(
        "ix_vehicle_identity_aliases_canonical_vehicle_id",
        "vehicle_identity_aliases",
        ["canonical_vehicle_id"],
    )
    op.create_table(
        "research_graph_edges",
        sa.Column("edge_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("snapshot_id", sa.String(160), nullable=False),
        sa.Column("nation", sa.String(40), nullable=False),
        sa.Column("domain", sa.String(40), nullable=False),
        sa.Column("parent_vehicle_id", sa.String(160), nullable=False),
        sa.Column("child_vehicle_id", sa.String(160), nullable=False),
        sa.Column("edge_type", sa.String(60), nullable=False),
        sa.Column("prerequisite_group", sa.String(120), nullable=False),
        sa.Column("group_semantics", sa.String(10), nullable=False),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["data_snapshots.snapshot_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["parent_vehicle_id"], ["vehicles.vehicle_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["child_vehicle_id"], ["vehicles.vehicle_id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "snapshot_id",
            "parent_vehicle_id",
            "child_vehicle_id",
            "edge_type",
            "prerequisite_group",
            name="uq_research_graph_edge",
        ),
    )
    op.create_index("ix_research_graph_edges_snapshot_id", "research_graph_edges", ["snapshot_id"])
    op.create_index(
        "ix_research_graph_edges_child_vehicle_id", "research_graph_edges", ["child_vehicle_id"]
    )
    op.create_table(
        "import_attempts",
        sa.Column("attempt_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("dataset_type", sa.String(40), nullable=False),
        sa.Column("provider", sa.String(120), nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("raw_checksum", sa.String(64), nullable=False),
        sa.Column("media_type", sa.String(120), nullable=False),
        sa.Column("raw_content", sa.LargeBinary(), nullable=False),
        sa.UniqueConstraint(
            "dataset_type", "provider", "raw_checksum", "status", name="uq_import_attempt"
        ),
    )
    op.create_index("ix_import_attempts_dataset_type", "import_attempts", ["dataset_type"])


def downgrade() -> None:
    for table_name in (
        "import_attempts",
        "research_graph_edges",
        "vehicle_identity_aliases",
        "availability_observations",
        "capability_observations",
    ):
        op.drop_table(table_name)
