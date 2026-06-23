"""ticket tags and component retrieval facets (ATLAS-127)

Free-form facets the Phase 5 context renderer matches ADRs and lessons against:
``tags`` (a list of free-form labels) and ``component`` (a single optional
label). Planner-populated later (ATLAS-128); this is the storage half only, so
every existing ticket reads back the defaults.

``tags`` is JSONB NOT NULL with a server-side DEFAULT '[]' (mirroring
``relevant_docs`` and ``lessons.tags``): the default backfills existing rows
with the empty list without a data migration. ``component`` is a nullable TEXT
column (mirroring ``external_linear_id``): existing rows backfill to NULL.
batch_alter_table is required for SQLite, which cannot ALTER in place.

Revision ID: 0015
Revises: 0014
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

# JSONB on PostgreSQL, JSON shim on SQLite (matches atlas.storage.tables).
JSONB = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    with op.batch_alter_table("tickets") as batch:
        batch.add_column(
            sa.Column(
                "tags",
                JSONB,
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
        batch.add_column(sa.Column("component", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tickets") as batch:
        batch.drop_column("component")
        batch.drop_column("tags")
