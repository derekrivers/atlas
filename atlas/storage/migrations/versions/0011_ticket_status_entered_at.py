"""ticket status_entered_at dwell clock (ATLAS-119)

The PM-Engine dwell clock: when the ticket entered its current status. Stamped
by ``apply_linear_status`` (the sole post-creation status writer) only on a real
status change, so it marks the start of the current dwell episode. Dwell-breach
detection measures ``now - status_entered_at`` against the per-status horizon and
uses it as the per-episode dedup boundary. Written only by the sync loop and
never bumps updated_at, like linear_synced_at / last_observed_linear_state_id.
Nullable, added WITHOUT backfill — no entry time was recorded before this field,
so every existing ticket legitimately starts NULL, and dwell SKIPS a NULL entry
time (unknown, never a false breach) rather than guessing. batch_alter_table is
required for SQLite, which cannot ALTER in place.

Revision ID: 0011
Revises: 0010
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tickets") as batch:
        batch.add_column(
            sa.Column("status_entered_at", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("tickets") as batch:
        batch.drop_column("status_entered_at")
