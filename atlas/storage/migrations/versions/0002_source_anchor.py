"""source_anchor on epics and tickets (pre-Phase-2 contract session, R2)

Reconciler anchor-match pass; AT-1 traceability. Added NOT NULL without
backfill because no backlog content exists yet (asserted precondition:
docs/planning/ empty, zero rows in epics/tickets). batch_alter_table is
required for SQLite, which cannot ADD COLUMN NOT NULL in place.

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("epics") as batch:
        batch.add_column(sa.Column("source_anchor", sa.Text(), nullable=False))
    with op.batch_alter_table("tickets") as batch:
        batch.add_column(sa.Column("source_anchor", sa.Text(), nullable=False))


def downgrade() -> None:
    with op.batch_alter_table("tickets") as batch:
        batch.drop_column("source_anchor")
    with op.batch_alter_table("epics") as batch:
        batch.drop_column("source_anchor")
