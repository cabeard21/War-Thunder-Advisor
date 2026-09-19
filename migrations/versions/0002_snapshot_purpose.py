"""Separate operational evidence from frozen acceptance bundles."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

ACCEPTANCE_BUNDLE_KEY = "usa-ground-rb-m1-acceptance"
ACCEPTANCE_SNAPSHOT_IDS = (
    "fixture-usa-ground-rb-vehicles-m1",
    "fixture-usa-ground-rb-statistics-m1",
)


def upgrade() -> None:
    existing = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("data_snapshots")
    }
    if "purpose" not in existing:
        op.add_column(
            "data_snapshots",
            sa.Column(
                "purpose", sa.String(length=30), nullable=False, server_default="operational"
            ),
        )
    if "compatibility_key" not in existing:
        op.add_column(
            "data_snapshots",
            sa.Column("compatibility_key", sa.String(length=160), nullable=True),
        )
    snapshots = sa.table(
        "data_snapshots",
        sa.column("snapshot_id", sa.String()),
        sa.column("purpose", sa.String()),
        sa.column("compatibility_key", sa.String()),
    )
    op.execute(
        snapshots.update()
        .where(snapshots.c.snapshot_id.in_(ACCEPTANCE_SNAPSHOT_IDS))
        .values(purpose="acceptance", compatibility_key=ACCEPTANCE_BUNDLE_KEY)
    )


def downgrade() -> None:
    existing = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("data_snapshots")
    }
    if "compatibility_key" in existing:
        op.drop_column("data_snapshots", "compatibility_key")
    if "purpose" in existing:
        op.drop_column("data_snapshots", "purpose")
