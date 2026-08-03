"""durable exact-head acceptance sessions.

Revision ID: 0024
Revises: 0023
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from atlas.storage.tables import UTCDateTime

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")

PINNED_COLUMNS = (
    "id",
    "repository_owner",
    "repository_name",
    "pr_number",
    "close_set",
    "head_ref",
    "head_sha",
    "head_repository",
    "base_ref",
    "base_sha",
    "base_repository",
    "initial_assessment",
    "criteria_snapshot",
    "criteria_fingerprint",
    "creation_idempotency_key_identity",
    "created_by_type",
    "created_by_id",
    "created_at",
)


def _create_sqlite_pinned_trigger() -> None:
    comparisons = " OR ".join(
        f"NEW.{column} IS NOT OLD.{column}" for column in PINNED_COLUMNS
    )
    op.execute(
        f"""
        CREATE TRIGGER acceptance_sessions_pinned_identity
        BEFORE UPDATE ON acceptance_sessions
        WHEN {comparisons}
        BEGIN
            SELECT RAISE(ABORT, 'acceptance session pinned identity is immutable');
        END
        """
    )


def _create_postgresql_pinned_trigger() -> None:
    comparisons = " OR ".join(
        f"NEW.{column} IS DISTINCT FROM OLD.{column}" for column in PINNED_COLUMNS
    )
    op.execute(
        f"""
        CREATE FUNCTION acceptance_sessions_reject_pinned_update()
        RETURNS trigger AS $$
        BEGIN
            IF {comparisons} THEN
                RAISE EXCEPTION 'acceptance session pinned identity is immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER acceptance_sessions_pinned_identity
        BEFORE UPDATE ON acceptance_sessions
        FOR EACH ROW EXECUTE FUNCTION acceptance_sessions_reject_pinned_update()
        """
    )


def upgrade() -> None:
    op.create_table(
        "acceptance_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("repository_owner", sa.Text(), nullable=False),
        sa.Column("repository_name", sa.Text(), nullable=False),
        sa.Column("pr_number", sa.Integer(), nullable=False),
        sa.Column("close_set", JSONB, nullable=False),
        sa.Column("head_ref", sa.Text(), nullable=False),
        sa.Column("head_sha", sa.Text(), nullable=False),
        sa.Column("head_repository", sa.Text(), nullable=False),
        sa.Column("base_ref", sa.Text(), nullable=False),
        sa.Column("base_sha", sa.Text(), nullable=False),
        sa.Column("base_repository", sa.Text(), nullable=False),
        sa.Column("initial_assessment", JSONB, nullable=False),
        sa.Column("criteria_snapshot", JSONB, nullable=False),
        sa.Column("criteria_fingerprint", sa.Text(), nullable=False),
        sa.Column("creation_idempotency_key_identity", sa.Text(), nullable=False),
        sa.Column("created_by_type", sa.Text(), nullable=False),
        sa.Column("created_by_id", sa.Text(), nullable=False),
        sa.Column("lifecycle", sa.Text(), nullable=False),
        sa.Column("step_summaries", JSONB, nullable=False),
        sa.Column("blocking_reasons", JSONB, nullable=False),
        sa.Column(
            "stored_merge_ready",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("FALSE"),
        ),
        sa.Column("historical_readiness_reasons", JSONB, nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.Column("staled_at", UTCDateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("creation_idempotency_key_identity"),
        sa.CheckConstraint(
            "created_by_type = 'human' AND created_by_id = 'operator'",
            name="acceptance_sessions_operator_actor",
        ),
        sa.CheckConstraint(
            "(lifecycle = 'stale' AND staled_at IS NOT NULL) OR "
            "(lifecycle <> 'stale' AND staled_at IS NULL)",
            name="acceptance_sessions_stale_timestamp",
        ),
    )
    op.create_index(
        "uq_acceptance_sessions_non_terminal_pr",
        "acceptance_sessions",
        ["repository_owner", "repository_name", "pr_number"],
        unique=True,
        sqlite_where=sa.text("lifecycle NOT IN ('stale', 'blocked', 'failed')"),
        postgresql_where=sa.text("lifecycle NOT IN ('stale', 'blocked', 'failed')"),
    )

    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        _create_sqlite_pinned_trigger()
    elif dialect == "postgresql":
        _create_postgresql_pinned_trigger()


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS acceptance_sessions_pinned_identity")
    elif dialect == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS acceptance_sessions_pinned_identity "
            "ON acceptance_sessions"
        )
        op.execute("DROP FUNCTION IF EXISTS acceptance_sessions_reject_pinned_update()")

    op.drop_index(
        "uq_acceptance_sessions_non_terminal_pr",
        table_name="acceptance_sessions",
    )
    op.drop_table("acceptance_sessions")
