"""ORM table mappings (ATLAS-18), private to the storage package.

The Pydantic models in atlas.core.models are the contract; these row
classes mirror the SQL blocks in data-model-and-schemas.md exactly
(names, nullability, defaults, constraints — including the deliberately
FK-less evidence.agent_run_id and agent_runs.input_context_pack_id).
Conversion to and from the Pydantic models happens at the repository
boundary; row mappings stay inside atlas/storage/. Ordinary repository
consumers never receive sessions. The operator-action gateway is the explicit
transaction-context seam for composing a domain mutation and receipt atomically.

PostgreSQL-compatible types only: JSONB maps through a JSON shim on
SQLite (knowledge-core "Storage architecture"); datetimes go through
UTCDateTime (TIMESTAMPTZ on PostgreSQL, naive-UTC storage on SQLite,
always returned UTC-aware).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class UTCDateTime(sa.types.TypeDecorator[datetime]):
    """Normalise to UTC on write; return UTC-aware on read.

    Naive datetimes are rejected at the repository boundary
    (NaiveDatetimeError) before they ever reach this type.
    """

    impl = sa.DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(
        self, value: datetime | None, dialect: sa.Dialect
    ) -> datetime | None:
        if value is None:
            return None
        return value.astimezone(UTC)

    def process_result_value(
        self, value: datetime | None, dialect: sa.Dialect
    ) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:  # SQLite stores naive; stored values are UTC
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


# JSONB on PostgreSQL, JSON shim on SQLite (knowledge-core).
JSONB = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")

_EMPTY_LIST = sa.text("'[]'")
_EMPTY_DICT = sa.text("'{}'")
_OPERATOR_ACTION_OUTCOME_RESULT_CHECK = """
    (outcome = 'succeeded' AND result_code = 'action_succeeded') OR
    (outcome = 'refused' AND result_code IN ('action_refused', 'stale_state')) OR
    (outcome = 'failed' AND result_code IN (
        'action_failed',
        'evidence_transport_failed',
        'evidence_authentication_failed',
        'evidence_rate_limit_failed',
        'evidence_malformed_source'
    )) OR
    (outcome = 'conflict' AND result_code = 'action_conflict')
