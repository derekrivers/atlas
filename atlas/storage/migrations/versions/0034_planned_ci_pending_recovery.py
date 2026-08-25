"""governed planned-to-ci-pending mirror recovery evidence.

Revision ID: 0034
Revises: 0033
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from atlas.storage.tables import UTCDateTime

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "planned_ci_pending_recoveries"


def _create_sqlite_append_only_triggers() -> None:
    for operation in ("UPDATE", "DELETE"):
        name = f"{TABLE}_no_{operation.lower()}"
        op.execute(
            f"""
            CREATE TRIGGER {name}
            BEFORE {operation} ON {TABLE}
            BEGIN
                SELECT RAISE(ABORT, '{TABLE} is append-only');
            END
            """
        )


def _drop_sqlite_append_only_triggers() -> None:
    for operation in ("UPDATE", "DELETE"):
        op.execute(f"DROP TRIGGER IF EXISTS {TABLE}_no_{operation.lower()}")


def _create_postgresql_append_only_trigger() -> None:
    function_name = f"{TABLE}_reject_mutation"
    op.execute(
        f"""
        CREATE FUNCTION {function_name}()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '{TABLE} is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {TABLE}_append_only
        BEFORE UPDATE OR DELETE ON {TABLE}
        FOR EACH ROW EXECUTE FUNCTION {function_name}()
        """
    )


def _drop_postgresql_append_only_trigger() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS {TABLE}_append_only ON {TABLE}")
    op.execute(f"DROP FUNCTION IF EXISTS {TABLE}_reject_mutation()")


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.Text(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("ticket_key", sa.Text(), nullable=False),
        sa.Column("linear_issue_id", sa.Text(), nullable=False),
        sa.Column("linear_project_id", sa.Text(), nullable=False),
        sa.Column("observed_linear_state_id", sa.Text(), nullable=False),
        sa.Column("source_local_status", sa.Text(), nullable=False),
        sa.Column("recovered_local_status", sa.Text(), nullable=False),
        sa.Column("admission_run_id", sa.Uuid(), nullable=False),
        sa.Column("pm_sync_receipt_id", sa.Uuid(), nullable=False),
        sa.Column("publication_attachment_id", sa.Text(), nullable=False),
        sa.Column("publication_repository_owner", sa.Text(), nullable=False),
        sa.Column("publication_repository_name", sa.Text(), nullable=False),
        sa.Column("publication_pr_number", sa.Integer(), nullable=False),
        sa.Column("board_fingerprint", sa.Text(), nullable=False),
        sa.Column("board_issue_count", sa.Integer(), nullable=False),
        sa.Column("observed_at", UTCDateTime(), nullable=False),
        sa.Column("created_by_type", sa.Text(), nullable=False),
        sa.Column("created_by_id", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ticket_id"),
        sa.UniqueConstraint("admission_run_id"),
        sa.UniqueConstraint("pm_sync_receipt_id"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"]),
        sa.ForeignKeyConstraint(["admission_run_id"], ["admission_runs.id"]),
        sa.ForeignKeyConstraint(["pm_sync_receipt_id"], ["pm_sync_receipts.id"]),
        sa.CheckConstraint(
            "schema_version = 'planned-ci-pending-recovery-v1'",
            name="planned_ci_pending_recoveries_schema_version",
        ),
        sa.CheckConstraint(
            "source_local_status = 'planned' AND recovered_local_status = 'ci_pending'",
            name="planned_ci_pending_recoveries_status_edge",
        ),
        sa.CheckConstraint(
            "created_by_type = 'system' AND "
            "created_by_id = 'pm-engine:planned-ci-pending-recovery'",
            name="planned_ci_pending_recoveries_authority",
        ),
    )

    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        _create_sqlite_append_only_triggers()
    elif dialect == "postgresql":
        _create_postgresql_append_only_trigger()


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        _drop_sqlite_append_only_triggers()
    elif dialect == "postgresql":
        _drop_postgresql_append_only_trigger()
    op.drop_table(TABLE)
