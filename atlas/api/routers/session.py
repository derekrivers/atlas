"""Operator browser session routes."""

from fastapi import APIRouter

from atlas.api.dependencies import (
    CreatedOperatorSessionDependency,
    CurrentSessionStateDependency,
    RevokedOperatorSessionDependency,
)
from atlas.api.schemas import SessionLoginResponse, SessionStateResponse

router = APIRouter(tags=["session"])


@router.post("/session", response_model=SessionLoginResponse)
def create_operator_session(
    session: CreatedOperatorSessionDependency,
) -> SessionLoginResponse:
    return session


@router.get("/session", response_model=SessionStateResponse)
def read_operator_session(
    session: CurrentSessionStateDependency,
) -> SessionStateResponse:
    return session


@router.delete(
    "/session",
    response_model=SessionStateResponse,
    openapi_extra={
        "security": [{"AtlasSessionCookie": [], "AtlasCSRFToken": []}],
    },
)
def revoke_operator_session(
    session: RevokedOperatorSessionDependency,
) -> SessionStateResponse:
    return session
