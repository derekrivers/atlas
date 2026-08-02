"""immutable delivery admission policy revisions and active pointers.

The bootstrap writes one explicit running revision for every existing product.
It preserves the live Symphony ceiling and working budget at three and does not
modify WORKFLOW.md or any external configuration.

Revision ID: 0025
Revises: 0024
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

from atlas.storage.tables import JSONB, UTCDateTime

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REVISION_TABLE = "delivery_admission_policy_revisions"


def _create_sqlite_append_only_triggers() -> None:
    for operation in ("UPDATE", "DELETE"):
        trigger_name = f"{REVISION_TABLE}_no_{operation.lower()}"
        op.execute(
            f"""
            CREATE TRIGGER {trigger_name}
            BEFORE {operation} ON {REVISION_TABLE}
            BEGIN
                SELECT RAISE(ABORT, '{REVISION_TABLE} is append-only');
            END
            """
        )


def _drop_sqlite_append_only_triggers() -> None:
    for operation in ("UPDATE", "DELETE"):
        trigger_name = f"{REVISION_TABLE}_no_{operation.lower()}"
        op.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")


def _create_postgresql_append_only_triggers() -> None:
    function_name = f"{REVISION_TABLE}_reject_mutation"
    trigger_name = f"{REVISION_TABLE}_append_only"
    op.execute(
        f"""
        CREATE FUNCTION {function_name}()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '{REVISION_TABLE} is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {trigger_name}
        BEFORE UPDATE OR DELETE ON {REVISION_TABLE}
        FOR EACH ROW EXECUTE FUNCTION {function_name}()
        """
    )


def _drop_postgresql_append_only_triggers() -> None:
    function_name = f"{REVISION_TABLE}_reject_mutation"
    trigger_name = f"{REVISION_TABLE}_append_only"
    op.execute(f"DROP TRIGGER IF EXISTS {trigger_name} ON {REVISION_TABLE}")
    op.execute(f"DROP FUNCTION IF EXISTS {function_name}()")


def upgrade() -> None:
    revisions = op.create_table(
        REVISION_TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("mode", sa.Text(), nullable=False),
        sa.Column("approved_symphony_ceiling", sa.Integer(), nullable=False),
        sa.Column("working_budget", sa.Integer(), nullable=False),
        sa.Column("review_budget", sa.Integer(), nullable=False),
        sa.Column("changes_requested_reserve", sa.Integer(), nullable=False),
        sa.Column(
            "risk_lane_limits",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column(
            "component_lane_limits",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column("created_by_type", sa.Text(), nullable=False),
        sa.Column("created_by_id", sa.Text(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.UniqueConstraint("product_id", "revision"),
        sa.CheckConstraint(
            "mode IN ('running', 'paused', 'draining')",
            name="delivery_admission_policy_mode",
        ),
        sa.CheckConstraint(
            "approved_symphony_ceiling >= 1 AND approved_symphony_ceiling <= 10",
            name="delivery_admission_policy_ceiling_bounds",
        ),
        sa.CheckConstraint(
            "working_budget >= 1 AND working_budget <= approved_symphony_ceiling",
            name="delivery_admission_policy_working_bounds",
        ),
        sa.CheckConstraint(
            "review_budget >= 1 AND review_budget <= 10",
            name="delivery_admission_policy_review_bounds",
        ),
        sa.CheckConstraint(
            "changes_requested_reserve >= 0 "
            "AND changes_requested_reserve <= working_budget",
            name="delivery_admission_policy_reserve_bounds",
        ),
        sa.CheckConstraint(
            "revision >= 1",
            name="delivery_admission_policy_revision_positive",
        ),
    )
    active = op.create_table(
        "delivery_admission_policy_active",
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("product_id"),
        sa.ForeignKeyConstraint(
            ["product_id", "revision"],
            [
                "delivery_admission_policy_revisions.product_id",
                "delivery_admission_policy_revisions.revision",
            ],
        ),
        sa.CheckConstraint(
            "revision >= 1",
            name="delivery_admission_policy_active_revision_positive",
        ),
    )

    bind = op.get_bind()
    products = sa.table("products", sa.column("id", sa.Uuid()))
    product_ids = list(bind.execute(sa.select(products.c.id)).scalars())
    bootstrapped_at = datetime.now(UTC)
    if product_ids:
        op.bulk_insert(
            revisions,
            [
                {
                    "id": product_id,
                    "product_id": product_id,
                    "revision": 1,
                    "mode": "running",
                    "approved_symphony_ceiling": 3,
                    "working_budget": 3,
                    "review_budget": 3,
                    "changes_requested_reserve": 0,
                    "risk_lane_limits": [],
                    "component_lane_limits": [],
                    "created_by_type": "system",
                    "created_by_id": "migration-0025",
                    "created_at": bootstrapped_at,
                }
                for product_id in product_ids
            ],
        )
        op.bulk_insert(
            active,
            [{"product_id": product_id, "revision": 1} for product_id in product_ids],
        )

    dialect = bind.dialect.name
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

    op.drop_table("delivery_admission_policy_active")
    op.drop_table(REVISION_TABLE)
