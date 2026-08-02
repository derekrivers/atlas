"""FastAPI application construction and database lifecycle."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from atlas.api.routers import (
    dependencies,
    epics,
    lessons,
    reviews,
    session,
    status,
    tickets,
)
from atlas.api.security import (
    Clock,
    InMemoryOperatorSessionStore,
    LoginAttemptThrottle,
    build_operator_session_service,
    utc_now,
)
from atlas.dependencies import GraphValidationFailed
from atlas.orchestration.operator_security import (
    bind_host_from_env,
    operator_token_from_env,
    writable_routes_enabled,
)
from atlas.storage import Database
from atlas.storage.db import resolve_url
from atlas.storage.preconditions import assert_schema_at_head

API_V1_PREFIX = "/api/v1"
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; object-src 'none'"
)
MUTATION_SECURITY_REQUIREMENT: dict[str, list[str]] = {
    "AtlasSessionCookie": [],
    "AtlasCSRFToken": [],
}


def _install_openapi_contract(application: FastAPI) -> None:
    """Publish mutation authentication as one AND requirement in OpenAPI."""

    generate_openapi = application.openapi

    def openapi() -> dict[str, Any]:
        document = generate_openapi()
        for path_item in cast(dict[str, dict[str, Any]], document["paths"]).values():
            for operation in path_item.values():
                if not isinstance(operation, dict):
                    continue
                security = operation.get("security", [])
                if not isinstance(security, list):
                    continue
                security_names = {
                    name
                    for requirement in security
                    if isinstance(requirement, dict)
                    for name in requirement
                }
                if security_names >= set(MUTATION_SECURITY_REQUIREMENT):
                    operation["security"] = [MUTATION_SECURITY_REQUIREMENT]
        return document

    application.openapi = openapi  # type: ignore[method-assign]


def create_app(
    *,
    database: Database | None = None,
    database_url: str | None = None,
    enable_writes: bool = False,
    operator_token: str | None = None,
    bind_host: str = "127.0.0.1",
    clock: Clock | None = None,
    session_store: InMemoryOperatorSessionStore | None = None,
    login_throttle: LoginAttemptThrottle | None = None,
) -> FastAPI:
    """Build the HTTP adapter with one validated database handle per lifespan."""

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        resolved_database = database or Database(resolve_url(database_url))
        assert_schema_at_head(resolved_database)
        application.state.database = resolved_database
        if enable_writes:
            application.state.operator_session_service = build_operator_session_service(
                operator_token=(
                    operator_token
                    if operator_token is not None
                    else operator_token_from_env()
                ),
                bind_host=bind_host,
                clock=clock or utc_now,
                store=session_store,
                throttle=login_throttle,
            )
        try:
            yield
        finally:
            if database is None:
                resolved_database.engine.dispose()

    application = FastAPI(title="Atlas API", lifespan=lifespan)

    @application.middleware("http")
    async def add_security_headers(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
        if request.url.path.startswith(f"{API_V1_PREFIX}/session") or (
            request.method not in {"GET", "HEAD", "OPTIONS"}
        ):
            response.headers["Cache-Control"] = "no-store"
        return response

    @application.exception_handler(GraphValidationFailed)
    async def graph_validation_failed(
        request: Request, error: GraphValidationFailed
    ) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=409,
            content={
                "detail": "Stored dependency graph is invalid",
                "violations": [
                    {
                        "code": type(violation).__name__,
                        "message": str(violation),
                    }
                    for violation in error.violations
                ],
            },
        )

    application.include_router(tickets.router, prefix=API_V1_PREFIX)
    application.include_router(epics.router, prefix=API_V1_PREFIX)
    application.include_router(lessons.router, prefix=API_V1_PREFIX)
    application.include_router(dependencies.router, prefix=API_V1_PREFIX)
    application.include_router(reviews.router, prefix=API_V1_PREFIX)
    application.include_router(status.router, prefix=API_V1_PREFIX)
    if enable_writes:
        application.include_router(session.router, prefix=API_V1_PREFIX)
        _install_openapi_contract(application)
    return application


app = create_app(
    enable_writes=writable_routes_enabled(),
    bind_host=bind_host_from_env(),
)