"""


class Base(DeclarativeBase):
    pass


class ProductRow(Base):
    __tablename__ = "products"

    id: Mapped[UUID] = mapped_column(sa.Uuid, primary_key=True)
    key: Mapped[str] = mapped_column(sa.Text, unique=True)
    name: Mapped[str] = mapped_column(sa.Text)
    description: Mapped[str] = mapped_column(sa.Text)
    vision: Mapped[str] = mapped_column(sa.Text)
    status: Mapped[str] = mapped_column(sa.Text)
    goals: Mapped[list[str]] = mapped_column(JSONB, server_default=_EMPTY_LIST)
    non_goals: Mapped[list[str]] = mapped_column(JSONB, server_default=_EMPTY_LIST)
    constraints: Mapped[list[str]] = mapped_column(JSONB, server_default=_EMPTY_LIST)
    created_by_type: Mapped[str] = mapped_column(sa.Text)
    created_by_id: Mapped[str] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class ArchitectureDecisionRecordRow(Base):
    __tablename__ = "architecture_decision_records"
    __table_args__ = (sa.UniqueConstraint("product_id", "number"),)

    id: Mapped[UUID] = mapped_column(sa.Uuid, primary_key=True)
    product_id: Mapped[UUID] = mapped_column(sa.Uuid, sa.ForeignKey("products.id"))
    number: Mapped[int] = mapped_column(sa.Integer)
    title: Mapped[str] = mapped_column(sa.Text)
    status: Mapped[str] = mapped_column(sa.Text)
    context: Mapped[str] = mapped_column(sa.Text)
    decision: Mapped[str] = mapped_column(sa.Text)
    rationale: Mapped[str] = mapped_column(sa.Text)
    consequences: Mapped[list[str]] = mapped_column(JSONB, server_default=_EMPTY_LIST)
    alternatives_considered: Mapped[list[str]] = mapped_column(
        JSONB, server_default=_EMPTY_LIST
    )
    supersedes_adr_id: Mapped[UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("architecture_decision_records.id")
    )
    created_by_type: Mapped[str] = mapped_column(sa.Text)
    created_by_id: Mapped[str] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class EpicRow(Base):
    __tablename__ = "epics"

    id: Mapped[UUID] = mapped_column(sa.Uuid, primary_key=True)
    product_id: Mapped[UUID] = mapped_column(sa.Uuid, sa.ForeignKey("products.id"))
    key: Mapped[str] = mapped_column(sa.Text, unique=True)
    title: Mapped[str] = mapped_column(sa.Text)
    description: Mapped[str] = mapped_column(sa.Text)
    objective: Mapped[str] = mapped_column(sa.Text)
    status: Mapped[str] = mapped_column(sa.Text)
    priority: Mapped[int] = mapped_column(sa.Integer, server_default=sa.text("0"))
    risk_level: Mapped[str] = mapped_column(sa.Text)
    source_anchor: Mapped[str] = mapped_column(sa.Text)
    created_by_type: Mapped[str] = mapped_column(sa.Text)
    created_by_id: Mapped[str] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class TicketRow(Base):
    __tablename__ = "tickets"

    id: Mapped[UUID] = mapped_column(sa.Uuid, primary_key=True)
    product_id: Mapped[UUID] = mapped_column(sa.Uuid, sa.ForeignKey("products.id"))
    epic_id: Mapped[UUID | None] = mapped_column(sa.Uuid, sa.ForeignKey("epics.id"))
    key: Mapped[str] = mapped_column(sa.Text, unique=True)
    title: Mapped[str] = mapped_column(sa.Text)
    objective: Mapped[str] = mapped_column(sa.Text)
    context: Mapped[str] = mapped_column(sa.Text)
    status: Mapped[str] = mapped_column(sa.Text)
    ticket_type: Mapped[str] = mapped_column(sa.Text)
    risk_level: Mapped[str] = mapped_column(sa.Text)
    priority: Mapped[int] = mapped_column(sa.Integer, server_default=sa.text("0"))
    relevant_docs: Mapped[list[str]] = mapped_column(JSONB, server_default=_EMPTY_LIST)
    acceptance_criteria: Mapped[list[str]] = mapped_column(
        JSONB, server_default=_EMPTY_LIST
    )
    non_goals: Mapped[list[str]] = mapped_column(JSONB, server_default=_EMPTY_LIST)
    implementation_notes: Mapped[list[str]] = mapped_column(
        JSONB, server_default=_EMPTY_LIST
    )
    test_requirements: Mapped[list[str]] = mapped_column(
        JSONB, server_default=_EMPTY_LIST
    )
    documentation_requirements: Mapped[list[str]] = mapped_column(
        JSONB, server_default=_EMPTY_LIST
    )
    definition_of_done: Mapped[list[str]] = mapped_column(
        JSONB, server_default=_EMPTY_LIST
    )
    estimated_effort: Mapped[int | None] = mapped_column(sa.Integer)
    external_linear_id: Mapped[str | None] = mapped_column(sa.Text)
    external_github_issue_id: Mapped[str | None] = mapped_column(sa.Text)
    tags: Mapped[list[str]] = mapped_column(JSONB, server_default=_EMPTY_LIST)
    component: Mapped[str | None] = mapped_column(sa.Text)
    linear_synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_observed_linear_state_id: Mapped[str | None] = mapped_column(sa.Text)
    status_entered_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    review_cycle_count: Mapped[int] = mapped_column(
        sa.Integer, server_default=sa.text("0")
    )
    lesson_extraction_attempted_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime()
    )
    source_anchor: Mapped[str] = mapped_column(sa.Text)
    created_by_type: Mapped[str] = mapped_column(sa.Text)
    created_by_id: Mapped[str] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class TicketDependencyRow(Base):
    __tablename__ = "ticket_dependencies"

    id: Mapped[UUID] = mapped_column(sa.Uuid, primary_key=True)
    source_ticket_id: Mapped[UUID] = mapped_column(sa.Uuid, sa.ForeignKey("tickets.id"))
    target_entity_type: Mapped[str] = mapped_column(sa.Text)
    target_entity_id: Mapped[UUID] = mapped_column(sa.Uuid)
    dependency_type: Mapped[str] = mapped_column(sa.Text)
    reason: Mapped[str] = mapped_column(sa.Text)
    created_by_type: Mapped[str] = mapped_column(sa.Text)
    created_by_id: Mapped[str] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


class LessonRow(Base):
    __tablename__ = "lessons"
    __table_args__ = (
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="lessons_confidence_bounds"
        ),
    )

    id: Mapped[UUID] = mapped_column(sa.Uuid, primary_key=True)
    product_id: Mapped[UUID] = mapped_column(sa.Uuid, sa.ForeignKey("products.id"))
    status: Mapped[str] = mapped_column(sa.Text, server_default=sa.text("'draft'"))
    category: Mapped[str] = mapped_column(sa.Text)
    title: Mapped[str] = mapped_column(sa.Text)
    problem: Mapped[str] = mapped_column(sa.Text)
    solution: Mapped[str] = mapped_column(sa.Text)
    outcome: Mapped[str] = mapped_column(sa.Text)
    confidence: Mapped[float | None] = mapped_column(sa.Numeric(4, 3, asdecimal=False))
    source_ticket_id: Mapped[UUID] = mapped_column(sa.Uuid)
    related_ticket_ids: Mapped[list[str]] = mapped_column(
        JSONB, server_default=_EMPTY_LIST
    )
    related_adr_ids: Mapped[list[str]] = mapped_column(
        JSONB, server_default=_EMPTY_LIST
    )
    tags: Mapped[list[str]] = mapped_column(JSONB, server_default=_EMPTY_LIST)
    created_by_type: Mapped[str] = mapped_column(sa.Text)
    created_by_id: Mapped[str] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class EvidenceRow(Base):
    __tablename__ = "evidence"

    id: Mapped[UUID] = mapped_column(sa.Uuid, primary_key=True)
    product_id: Mapped[UUID] = mapped_column(sa.Uuid, sa.ForeignKey("products.id"))
    ticket_id: Mapped[UUID | None] = mapped_column(sa.Uuid, sa.ForeignKey("tickets.id"))
    # Deliberately no FK: Phase 8 reconstructs agent runs from
    # observation, so evidence may precede its run row (data-model §3.7).
    agent_run_id: Mapped[UUID | None] = mapped_column(sa.Uuid)
    evidence_type: Mapped[str] = mapped_column(sa.Text)
    status: Mapped[str] = mapped_column(sa.Text)
    summary: Mapped[str] = mapped_column(sa.Text)
    commit_sha: Mapped[str | None] = mapped_column(sa.Text)
    external_run_id: Mapped[str | None] = mapped_column(sa.Text)
    job_name: Mapped[str | None] = mapped_column(sa.Text)
    source_event_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    payload_hash: Mapped[str | None] = mapped_column(sa.Text)
    source_uri: Mapped[str | None] = mapped_column(sa.Text)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=_EMPTY_DICT
    )
    created_by_type: Mapped[str] = mapped_column(sa.Text)
    created_by_id: Mapped[str] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


class AgentRunRow(Base):
    __tablename__ = "agent_runs"

    id: Mapped[UUID] = mapped_column(sa.Uuid, primary_key=True)
    product_id: Mapped[UUID] = mapped_column(sa.Uuid, sa.ForeignKey("products.id"))
    ticket_id: Mapped[UUID | None] = mapped_column(sa.Uuid, sa.ForeignKey("tickets.id"))
    provider: Mapped[str] = mapped_column(sa.Text)
    model: Mapped[str | None] = mapped_column(sa.Text)
    status: Mapped[str] = mapped_column(sa.Text)
    objective: Mapped[str] = mapped_column(sa.Text)
    # FK-less per the §3.8 SQL contract.
    input_context_pack_id: Mapped[UUID | None] = mapped_column(sa.Uuid)
    output_summary: Mapped[str | None] = mapped_column(sa.Text)
    error_summary: Mapped[str | None] = mapped_column(sa.Text)
    cost_estimate_usd: Mapped[float | None] = mapped_column(
        sa.Numeric(12, 4, asdecimal=False)
    )
    prompt_tokens: Mapped[int | None] = mapped_column(sa.Integer)
    completion_tokens: Mapped[int | None] = mapped_column(sa.Integer)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


class ContextPackRow(Base):
    __tablename__ = "context_packs"

    id: Mapped[UUID] = mapped_column(sa.Uuid, primary_key=True)
    product_id: Mapped[UUID] = mapped_column(sa.Uuid, sa.ForeignKey("products.id"))
    ticket_id: Mapped[UUID | None] = mapped_column(sa.Uuid, sa.ForeignKey("tickets.id"))
    title: Mapped[str] = mapped_column(sa.Text)
    objective: Mapped[str] = mapped_column(sa.Text)
    constraints: Mapped[list[str]] = mapped_column(JSONB, server_default=_EMPTY_LIST)
    relevant_docs: Mapped[list[str]] = mapped_column(JSONB, server_default=_EMPTY_LIST)
    relevant_adrs: Mapped[list[str]] = mapped_column(JSONB, server_default=_EMPTY_LIST)
    related_tickets: Mapped[list[str]] = mapped_column(
        JSONB, server_default=_EMPTY_LIST
    )
    historical_lessons: Mapped[list[str]] = mapped_column(
        JSONB, server_default=_EMPTY_LIST
    )
    acceptance_criteria: Mapped[list[str]] = mapped_column(
        JSONB, server_default=_EMPTY_LIST
    )
    risks: Mapped[list[str]] = mapped_column(JSONB, server_default=_EMPTY_LIST)
    test_commands: Mapped[list[str]] = mapped_column(JSONB, server_default=_EMPTY_LIST)
    definition_of_done: Mapped[list[str]] = mapped_column(
        JSONB, server_default=_EMPTY_LIST
    )
    rendered_markdown: Mapped[str] = mapped_column(sa.Text)
    compression_applied: Mapped[list[str]] = mapped_column(
        JSONB, server_default=_EMPTY_LIST
    )
    input_doc_shas: Mapped[dict[str, str]] = mapped_column(
        JSONB, server_default=_EMPTY_DICT
    )
    token_estimate: Mapped[int | None] = mapped_column(sa.Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


class DebtItemRow(Base):
    """One delivery-anomaly observation (ATLAS-116, data-model §6.2).

    Append-only (enforced in DebtItemRepo, not here). ticket_id is
    FK-backed and NOT NULL: a delivery anomaly is always observed against
    an existing synced Atlas ticket (unlike evidence.agent_run_id, which
    is deliberately FK-less). No status, no updated_at — recurrence and
    severity derive by query.
    """

    __tablename__ = "debt_items"

    id: Mapped[UUID] = mapped_column(sa.Uuid, primary_key=True)
    product_id: Mapped[UUID] = mapped_column(sa.Uuid, sa.ForeignKey("products.id"))
    ticket_id: Mapped[UUID] = mapped_column(sa.Uuid, sa.ForeignKey("tickets.id"))
    anomaly_type: Mapped[str] = mapped_column(sa.Text)
    summary: Mapped[str] = mapped_column(sa.Text)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime())
    created_by_type: Mapped[str] = mapped_column(sa.Text)
    created_by_id: Mapped[str] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


class TickFailureRow(Base):
    """One recorded PM-scheduler tick crash (ATLAS-125, data-model §6.4).

    Append-only (enforced in TickFailureRepo, not here). Unlike DebtItemRow
    there is NO ticket_id and NO product_id and so NO foreign key: a tick
    crash is observed at the tick level, not against any one ticket — the
    very reason it is a separate model rather than a DebtItem. No status, no
    updated_at — query-time dedup derives from occurred_at and
    failure_signature.
    """

    __tablename__ = "tick_failures"

    id: Mapped[UUID] = mapped_column(sa.Uuid, primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime())
    failure_signature: Mapped[str] = mapped_column(sa.Text)
    detail: Mapped[str] = mapped_column(sa.Text)
    created_by_type: Mapped[str] = mapped_column(sa.Text)
    created_by_id: Mapped[str] = mapped_column(sa.Text)


class PmSyncReceiptRow(Base):
    """One PM sync tick receipt (ATLAS-245).

    Append-only (enforced in PmSyncReceiptRepo, not here). Tick-scoped, with
    optional product identity for cold or empty stores and the configured Linear
    project id for every run. It stores fingerprints and counters only, never
    Linear payload bodies or credentials.
    """

    __tablename__ = "pm_sync_receipts"

    id: Mapped[UUID] = mapped_column(sa.Uuid, primary_key=True)
    product_id: Mapped[UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("products.id")
    )
    product_key: Mapped[str | None] = mapped_column(sa.Text)
    linear_project_id: Mapped[str] = mapped_column(sa.Text)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime] = mapped_column(UTCDateTime())
    status_map_fingerprint: Mapped[str] = mapped_column(sa.Text)
    fetched_board_fingerprint: Mapped[str] = mapped_column(sa.Text)
    fetched_board_issue_count: Mapped[int] = mapped_column(sa.Integer)
    result: Mapped[str] = mapped_column(sa.Text)
    counters: Mapped[dict[str, int]] = mapped_column(JSONB, server_default=_EMPTY_DICT)
    error_summary: Mapped[str | None] = mapped_column(sa.Text)
    created_by_type: Mapped[str] = mapped_column(sa.Text)
    created_by_id: Mapped[str] = mapped_column(sa.Text)


class TicketStatusTransitionRow(Base):
    """One recorded real status transition (ATLAS-121, data-model §6.6).

    Append-only (enforced in TicketStatusTransitionRepo, not here). Modelled on
    DebtItemRow for the ticket_id foreign key — and unlike TickFailureRow, which
    is tick-level and has no FK, a transition is ALWAYS observed against an
    existing synced ticket, so ticket_id is FK-backed and NOT NULL. There is no
    product_id (transitions are ticket-scoped, not product-scoped). No status,
    no created_at, no updated_at — occurred_at is the only instant and the row
    is never mutated.
    """

    __tablename__ = "ticket_status_transitions"

    id: Mapped[UUID] = mapped_column(sa.Uuid, primary_key=True)
    ticket_id: Mapped[UUID] = mapped_column(sa.Uuid, sa.ForeignKey("tickets.id"))
    from_status: Mapped[str] = mapped_column(sa.Text)
    to_status: Mapped[str] = mapped_column(sa.Text)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime())
    created_by_type: Mapped[str] = mapped_column(sa.Text)
    created_by_id: Mapped[str] = mapped_column(sa.Text)


class VerificationCheckRow(Base):
    """One Verification Engine evaluation for a ticket (ATLAS-71,
    data-model §5.2).

    Append-only (enforced in VerificationCheckRepo, not here). NOT
    evidence: ``status`` is an EvidenceStatus outcome, but there is no
    trust tier and no commit pin (contrast EvidenceRow) — so no trust-tier
    cap and no commit-pin guard live on its repo. ``ticket_id`` is FK-backed
    and NOT NULL (a check is always evaluated against an existing ticket).
    ``required`` defaults TRUE and ``evidence_ids`` defaults '[]', mirroring
    the §5.2 SQL block. There is no ``updated_at``; ``completed_at`` is
    nullable.
    """

    __tablename__ = "verification_checks"

    id: Mapped[UUID] = mapped_column(sa.Uuid, primary_key=True)
    ticket_id: Mapped[UUID] = mapped_column(sa.Uuid, sa.ForeignKey("tickets.id"))
    check_type: Mapped[str] = mapped_column(sa.Text)
    status: Mapped[str] = mapped_column(sa.Text)
    summary: Mapped[str] = mapped_column(sa.Text)
    required: Mapped[bool] = mapped_column(sa.Boolean, server_default=sa.text("TRUE"))
    evidence_ids: Mapped[list[str]] = mapped_column(JSONB, server_default=_EMPTY_LIST)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class DeliveryAdmissionPolicyRevisionRow(Base):
    """One immutable, product-scoped delivery admission policy revision."""

    __tablename__ = "delivery_admission_policy_revisions"
    __table_args__ = (
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

    id: Mapped[UUID] = mapped_column(sa.Uuid, primary_key=True)
    product_id: Mapped[UUID] = mapped_column(sa.Uuid, sa.ForeignKey("products.id"))
    revision: Mapped[int] = mapped_column(sa.Integer)
    mode: Mapped[str] = mapped_column(sa.Text)
    approved_symphony_ceiling: Mapped[int] = mapped_column(sa.Integer)
    working_budget: Mapped[int] = mapped_column(sa.Integer)
    review_budget: Mapped[int] = mapped_column(sa.Integer)
    changes_requested_reserve: Mapped[int] = mapped_column(sa.Integer)
    risk_lane_limits: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default=_EMPTY_LIST
    )
    component_lane_limits: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default=_EMPTY_LIST
    )
    created_by_type: Mapped[str] = mapped_column(sa.Text)
    created_by_id: Mapped[str] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


class DeliveryAdmissionPolicyActiveRow(Base):
    """Mutable pointer to one product's authoritative policy revision."""

    __tablename__ = "delivery_admission_policy_active"
    product_id: Mapped[UUID] = mapped_column(
        sa.Uuid,
        primary_key=True,
    )
    revision: Mapped[int] = mapped_column(sa.Integer)

    __table_args__ = (
        sa.CheckConstraint(
            "revision >= 1",
            name="delivery_admission_policy_active_revision_positive",
        ),
        sa.ForeignKeyConstraint(
            ["product_id", "revision"],
            [
                "delivery_admission_policy_revisions.product_id",
                "delivery_admission_policy_revisions.revision",
            ],
        ),
    )


