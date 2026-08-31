"""extend bounded PM recovery blocker causes.

Revision ID: 0037
Revises: 0036
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_CODES = (
    "code IN ('lease_unavailable', 'provider_unavailable', "
    "'publication_ambiguous', 'publication_not_yet_complete')"
)
_NEW_CODES = (
    "code IN ('lease_unavailable', 'provider_unavailable', "
    "'publication_ambiguous', 'publication_not_yet_complete', "
    "'ci_evidence_not_yet_complete', 'ci_evidence_ambiguous', "
    "'authority_changed', 'write_fence_unresolved')"
)


def _replace_code_constraint(expression: str) -> None:
    with op.batch_alter_table("pm_blocker_occurrences") as batch:
        batch.drop_constraint("pm_blocker_occurrences_code", type_="check")
        batch.create_check_constraint("pm_blocker_occurrences_code", expression)


def upgrade() -> None:
    _replace_code_constraint(_NEW_CODES)


def downgrade() -> None:
    _replace_code_constraint(_OLD_CODES)
