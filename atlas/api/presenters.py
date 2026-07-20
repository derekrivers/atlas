"""Pure schema presenters for HTTP responses."""

from __future__ import annotations

from collections.abc import Sequence

from atlas.api.schemas import (
    ReviewCheckSchema,
    ReviewQueueItemSchema,
    ReviewQueueResponse,
    TicketBoardItemSchema,
    TicketBoardResponse,
)
from atlas.core.models import Ticket
from atlas.orchestration import TicketReviewState


def present_ticket_board(tickets: Sequence[Ticket]) -> TicketBoardResponse:
    """Present tickets as a key-ordered lean board."""
    ordered = sorted(tickets, key=lambda ticket: ticket.key)
    return TicketBoardResponse(
        tickets=[
            TicketBoardItemSchema(
                key=ticket.key,
                title=ticket.title,
                status=ticket.status.value,
                ticket_type=ticket.ticket_type.value,
                priority=ticket.priority,
                risk_level=ticket.risk_level.value,
            )
            for ticket in ordered
        ]
    )


def present_review_queue(
    states: tuple[TicketReviewState, ...],
) -> ReviewQueueResponse:
    """Present stored review states as an HTTP response."""
    return ReviewQueueResponse(
        reviews=[
            ReviewQueueItemSchema(
                key=state.key,
                title=state.title,
                status=state.status.value,
                ticket_type=state.ticket_type.value,
                verdict=state.verdict.value,
                checks=[
                    ReviewCheckSchema(
                        check_type=check.check_type.value,
                        status=check.status.value,
                    )
                    for check in state.checks
                ],
                has_system_evidence=state.has_system_evidence,
                has_pr_merged_evidence=state.has_pr_merged_evidence,
            )
            for state in states
        ]
    )
