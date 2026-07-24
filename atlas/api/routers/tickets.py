"""Trivial ticket route demonstrating the thin-handler pattern."""

from fastapi import APIRouter

from atlas.api.dependencies import TicketBoardDependency, TicketRepoDependency
from atlas.api.schemas import TicketBoardResponse, TicketCountResponse

router = APIRouter(prefix="/tickets", tags=["tickets"])


@router.get("", response_model=TicketBoardResponse)
def list_tickets(board: TicketBoardDependency) -> TicketBoardResponse:
    return board


@router.get("/count", response_model=TicketCountResponse)
def ticket_count(tickets: TicketRepoDependency) -> TicketCountResponse:
    return TicketCountResponse(count=tickets.count())