class AdmissionRunRow(Base):
    """One immutable admission evaluation with bounded decision JSON."""

    __tablename__ = "admission_runs"
    __table_args__ = (
        sa.CheckConstraint(
            "schema_version = 'admission-run-v1'",
            name="admission_runs_schema_version",
        ),
        sa.CheckConstraint(
            "policy_revision >= 1",
            name="admission_runs_policy_revision_positive",
        ),
    )

    id: Mapped[UUID] = mapped_column(sa.Uuid, primary_key=True)
    schema_version: Mapped[str] = mapped_column(sa.Text)
    product_id: Mapped[UUID] = mapped_column(sa.Uuid, sa.ForeignKey("products.id"))
    policy_id: Mapped[UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("delivery_admission_policy_revisions.id")
    )
    policy_revision: Mapped[int] = mapped_column(sa.Integer)
    policy_fingerprint: Mapped[str] = mapped_column(sa.Text)
    snapshot_fingerprint: Mapped[str] = mapped_column(sa.Text)
    snapshot_observed_at: Mapped[datetime] = mapped_column(UTCDateTime())
    evaluated_at: Mapped[datetime] = mapped_column(UTCDateTime())
    selected_ticket_id: Mapped[UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("tickets.id")
    )
    selected_ticket_key: Mapped[str | None] = mapped_column(sa.Text)
    decisions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, server_default=_EMPTY_LIST
    )
    created_by_type: Mapped[str] = mapped_column(sa.Text)
    created_by_id: Mapped[str] = mapped_column(sa.Text)


