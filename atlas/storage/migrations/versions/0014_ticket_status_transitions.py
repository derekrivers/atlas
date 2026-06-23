"""ticket_status_transitions table (ATLAS-121, data-model §6.6)

The status-transition history record: one append-only row per REAL status
transition, written inline by apply_linear_status (the sole status writer) with
created_by_type = system, atomically with the status change. NOT evidence — no
status, no trust tier, no updated_at, no created_at. Unlike tick_failures it IS
ticket-scoped: ticket_id is FK-backed and NOT NULL (modelled on debt_items), but
there is no product_id. Historical per-state cycle time derives from these rows
(the consumer is ATLAS-126), replacing the single overwritten status_entered_at
dwell clock (ATLAS-119) for history.

No backfill: transition history is prospective-only. Every status change before
this lands is a transition Atlas can never record; capture starts here.

Revision ID: 0014
Revises: 0013
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ticket_status_transitions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("from_status", sa.Text(), nullable=False),
        sa.Column("to_status", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_type", sa.Text(), nullable=False),
        sa.Column("created_by_id", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("ticket_status_transitions")
