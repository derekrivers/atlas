"""context pack compression_applied provenance (ATLAS-55)

The ordered compression-ladder rungs that fired while rendering a pack
(context-renderer.md "Token budget and compression ladder"). ``compression_applied``
is JSONB NOT NULL with a server-side DEFAULT '[]' (mirroring ``constraints`` and
``relevant_docs``): the default backfills existing rows with the empty list — the
honest value for a pack rendered before the ladder existed (under budget,
unchanged) — without a data migration. batch_alter_table is required for SQLite,
which cannot ALTER in place.

Revision ID: 0016
Revises: 0015
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

# JSONB on PostgreSQL, JSON shim on SQLite (matches atlas.storage.tables).
JSONB = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    with op.batch_alter_table("context_packs") as batch:
        batch.add_column(
            sa.Column(
                "compression_applied",
                JSONB,
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("context_packs") as batch:
        batch.drop_column("compression_applied")