class OperatorActionKeyRow(Base):
    """One idempotency-key reservation for governed operator writes.

    The row is inserted before command invocation and never updated. If a row
    exists without a matching terminal receipt, the key has an explicit
    in-progress owner and retries must not infer that the command is safe to
    rerun.
    """

    __tablename__ = "operator_action_keys"

    idempotency_key_identity: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    request_fingerprint: Mapped[str] = mapped_column(sa.Text)
    receipt_id: Mapped[UUID] = mapped_column(sa.Uuid)
    correlation_id: Mapped[UUID] = mapped_column(sa.Uuid)
    action: Mapped[str] = mapped_column(sa.Text)
    target_type: Mapped[str] = mapped_column(sa.Text)
    target_id: Mapped[str] = mapped_column(sa.Text)
    created_by_type: Mapped[str] = mapped_column(sa.Text)
    created_by_id: Mapped[str] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


class OperatorActionReceiptRow(Base):
    """Append-only terminal receipt for one governed operator write."""

    __tablename__ = "operator_action_receipts"
    __table_args__ = (
        sa.UniqueConstraint("idempotency_key_identity"),
        sa.UniqueConstraint("correlation_id"),
        sa.CheckConstraint(
            _OPERATOR_ACTION_OUTCOME_RESULT_CHECK,
            name="operator_action_receipts_outcome_result_code",
        ),
    )

    id: Mapped[UUID] = mapped_column(sa.Uuid, primary_key=True)
    correlation_id: Mapped[UUID] = mapped_column(sa.Uuid)
    action: Mapped[str] = mapped_column(sa.Text)
    target_type: Mapped[str] = mapped_column(sa.Text)
    target_id: Mapped[str] = mapped_column(sa.Text)
    created_by_type: Mapped[str] = mapped_column(sa.Text)
    created_by_id: Mapped[str] = mapped_column(sa.Text)
    idempotency_key_identity: Mapped[str] = mapped_column(
        sa.Text, sa.ForeignKey("operator_action_keys.idempotency_key_identity")
    )
    request_fingerprint: Mapped[str] = mapped_column(sa.Text)
    outcome: Mapped[str] = mapped_column(sa.Text)
    result_code: Mapped[str] = mapped_column(sa.Text)
    result_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=_EMPTY_DICT
    )
    before_status: Mapped[str | None] = mapped_column(sa.Text)
    after_status: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    completed_at: Mapped[datetime] = mapped_column(UTCDateTime())


