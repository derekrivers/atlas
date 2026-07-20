"""HTTP application wiring and executable route-coverage inventory."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from schema_drift_helpers import drifted_database
from test_apply import _epic_model_kwargs, _ticket_model_kwargs
from test_plan_pipeline import fresh_db

from atlas.api.app import create_app
from atlas.core.enums import EvidenceStatus
from atlas.core.models import Epic, Ticket, TicketStatus, VerificationCheck
from atlas.storage import (
    Database,
    EpicRepo,
    ProductRepo,
    TicketRepo,
    VerificationCheckRepo,
)
from atlas.storage.preconditions import SchemaDriftError
from atlas.verification import required_checks

RouteAssertion = Callable[[Any], None]


def _assert_empty_count(response: Any) -> None:
    assert response.status_code == 200
    assert response.json() == {"count": 0}


def _assert_empty_tickets(response: Any) -> None:
    assert response.status_code == 200
    assert response.json() == {"tickets": []}


def _assert_empty_reviews(response: Any) -> None:
    assert response.status_code == 200
    assert response.json() == {"reviews": []}


# This is executable inventory: every entry is issued by test_every_api_route,
# and test_every_registered_route_has_an_executable_case requires exact parity.
API_ROUTE_CASES: dict[tuple[str, str], RouteAssertion] = {
    ("GET", "/api/tickets"): _assert_empty_tickets,
    ("GET", "/api/tickets/count"): _assert_empty_count,
    ("GET", "/api/reviews"): _assert_empty_reviews,
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


def test_ticket_board_returns_key_ordered_lean_cards(database: Database) -> None:
    product = ProductRepo(database).get_by_key("ATLAS")
    assert product is not None
    epic = Epic(**_epic_model_kwargs(product.id, key="ATLAS-E1"))
    tickets = [
        Ticket(
            **(
                _ticket_model_kwargs(product.id, epic.id, key=key)
                | {
                    "id": uuid4(),
                    "title": title,
                    "status": status,
                    "priority": priority,
                    "risk_level": risk_level,
                }
            )
        )
        for key, title, status, priority, risk_level in [
            (
                "ATLAS-192",
                "Later human decision",
                TicketStatus.NEEDS_HUMAN_DECISION,
                30,
                "high",
            ),
            ("ATLAS-190", "Board endpoint", TicketStatus.PLANNED, 10, "medium"),
            (
                "ATLAS-191",
                "Earlier human decision",
                TicketStatus.NEEDS_HUMAN_DECISION,
                20,
                "critical",
            ),
        ]
    ]
    EpicRepo(database).add(epic)
    ticket_repo = TicketRepo(database)
    for ticket in tickets:
        ticket_repo.add(ticket)

    with TestClient(create_app(database=database)) as client:
        response = client.get("/api/tickets")

    assert response.status_code == 200
    assert response.json() == {
        "tickets": [
            {
                "key": ticket.key,
                "title": ticket.title,
                "status": ticket.status.value,
                "ticket_type": ticket.ticket_type.value,
                "priority": ticket.priority,
                "risk_level": ticket.risk_level.value,
            }
            for ticket in sorted(tickets, key=lambda ticket: ticket.key)
        ]
    }


def test_ticket_board_filters_status_and_preserves_key_order(
    database: Database,
) -> None:
    product = ProductRepo(database).get_by_key("ATLAS")
    assert product is not None
    epic = Epic(**_epic_model_kwargs(product.id, key="ATLAS-E1"))
    tickets = [
        Ticket(
            **(
                _ticket_model_kwargs(product.id, epic.id, key=key)
                | {
                    "id": uuid4(),
                    "title": title,
                    "status": status,
                }
            )
        )
        for key, title, status in [
            (
                "ATLAS-192",
                "Later human decision",
                TicketStatus.NEEDS_HUMAN_DECISION,
            ),
            ("ATLAS-190", "Board endpoint", TicketStatus.PLANNED),
            (
                "ATLAS-191",
                "Earlier human decision",
                TicketStatus.NEEDS_HUMAN_DECISION,
            ),
        ]
    ]
    EpicRepo(database).add(epic)
    ticket_repo = TicketRepo(database)
    for ticket in tickets:
        ticket_repo.add(ticket)

    with TestClient(create_app(database=database)) as client:
        response = client.get("/api/tickets?status=needs_human_decision")

    assert response.status_code == 200
    assert [ticket["key"] for ticket in response.json()["tickets"]] == [
        "ATLAS-191",
        "ATLAS-192",
    ]
    assert all(
        ticket["status"] == "needs_human_decision"
        for ticket in response.json()["tickets"]
    )


def test_ticket_board_rejects_invalid_status(database: Database) -> None:
    with TestClient(create_app(database=database)) as client:
        response = client.get("/api/tickets?status=not_a_status")

    assert response.status_code == 422


def test_review_queue_serialises_persisted_review_state(database: Database) -> None:
    product = ProductRepo(database).get_by_key("ATLAS")
    assert product is not None
    epic = Epic(**_epic_model_kwargs(product.id, key="ATLAS-E1"))
    ticket = Ticket(
        **(
            _ticket_model_kwargs(product.id, epic.id, key="ATLAS-189")
            | {
                "title": "Expose the review queue",
                "status": TicketStatus.REVIEW_REQUIRED,
            }
        )
    )
    EpicRepo(database).add(epic)
    TicketRepo(database).add(ticket)

    checks = [
        VerificationCheck(
            id=uuid4(),
            ticket_id=ticket.id,
            check_type=required.check_type,
            status=EvidenceStatus.PASSED,
            summary=f"{required.check_type.value}: passed",
            required=True,
            created_at=datetime(2026, 7, 20, 12, index, tzinfo=UTC),
        )
        for index, required in enumerate(required_checks(ticket))
        if required.required
    ]
    check_repo = VerificationCheckRepo(database)
    for check in checks:
        check_repo.add(check)

    with TestClient(create_app(database=database)) as client:
        response = client.get("/api/reviews")

    assert response.status_code == 200
    assert response.json() == {
        "reviews": [
            {
                "key": "ATLAS-189",
                "title": "Expose the review queue",
                "status": "review_required",
                "ticket_type": ticket.ticket_type.value,
                "verdict": "passed",
                "checks": [
                    {
                        "check_type": check.check_type.value,
                        "status": "passed",
                    }
                    for check in checks
                ],
                "has_system_evidence": False,
                "has_pr_merged_evidence": False,
            }
        ]
    }


def test_schema_drift_refuses_application_startup(database: Database) -> None:
    drifted_database(database)

    with (
        pytest.raises(SchemaDriftError),
        TestClient(create_app(database=database)),
    ):
        pass
