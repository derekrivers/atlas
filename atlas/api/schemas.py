"""Pydantic response schemas exposed by the HTTP adapter."""

from datetime import datetime

from pydantic import BaseModel

from atlas.core.enums import EvidenceStatus, RiskLevel
from atlas.core.models import TicketStatus, TicketType, VerificationCheckType


class TicketCountResponse(BaseModel):
    """Number of tickets in the Atlas store."""

    count: int


class TicketBoardItemSchema(BaseModel):
    """One lean ticket card on the operator board."""

    key: str
    title: str
    status: TicketStatus
    ticket_type: TicketType
    priority: int
    risk_level: RiskLevel


class TicketBoardResponse(BaseModel):
    """Lean tickets displayed on the operator board."""

    tickets: list[TicketBoardItemSchema]


class TicketDetailResponse(BaseModel):
    """Operator-facing definition and execution state for one ticket."""

    key: str
    title: str
    objective: str
    context: str
    status: TicketStatus
    ticket_type: TicketType
    risk_level: RiskLevel
    priority: int
    estimated_effort: int | None
    relevant_docs: list[str]
    acceptance_criteria: list[str]
    non_goals: list[str]
    implementation_notes: list[str]
    test_requirements: list[str]
    documentation_requirements: list[str]
    definition_of_done: list[str]
    tags: list[str]
    component: str | None
    external_linear_id: str | None
    external_github_issue_id: str | None
    source_anchor: str
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class ReviewCheckSchema(BaseModel):
    """One persisted verification outcome in a review item."""

    check_type: VerificationCheckType
    status: EvidenceStatus


class ReviewQueueItemSchema(BaseModel):
    """One ticket awaiting operator review."""

    key: str
    title: str
    status: TicketStatus
    ticket_type: TicketType
    verdict: EvidenceStatus
    checks: list[ReviewCheckSchema]
    has_system_evidence: bool
    has_pr_merged_evidence: bool


class ReviewQueueResponse(BaseModel):
    """Operator review queue."""

    reviews: list[ReviewQueueItemSchema]
