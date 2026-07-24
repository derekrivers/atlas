"""Pydantic response schemas exposed by the HTTP adapter."""

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
