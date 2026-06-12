"""prompt_hash on plan_runs (ATLAS-22, D1)

Completes the provenance chain input_doc_shas -> prompt_hash ->
raw_output_hash (data-model §3.10). Added NOT NULL without backfill
because no plan runs exist yet (asserted precondition: docs/planning/
empty, zero plan_runs rows). batch_alter_table for SQLite ALTER
compatibility.

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("plan_runs") as batch:
        batch.add_column(sa.Column("prompt_hash", sa.Text(), nullable=False))


def downgrade() -> None:
    with op.batch_alter_table("plan_runs") as batch:
        batch.drop_column("prompt_hash")
