"""verification_checks table (ATLAS-71, data-model §5.2)

One Verification Engine evaluation per row, NOT evidence (ADR-0008):
``status`` is an EvidenceStatus outcome, but there is no trust tier and no
commit pin (contrast the evidence table) — so no trust-tier cap and no
commit-pin guard. ticket_id is NOT NULL with an FK to tickets (a check is
always evaluated against an existing ticket). ``required`` defaults TRUE
and ``evidence_ids`` defaults '[]', verbatim from the §5.2 SQL. There is
no updated_at; completed_at is nullable.

No backfill needed — the table is new and no evaluator writes a row yet
(the per-check evaluators are later Phase 7 tickets).

Revision ID: 0017
Revises: 0016
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

# JSONB on PostgreSQL, JSON shim on SQLite (matches atlas.storage.tables).
JSONB = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "verification_checks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("check_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("TRUE"),
        ),
        sa.Column(
            "evidence_ids",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"]),
    )


def downgrade() -> None:
    op.drop_table("verification_checks")
