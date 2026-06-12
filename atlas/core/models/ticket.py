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
    priority: int
    relevant_docs: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    non_goals: list[str] = Field(default_factory=list)
    implementation_notes: list[str] = Field(default_factory=list)
    test_requirements: list[str] = Field(default_factory=list)
    documentation_requirements: list[str] = Field(default_factory=list)
    definition_of_done: list[str] = Field(default_factory=list)
    estimated_effort: int | None = None  # populated from Phase 3 (critical path)
    external_linear_id: str | None = None
    external_github_issue_id: str | None = None
    created_by_type: ActorType
    created_by_id: str
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
