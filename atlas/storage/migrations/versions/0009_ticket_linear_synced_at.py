"""linear_synced_at sync cursor on tickets (ATLAS-42)

The PM-Engine sync cursor: updated_at at the last confirmed definition push
to Linear. Nullable (a ticket that has never synced reads NULL and is
eligible for its first push). Added without backfill — no PM Engine has
pushed yet, so every existing ticket legitimately starts NULL.
batch_alter_table is required for SQLite, which cannot ALTER in place.

Revision ID: 0009
Revises: 0008
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tickets") as batch:
        batch.add_column(
            sa.Column("linear_synced_at", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("tickets") as batch:
        batch.drop_column("linear_synced_at")
