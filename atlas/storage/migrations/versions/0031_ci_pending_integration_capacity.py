"""CI-pending integration capacity policy input.

Adds one conservative compatibility value to existing immutable policy rows by
column default.  The migration does not update, delete or recreate historical
revisions and leaves migration 0025 unchanged.

Revision ID: 0031
Revises: 0030
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "delivery_admission_policy_revisions"
CONSTRAINT = "delivery_admission_policy_integration_bounds"


def upgrade() -> None:
    op.add_column(
        TABLE,
        sa.Column(
            "integration_budget",
            sa.Integer(),
            sa.CheckConstraint(
                "integration_budget >= 1 AND integration_budget <= 10",
                name=CONSTRAINT,
            ),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )


def downgrade() -> None:
    op.drop_column(TABLE, "integration_budget")
