"""lesson confidence nullable for extraction drafts (ATLAS-99)

Agent-authored extraction drafts enter with ``confidence`` NULL; the operator
assigns confidence only at promotion (ADR-0009). The bounds CHECK remains in
place for promoted/non-null confidence values; SQL CHECKs naturally allow NULL.

Revision ID: 0018
Revises: 0017
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("lessons") as batch:
        batch.alter_column(
            "confidence",
            existing_type=sa.Numeric(precision=4, scale=3, asdecimal=False),
            nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("lessons") as batch:
        batch.alter_column(
            "confidence",
            existing_type=sa.Numeric(precision=4, scale=3, asdecimal=False),
            nullable=False,
        )
