"""Dependency providers for API route handlers."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from atlas.api.schemas import (
    ReviewCheckSchema,
    ReviewQueueItemSchema,
    ReviewQueueResponse,
)
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


def get_review_queue(database: DatabaseDependency) -> ReviewQueueResponse:
    """Build the serialised operator review queue from persisted state."""
    states = review_queue(database)
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


ReviewQueueDependency = Annotated[ReviewQueueResponse, Depends(get_review_queue)]
