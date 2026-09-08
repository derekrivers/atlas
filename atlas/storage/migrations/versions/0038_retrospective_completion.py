"""retrospective completion proof and crash-safe write fence.

Revision ID: 0038
Revises: 0037
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from atlas.storage.tables import JSONB, UTCDateTime

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OUTCOME_TABLE = "retrospective_completion_reconciliations"
FENCE_TABLE = "retrospective_completion_write_fences"

_OLD_BLOCKER_CODES = (
    "code IN ('lease_unavailable', 'provider_unavailable', "
    "'publication_ambiguous', 'publication_not_yet_complete', "
    "'ci_evidence_not_yet_complete', 'ci_evidence_ambiguous', "
    "'authority_changed', 'write_fence_unresolved')"
)
_NEW_BLOCKER_CODES = (
    "code IN ('lease_unavailable', 'provider_unavailable', "
    "'publication_ambiguous', 'publication_not_yet_complete', "
    "'ci_evidence_not_yet_complete', 'ci_evidence_ambiguous', "
    "'authority_changed', 'write_fence_unresolved', "
    "'retrospective_proof_incomplete', 'retrospective_proof_ambiguous')"
)


def _replace_blocker_constraint(expression: str) -> None:
    with op.batch_alter_table("pm_blocker_occurrences") as batch:
        batch.drop_constraint("pm_blocker_occurrences_code", type_="check")
        batch.create_check_constraint("pm_blocker_occurrences_code", expression)


def _create_append_only_guard() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        for operation in ("UPDATE", "DELETE"):
            op.execute(
                f"""
                CREATE TRIGGER {OUTCOME_TABLE}_no_{operation.lower()}
                BEFORE {operation} ON {OUTCOME_TABLE}
                BEGIN
                    SELECT RAISE(ABORT, '{OUTCOME_TABLE} is append-only');
                END
                """
            )
    elif dialect == "postgresql":
        op.execute(
            f"""
            CREATE FUNCTION {OUTCOME_TABLE}_reject_mutation()
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
            FOR EACH ROW EXECUTE FUNCTION {OUTCOME_TABLE}_reject_mutation()
            """
        )


def _drop_append_only_guard() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        for operation in ("UPDATE", "DELETE"):
            op.execute(f"DROP TRIGGER IF EXISTS {OUTCOME_TABLE}_no_{operation.lower()}")
    elif dialect == "postgresql":
        op.execute(
            f"DROP TRIGGER IF EXISTS {OUTCOME_TABLE}_append_only ON {OUTCOME_TABLE}"
        )
        op.execute(f"DROP FUNCTION IF EXISTS {OUTCOME_TABLE}_reject_mutation()")


def upgrade() -> None:
    _replace_blocker_constraint(_NEW_BLOCKER_CODES)
    op.create_table(
        OUTCOME_TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.Text(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("ticket_key", sa.Text(), nullable=False),
        sa.Column("linear_issue_id", sa.Text(), nullable=True),
        sa.Column("recovery_episode_id", sa.Uuid(), nullable=True),
        sa.Column("publication_attachment_id", sa.Text(), nullable=True),
        sa.Column("repository_owner", sa.Text(), nullable=True),
        sa.Column("repository_name", sa.Text(), nullable=True),
        sa.Column("pr_number", sa.Integer(), nullable=True),
        sa.Column("contributor_head", sa.Text(), nullable=True),
        sa.Column("merge_commit", sa.Text(), nullable=True),
        sa.Column("canonical_main", sa.Text(), nullable=True),
        sa.Column("policy_id", sa.Uuid(), nullable=True),
        sa.Column("policy_revision", sa.Integer(), nullable=True),
        sa.Column("policy_fingerprint", sa.Text(), nullable=True),
        sa.Column("snapshot_fingerprint", sa.Text(), nullable=True),
        sa.Column("acceptance_session_id", sa.Uuid(), nullable=True),
        sa.Column("verification_verdict_id", sa.Uuid(), nullable=True),
        sa.Column("criteria_fingerprint", sa.Text(), nullable=True),
        sa.Column(
            "verification_check_ids",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column(
            "deciding_evidence_ids",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column("merged_evidence_id", sa.Uuid(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("observed_at", UTCDateTime(), nullable=False),
        sa.Column("created_by_type", sa.Text(), nullable=False),
        sa.Column("created_by_id", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"]),
        sa.ForeignKeyConstraint(["recovery_episode_id"], ["pm_recovery_episodes.id"]),
        sa.ForeignKeyConstraint(
            ["policy_id"], ["delivery_admission_policy_revisions.id"]
        ),
        sa.ForeignKeyConstraint(["acceptance_session_id"], ["acceptance_sessions.id"]),
        sa.ForeignKeyConstraint(["merged_evidence_id"], ["evidence.id"]),
        sa.CheckConstraint(
            "schema_version = 'retrospective-completion-reconciliation-v1'",
            name="retrospective_completion_reconciliations_schema_version",
        ),
        sa.CheckConstraint(
            "decision IN ('hold', 'done')",
            name="retrospective_completion_reconciliations_decision",
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
        sa.ForeignKeyConstraint(["reconciliation_id"], [OUTCOME_TABLE + ".id"]),
        sa.CheckConstraint(
            "target_status = 'done'",
            name="retrospective_completion_write_fences_target_status",
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'indeterminate')",
            name="retrospective_completion_write_fences_state",
        ),
    )
    _create_append_only_guard()


def downgrade() -> None:
    op.drop_table(FENCE_TABLE)
    _drop_append_only_guard()
    op.drop_table(OUTCOME_TABLE)
    _replace_blocker_constraint(_OLD_BLOCKER_CODES)
