"""system-tier CI handoff audit and crash-safe write fence.

Revision ID: 0032
Revises: 0031
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from atlas.storage.tables import JSONB, UTCDateTime

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OUTCOME_TABLE = "ci_handoff_reconciliations"
FENCE_TABLE = "ci_handoff_write_fences"


def _create_sqlite_append_only_triggers() -> None:
    for operation in ("UPDATE", "DELETE"):
        name = f"{OUTCOME_TABLE}_no_{operation.lower()}"
        op.execute(
            f"""
            CREATE TRIGGER {name}
            BEFORE {operation} ON {OUTCOME_TABLE}
            BEGIN
                SELECT RAISE(ABORT, '{OUTCOME_TABLE} is append-only');
            END
            """
        )


def _drop_sqlite_append_only_triggers() -> None:
    for operation in ("UPDATE", "DELETE"):
        op.execute(f"DROP TRIGGER IF EXISTS {OUTCOME_TABLE}_no_{operation.lower()}")


def _create_postgresql_append_only_trigger() -> None:
    function_name = f"{OUTCOME_TABLE}_reject_mutation"
    op.execute(
        f"""
        CREATE FUNCTION {function_name}()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '{OUTCOME_TABLE} is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {OUTCOME_TABLE}_append_only
        BEFORE UPDATE OR DELETE ON {OUTCOME_TABLE}
        FOR EACH ROW EXECUTE FUNCTION {function_name}()
        """
    )


def _drop_postgresql_append_only_trigger() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS {OUTCOME_TABLE}_append_only ON {OUTCOME_TABLE}")
    op.execute(f"DROP FUNCTION IF EXISTS {OUTCOME_TABLE}_reject_mutation()")


def upgrade() -> None:
    op.create_table(
        OUTCOME_TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.Text(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("ticket_key", sa.Text(), nullable=False),
        sa.Column("linear_issue_id", sa.Text(), nullable=True),
        sa.Column("repository_owner", sa.Text(), nullable=False),
        sa.Column("repository_name", sa.Text(), nullable=False),
        sa.Column("pr_number", sa.Integer(), nullable=False),
        sa.Column("head_commit", sa.Text(), nullable=False),
        sa.Column("policy_id", sa.Uuid(), nullable=True),
        sa.Column("policy_revision", sa.Integer(), nullable=True),
        sa.Column("policy_fingerprint", sa.Text(), nullable=True),
        sa.Column("snapshot_fingerprint", sa.Text(), nullable=True),
        sa.Column("classification", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column(
            "check_results", JSONB, nullable=False, server_default=sa.text("'[]'")
        ),
        sa.Column("observed_at", UTCDateTime(), nullable=False),
        sa.Column("created_by_type", sa.Text(), nullable=False),
        sa.Column("created_by_id", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"]),
        sa.ForeignKeyConstraint(
            ["policy_id"], ["delivery_admission_policy_revisions.id"]
        ),
        sa.CheckConstraint(
            "schema_version = 'ci-handoff-reconciliation-v1'",
            name="ci_handoff_reconciliations_schema_version",
        ),
        sa.CheckConstraint(
            "classification IN ('passed', 'implementation_failure', 'pending', "
            "'missing', 'infrastructure', 'stale', 'malformed', 'indeterminate')",
            name="ci_handoff_reconciliations_classification",
        ),
        sa.CheckConstraint(
            "decision IN ('hold', 'review_required', 'changes_requested')",
            name="ci_handoff_reconciliations_decision",
        ),
    )
    op.create_table(
        FENCE_TABLE,
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("reconciliation_id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("ticket_key", sa.Text(), nullable=False),
        sa.Column("issue_id", sa.Text(), nullable=False),
        sa.Column("source_state_id", sa.Text(), nullable=False),
        sa.Column("target_state_id", sa.Text(), nullable=False),
        sa.Column("target_status", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("product_id"),
        sa.UniqueConstraint("reconciliation_id"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"]),
        sa.ForeignKeyConstraint(
            ["reconciliation_id"], ["ci_handoff_reconciliations.id"]
        ),
        sa.CheckConstraint(
            "target_status IN ('review_required', 'changes_requested')",
            name="ci_handoff_write_fences_target_status",
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'indeterminate')",
            name="ci_handoff_write_fences_state",
        ),
    )

    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        _create_sqlite_append_only_triggers()
    elif dialect == "postgresql":
        _create_postgresql_append_only_trigger()


def downgrade() -> None:
    op.drop_table(FENCE_TABLE)
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        _drop_sqlite_append_only_triggers()
    elif dialect == "postgresql":
        _drop_postgresql_append_only_trigger()
    op.drop_table(OUTCOME_TABLE)
