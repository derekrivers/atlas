"""model_parameters on plan_runs (ATLAS-26, D3)

Records the model call settings (temperature, max_tokens) alongside
model_name and prompt_hash so a plan run is reproducible-by-record
(data-model §3.10). Added NOT NULL with a '{}' server default; no
backfill needed — existing rows take the default, and no plan runs
exist yet in any real database. batch_alter_table for SQLite ALTER
compatibility.

Revision ID: 0005
Revises: 0004
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from atlas.storage.tables import JSONB

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("plan_runs") as batch:
        batch.add_column(
            sa.Column(
                "model_parameters",
                JSONB,
                server_default=sa.text("'{}'"),
                nullable=False,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("plan_runs") as batch:
        batch.drop_column("model_parameters")
