"""typed acceptance evidence-pull receipt outcomes.

Revision ID: 0028
Revises: 0027
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT_NAME = "operator_action_receipts_outcome_result_code"
OLD_CHECK = """
    (outcome = 'succeeded' AND result_code = 'action_succeeded') OR
    (outcome = 'refused' AND result_code IN ('action_refused', 'stale_state')) OR
    (outcome = 'failed' AND result_code = 'action_failed') OR
    (outcome = 'conflict' AND result_code = 'action_conflict')
"""
NEW_CHECK = """
    (outcome = 'succeeded' AND result_code = 'action_succeeded') OR
    (outcome = 'refused' AND result_code IN ('action_refused', 'stale_state')) OR
    (outcome = 'failed' AND result_code IN (
        'action_failed',
        'evidence_transport_failed',
        'evidence_authentication_failed',
        'evidence_rate_limit_failed',
        'evidence_malformed_source'
    )) OR
    (outcome = 'conflict' AND result_code = 'action_conflict')
"""


def _drop_append_only_guard() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS operator_action_receipts_no_update")
        op.execute("DROP TRIGGER IF EXISTS operator_action_receipts_no_delete")
    elif dialect == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS operator_action_receipts_append_only "
            "ON operator_action_receipts"
        )
        op.execute("DROP FUNCTION IF EXISTS operator_action_receipts_reject_mutation()")


def _create_append_only_guard() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        for operation in ("UPDATE", "DELETE"):
            op.execute(
                f"""
                CREATE TRIGGER operator_action_receipts_no_{operation.lower()}
                BEFORE {operation} ON operator_action_receipts
                BEGIN
                    SELECT RAISE(ABORT, 'operator_action_receipts is append-only');
                END
                """
            )
    elif dialect == "postgresql":
        op.execute(
            """
            CREATE FUNCTION operator_action_receipts_reject_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'operator_action_receipts is append-only';
            END;
            $$ LANGUAGE plpgsql
            """
        )
        op.execute(
            """
            CREATE TRIGGER operator_action_receipts_append_only
            BEFORE UPDATE OR DELETE ON operator_action_receipts
            FOR EACH ROW EXECUTE FUNCTION operator_action_receipts_reject_mutation()
            """
        )


def _replace_check(check: str) -> None:
    with op.batch_alter_table("operator_action_receipts") as batch_op:
        batch_op.drop_constraint(CONSTRAINT_NAME, type_="check")
        batch_op.create_check_constraint(CONSTRAINT_NAME, sa.text(check))


def upgrade() -> None:
    _drop_append_only_guard()
    _replace_check(NEW_CHECK)
    _create_append_only_guard()


def downgrade() -> None:
    _drop_append_only_guard()
    op.execute(
        """
        UPDATE operator_action_receipts
        SET result_code = 'action_failed'
        WHERE result_code IN (
            'evidence_transport_failed',
            'evidence_authentication_failed',
            'evidence_rate_limit_failed',
            'evidence_malformed_source'
        )
        """
    )
    _replace_check(OLD_CHECK)
    _create_append_only_guard()
