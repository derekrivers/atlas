"""Dependency providers for API route handlers."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request, Response, Security, status
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyCookie, APIKeyHeader

from atlas.api.acceptance_policy import (
    AcceptanceRepositoryPolicy,
    ConfiguredAcceptanceRepository,
)
from atlas.api.presenters import (
    present_acceptance_confirmation,
    present_acceptance_evidence,
    present_acceptance_session_creation,
    present_acceptance_verification,
    present_delivery_admission_policy_change,
    present_delivery_control,
    present_dependency_critical_path,
    present_dependency_graph,
    present_epics,
    present_lesson_disposition,
    present_lessons,
    present_live_acceptance_readiness,
    present_review_queue,
    present_system_status,
    present_ticket_board,
    present_ticket_dependencies,
    present_ticket_detail,
    present_ticket_evidence,
)
from atlas.api.schemas import (
    AcceptanceConfirmationRequestSchema,
    AcceptanceEvidenceRequest,
    AcceptanceSessionActionResponse,
    AcceptanceSessionCreationResponse,
    AcceptanceSessionReadResponse,
    AcceptanceVerificationRequest,
    CreateAcceptanceSessionRequest,
    DeliveryAdmissionPolicyRequest,
    DeliveryAdmissionPolicyResponse,
    DeliveryControlResponse,
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
    AuthenticatedSessionContext,
    MutationContext,
    OperatorSessionService,
    utc_now,
)
from atlas.core.enums import EntityStatus
from atlas.core.models import TicketStatus
from atlas.github import GitHubClient, GitHubRESTClient
from atlas.learning import PromoteLesson, RejectLesson
from atlas.orchestration import (
    AcceptanceConfirmationRequest,
    AcceptanceEvidencePullContext,
    AcceptanceSessionConfirmationService,
    AcceptanceSessionCreationService,
    AcceptanceSessionEvidencePullService,
    AcceptanceSessionLiveReadinessService,
    AcceptanceSessionVerificationService,
    AcceptanceSessionWorkflowServices,
    AcceptanceVerificationContext,
    DeliveryAdmissionPolicyService,
    LessonDispositionCommandContext,
    LessonDispositionService,
    build_acceptance_session_workflow,
    delivery_control_status,
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


def resolve_authenticated_session(
    sessions: OperatorSessionServiceDependency,
    session_id: Annotated[str | None, Security(SessionCookieSecurity)],
) -> AuthenticatedSessionContext:
    """Require the shared live session for one protected observational read."""

    return sessions.resolve_authenticated_context(session_id=session_id)


AuthenticatedSessionDependency = Annotated[
    AuthenticatedSessionContext,
    Depends(resolve_authenticated_session),
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


def get_acceptance_repository_policy(request: Request) -> AcceptanceRepositoryPolicy:
    """Return the immutable server-owned repository allowlist."""

    policy: AcceptanceRepositoryPolicy = request.app.state.acceptance_repository_policy
    return policy


AcceptanceRepositoryPolicyDependency = Annotated[
    AcceptanceRepositoryPolicy,
    Depends(get_acceptance_repository_policy),
]


def resolve_configured_acceptance_repository(
    body: CreateAcceptanceSessionRequest,
    policy: AcceptanceRepositoryPolicyDependency,
) -> ConfiguredAcceptanceRepository:
    """Resolve a parsed owner/name pair before any external service call."""

    return policy.require(body.repository)


ConfiguredAcceptanceRepositoryDependency = Annotated[
    ConfiguredAcceptanceRepository,
    Depends(resolve_configured_acceptance_repository),
]


def get_acceptance_github_client(request: Request) -> GitHubClient:
    """Return an injected client or one server-token client with finite I/O timeout."""

    client: GitHubClient | None = request.app.state.acceptance_github_client
    if client is None:
        client = GitHubRESTClient(
            timeout_seconds=request.app.state.acceptance_external_timeout_seconds
        )
        request.app.state.acceptance_github_client = client
    return client


AcceptanceGitHubClientDependency = Annotated[
    GitHubClient,
    Depends(get_acceptance_github_client),
]


def get_acceptance_clock(request: Request) -> Callable[[], datetime]:
    """Return the application clock shared with authenticated session services."""

    clock: Callable[[], datetime] = request.app.state.clock
    return clock


AcceptanceClockDependency = Annotated[
    Callable[[], datetime],
    Depends(get_acceptance_clock),
]


def get_acceptance_workflow_services(
    database: DatabaseDependency,
    github_client: AcceptanceGitHubClientDependency,
    clock: AcceptanceClockDependency,
) -> AcceptanceSessionWorkflowServices:
    """Build the Phase 14 application-service bundle for one API request."""

    return build_acceptance_session_workflow(database, github_client, clock)


AcceptanceWorkflowServicesDependency = Annotated[
    AcceptanceSessionWorkflowServices,
    Depends(get_acceptance_workflow_services),
]


def get_acceptance_session_creation_service(
    services: AcceptanceWorkflowServicesDependency,
) -> AcceptanceSessionCreationService:
    return services.creation


AcceptanceSessionCreationServiceDependency = Annotated[
    AcceptanceSessionCreationService,
    Depends(get_acceptance_session_creation_service),
]


def get_acceptance_session_readiness_service(
    services: AcceptanceWorkflowServicesDependency,
) -> AcceptanceSessionLiveReadinessService:
    return services.live_readiness


AcceptanceSessionReadinessServiceDependency = Annotated[
    AcceptanceSessionLiveReadinessService,
    Depends(get_acceptance_session_readiness_service),
]


def get_acceptance_session_evidence_service(
    services: AcceptanceWorkflowServicesDependency,
) -> AcceptanceSessionEvidencePullService:
    return services.evidence


AcceptanceSessionEvidenceServiceDependency = Annotated[
    AcceptanceSessionEvidencePullService,
    Depends(get_acceptance_session_evidence_service),
]


def get_acceptance_session_confirmation_service(
    services: AcceptanceWorkflowServicesDependency,
) -> AcceptanceSessionConfirmationService:
    return services.confirmation


AcceptanceSessionConfirmationServiceDependency = Annotated[
    AcceptanceSessionConfirmationService,
    Depends(get_acceptance_session_confirmation_service),
]


def get_acceptance_session_verification_service(
    services: AcceptanceWorkflowServicesDependency,
) -> AcceptanceSessionVerificationService:
    return services.verification


AcceptanceSessionVerificationServiceDependency = Annotated[
    AcceptanceSessionVerificationService,
    Depends(get_acceptance_session_verification_service),
]


def _acceptance_evidence_context(
    context: MutationContext,
    idempotency_key: str,
) -> AcceptanceEvidencePullContext:
    return AcceptanceEvidencePullContext(
        idempotency_key=idempotency_key,
        created_by_type=context.actor.created_by_type,
        created_by_id=context.actor.created_by_id,
    )


def _acceptance_confirmation_request(
    session_id: UUID,
    body: AcceptanceConfirmationRequestSchema,
) -> AcceptanceConfirmationRequest:
    return AcceptanceConfirmationRequest(
        session_id=session_id,
        criteria_fingerprint=body.criteria_fingerprint,
        criterion_indexes=body.criterion_indexes,
        manual_approval=body.manual_approval,
    )


def _acceptance_verification_context(
    context: MutationContext,
    idempotency_key: str,
) -> AcceptanceVerificationContext:
    return AcceptanceVerificationContext(
        idempotency_key=idempotency_key,
        created_by_type=context.actor.created_by_type,
        created_by_id=context.actor.created_by_id,
    )


def create_acceptance_session_response(
    pr_number: int,
    repository: ConfiguredAcceptanceRepositoryDependency,
    context: MutationContextDependency,
    idempotency_key: IdempotencyKeyHeader,
    service: AcceptanceSessionCreationServiceDependency,
) -> AcceptanceSessionCreationResponse | JSONResponse:
    """Create one configured-repository session and present its typed result."""

    result = service.create(
        repository_owner=repository.owner,
        repository_name=repository.name,
        pr_number=pr_number,
        idempotency_key=idempotency_key,
        created_by_type=context.actor.created_by_type,
        created_by_id=context.actor.created_by_id,
    )
    return present_acceptance_session_creation(result)


CreatedAcceptanceSessionDependency = Annotated[
    AcceptanceSessionCreationResponse | JSONResponse,
    Depends(create_acceptance_session_response),
]


def read_acceptance_session_response(
    session_id: UUID,
    authenticated: AuthenticatedSessionDependency,
    service: AcceptanceSessionReadinessServiceDependency,
) -> AcceptanceSessionReadResponse | JSONResponse:
    """Evaluate current live readiness once and present without a refresh write."""

    del authenticated
    result = service.evaluate(session_id)
    return present_live_acceptance_readiness(result)


ReadAcceptanceSessionDependency = Annotated[
    AcceptanceSessionReadResponse | JSONResponse,
    Depends(read_acceptance_session_response),
]


def pull_acceptance_evidence_response(
    session_id: UUID,
    body: AcceptanceEvidenceRequest,
    context: MutationContextDependency,
    idempotency_key: IdempotencyKeyHeader,
    service: AcceptanceSessionEvidenceServiceDependency,
) -> AcceptanceSessionActionResponse | JSONResponse:
    """Execute one synchronous evidence action and present its typed result."""

    del body
    result = service.execute(
        session_id,
        _acceptance_evidence_context(context, idempotency_key),
    )
    return present_acceptance_evidence(result)


PulledAcceptanceEvidenceDependency = Annotated[
    AcceptanceSessionActionResponse | JSONResponse,
    Depends(pull_acceptance_evidence_response),
]


def confirm_acceptance_session_response(
    session_id: UUID,
    body: AcceptanceConfirmationRequestSchema,
    context: MutationContextDependency,
    idempotency_key: IdempotencyKeyHeader,
    service: AcceptanceSessionConfirmationServiceDependency,
) -> AcceptanceSessionActionResponse | JSONResponse:
    """Execute one strict confirmation and present its typed result."""

    del context
    result = service.confirm(
        _acceptance_confirmation_request(session_id, body),
        idempotency_key=idempotency_key,
    )
    return present_acceptance_confirmation(result)


ConfirmedAcceptanceSessionDependency = Annotated[
    AcceptanceSessionActionResponse | JSONResponse,
    Depends(confirm_acceptance_session_response),
]


def verify_acceptance_session_response(
    session_id: UUID,
    body: AcceptanceVerificationRequest,
    context: MutationContextDependency,
    idempotency_key: IdempotencyKeyHeader,
    service: AcceptanceSessionVerificationServiceDependency,
) -> AcceptanceSessionActionResponse | JSONResponse:
    """Execute one synchronous exact-head verification and present its result."""

    del body
    result = service.execute(
        session_id,
        _acceptance_verification_context(context, idempotency_key),
    )
    return present_acceptance_verification(result)


VerifiedAcceptanceSessionDependency = Annotated[
    AcceptanceSessionActionResponse | JSONResponse,
    Depends(verify_acceptance_session_response),
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


def get_delivery_admission_policy_service(
    database: DatabaseDependency,
) -> DeliveryAdmissionPolicyService:
    """Build the governed delivery-policy service over the shared database."""

    return DeliveryAdmissionPolicyService(database, clock=utc_now)


DeliveryAdmissionPolicyServiceDependency = Annotated[
    DeliveryAdmissionPolicyService,
    Depends(get_delivery_admission_policy_service),
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


def get_delivery_control(
    database: DatabaseDependency,
    authenticated: AuthenticatedSessionDependency,
) -> DeliveryControlResponse | JSONResponse:
    """Read and present delivery control through one coordinating operation."""

    del authenticated
    state = delivery_control_status(database)
    return present_delivery_control(state)


DeliveryControlDependency = Annotated[
    DeliveryControlResponse | JSONResponse,
    Depends(get_delivery_control),
]


def replace_delivery_admission_policy(
    body: DeliveryAdmissionPolicyRequest,
    context: MutationContextDependency,
    idempotency_key: IdempotencyKeyHeader,
    service: DeliveryAdmissionPolicyServiceDependency,
) -> DeliveryAdmissionPolicyResponse | JSONResponse:
    """Execute one authenticated complete-policy compare-and-set command."""

    del context
    result = service.revise_current(
        expected_revision=body.expected_revision,
        idempotency_key=idempotency_key,
        policy=body.policy_spec(),
    )
    return present_delivery_admission_policy_change(result)


ReplacedDeliveryAdmissionPolicyDependency = Annotated[
    DeliveryAdmissionPolicyResponse | JSONResponse,
    Depends(replace_delivery_admission_policy),
]
