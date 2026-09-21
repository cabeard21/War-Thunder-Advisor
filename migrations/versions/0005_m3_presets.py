"""Add durable advisor presets and context."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # This revision has not been applied to the retained local databases.  Keep
    # all M3 state together so an upgrade from the M2 head is atomic.
    op.add_column(
        "user_profiles",
        sa.Column("write_revision", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "lineup_presets",
        sa.Column("preset_id", sa.String(36), primary_key=True),
        sa.Column("profile_id", sa.String(160), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("normalized_name", sa.String(160), nullable=False),
        sa.Column("slots", sa.JSON(), nullable=False),
        sa.Column("required_vehicle_ids", sa.JSON(), nullable=False),
        sa.Column("excluded_vehicle_ids", sa.JSON(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["user_profiles.profile_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("profile_id", "normalized_name", name="uq_preset_profile_name"),
    )
    op.create_table(
        "stored_evaluations",
        sa.Column("evaluation_id", sa.String(64), primary_key=True),
        sa.Column("profile_id", sa.String(160), nullable=False, index=True),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("profile_revision", sa.String(80), nullable=False),
        sa.Column("profile_state", sa.JSON(), nullable=False),
        sa.Column("effective_inputs", sa.JSON(), nullable=False),
        sa.Column("evidence_context", sa.JSON(), nullable=False),
        sa.Column("ruleset_hash", sa.String(64), nullable=False),
        sa.Column("schema_revision", sa.String(20), nullable=False),
        sa.Column("result_id", sa.String(64), nullable=False),
        sa.Column("result_payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["user_profiles.profile_id"], ondelete="CASCADE"),
    )
    op.create_table(
        "advisor_contexts",
        sa.Column("profile_id", sa.String(160), primary_key=True),
        sa.Column("selected_preset_id", sa.String(36)),
        sa.Column("target_br", sa.Integer()),
        sa.Column("required_vehicle_ids", sa.JSON(), nullable=False),
        sa.Column("excluded_vehicle_ids", sa.JSON(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["user_profiles.profile_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["selected_preset_id"], ["lineup_presets.preset_id"], ondelete="SET NULL"
        ),
    )


def downgrade() -> None:
    op.drop_table("stored_evaluations")
    op.drop_table("advisor_contexts")
    op.drop_table("lineup_presets")
    op.drop_column("user_profiles", "write_revision")
