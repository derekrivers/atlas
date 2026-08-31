"""durable PM recovery episodes, fairness, and blocker state.

Revision ID: 0036
Revises: 0035
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from atlas.storage.tables import UTCDateTime

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pm_recovery_sequence_counters",
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column(
            "high_water", sa.BigInteger(), server_default=sa.text("0"), nullable=False
        ),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.PrimaryKeyConstraint("product_id"),
        sa.CheckConstraint(
            "high_water >= 0 AND high_water <= 9223372036854775807",
            name="pm_recovery_sequence_counters_bounds",
        ),
    )

    op.create_table(
        "pm_recovery_episodes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.Text(), nullable=False),
        sa.Column("identity_fingerprint", sa.Text(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("authority_id", sa.Text(), nullable=False),
        sa.Column("authoritative_episode_id", sa.Text(), nullable=False),
        sa.Column("active_scope_fingerprint", sa.Text(), nullable=True),
        sa.Column("candidate_ticket_id", sa.Uuid(), nullable=True),
        sa.Column("candidate_ticket_key", sa.Text(), nullable=True),
        sa.Column("episode_created_sequence", sa.BigInteger(), nullable=False),
        sa.Column("last_evaluated_sequence", sa.BigInteger(), nullable=True),
        sa.Column("last_evaluation_id", sa.Text(), nullable=True),
        sa.Column("last_evaluation_fingerprint", sa.Text(), nullable=True),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("last_evaluated_at", UTCDateTime(), nullable=True),
        sa.Column("closed_at", UTCDateTime(), nullable=True),
        sa.Column("closure_event_id", sa.Text(), nullable=True),
        sa.Column("closure_kind", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["candidate_ticket_id"], ["tickets.id"]),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("identity_fingerprint"),
        sa.UniqueConstraint("product_id", "active_scope_fingerprint"),
        sa.UniqueConstraint("product_id", "episode_created_sequence"),
        sa.UniqueConstraint("product_id", "last_evaluated_sequence"),
        sa.CheckConstraint(
            "schema_version = 'pm-recovery-episode-v1'",
            name="pm_recovery_episodes_schema_version",
        ),
        sa.CheckConstraint(
            "length(identity_fingerprint) = 64 AND "
            "length(operation) BETWEEN 1 AND 128 AND "
            "length(authority_id) BETWEEN 1 AND 128 AND "
            "length(authoritative_episode_id) BETWEEN 1 AND 128",
            name="pm_recovery_episodes_identity_bounds",
        ),
        sa.CheckConstraint(
            "(candidate_ticket_id IS NULL AND candidate_ticket_key IS NULL) OR "
            "(candidate_ticket_id IS NOT NULL AND "
            "length(candidate_ticket_key) BETWEEN 1 AND 128)",
            name="pm_recovery_episodes_candidate_pair",
        ),
        sa.CheckConstraint(
            "episode_created_sequence > 0 AND "
            "(last_evaluated_sequence IS NULL OR "
            "last_evaluated_sequence > episode_created_sequence)",
            name="pm_recovery_episodes_sequence_order",
        ),
        sa.CheckConstraint(
            "(last_evaluated_sequence IS NULL AND last_evaluation_id IS NULL AND "
            "last_evaluation_fingerprint IS NULL AND last_evaluated_at IS NULL) OR "
            "(last_evaluated_sequence IS NOT NULL AND "
            "length(last_evaluation_id) BETWEEN 1 AND 128 AND "
            "length(last_evaluation_fingerprint) = 64 AND "
            "last_evaluated_at IS NOT NULL AND last_evaluated_at >= created_at)",
            name="pm_recovery_episodes_evaluation_fields",
        ),
        sa.CheckConstraint(
            "(closed_at IS NULL AND closure_event_id IS NULL AND closure_kind IS NULL) "
            "OR (closed_at IS NOT NULL AND length(closure_event_id) BETWEEN 1 AND 128 "
            "AND closure_kind IN ('authoritative_lifecycle_entry', "
            "'publication_replacement', 'recovery_completed') "
            "AND closed_at >= created_at AND "
            "(last_evaluated_at IS NULL OR closed_at >= last_evaluated_at))",
            name="pm_recovery_episodes_closure_fields",
        ),
        sa.CheckConstraint(
            "(closed_at IS NULL AND length(active_scope_fingerprint) = 64) OR "
            "(closed_at IS NOT NULL AND active_scope_fingerprint IS NULL)",
            name="pm_recovery_episodes_active_scope",
        ),
    )
    op.create_index(
        "ix_pm_recovery_episodes_active_operation",
        "pm_recovery_episodes",
        ["product_id", "closed_at", "operation"],
    )
    op.create_index(
        "ix_pm_recovery_episodes_active_candidate",
        "pm_recovery_episodes",
        ["product_id", "candidate_ticket_id", "closed_at"],
    )
    op.create_index(
        "ix_pm_recovery_episodes_fairness",
        "pm_recovery_episodes",
        [
            "product_id",
            "closed_at",
            "last_evaluated_sequence",
            "episode_created_sequence",
            "candidate_ticket_key",
        ],
    )

    op.create_table(
        "pm_blocker_occurrences",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.Text(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("authority_kind", sa.Text(), nullable=False),
        sa.Column("authority_id", sa.Text(), nullable=False),
        sa.Column("recovery_episode_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_ticket_id", sa.Uuid(), nullable=True),
        sa.Column("candidate_ticket_key", sa.Text(), nullable=True),
        sa.Column("blocker_fingerprint", sa.Text(), nullable=False),
        sa.Column("active_fingerprint", sa.Text(), nullable=True),
        sa.Column("first_evaluation_id", sa.Text(), nullable=False),
        sa.Column("latest_evaluation_id", sa.Text(), nullable=False),
        sa.Column("first_observed_at", UTCDateTime(), nullable=False),
        sa.Column("latest_observed_at", UTCDateTime(), nullable=False),
        sa.Column("consecutive_observations", sa.BigInteger(), nullable=False),
        sa.Column("next_safe_retry_at", UTCDateTime(), nullable=True),
        sa.Column(
            "capacity_impact",
            sa.Boolean(),
            server_default=sa.text("FALSE"),
            nullable=False,
        ),
        sa.Column("policy_namespace", sa.Text(), nullable=True),
        sa.Column("policy_revision", sa.BigInteger(), nullable=True),
        sa.Column("policy_fingerprint", sa.Text(), nullable=True),
        sa.Column("superseded_at", UTCDateTime(), nullable=True),
        sa.Column("superseded_by_event_id", sa.Text(), nullable=True),
        sa.Column("supersession_kind", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["candidate_ticket_id"], ["tickets.id"]),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.ForeignKeyConstraint(["recovery_episode_id"], ["pm_recovery_episodes.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("product_id", "active_fingerprint"),
        sa.CheckConstraint(
            "schema_version = 'pm-blocker-observation-v1'",
            name="pm_blocker_occurrences_schema_version",
        ),
        sa.CheckConstraint(
            "kind IN ('routine_wait', 'retryable', 'unresolved_fence', 'unknown')",
            name="pm_blocker_occurrences_kind",
        ),
        sa.CheckConstraint(
            "code IN ('lease_unavailable', 'provider_unavailable', "
            "'publication_ambiguous', 'publication_not_yet_complete')",
            name="pm_blocker_occurrences_code",
        ),
        sa.CheckConstraint(
            "authority_kind IN ('operation', 'lease', 'fence', 'intent')",
            name="pm_blocker_occurrences_authority_kind",
        ),
        sa.CheckConstraint(
            "kind <> 'unresolved_fence' OR authority_kind = 'fence'",
            name="pm_blocker_occurrences_fence_authority",
        ),
        sa.CheckConstraint(
            "length(operation) BETWEEN 1 AND 128 AND "
            "length(code) BETWEEN 1 AND 128 AND "
            "length(authority_id) BETWEEN 1 AND 128 AND "
            "length(blocker_fingerprint) = 64",
            name="pm_blocker_occurrences_identity_bounds",
        ),
        sa.CheckConstraint(
            "(candidate_ticket_id IS NULL AND candidate_ticket_key IS NULL) OR "
            "(candidate_ticket_id IS NOT NULL AND "
            "length(candidate_ticket_key) BETWEEN 1 AND 128)",
            name="pm_blocker_occurrences_candidate_pair",
        ),
        sa.CheckConstraint(
            "length(first_evaluation_id) BETWEEN 1 AND 128 AND "
            "length(latest_evaluation_id) BETWEEN 1 AND 128 AND "
            "latest_observed_at >= first_observed_at AND "
            "consecutive_observations BETWEEN 1 AND 2147483647",
            name="pm_blocker_occurrences_observation_bounds",
        ),
        sa.CheckConstraint(
            "(policy_namespace IS NULL AND policy_revision IS NULL AND "
            "policy_fingerprint IS NULL) OR "
            "(length(policy_namespace) BETWEEN 1 AND 128 AND policy_revision > 0 "
            "AND length(policy_fingerprint) = 64)",
            name="pm_blocker_occurrences_policy_fields",
        ),
        sa.CheckConstraint(
            "(active_fingerprint = blocker_fingerprint AND superseded_at IS NULL "
            "AND superseded_by_event_id IS NULL AND supersession_kind IS NULL) OR "
            "(active_fingerprint IS NULL AND superseded_at IS NOT NULL "
            "AND length(superseded_by_event_id) BETWEEN 1 AND 128 "
            "AND supersession_kind IN ('progress', 'recovery') "
            "AND superseded_at >= latest_observed_at)",
            name="pm_blocker_occurrences_active_or_superseded",
        ),
    )
    op.create_index(
        "ix_pm_blocker_occurrences_active_operation",
        "pm_blocker_occurrences",
        ["product_id", "active_fingerprint", "operation"],
    )
    op.create_index(
        "ix_pm_blocker_occurrences_active_candidate",
        "pm_blocker_occurrences",
        ["product_id", "candidate_ticket_id", "active_fingerprint"],
    )
    op.create_index(
        "ix_pm_blocker_occurrences_episode",
        "pm_blocker_occurrences",
        ["recovery_episode_id", "active_fingerprint"],
    )

    op.create_table(
        "pm_blocker_starved_candidates",
        sa.Column("blocker_occurrence_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("ticket_key", sa.Text(), nullable=False),
        sa.Column("started_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["blocker_occurrence_id"], ["pm_blocker_occurrences.id"]
        ),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"]),
        sa.PrimaryKeyConstraint("blocker_occurrence_id", "ordinal"),
        sa.UniqueConstraint("blocker_occurrence_id", "ticket_id"),
        sa.UniqueConstraint("blocker_occurrence_id", "ticket_key"),
        sa.CheckConstraint(
            "ordinal BETWEEN 1 AND 128",
            name="pm_blocker_starved_candidates_ordinal_bounds",
        ),
        sa.CheckConstraint(
            "length(ticket_key) BETWEEN 1 AND 128",
            name="pm_blocker_starved_candidates_key_bounds",
        ),
    )


def downgrade() -> None:
    op.drop_table("pm_blocker_starved_candidates")
    op.drop_index(
        "ix_pm_blocker_occurrences_episode", table_name="pm_blocker_occurrences"
    )
    op.drop_index(
        "ix_pm_blocker_occurrences_active_candidate",
        table_name="pm_blocker_occurrences",
    )
    op.drop_index(
        "ix_pm_blocker_occurrences_active_operation",
        table_name="pm_blocker_occurrences",
    )
    op.drop_table("pm_blocker_occurrences")
    op.drop_index("ix_pm_recovery_episodes_fairness", table_name="pm_recovery_episodes")
    op.drop_index(
        "ix_pm_recovery_episodes_active_candidate",
        table_name="pm_recovery_episodes",
    )
    op.drop_index(
        "ix_pm_recovery_episodes_active_operation",
        table_name="pm_recovery_episodes",
    )
    op.drop_table("pm_recovery_episodes")
    op.drop_table("pm_recovery_sequence_counters")
