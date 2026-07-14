"""ticket lesson_extraction_attempted_at extraction cursor (ATLAS-106)

The learning extractor records the last time it attempted lesson extraction for
a ticket. The cursor prevents terminal tickets from being re-extracted on every
scheduler tick and lets PM failure-analysis DebtItems trigger a fresh extraction
only when they were recorded after the last attempt. Nullable, added without
backfill: tickets that predate the scheduler have no attempt recorded and are
eligible for the first scheduler pass. Never bumps updated_at.

Revision ID: 0019
Revises: 0018
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tickets") as batch:
        batch.add_column(
            sa.Column(
                "lesson_extraction_attempted_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("tickets") as batch:
        batch.drop_column("lesson_extraction_attempted_at")
