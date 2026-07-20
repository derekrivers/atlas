"""HTTP application wiring and executable route-coverage inventory."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from schema_drift_helpers import drifted_database
from test_apply import _epic_model_kwargs, _ticket_model_kwargs
from test_plan_pipeline import fresh_db

from atlas.api.app import create_app
from atlas.core.models import Epic, Ticket
from atlas.storage import Database, EpicRepo, ProductRepo, TicketRepo
from atlas.storage.preconditions import SchemaDriftError

RouteAssertion = Callable[[Any], None]


def _assert_empty_count(response: Any) -> None:
    assert response.status_code == 200
    assert response.json() == {"count": 0}


# This is executable inventory: every entry is issued by test_every_api_route,
# and test_every_registered_route_has_an_executable_case requires exact parity.
API_ROUTE_CASES: dict[tuple[str, str], RouteAssertion] = {
    ("GET", "/api/tickets/count"): _assert_empty_count,
}


@pytest.fixture
def database(tmp_path: Path) -> Database:
    return fresh_db(tmp_path)


@pytest.fixture
def app(database: Database) -> FastAPI:
    return create_app(database=database)


@pytest.mark.parametrize(
    ("method", "path", "assert_response"),
    [(method, path, check) for (method, path), check in API_ROUTE_CASES.items()],
)
def test_every_api_route(
    app: FastAPI,
    method: str,
    path: str,
    assert_response: RouteAssertion,
) -> None:
    with TestClient(app) as client:
        assert_response(client.request(method, path))


def test_every_registered_route_has_an_executable_case(app: FastAPI) -> None:
    registered = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        for method in operations
    }
    assert registered == set(API_ROUTE_CASES)


def test_ticket_count_reflects_stored_tickets(database: Database) -> None:
    product = ProductRepo(database).get_by_key("ATLAS")
    assert product is not None
    epic = Epic(**_epic_model_kwargs(product.id, key="ATLAS-E1"))
    ticket = Ticket(**_ticket_model_kwargs(product.id, epic.id, key="ATLAS-187"))
    EpicRepo(database).add(epic)
    TicketRepo(database).add(ticket)

    with TestClient(create_app(database=database)) as client:
        response = client.get("/api/tickets/count")

    assert response.status_code == 200
    assert response.json() == {"count": 1}


def test_schema_drift_refuses_application_startup(database: Database) -> None:
    drifted_database(database)

    with (
        pytest.raises(SchemaDriftError),
        TestClient(create_app(database=database)),
    ):
        pass
