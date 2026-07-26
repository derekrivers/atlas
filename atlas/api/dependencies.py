"""Dependency providers for API route handlers."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from atlas.api.presenters import (
    present_dependency_critical_path,
    present_epics,
    present_lessons,
    present_review_queue,
    present_system_status,
    present_ticket_board,
    present_ticket_dependencies,
    present_ticket_detail,
    present_ticket_evidence,
)
from atlas.api.schemas import (
    DependencyCriticalPathResponse,
    EpicsResponse,
    LessonsResponse,
    ReviewQueueResponse,
    SystemStatusResponse,
    TicketBoardResponse,
    TicketDependenciesResponse,
    TicketDetailResponse,
    TicketEvidenceResponse,
)
from atlas.core.enums import EntityStatus
from atlas.core.models import TicketStatus
from atlas.orchestration import (
    dependency_critical_path,
    review_queue,
    system_status,
    ticket_board,
    ticket_dependencies,
    ticket_evidence,
)
from atlas.storage import Database, EpicRepo, LessonRepo, TicketRepo


def get_database(request: Request) -> Database:
    """Return the application-scoped database created during startup."""
    database: Database = request.app.state.database
    return database


DatabaseDependency = Annotated[Database, Depends(get_database)]


def get_ticket_repo(database: DatabaseDependency) -> TicketRepo:
    """Build the single-domain ticket service over the shared database."""
    return TicketRepo(database)


TicketRepoDependency = Annotated[TicketRepo, Depends(get_ticket_repo)]


def get_lesson_repo(database: DatabaseDependency) -> LessonRepo:
    """Build the single-domain lesson service over the shared database."""
    return LessonRepo(database)


LessonRepoDependency = Annotated[LessonRepo, Depends(get_lesson_repo)]


def get_epic_repo(database: DatabaseDependency) -> EpicRepo:
    """Build the single-domain epic service over the shared database."""
    return EpicRepo(database)


EpicRepoDependency = Annotated[EpicRepo, Depends(get_epic_repo)]


def get_ticket_board(
    database: DatabaseDependency,
    status: TicketStatus | None = None,
) -> TicketBoardResponse:
    """Build a key-ordered lean board from the requested ticket set."""
    board = ticket_board(database, status)
    return present_ticket_board(board)


TicketBoardDependency = Annotated[TicketBoardResponse, Depends(get_ticket_board)]


def get_ticket_detail(
    key: str,
    tickets: TicketRepoDependency,
) -> TicketDetailResponse:
    """Read and serialise one ticket, mapping an absent key to HTTP 404."""
    ticket = tickets.get_by_key(key)
    if ticket is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ticket {key} not found",
        )
    return present_ticket_detail(ticket)


TicketDetailDependency = Annotated[
    TicketDetailResponse,
    Depends(get_ticket_detail),
]


def get_ticket_evidence(
    key: str,
    database: DatabaseDependency,
) -> TicketEvidenceResponse:
    """Read and serialise one ticket's evidence, mapping absent key to 404."""
    evidence = ticket_evidence(database, key)
    if evidence is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ticket {key} not found",
        )
    return present_ticket_evidence(evidence)


TicketEvidenceDependency = Annotated[
    TicketEvidenceResponse,
    Depends(get_ticket_evidence),
]


def get_lessons(
    lessons: LessonRepoDependency,
    status: EntityStatus | None = None,
) -> LessonsResponse:
    """Read and serialise the requested stored lesson collection."""
    selected = lessons.list_by_status(status) if status is not None else lessons.list()
    return present_lessons(selected)


LessonsDependency = Annotated[LessonsResponse, Depends(get_lessons)]


def get_epics(epics: EpicRepoDependency) -> EpicsResponse:
    """Read and serialise the stored epic collection."""
    return present_epics(epics.list())


EpicsDependency = Annotated[EpicsResponse, Depends(get_epics)]


def get_ticket_dependencies(
    key: str,
    database: DatabaseDependency,
) -> TicketDependenciesResponse:
    """Read and serialise one ticket's dependency projection, mapping 404."""
    dependencies = ticket_dependencies(database, key)
    if dependencies is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ticket {key} not found",
        )
    return present_ticket_dependencies(dependencies)


TicketDependenciesDependency = Annotated[
    TicketDependenciesResponse,
    Depends(get_ticket_dependencies),
]


def get_dependency_critical_path(
    database: DatabaseDependency,
) -> DependencyCriticalPathResponse:
    """Build the serialised graph-wide dependency critical path."""
    path = dependency_critical_path(database)
    return present_dependency_critical_path(path)


DependencyCriticalPathDependency = Annotated[
    DependencyCriticalPathResponse,
    Depends(get_dependency_critical_path),
]


def get_review_queue(database: DatabaseDependency) -> ReviewQueueResponse:
    """Build the serialised operator review queue from persisted state."""
    states = review_queue(database)
    return present_review_queue(states)


ReviewQueueDependency = Annotated[ReviewQueueResponse, Depends(get_review_queue)]


def get_system_status(database: DatabaseDependency) -> SystemStatusResponse:
    """Build the serialised singleton operator status snapshot."""
    state = system_status(database)
    return present_system_status(state)


SystemStatusDependency = Annotated[SystemStatusResponse, Depends(get_system_status)]
