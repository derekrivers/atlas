"""Pydantic response schemas exposed by the HTTP adapter."""

from pydantic import BaseModel


class TicketCountResponse(BaseModel):
    """Number of tickets in the Atlas store."""

    count: int


class ReviewCheckSchema(BaseModel):
    """One persisted verification outcome in a review item."""

    check_type: str
    status: str


class ReviewQueueItemSchema(BaseModel):
    """One ticket awaiting operator review."""

    key: str
    title: str
    status: str
    ticket_type: str
    verdict: str
    checks: list[ReviewCheckSchema]
    has_system_evidence: bool
    has_pr_merged_evidence: bool


class ReviewQueueResponse(BaseModel):
    """Operator review queue."""

    reviews: list[ReviewQueueItemSchema]
