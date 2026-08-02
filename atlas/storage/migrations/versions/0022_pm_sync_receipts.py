"""pm_sync_receipts table (ATLAS-245, data-model §6.8)

The PM sync receipt record: one append-only row per sync tick, written at the
tick's local completion boundary with product/project identity, fingerprints,
bounded result classification, counters, and bounded error summary. It stores no
Linear payload bodies and no credentials. Latest successful sync time is a query
over successful classifications, not a ticket definition cursor.

No backfill: historical ticket.linear_synced_at cursors remain untouched, and
last_successful_linear_sync_at stays null until the first successful receipt.

Revision ID: 0022
Revises: 0021
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from atlas.storage.tables import JSONB, UTCDateTime

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pm_sync_receipts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=True),
        sa.Column("product_key", sa.Text(), nullable=True),
        sa.Column("linear_project_id", sa.Text(), nullable=False),
        sa.Column("started_at", UTCDateTime(), nullable=False),
        sa.Column("finished_at", UTCDateTime(), nullable=False),
        sa.Column("status_map_fingerprint", sa.Text(), nullable=False),
        sa.Column("fetched_board_fingerprint", sa.Text(), nullable=False),
        sa.Column("fetched_board_issue_count", sa.Integer(), nullable=False),
        sa.Column("result", sa.Text(), nullable=False),
        sa.Column(
            "counters",
            JSONB,
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("created_by_type", sa.Text(), nullable=False),
        sa.Column("created_by_id", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("pm_sync_receipts")
