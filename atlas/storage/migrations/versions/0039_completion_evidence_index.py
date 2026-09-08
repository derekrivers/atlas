"""index the ordinary completion evidence lookup.

Revision ID: 0039
Revises: 0038
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0039"
down_revision: str | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX_NAME = "ix_evidence_ticket_type_actor_commit"


def upgrade() -> None:
    op.create_index(
        INDEX_NAME,
        "evidence",
        ["ticket_id", "evidence_type", "created_by_type", "commit_sha"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="evidence")
