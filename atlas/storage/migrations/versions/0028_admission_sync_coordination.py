"""database-backed admission lease, eligibility clock and write fence.

Revision ID: 0028
Revises: 0027
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from atlas.storage.tables import UTCDateTime

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "admission_leases",
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("acquired_at", UTCDateTime(), nullable=False),
        sa.Column("expires_at", UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("product_id"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
    )
    op.create_table(
        "admission_eligibility",
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("continuously_eligible_since", UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("ticket_id"),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"]),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
    )
    op.create_table(
        "admission_write_fences",
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("admission_run_id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("ticket_key", sa.Text(), nullable=False),
        sa.Column("issue_id", sa.Text(), nullable=False),
        sa.Column("source_state_id", sa.Text(), nullable=False),
        sa.Column("target_state_id", sa.Text(), nullable=False),
        sa.Column("policy_revision", sa.Integer(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("product_id"),
        sa.UniqueConstraint("admission_run_id"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.ForeignKeyConstraint(["admission_run_id"], ["admission_runs.id"]),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"]),
        sa.CheckConstraint(
            "state IN ('pending', 'indeterminate')",
            name="admission_write_fences_state",
        ),
        sa.CheckConstraint(
            "policy_revision >= 1",
            name="admission_write_fences_policy_revision_positive",
        ),
    )


def downgrade() -> None:
    op.drop_table("admission_write_fences")
    op.drop_table("admission_eligibility")
    op.drop_table("admission_leases")
