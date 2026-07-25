"""Trivial ticket route demonstrating the thin-handler pattern."""

from fastapi import APIRouter

from atlas.api.dependencies import (
    TicketBoardDependency,
    TicketDetailDependency,
    TicketEvidenceDependency,
    TicketRepoDependency,
)
from atlas.api.schemas import (
    TicketBoardResponse,
    TicketCountResponse,
    TicketDetailResponse,
    TicketEvidenceResponse,
)

router = APIRouter(prefix="/tickets", tags=["tickets"])


@router.get("", response_model=TicketBoardResponse)
def list_tickets(board: TicketBoardDependency) -> TicketBoardResponse:
    return board


@router.get("/count", response_model=TicketCountResponse)
def ticket_count(tickets: TicketRepoDependency) -> TicketCountResponse:
    return TicketCountResponse(count=tickets.count())


@router.get("/{key}", response_model=TicketDetailResponse)
def ticket_detail(detail: TicketDetailDependency) -> TicketDetailResponse:
    return detail


@router.get("/{key}/evidence", response_model=TicketEvidenceResponse)
def ticket_evidence_records(
    evidence: TicketEvidenceDependency,
) -> TicketEvidenceResponse:
    return evidence
