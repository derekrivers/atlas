"""debt_items table (ATLAS-116, data-model §6.2)

The delivery-anomaly record: one append-only row per observation, written
by the PM Engine (created_by_type = system). NOT evidence — no status, no
trust tier, no updated_at. ticket_id is NOT NULL with an FK to tickets
(an anomaly is always observed against an existing synced ticket);
product_id FK to products. Recurrence is a query-time predicate, never a
stored counter.

No backfill needed — the table is new and no PM Engine writes a row yet.

Revision ID: 0008
Revises: 0007
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "debt_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("anomaly_type", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_type", sa.Text(), nullable=False),
        sa.Column("created_by_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"]),
    )


def downgrade() -> None:
    op.drop_table("debt_items")
