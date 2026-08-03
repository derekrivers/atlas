"""immutable successful lesson disposition result snapshots.

Revision ID: 0026
Revises: 0025
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from atlas.storage.tables import JSONB, UTCDateTime

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAME = "lesson_disposition_result_snapshots"


def _create_sqlite_append_only_triggers() -> None:
    for operation in ("UPDATE", "DELETE"):
        trigger_name = f"{TABLE_NAME}_no_{operation.lower()}"
        op.execute(
            f"""
            CREATE TRIGGER {trigger_name}
            BEFORE {operation} ON {TABLE_NAME}
            BEGIN
                SELECT RAISE(ABORT, '{TABLE_NAME} is append-only');
            END
            """
        )


def _drop_sqlite_append_only_triggers() -> None:
    for operation in ("UPDATE", "DELETE"):
        trigger_name = f"{TABLE_NAME}_no_{operation.lower()}"
        op.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")


def _create_postgresql_append_only_triggers() -> None:
    function_name = f"{TABLE_NAME}_reject_mutation"
    trigger_name = f"{TABLE_NAME}_append_only"
    op.execute(
        f"""
        CREATE FUNCTION {function_name}()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '{TABLE_NAME} is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {trigger_name}
        BEFORE UPDATE OR DELETE ON {TABLE_NAME}
        FOR EACH ROW EXECUTE FUNCTION {function_name}()
        """
    )


def _drop_postgresql_append_only_triggers() -> None:
    function_name = f"{TABLE_NAME}_reject_mutation"
    trigger_name = f"{TABLE_NAME}_append_only"
    op.execute(f"DROP TRIGGER IF EXISTS {trigger_name} ON {TABLE_NAME}")
    op.execute(f"DROP FUNCTION IF EXISTS {function_name}()")


def upgrade() -> None:
    op.create_table(
        TABLE_NAME,
        sa.Column("idempotency_key_identity", sa.Text(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("problem", sa.Text(), nullable=False),
        sa.Column("solution", sa.Text(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column(
            "confidence",
            sa.Numeric(precision=4, scale=3, asdecimal=False),
            nullable=True,
        ),
        sa.Column("source_ticket_id", sa.Uuid(), nullable=False),
        sa.Column("related_ticket_ids", JSONB, nullable=False),
        sa.Column("related_adr_ids", JSONB, nullable=False),
        sa.Column("tags", JSONB, nullable=False),
        sa.Column("created_by_type", sa.Text(), nullable=False),
        sa.Column("created_by_id", sa.Text(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("idempotency_key_identity"),
        sa.ForeignKeyConstraint(
            ["idempotency_key_identity"],
            ["operator_action_keys.idempotency_key_identity"],
        ),
        sa.CheckConstraint(
            "status IN ('active', 'archived')",
            name="lesson_disposition_result_snapshots_terminal_status",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="lesson_disposition_result_snapshots_confidence_bounds",
        ),
    )

    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        _create_sqlite_append_only_triggers()
    elif dialect == "postgresql":
        _create_postgresql_append_only_triggers()


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        _drop_sqlite_append_only_triggers()
    elif dialect == "postgresql":
        _drop_postgresql_append_only_triggers()

    op.drop_table(TABLE_NAME)
