"""Store GitHub job identity and source lifecycle time on evidence.

Revision ID: 0021
Revises: 0020

The columns are nullable because non-CI evidence has no job identity and
historical rows predate the source metadata. Verification deliberately fails
closed over legacy CI rows until they are re-pulled; this migration never
rewrites append-only evidence.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from atlas.storage.tables import UTCDateTime

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("evidence", sa.Column("job_name", sa.Text(), nullable=True))
    op.add_column(
        "evidence",
        sa.Column("source_event_at", UTCDateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("evidence", "source_event_at")
    op.drop_column("evidence", "job_name")