class LessonDispositionResultSnapshotRow(Base):
    """Immutable safe lesson projection for one successful disposition."""

    __tablename__ = "lesson_disposition_result_snapshots"
    __table_args__ = (
        sa.CheckConstraint(
            "status IN ('active', 'archived')",
            name="lesson_disposition_result_snapshots_terminal_status",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="lesson_disposition_result_snapshots_confidence_bounds",
        ),
    )

    idempotency_key_identity: Mapped[str] = mapped_column(
        sa.Text,
        sa.ForeignKey("operator_action_keys.idempotency_key_identity"),
        primary_key=True,
    )
    id: Mapped[UUID] = mapped_column(sa.Uuid)
    product_id: Mapped[UUID] = mapped_column(sa.Uuid)
    status: Mapped[str] = mapped_column(sa.Text)
    category: Mapped[str] = mapped_column(sa.Text)
    title: Mapped[str] = mapped_column(sa.Text)
    problem: Mapped[str] = mapped_column(sa.Text)
    solution: Mapped[str] = mapped_column(sa.Text)
    outcome: Mapped[str] = mapped_column(sa.Text)
    confidence: Mapped[float | None] = mapped_column(sa.Numeric(4, 3, asdecimal=False))
    source_ticket_id: Mapped[UUID] = mapped_column(sa.Uuid)
    related_ticket_ids: Mapped[list[str]] = mapped_column(JSONB)
    related_adr_ids: Mapped[list[str]] = mapped_column(JSONB)
    tags: Mapped[list[str]] = mapped_column(JSONB)
    created_by_type: Mapped[str] = mapped_column(sa.Text)
    created_by_id: Mapped[str] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class AcceptanceSessionRow(Base):
    """Durable summary for one immutable-head acceptance attempt."""

    __tablename__ = "acceptance_sessions"
    __table_args__ = (
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
        sa.Index(
            "uq_acceptance_sessions_non_terminal_pr",
            "repository_owner",
            "repository_name",
            "pr_number",
            unique=True,
            sqlite_where=sa.text("lifecycle NOT IN ('stale', 'blocked', 'failed')"),
            postgresql_where=sa.text("lifecycle NOT IN ('stale', 'blocked', 'failed')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(sa.Uuid, primary_key=True)
    repository_owner: Mapped[str] = mapped_column(sa.Text)
    repository_name: Mapped[str] = mapped_column(sa.Text)
    pr_number: Mapped[int] = mapped_column(sa.Integer)
    close_set: Mapped[list[str]] = mapped_column(JSONB)
    head_ref: Mapped[str] = mapped_column(sa.Text)
    head_sha: Mapped[str] = mapped_column(sa.Text)
    head_repository: Mapped[str] = mapped_column(sa.Text)
    base_ref: Mapped[str] = mapped_column(sa.Text)
    base_sha: Mapped[str] = mapped_column(sa.Text)
    base_repository: Mapped[str] = mapped_column(sa.Text)
    initial_assessment: Mapped[dict[str, Any]] = mapped_column(JSONB)
    criteria_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    criteria_fingerprint: Mapped[str] = mapped_column(sa.Text)
    creation_idempotency_key_identity: Mapped[str] = mapped_column(sa.Text)
    created_by_type: Mapped[str] = mapped_column(sa.Text)
    created_by_id: Mapped[str] = mapped_column(sa.Text)
    lifecycle: Mapped[str] = mapped_column(sa.Text)
    step_summaries: Mapped[dict[str, Any]] = mapped_column(JSONB)
    blocking_reasons: Mapped[list[str]] = mapped_column(JSONB)
    stored_merge_ready: Mapped[bool] = mapped_column(
        sa.Boolean, server_default=sa.text("FALSE")
    )
    historical_readiness_reasons: Mapped[list[str]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime())
    staled_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class KeyCounterRow(Base):
    """Monotonic per-prefix key counter (ATLAS-25, data-model §3.12).

    One row per key prefix, keyed on the prefix itself: no-reuse is
    structural (the value only advances and is decoupled from backlog
    membership; the PK makes one authoritative counter per prefix).
    """

    __tablename__ = "key_counters"
    __table_args__ = (
        sa.CheckConstraint("high_water >= 0", name="key_counters_high_water_nonneg"),
    )

    prefix: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    high_water: Mapped[int] = mapped_column(sa.Integer, server_default=sa.text("0"))


class PlanRunRow(Base):
    __tablename__ = "plan_runs"

    id: Mapped[UUID] = mapped_column(sa.Uuid, primary_key=True)
    product_id: Mapped[UUID] = mapped_column(sa.Uuid, sa.ForeignKey("products.id"))
    status: Mapped[str] = mapped_column(sa.Text)
    input_doc_shas: Mapped[dict[str, str]] = mapped_column(
        JSONB, server_default=_EMPTY_DICT
    )
    model_provider: Mapped[str] = mapped_column(sa.Text)
    model_name: Mapped[str] = mapped_column(sa.Text)
    prompt_version: Mapped[str] = mapped_column(sa.Text)
    prompt_hash: Mapped[str] = mapped_column(sa.Text)
    model_parameters: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=_EMPTY_DICT
    )
    similarity_threshold: Mapped[float] = mapped_column(
        sa.Numeric(4, 3, asdecimal=False)
    )
    raw_output_hash: Mapped[str] = mapped_column(sa.Text)
    proposal: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=_EMPTY_DICT)
    generation_stages: Mapped[list[dict[str, str]]] = mapped_column(
        JSONB, server_default=_EMPTY_LIST
    )
    diff_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=_EMPTY_DICT
    )
    failure_reason: Mapped[str | None] = mapped_column(sa.Text)
    approved_by: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    applied_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


