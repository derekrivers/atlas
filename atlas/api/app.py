"""FastAPI application construction and database lifecycle."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from atlas.api.routers import dependencies, epics, lessons, reviews, status, tickets
from atlas.dependencies import GraphValidationFailed
from atlas.storage import Database
from atlas.storage.db import resolve_url
from atlas.storage.preconditions import assert_schema_at_head

API_V1_PREFIX = "/api/v1"


def create_app(
    *,
    database: Database | None = None,
    database_url: str | None = None,
) -> FastAPI:
    """Build the HTTP adapter with one validated database handle per lifespan."""

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        resolved_database = database or Database(resolve_url(database_url))
        assert_schema_at_head(resolved_database)
        application.state.database = resolved_database
        try:
            yield
        finally:
            if database is None:
                resolved_database.engine.dispose()

    application = FastAPI(title="Atlas API", lifespan=lifespan)

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
    return application


app = create_app()
