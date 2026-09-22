"""Persist advisor preferences and index latest advisor snapshots."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "advisor_contexts",
        sa.Column("preferred_roles", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "advisor_contexts",
        sa.Column("duplicate_role_penalty", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index(
        "ix_stored_evaluations_profile_kind_created",
        "stored_evaluations",
        ["profile_id", "kind", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_stored_evaluations_profile_kind_created", table_name="stored_evaluations")
    op.drop_column("advisor_contexts", "duplicate_role_penalty")
    op.drop_column("advisor_contexts", "preferred_roles")
