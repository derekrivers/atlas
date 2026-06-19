"""ticket review_cycle_count round-trip counter (ATLAS-120)

The PM-Engine review-cycling counter: the number of
``changes_requested -> pr_open`` round trips. Incremented by
``apply_linear_status`` (the sole post-creation status writer) only on that
transition; at more than three the step-5 review-cycling pass routes the ticket
to ``needs_human_decision`` via the sanctioned ``set_state``. Status-coupled and
never bumps ``updated_at``, like status_entered_at / linear_synced_at.

NOT NULL with a server-side DEFAULT 0 (a counter defaulting to zero is natural,
matching the ``priority`` convention): every existing ticket legitimately starts
at zero round trips, so the default backfills them without a data migration.
batch_alter_table is required for SQLite, which cannot ALTER in place.

Revision ID: 0012
Revises: 0011
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tickets") as batch:
        batch.add_column(
            sa.Column(
                "review_cycle_count",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("tickets") as batch:
        batch.drop_column("review_cycle_count")
