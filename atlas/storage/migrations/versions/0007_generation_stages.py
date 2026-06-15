"""generation_stages on plan_runs (ATLAS-105, §5.3)

Stores the per-stage generation provenance the multi-call generator
produces (ADR-0010): one record per model call — stage, prompt_version,
prompt_hash, raw_output_hash — that the composite top-level prompt_hash
is derived from (data-model §3.10). Added NOT NULL with a '[]' server
default; no backfill needed — existing rows take the default and read
back as the empty list, and no real plan runs exist yet.
batch_alter_table for SQLite ALTER compatibility.

Revision ID: 0007
Revises: 0006
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from atlas.storage.tables import JSONB

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("plan_runs") as batch:
        batch.add_column(
            sa.Column(
                "generation_stages",
                JSONB,
                server_default=sa.text("'[]'"),
                nullable=False,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("plan_runs") as batch:
        batch.drop_column("generation_stages")
