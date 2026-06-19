"""Ticket model and its enums (ATLAS-12), per data-model-and-schemas.md §3.4."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field

from atlas.core.enums import ActorType, RiskLevel


class TicketStatus(StrEnum):
    """Lifecycle of a ticket (data-model §3.4)."""

    BACKLOG = "backlog"
    PLANNED = "planned"
    BLOCKED = "blocked"
    READY_FOR_AGENT = "ready_for_agent"
    IN_PROGRESS = "in_progress"
    PR_OPEN = "pr_open"
    REVIEW_REQUIRED = "review_required"
    CHANGES_REQUESTED = "changes_requested"
    DONE = "done"
    REJECTED = "rejected"
    NEEDS_HUMAN_DECISION = "needs_human_decision"


class TicketType(StrEnum):
    """Kind of work a ticket represents (data-model §3.4)."""

    FEATURE = "feature"
    BUG = "bug"
    TECH_DEBT = "tech_debt"
    SPIKE = "spike"
    DOCUMENTATION = "documentation"
    INFRASTRUCTURE = "infrastructure"
    RESEARCH = "research"


class Ticket(BaseModel):
    """The atomic unit of agent-executable work: small, scoped,
    dependency-aware, and verifiable."""

    id: UUID
    product_id: UUID
    epic_id: UUID | None = None
    key: str
    title: str
    objective: str
    context: str
    status: TicketStatus
    ticket_type: TicketType
    risk_level: RiskLevel
    priority: int = Field(ge=-2147483648, le=2147483647)  # SQL INTEGER range
    relevant_docs: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    non_goals: list[str] = Field(default_factory=list)
    implementation_notes: list[str] = Field(default_factory=list)
    test_requirements: list[str] = Field(default_factory=list)
    documentation_requirements: list[str] = Field(default_factory=list)
    definition_of_done: list[str] = Field(default_factory=list)
    # Populated from Phase 3 (critical path); SQL INTEGER range.
    estimated_effort: int | None = Field(default=None, ge=-2147483648, le=2147483647)
    external_linear_id: str | None = None
    external_github_issue_id: str | None = None
    # PM-Engine sync cursor (ATLAS-42): the value of ``updated_at`` at the
    # last confirmed definition push to Linear. A definition is re-pushed
    # only while ``updated_at > linear_synced_at`` (or this is null and the
    # ticket has never synced). Written by the sync loop, never by an Atlas
    # definition edit, so stamping it cannot itself trigger a re-push.
    linear_synced_at: datetime | None = None
    # Reconciler anchor-match pass; AT-1 traceability — every item
    # traceable to a document anchor.
    source_anchor: str
    created_by_type: ActorType
    created_by_id: str
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
