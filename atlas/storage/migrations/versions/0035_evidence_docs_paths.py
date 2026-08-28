"""bounded structured documentation paths on append-only evidence.

Revision ID: 0035
Revises: 0034

The column is nullable so every historical evidence row remains byte-for-byte
untouched: NULL means the legacy/unavailable projection.  New documentation
observations populate it through the model and repository path.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from atlas.storage.tables import JSONB

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("evidence", sa.Column("docs_paths", JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("evidence", "docs_paths")
