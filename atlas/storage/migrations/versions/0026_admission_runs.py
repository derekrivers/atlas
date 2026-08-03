"""append-only deterministic admission runs.

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

TABLE = "admission_runs"


def _create_sqlite_append_only_triggers() -> None:
    for operation in ("UPDATE", "DELETE"):
        trigger_name = f"{TABLE}_no_{operation.lower()}"
        op.execute(
            f"""
            CREATE TRIGGER {trigger_name}
            BEFORE {operation} ON {TABLE}
            BEGIN
                SELECT RAISE(ABORT, '{TABLE} is append-only');
            END
            """
        )


def _drop_sqlite_append_only_triggers() -> None:
    for operation in ("UPDATE", "DELETE"):
        trigger_name = f"{TABLE}_no_{operation.lower()}"
        op.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")


def _create_postgresql_append_only_triggers() -> None:
    function_name = f"{TABLE}_reject_mutation"
    trigger_name = f"{TABLE}_append_only"
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
        CREATE TRIGGER {trigger_name}
        BEFORE UPDATE OR DELETE ON {TABLE}
        FOR EACH ROW EXECUTE FUNCTION {function_name}()
        """
    )


def _drop_postgresql_append_only_triggers() -> None:
    function_name = f"{TABLE}_reject_mutation"
    trigger_name = f"{TABLE}_append_only"
    op.execute(f"DROP TRIGGER IF EXISTS {trigger_name} ON {TABLE}")
    op.execute(f"DROP FUNCTION IF EXISTS {function_name}()")


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.Text(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("policy_revision", sa.Integer(), nullable=False),
        sa.Column("policy_fingerprint", sa.Text(), nullable=False),
        sa.Column("snapshot_fingerprint", sa.Text(), nullable=False),
        sa.Column("snapshot_observed_at", UTCDateTime(), nullable=False),
        sa.Column("evaluated_at", UTCDateTime(), nullable=False),
        sa.Column("selected_ticket_id", sa.Uuid(), nullable=True),
        sa.Column("selected_ticket_key", sa.Text(), nullable=True),
        sa.Column("decisions", JSONB, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("created_by_type", sa.Text(), nullable=False),
        sa.Column("created_by_id", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.ForeignKeyConstraint(
            ["policy_id"], ["delivery_admission_policy_revisions.id"]
        ),
        sa.ForeignKeyConstraint(["selected_ticket_id"], ["tickets.id"]),
        sa.CheckConstraint(
            "schema_version = 'admission-run-v1'",
            name="admission_runs_schema_version",
        ),
        sa.CheckConstraint(
            "policy_revision >= 1",
            name="admission_runs_policy_revision_positive",
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
    op.drop_table(TABLE)
