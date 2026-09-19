"""Add reconciliation audit and statistics diagnostics."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("user_vehicle_states") as batch:
        batch.add_column(
            sa.Column("superseded", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(sa.Column("superseded_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("superseded_by_vehicle_id", sa.String(160)))
    with op.batch_alter_table("vehicle_statistics") as batch:
        batch.add_column(sa.Column("reported_win_rate", sa.Float()))
        batch.add_column(sa.Column("reported_kd", sa.Float()))
        batch.add_column(sa.Column("reported_kills_per_battle", sa.Float()))
        batch.add_column(sa.Column("ratio_provenance", sa.String(60)))
    op.create_table(
        "statistics_import_diagnostics",
        sa.Column("snapshot_id", sa.String(160), primary_key=True),
        sa.Column("recognized_columns", sa.JSON(), nullable=False),
        sa.Column("unknown_columns", sa.JSON(), nullable=False),
        sa.Column("matched_count", sa.Integer(), nullable=False),
        sa.Column("unresolved_source_ids", sa.JSON(), nullable=False),
        sa.Column("duplicate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["data_snapshots.snapshot_id"], ondelete="RESTRICT"
        ),
    )
    op.create_table(
        "profile_reconciliation_audits",
        sa.Column("audit_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("profile_id", sa.String(160), nullable=False),
        sa.Column("identity_snapshot_id", sa.String(160), nullable=False),
        sa.Column("source_revision", sa.Integer(), nullable=False),
        sa.Column("resulting_revision", sa.Integer(), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["user_profiles.profile_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["identity_snapshot_id"], ["data_snapshots.snapshot_id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "profile_id", "identity_snapshot_id", "source_revision", name="uq_reconciliation_run"
        ),
    )
    op.create_index(
        "ix_profile_reconciliation_audits_profile_id",
        "profile_reconciliation_audits",
        ["profile_id"],
    )
    op.create_table(
        "profile_reconciliation_items",
        sa.Column("item_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("audit_id", sa.Integer(), nullable=False),
        sa.Column("deprecated_vehicle_id", sa.String(160), nullable=False),
        sa.Column("canonical_vehicle_id", sa.String(160), nullable=False),
        sa.Column("deprecated_status", sa.String(60), nullable=False),
        sa.Column("canonical_status", sa.String(60)),
        sa.Column("chosen_status", sa.String(60), nullable=False),
        sa.Column("conflict", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["audit_id"], ["profile_reconciliation_audits.audit_id"], ondelete="CASCADE"
        ),
    )
    op.create_index(
        "ix_profile_reconciliation_items_audit_id", "profile_reconciliation_items", ["audit_id"]
    )


def downgrade() -> None:
    op.drop_table("profile_reconciliation_items")
    op.drop_table("profile_reconciliation_audits")
    op.drop_table("statistics_import_diagnostics")
    with op.batch_alter_table("vehicle_statistics") as batch:
        for name in (
            "ratio_provenance",
            "reported_kills_per_battle",
            "reported_kd",
            "reported_win_rate",
        ):
            batch.drop_column(name)
    with op.batch_alter_table("user_vehicle_states") as batch:
        for name in ("superseded_by_vehicle_id", "superseded_at", "superseded"):
            batch.drop_column(name)
