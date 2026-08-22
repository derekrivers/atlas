"""one-time ATLAS-280 bootstrap mirror-recovery receipt.

Revision ID: 0033
Revises: 0032
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from atlas.storage.tables import UTCDateTime

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "atlas_280_bootstrap_recovery_receipts"


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
        sa.Column("blocker_ticket_id", sa.Uuid(), nullable=False),
        sa.Column("blocker_ticket_key", sa.Text(), nullable=False),
        sa.Column("blocker_linear_issue_id", sa.Text(), nullable=False),
        sa.Column("blocker_linear_identifier", sa.Text(), nullable=False),
        sa.Column("blocker_linear_state_id", sa.Text(), nullable=False),
        sa.Column("repair_ticket_id", sa.Uuid(), nullable=False),
        sa.Column("repair_ticket_key", sa.Text(), nullable=False),
        sa.Column("repair_linear_issue_id", sa.Text(), nullable=False),
        sa.Column("repair_linear_identifier", sa.Text(), nullable=False),
        sa.Column("repair_linear_state_id", sa.Text(), nullable=False),
        sa.Column("source_local_status", sa.Text(), nullable=False),
        sa.Column("recovered_local_status", sa.Text(), nullable=False),
        sa.Column("admission_run_id", sa.Uuid(), nullable=False),
        sa.Column("pm_sync_receipt_id", sa.Uuid(), nullable=False),
        sa.Column("publication_repository_owner", sa.Text(), nullable=False),
        sa.Column("publication_repository_name", sa.Text(), nullable=False),
        sa.Column("publication_pr_number", sa.Integer(), nullable=False),
        sa.Column("publication_head", sa.Text(), nullable=False),
        sa.Column("historical_debt_item_id", sa.Uuid(), nullable=False),
        sa.Column("board_fingerprint", sa.Text(), nullable=False),
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("policy_revision", sa.Integer(), nullable=False),
        sa.Column("policy_fingerprint", sa.Text(), nullable=False),
        sa.Column("accepted_main_commit", sa.Text(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("created_by_type", sa.Text(), nullable=False),
        sa.Column("created_by_id", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("blocker_ticket_id"),
        sa.UniqueConstraint("admission_run_id"),
        sa.UniqueConstraint("pm_sync_receipt_id"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.ForeignKeyConstraint(["blocker_ticket_id"], ["tickets.id"]),
        sa.ForeignKeyConstraint(["repair_ticket_id"], ["tickets.id"]),
        sa.ForeignKeyConstraint(["admission_run_id"], ["admission_runs.id"]),
        sa.ForeignKeyConstraint(["pm_sync_receipt_id"], ["pm_sync_receipts.id"]),
        sa.ForeignKeyConstraint(["historical_debt_item_id"], ["debt_items.id"]),
        sa.ForeignKeyConstraint(
            ["policy_id"], ["delivery_admission_policy_revisions.id"]
        ),
        sa.CheckConstraint(
            "schema_version = 'atlas-280-bootstrap-mirror-recovery-v1'",
            name="atlas_280_bootstrap_recovery_schema_version",
        ),
        sa.CheckConstraint(
            "blocker_ticket_key = 'ATLAS-280' AND repair_ticket_key = 'ATLAS-281'",
            name="atlas_280_bootstrap_recovery_fixed_pair",
        ),
        sa.CheckConstraint(
            "blocker_linear_identifier = 'ATL-456' AND "
            "repair_linear_identifier = 'ATL-457'",
            name="atlas_280_bootstrap_recovery_fixed_linear_pair",
        ),
        sa.CheckConstraint(
            "source_local_status = 'planned' AND recovered_local_status = 'ci_pending'",
            name="atlas_280_bootstrap_recovery_status_edge",
        ),
        sa.CheckConstraint(
            "publication_repository_owner = 'derekrivers' AND "
            "publication_repository_name = 'atlas' AND publication_pr_number = 350",
            name="atlas_280_bootstrap_recovery_publication",
        ),
        sa.CheckConstraint(
            "policy_revision = 17 AND created_by_type = 'human'",
            name="atlas_280_bootstrap_recovery_authority",
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
