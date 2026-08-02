"""operator action idempotency reservations and append-only receipts.

Revision ID: 0023
Revises: 0022
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from atlas.storage.tables import UTCDateTime

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def _create_sqlite_append_only_triggers(table_name: str) -> None:
    for operation in ("UPDATE", "DELETE"):
        trigger_name = f"{table_name}_no_{operation.lower()}"
        op.execute(
            f"""
            CREATE TRIGGER {trigger_name}
            BEFORE {operation} ON {table_name}
            BEGIN
                SELECT RAISE(ABORT, '{table_name} is append-only');
            END
            """
        )


def _drop_sqlite_append_only_triggers(table_name: str) -> None:
    for operation in ("UPDATE", "DELETE"):
        trigger_name = f"{table_name}_no_{operation.lower()}"
        op.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")


def _create_postgresql_append_only_triggers(table_name: str) -> None:
    function_name = f"{table_name}_reject_mutation"
    trigger_name = f"{table_name}_append_only"
    op.execute(
        f"""
        CREATE FUNCTION {function_name}()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '{table_name} is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {trigger_name}
        BEFORE UPDATE OR DELETE ON {table_name}
        FOR EACH ROW EXECUTE FUNCTION {function_name}()
        """
    )


def _drop_postgresql_append_only_triggers(table_name: str) -> None:
    function_name = f"{table_name}_reject_mutation"
    trigger_name = f"{table_name}_append_only"
    op.execute(f"DROP TRIGGER IF EXISTS {trigger_name} ON {table_name}")
    op.execute(f"DROP FUNCTION IF EXISTS {function_name}()")


def upgrade() -> None:
    op.create_table(
        "operator_action_keys",
        sa.Column("idempotency_key_identity", sa.Text(), nullable=False),
        sa.Column("request_fingerprint", sa.Text(), nullable=False),
        sa.Column("receipt_id", sa.Uuid(), nullable=False),
        sa.Column("correlation_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target_type", sa.Text(), nullable=False),
        sa.Column("target_id", sa.Text(), nullable=False),
        sa.Column("created_by_type", sa.Text(), nullable=False),
        sa.Column("created_by_id", sa.Text(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("idempotency_key_identity"),
    )
    op.create_table(
        "operator_action_receipts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("correlation_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target_type", sa.Text(), nullable=False),
        sa.Column("target_id", sa.Text(), nullable=False),
        sa.Column("created_by_type", sa.Text(), nullable=False),
        sa.Column("created_by_id", sa.Text(), nullable=False),
        sa.Column("idempotency_key_identity", sa.Text(), nullable=False),
        sa.Column("request_fingerprint", sa.Text(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("result_code", sa.Text(), nullable=False),
        sa.Column(
            "result_metadata",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("before_status", sa.Text(), nullable=True),
        sa.Column("after_status", sa.Text(), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("completed_at", UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["idempotency_key_identity"],
            ["operator_action_keys.idempotency_key_identity"],
        ),
        sa.UniqueConstraint("idempotency_key_identity"),
        sa.UniqueConstraint("correlation_id"),
    )

    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        _create_sqlite_append_only_triggers("operator_action_keys")
        _create_sqlite_append_only_triggers("operator_action_receipts")
    elif dialect == "postgresql":
        _create_postgresql_append_only_triggers("operator_action_keys")
        _create_postgresql_append_only_triggers("operator_action_receipts")


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        _drop_sqlite_append_only_triggers("operator_action_receipts")
        _drop_sqlite_append_only_triggers("operator_action_keys")
    elif dialect == "postgresql":
        _drop_postgresql_append_only_triggers("operator_action_receipts")
        _drop_postgresql_append_only_triggers("operator_action_keys")

    op.drop_table("operator_action_receipts")
    op.drop_table("operator_action_keys")
