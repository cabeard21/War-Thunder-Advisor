"""Retain per-observation capability verification provenance."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("capability_observations", sa.Column("verified_at", sa.Date()))
    op.add_column("capability_observations", sa.Column("source_revision", sa.String(160)))


def downgrade() -> None:
    op.drop_column("capability_observations", "source_revision")
    op.drop_column("capability_observations", "verified_at")