def _append_only_sqlite_trigger(table_name: str, operation: str) -> sa.DDL:
    trigger_name = f"{table_name}_no_{operation.lower()}"
    return sa.DDL(  # type: ignore[no-untyped-call]
        f"""
        CREATE TRIGGER {trigger_name}
        BEFORE {operation} ON {table_name}
        BEGIN
            SELECT RAISE(ABORT, '{table_name} is append-only');
        END
        """
    )


def _drop_sqlite_trigger(table_name: str, operation: str) -> sa.DDL:
    trigger_name = f"{table_name}_no_{operation.lower()}"
    return sa.DDL(  # type: ignore[no-untyped-call]
        f"DROP TRIGGER IF EXISTS {trigger_name}"
    )


_APPEND_ONLY_TABLES = (
    cast(sa.Table, AdmissionRunRow.__table__),
    cast(sa.Table, DeliveryAdmissionPolicyRevisionRow.__table__),
    cast(sa.Table, LessonDispositionResultSnapshotRow.__table__),
    cast(sa.Table, OperatorActionKeyRow.__table__),
    cast(sa.Table, OperatorActionReceiptRow.__table__),
)

for _append_only_table in _APPEND_ONLY_TABLES:
    _append_only_table_name = _append_only_table.name
    for _operation in ("UPDATE", "DELETE"):
        sa.event.listen(
            _append_only_table,
            "after_create",
            _append_only_sqlite_trigger(_append_only_table_name, _operation).execute_if(
                dialect="sqlite"
            ),
        )
        sa.event.listen(
            _append_only_table,
            "before_drop",
            _drop_sqlite_trigger(_append_only_table_name, _operation).execute_if(
                dialect="sqlite"
            ),
        )


_ACCEPTANCE_SESSION_PINNED_COLUMNS = (
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


def _acceptance_session_pins_sqlite_trigger() -> sa.DDL:
    comparisons = " OR ".join(
        f"NEW.{column} IS NOT OLD.{column}"
        for column in _ACCEPTANCE_SESSION_PINNED_COLUMNS
    )
    return sa.DDL(  # type: ignore[no-untyped-call]
        f"""
        CREATE TRIGGER acceptance_sessions_pinned_identity
        BEFORE UPDATE ON acceptance_sessions
        WHEN {comparisons}
        BEGIN
            SELECT RAISE(ABORT, 'acceptance session pinned identity is immutable');
        END
        """
    )


sa.event.listen(
    cast(sa.Table, AcceptanceSessionRow.__table__),
    "after_create",
    _acceptance_session_pins_sqlite_trigger().execute_if(dialect="sqlite"),
)
sa.event.listen(
    cast(sa.Table, AcceptanceSessionRow.__table__),
    "before_drop",
    sa.DDL(  # type: ignore[no-untyped-call]
        "DROP TRIGGER IF EXISTS acceptance_sessions_pinned_identity"
    ).execute_if(dialect="sqlite"),
)
