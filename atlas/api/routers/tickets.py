"""Trivial ticket route demonstrating the thin-handler pattern."""

from fastapi import APIRouter

from atlas.api.dependencies import TicketRepoDependency
from atlas.api.schemas import TicketCountResponse

router = APIRouter(prefix="/api/tickets", tags=["tickets"])


@router.get("/count", response_model=TicketCountResponse)
def ticket_count(tickets: TicketRepoDependency) -> TicketCountResponse:
    return TicketCountResponse(count=tickets.count())
