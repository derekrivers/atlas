"""ticket last_observed_linear_state_id transition signal (ATLAS-118)

The out-of-ownership anomaly detector's dedup signal: the Linear state id
observed on the last pull. The PM Engine logs one DebtItem per *transition*
into an unmapped state, so the next pull compares the freshly fetched id
against this column and writes nothing while the same unmapped state
persists. Nullable (a ticket that has never been pulled reads NULL).
Written only by the sync loop and never bumps updated_at, like
linear_synced_at. Added without backfill — no PM Engine has observed a
state yet, so every existing ticket legitimately starts NULL.
batch_alter_table is required for SQLite, which cannot ALTER in place.

Revision ID: 0010
Revises: 0009
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tickets") as batch:
        batch.add_column(
            sa.Column("last_observed_linear_state_id", sa.Text(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("tickets") as batch:
        batch.drop_column("last_observed_linear_state_id")
