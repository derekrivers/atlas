"""FastAPI application construction and database lifecycle."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from atlas.api.routers import tickets
from atlas.storage import Database
from atlas.storage.db import resolve_url
from atlas.storage.preconditions import assert_schema_at_head


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
    application.include_router(tickets.router)
    return application


app = create_app()
