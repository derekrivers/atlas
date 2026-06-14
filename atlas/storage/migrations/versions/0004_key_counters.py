"""key_counters: monotonic per-prefix key counter (ATLAS-25)

Adds the single new table for key authority (data-model §3.12,
knowledge-core "Key counter"). One row per key prefix, keyed on the
prefix; `high_water` is a monotonic mark with a non-negative CHECK. No
backfill — the table is created empty (an unseen prefix reads as 0 and
mints `<prefix>-1` on first assignment).

Revision ID: 0004
Revises: 0003
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "key_counters",
        sa.Column("prefix", sa.Text(), nullable=False),
        sa.Column(
            "high_water", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.CheckConstraint("high_water >= 0", name="key_counters_high_water_nonneg"),
        sa.PrimaryKeyConstraint("prefix"),
    )


def downgrade() -> None:
    op.drop_table("key_counters")
