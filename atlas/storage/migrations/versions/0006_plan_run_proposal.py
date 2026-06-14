"""proposal on plan_runs (ATLAS-27, Gap 0)

Stores the validated proposal (post-parse, pre-key-assignment) so
`atlas apply` can materialise the backlog from the PlanRun record,
keeping the chain raw_output_hash -> proposal -> renders legible
(data-model §3.10). Added NOT NULL with a '{}' server default; no
backfill needed — existing rows take the default and no real plan runs
exist yet. batch_alter_table for SQLite ALTER compatibility.

Revision ID: 0006
Revises: 0005
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from atlas.storage.tables import JSONB

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("plan_runs") as batch:
        batch.add_column(
            sa.Column("proposal", JSONB, server_default=sa.text("'{}'"), nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table("plan_runs") as batch:
        batch.drop_column("proposal")
