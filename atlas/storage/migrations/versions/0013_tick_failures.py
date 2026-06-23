"""tick_failures table (ATLAS-125, data-model §6.4)

The PM-scheduler tick-crash record: one append-only row per crash, written
by the scheduler (ATLAS-50) with created_by_type = system. NOT evidence —
no status, no trust tier, no updated_at. Unlike debt_items there is NO
ticket_id and NO product_id and so NO foreign key: a tick crash is observed
at the tick level, not against any one ticket. Query-time dedup derives from
occurred_at and failure_signature, never a stored counter.

No backfill needed — the table is new and no writer records a row yet (the
scheduler that does is ATLAS-50).

Revision ID: 0013
Revises: 0012
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tick_failures",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("failure_signature", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("created_by_type", sa.Text(), nullable=False),
        sa.Column("created_by_id", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("tick_failures")
