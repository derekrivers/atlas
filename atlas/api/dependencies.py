"""Dependency providers for API route handlers."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request, Response, Security, status
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyCookie, APIKeyHeader

from atlas.api.presenters import (
    present_dependency_critical_path,
    present_dependency_graph,
    present_epics,
    present_lesson_disposition,
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
    DependencyGraphResponse,
    EpicsResponse,
    LessonDispositionResponse,
    LessonsResponse,
    PromoteLessonRequest,
    RejectLessonRequest,
    ReviewQueueResponse,
    SessionLoginRequest,
    SessionLoginResponse,
    SessionStateResponse,
    SystemStatusResponse,
    TicketBoardResponse,
    TicketDependenciesResponse,
    TicketDetailResponse,
    TicketEvidenceResponse,
)
from atlas.api.security import (
    CSRF_HEADER_NAME,
    SESSION_COOKIE_NAME,
    MutationContext,
    OperatorSessionService,
)
from atlas.core.enums import EntityStatus
from atlas.core.models import TicketStatus
from atlas.learning import PromoteLesson, RejectLesson
from atlas.orchestration import (
    LessonDispositionCommandContext,
    LessonDispositionService,
    dependency_critical_path,
    dependency_graph,
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


def get_operator_session_service(request: Request) -> OperatorSessionService:
    """Return the writable-session service installed during app startup."""
    service: OperatorSessionService = request.app.state.operator_session_service
    return service


OperatorSessionServiceDependency = Annotated[
    OperatorSessionService,
    Depends(get_operator_session_service),
]

SessionCookieSecurity = APIKeyCookie(
    name=SESSION_COOKIE_NAME,
    scheme_name="AtlasSessionCookie",
    auto_error=False,
)
CSRFHeaderSecurity = APIKeyHeader(
    name=CSRF_HEADER_NAME,
    scheme_name="AtlasCSRFToken",
    auto_error=False,
)


def create_operator_session_response(
    request: Request,
    response: Response,
    body: SessionLoginRequest,
    sessions: OperatorSessionServiceDependency,
) -> SessionLoginResponse:
    """Create one short-lived operator session from a strict JSON login body."""
    return sessions.login(request=request, response=response, body=body)


CreatedOperatorSessionDependency = Annotated[
    SessionLoginResponse,
    Depends(create_operator_session_response),
]


def get_current_session_state(
    request: Request,
    sessions: OperatorSessionServiceDependency,
) -> SessionStateResponse:
    """Read current session state without returning credentials or CSRF."""
    return sessions.read_state(request=request)


CurrentSessionStateDependency = Annotated[
    SessionStateResponse,
    Depends(get_current_session_state),
]


def resolve_mutation_context(
    request: Request,
    sessions: OperatorSessionServiceDependency,
    session_id: Annotated[str | None, Security(SessionCookieSecurity)],
    csrf_token: Annotated[str | None, Security(CSRFHeaderSecurity)],
) -> MutationContext:
    """Resolve the immutable server-owned actor for one protected mutation."""
    return sessions.resolve_mutation_context(
        request=request,
        session_id=session_id,
        csrf_token=csrf_token,
    )


MutationContextDependency = Annotated[
    MutationContext,
    Depends(resolve_mutation_context),
]

IdempotencyKeyHeader = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        pattern=r".*\S.*",
    ),
]


def revoke_operator_session_response(
    response: Response,
    context: MutationContextDependency,
    sessions: OperatorSessionServiceDependency,
) -> SessionStateResponse:
    """Revoke the exact live session resolved by the mutation dependency."""
    return sessions.revoke(context=context, response=response)


RevokedOperatorSessionDependency = Annotated[
    SessionStateResponse,
    Depends(revoke_operator_session_response),
]


def get_ticket_repo(database: DatabaseDependency) -> TicketRepo:
    """Build the single-domain ticket service over the shared database."""
    return TicketRepo(database)


TicketRepoDependency = Annotated[TicketRepo, Depends(get_ticket_repo)]


def get_lesson_repo(database: DatabaseDependency) -> LessonRepo:
    """Build the single-domain lesson service over the shared database."""
    return LessonRepo(database)


LessonRepoDependency = Annotated[LessonRepo, Depends(get_lesson_repo)]


def get_lesson_disposition_service(
    database: DatabaseDependency,
) -> LessonDispositionService:
    """Build the shared governed lesson disposition application service."""
    return LessonDispositionService(database)


LessonDispositionServiceDependency = Annotated[
    LessonDispositionService,
    Depends(get_lesson_disposition_service),
]


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


def _lesson_disposition_context(
    context: MutationContext,
    idempotency_key: str,
) -> LessonDispositionCommandContext:
    return LessonDispositionCommandContext(
        created_by_type=context.actor.created_by_type,
        created_by_id=context.actor.created_by_id,
        idempotency_key=idempotency_key,
    )


def promote_lesson_response(
    lesson_id: UUID,
    body: PromoteLessonRequest,
    context: MutationContextDependency,
    idempotency_key: IdempotencyKeyHeader,
    service: LessonDispositionServiceDependency,
) -> LessonDispositionResponse | JSONResponse:
    """Execute one governed promote command and present its typed result."""
    result = service.execute(
        PromoteLesson(lesson_id=lesson_id, confidence=body.confidence),
        _lesson_disposition_context(context, idempotency_key),
    )
    return present_lesson_disposition(result)


PromotedLessonDependency = Annotated[
    LessonDispositionResponse | JSONResponse,
    Depends(promote_lesson_response),
]


def reject_lesson_response(
    lesson_id: UUID,
    body: RejectLessonRequest,
    context: MutationContextDependency,
    idempotency_key: IdempotencyKeyHeader,
    service: LessonDispositionServiceDependency,
) -> LessonDispositionResponse | JSONResponse:
    """Execute one governed reject command and present its typed result."""
    result = service.execute(
        RejectLesson(lesson_id=lesson_id),
        _lesson_disposition_context(context, idempotency_key),
    )
    return present_lesson_disposition(result)


RejectedLessonDependency = Annotated[
    LessonDispositionResponse | JSONResponse,
    Depends(reject_lesson_response),
]


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


def get_dependency_graph(
    database: DatabaseDependency,
) -> DependencyGraphResponse:
    """Build the serialised whole dependency graph projection."""
    graph = dependency_graph(database)
    return present_dependency_graph(graph)


DependencyGraphDependency = Annotated[
    DependencyGraphResponse,
    Depends(get_dependency_graph),
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
