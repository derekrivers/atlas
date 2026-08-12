"""Authenticated exact-head acceptance-session HTTP adapter."""

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from atlas.api.dependencies import (
    ConfirmedAcceptanceSessionDependency,
    CreatedAcceptanceSessionDependency,
    PulledAcceptanceEvidenceDependency,
    ReadAcceptanceSessionDependency,
    VerifiedAcceptanceSessionDependency,
)
from atlas.api.schemas import (
    AcceptanceSessionActionResponse,
    AcceptanceSessionCreationResponse,
    AcceptanceSessionErrorResponse,
    AcceptanceSessionReadResponse,
)

create_router = APIRouter(prefix="/reviews", tags=["acceptance-sessions"])
router = APIRouter(prefix="/acceptance-sessions", tags=["acceptance-sessions"])

_ACCEPTANCE_WRITE_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": AcceptanceSessionErrorResponse},
    403: {"model": AcceptanceSessionErrorResponse},
    404: {"model": AcceptanceSessionErrorResponse},
    409: {"model": AcceptanceSessionErrorResponse},
    415: {"model": AcceptanceSessionErrorResponse},
    422: {"model": AcceptanceSessionErrorResponse},
    500: {"model": AcceptanceSessionErrorResponse},
    502: {"model": AcceptanceSessionErrorResponse},
    503: {"model": AcceptanceSessionErrorResponse},
    504: {"model": AcceptanceSessionErrorResponse},
}

_ACCEPTANCE_READ_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": AcceptanceSessionErrorResponse},
    404: {"model": AcceptanceSessionErrorResponse},
    500: {"model": AcceptanceSessionErrorResponse},
}


@create_router.post(
    "/{pr_number}/acceptance-sessions",
    response_model=AcceptanceSessionCreationResponse,
    responses=_ACCEPTANCE_WRITE_RESPONSES,
)
def create_acceptance_session(
    created: CreatedAcceptanceSessionDependency,
) -> AcceptanceSessionCreationResponse | JSONResponse:
    return created


@router.get(
    "/{session_id}",
    response_model=AcceptanceSessionReadResponse,
    responses=_ACCEPTANCE_READ_RESPONSES,
)
def read_acceptance_session(
    readiness: ReadAcceptanceSessionDependency,
) -> AcceptanceSessionReadResponse | JSONResponse:
    return readiness


@router.post(
    "/{session_id}/evidence",
    response_model=AcceptanceSessionActionResponse,
    responses=_ACCEPTANCE_WRITE_RESPONSES,
)
def pull_acceptance_evidence(
    evidence: PulledAcceptanceEvidenceDependency,
) -> AcceptanceSessionActionResponse | JSONResponse:
    return evidence


@router.post(
    "/{session_id}/confirm",
    response_model=AcceptanceSessionActionResponse,
    responses=_ACCEPTANCE_WRITE_RESPONSES,
)
def confirm_acceptance_session(
    confirmation: ConfirmedAcceptanceSessionDependency,
) -> AcceptanceSessionActionResponse | JSONResponse:
    return confirmation


@router.post(
    "/{session_id}/verify",
    response_model=AcceptanceSessionActionResponse,
    responses=_ACCEPTANCE_WRITE_RESPONSES,
)
def verify_acceptance_session(
    verification: VerifiedAcceptanceSessionDependency,
) -> AcceptanceSessionActionResponse | JSONResponse:
    return verification
