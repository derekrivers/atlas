"""ORM table mappings (ATLAS-18), private to the storage package.

The Pydantic models in atlas.core.models are the contract; these row
classes mirror the SQL blocks in data-model-and-schemas.md §3.1-§3.10
exactly (names, nullability, defaults, constraints — including the
deliberately FK-less evidence.agent_run_id and
agent_runs.input_context_pack_id). Conversion to and from the Pydantic
models happens at the repository boundary; nothing outside
atlas/storage/ touches a row class or a session.

PostgreSQL-compatible types only: JSONB maps through a JSON shim on
SQLite (knowledge-core "Storage architecture"); datetimes go through
UTCDateTime (TIMESTAMPTZ on PostgreSQL, naive-UTC storage on SQLite,
always returned UTC-aware).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
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
    linear_synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_observed_linear_state_id: Mapped[str | None] = mapped_column(sa.Text)
    status_entered_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    review_cycle_count: Mapped[int] = mapped_column(
        sa.Integer, server_default=sa.text("0")
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
    confidence: Mapped[float] = mapped_column(sa.Numeric(4, 3, asdecimal=False))
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
