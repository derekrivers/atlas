"""Dependency providers for API route handlers."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from atlas.api.presenters import present_review_queue, present_ticket_board
from atlas.api.schemas import ReviewQueueResponse, TicketBoardResponse
from atlas.core.models import TicketStatus
from atlas.orchestration import review_queue
from atlas.storage import Database, TicketRepo


def get_database(request: Request) -> Database:
    """Return the application-scoped database created during startup."""
    database: Database = request.app.state.database
    return database


DatabaseDependency = Annotated[Database, Depends(get_database)]


def get_ticket_repo(database: DatabaseDependency) -> TicketRepo:
    """Build the single-domain ticket service over the shared database."""
    return TicketRepo(database)


TicketRepoDependency = Annotated[TicketRepo, Depends(get_ticket_repo)]


def get_ticket_board(
    tickets: TicketRepoDependency,
    status: TicketStatus | None = None,
) -> TicketBoardResponse:
    """Build a key-ordered lean board from the requested ticket set."""
    selected = tickets.list_by_status(status) if status is not None else tickets.list()
    return present_ticket_board(selected)


TicketBoardDependency = Annotated[TicketBoardResponse, Depends(get_ticket_board)]


def get_review_queue(database: DatabaseDependency) -> ReviewQueueResponse:
    """Build the serialised operator review queue from persisted state."""
    states = review_queue(database)
    return present_review_queue(states)


ReviewQueueDependency = Annotated[ReviewQueueResponse, Depends(get_review_queue)]
