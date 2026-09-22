"""Version normalized snapshots and persist kill target identity."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLite batch recreation drops the original table while children reference it.
    # Migration runs on its own connection, before any data-changing statement.
    op.execute("PRAGMA foreign_keys=OFF")
    op.add_column(
        "vehicle_statistics",
        sa.Column(
            "kill_target_definition", sa.String(40), nullable=False, server_default="all_targets"
        ),
    )
    with op.batch_alter_table("data_snapshots") as batch:
        batch.drop_constraint("uq_snapshot_source_content", type_="unique")
        batch.create_unique_constraint(
            "uq_snapshot_source_content",
            ["dataset_type", "provider", "source_revision", "checksum", "provider_version"],
        )
    op.execute("PRAGMA foreign_keys=ON")


def downgrade() -> None:
    op.execute("PRAGMA foreign_keys=OFF")
    with op.batch_alter_table("data_snapshots") as batch:
        batch.drop_constraint("uq_snapshot_source_content", type_="unique")
        batch.create_unique_constraint(
            "uq_snapshot_source_content",
            ["dataset_type", "provider", "source_revision", "checksum"],
        )
    op.drop_column("vehicle_statistics", "kill_target_definition")
    op.execute("PRAGMA foreign_keys=ON")
