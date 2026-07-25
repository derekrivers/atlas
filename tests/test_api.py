"""HTTP application wiring and executable route-coverage inventory."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from schema_drift_helpers import (
    alembic_head_and_parent,
    drifted_database,
    stamp_database,
)
from test_apply import _epic_model_kwargs, _ticket_model_kwargs
from test_lesson_model import lesson_kwargs
from test_models_validation import dependency_kwargs
from test_plan_pipeline import fresh_db

from atlas import __version__
from atlas.api.app import create_app
from atlas.api.schemas import (
    CriticalPathStepSchema,
    DependencyBlockerSchema,
    DependencyCriticalPathResponse,
    LessonItemSchema,
    LessonsResponse,
    NotReadyReasonSchema,
    ReviewCheckSchema,
    ReviewQueueItemSchema,
    SystemStatusResponse,
    TicketBoardItemSchema,
    TicketDetailResponse,
    TicketEvidenceItemSchema,
    TicketReadinessSchema,
)
from atlas.core.enums import ActorType, EntityStatus, EvidenceStatus, RiskLevel
from atlas.core.models import (
    Epic,
    Evidence,
    EvidenceType,
    Lesson,
    LessonCategory,
    Ticket,
    TicketDependency,
    TicketStatus,
    TicketType,
    VerificationCheck,
    VerificationCheckType,
)
from atlas.dependencies import NotReadyCode
from atlas.storage import (
    Database,
    EpicRepo,
    EvidenceRepo,
    LessonRepo,
    ProductRepo,
    TicketDependencyRepo,
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


def _assert_empty_lessons(response: Any) -> None:
    assert response.status_code == 200
    assert response.json() == {"lessons": []}


def _assert_missing_ticket(response: Any) -> None:
    assert response.status_code == 404
    assert response.json() == {"detail": "Ticket ATLAS-MISSING not found"}


def _assert_missing_ticket_evidence(response: Any) -> None:
    assert response.status_code == 404
    assert response.json() == {"detail": "Ticket ATLAS-MISSING not found"}


def _assert_missing_ticket_dependencies(response: Any) -> None:
    assert response.status_code == 404
    assert response.json() == {"detail": "Ticket ATLAS-MISSING not found"}


def _assert_empty_critical_path(response: Any) -> None:
    assert response.status_code == 200
    assert response.json() == {"keys": [], "steps": [], "total_effort": 0}


def _assert_empty_status(response: Any) -> None:
    assert response.status_code == 200
    assert response.json() == {
        "package_version": __version__,
        "schema_revision": None,
        "ticket_count": 0,
        "evidence_count": 0,
        "last_linear_sync_at": None,
        "last_evidence_pull_at": None,
    }


# This is executable inventory: every entry is issued by test_every_api_route,
# and test_every_registered_route_has_an_executable_case requires exact parity.
API_ROUTE_CASES: dict[tuple[str, str], RouteAssertion] = {
    ("GET", "/api/v1/tickets"): _assert_empty_tickets,
    ("GET", "/api/v1/tickets/count"): _assert_empty_count,
    ("GET", "/api/v1/tickets/{key}"): _assert_missing_ticket,
    ("GET", "/api/v1/tickets/{key}/evidence"): _assert_missing_ticket_evidence,
    (
        "GET",
        "/api/v1/tickets/{key}/dependencies",
    ): _assert_missing_ticket_dependencies,
    ("GET", "/api/v1/lessons"): _assert_empty_lessons,
    ("GET", "/api/v1/dependencies/critical-path"): _assert_empty_critical_path,
    ("GET", "/api/v1/reviews"): _assert_empty_reviews,
    ("GET", "/api/v1/status"): _assert_empty_status,
}

FORMER_UNVERSIONED_PATHS = (
    "/api/tickets",
    "/api/tickets/count",
    "/api/reviews",
)


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
    request_path = path.replace("{key}", "ATLAS-MISSING")
    with TestClient(app) as client:
        assert_response(client.request(method, request_path))


def test_every_registered_route_has_an_executable_case(app: FastAPI) -> None:
    registered = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        for method in operations
    }
    assert registered == set(API_ROUTE_CASES)


def test_lessons_api_registers_only_read_route(app: FastAPI) -> None:
    lesson_operations = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        if path.startswith("/api/v1/lessons")
        for method in operations
    }
    assert lesson_operations == {("GET", "/api/v1/lessons")}


def test_former_unversioned_api_paths_return_404(app: FastAPI) -> None:
    with TestClient(app) as client:
        for path in FORMER_UNVERSIONED_PATHS:
            response = client.get(path)
            assert response.status_code == 404, path


def test_response_schema_closed_fields_use_canonical_enums() -> None:
    assert TicketBoardItemSchema.model_fields["status"].annotation is TicketStatus
    assert TicketBoardItemSchema.model_fields["ticket_type"].annotation is TicketType
    assert TicketBoardItemSchema.model_fields["risk_level"].annotation is RiskLevel
    assert TicketDetailResponse.model_fields["status"].annotation is TicketStatus
    assert TicketDetailResponse.model_fields["ticket_type"].annotation is TicketType
    assert TicketDetailResponse.model_fields["risk_level"].annotation is RiskLevel
    assert TicketEvidenceItemSchema.model_fields["type"].annotation is EvidenceType
    assert TicketEvidenceItemSchema.model_fields["trust_level"].annotation is ActorType
    assert TicketEvidenceItemSchema.model_fields["trust_level"].alias == "tier"
    assert TicketEvidenceItemSchema.model_fields["status"].annotation is EvidenceStatus
    assert DependencyBlockerSchema.model_fields["code"].annotation is NotReadyCode
    assert NotReadyReasonSchema.model_fields["code"].annotation is NotReadyCode
    assert LessonItemSchema.model_fields["status"].annotation is EntityStatus
    assert LessonItemSchema.model_fields["category"].annotation is LessonCategory
    assert LessonItemSchema.model_fields["created_by_type"].annotation is ActorType
    assert ReviewQueueItemSchema.model_fields["status"].annotation is TicketStatus
    assert ReviewQueueItemSchema.model_fields["ticket_type"].annotation is TicketType
    assert ReviewQueueItemSchema.model_fields["verdict"].annotation is EvidenceStatus
    assert (
        ReviewCheckSchema.model_fields["check_type"].annotation is VerificationCheckType
    )
    assert ReviewCheckSchema.model_fields["status"].annotation is EvidenceStatus


def _component_for_field(
    openapi: dict[str, Any],
    schema: str,
    field: str,
) -> dict[str, Any]:
    field_schema = openapi["components"]["schemas"][schema]["properties"][field]
    ref = field_schema["$ref"]
    _, component_name = ref.rsplit("/", maxsplit=1)
    return cast(dict[str, Any], openapi["components"]["schemas"][component_name])


@pytest.mark.parametrize(
    ("schema", "field", "enum_cls"),
    [
        ("TicketBoardItemSchema", "status", TicketStatus),
        ("TicketBoardItemSchema", "ticket_type", TicketType),
        ("TicketBoardItemSchema", "risk_level", RiskLevel),
        ("TicketDetailResponse", "status", TicketStatus),
        ("TicketDetailResponse", "ticket_type", TicketType),
        ("TicketDetailResponse", "risk_level", RiskLevel),
        ("TicketEvidenceItemSchema", "type", EvidenceType),
        ("TicketEvidenceItemSchema", "tier", ActorType),
        ("TicketEvidenceItemSchema", "status", EvidenceStatus),
        ("DependencyBlockerSchema", "code", NotReadyCode),
        ("NotReadyReasonSchema", "code", NotReadyCode),
        ("LessonItemSchema", "status", EntityStatus),
        ("LessonItemSchema", "category", LessonCategory),
        ("LessonItemSchema", "created_by_type", ActorType),
        ("ReviewQueueItemSchema", "status", TicketStatus),
        ("ReviewQueueItemSchema", "ticket_type", TicketType),
        ("ReviewQueueItemSchema", "verdict", EvidenceStatus),
        ("ReviewCheckSchema", "check_type", VerificationCheckType),
        ("ReviewCheckSchema", "status", EvidenceStatus),
    ],
)
def test_openapi_publishes_response_enum_members(
    app: FastAPI,
    schema: str,
    field: str,
    enum_cls: type[Any],
) -> None:
    field_component = _component_for_field(app.openapi(), schema, field)

    assert field_component["type"] == "string"
    assert field_component["enum"] == [member.value for member in enum_cls]


def test_ticket_count_reflects_stored_tickets(database: Database) -> None:
    product = ProductRepo(database).get_by_key("ATLAS")
    assert product is not None
    epic = Epic(**_epic_model_kwargs(product.id, key="ATLAS-E1"))
    ticket = Ticket(**_ticket_model_kwargs(product.id, epic.id, key="ATLAS-187"))
    EpicRepo(database).add(epic)
    TicketRepo(database).add(ticket)

    with TestClient(create_app(database=database)) as client:
        response = client.get("/api/v1/tickets/count")

    assert response.status_code == 200
    assert response.json() == {"count": 1}


def test_status_returns_operator_system_snapshot(database: Database) -> None:
    head, _parent = alembic_head_and_parent()
    stamp_database(database, head)
    product = ProductRepo(database).get_by_key("ATLAS")
    assert product is not None
    epic = Epic(**_epic_model_kwargs(product.id, key="ATLAS-E1"))
    older_sync = datetime(2026, 7, 24, 16, tzinfo=UTC)
    latest_sync = datetime(2026, 7, 25, 10, tzinfo=UTC)
    older_evidence_pull = datetime(2026, 7, 25, 8, tzinfo=UTC)
    latest_evidence_pull = datetime(2026, 7, 25, 11, tzinfo=UTC)
    tickets = [
        Ticket(
            **(
                _ticket_model_kwargs(product.id, epic.id, key="ATLAS-202")
                | {
                    "id": uuid4(),
                    "linear_synced_at": older_sync,
                }
            )
        ),
        Ticket(
            **(
                _ticket_model_kwargs(product.id, epic.id, key="ATLAS-203")
                | {
                    "id": uuid4(),
                    "linear_synced_at": latest_sync,
                }
            )
        ),
        Ticket(
            **(
                _ticket_model_kwargs(product.id, epic.id, key="ATLAS-204")
                | {
                    "id": uuid4(),
                    "linear_synced_at": None,
                }
            )
        ),
    ]
    EpicRepo(database).add(epic)
    ticket_repo = TicketRepo(database)
    for ticket in tickets:
        ticket_repo.add(ticket)
    evidence_repo = EvidenceRepo(database)
    evidence_repo.add(
        _evidence(
            tickets[0],
            created_by_type=ActorType.SYSTEM,
            created_at=older_evidence_pull,
        )
    )
    evidence_repo.add(
        _evidence(
            tickets[1],
            created_by_type=ActorType.SYSTEM,
            created_at=latest_evidence_pull,
        )
    )

    with TestClient(create_app(database=database)) as client:
        response = client.get("/api/v1/status")

    assert response.status_code == 200
    assert response.json() == SystemStatusResponse(
        package_version=__version__,
        schema_revision=head,
        ticket_count=3,
        evidence_count=2,
        last_linear_sync_at=latest_sync,
        last_evidence_pull_at=latest_evidence_pull,
    ).model_dump(mode="json")


def test_status_response_excludes_environment_secret_values(
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hidden_values = {
        "LINEAR_API_KEY": "linear-token-secret-value",
        "GITHUB_TOKEN": "github-token-secret-value",
        "ATLAS_DATABASE_URL": "sqlite:////tmp/atlas-secret-status-store.db",
        "ATLAS_CREDENTIAL_FILE": "/tmp/atlas-secret-credential-file",
    }
    for key, value in hidden_values.items():
        monkeypatch.setenv(key, value)

    with TestClient(create_app(database=database)) as client:
        response = client.get("/api/v1/status")

    assert response.status_code == 200
    for value in hidden_values.values():
        assert value not in response.text


def test_status_route_performs_no_database_writes(database: Database) -> None:
    statements: list[str] = []
    write_verbs = {
        "ALTER",
        "CREATE",
        "DELETE",
        "DROP",
        "INSERT",
        "REPLACE",
        "TRUNCATE",
        "UPDATE",
    }

    def capture_write(
        conn: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        del conn, cursor, parameters, context, executemany
        first_token = statement.lstrip().split(maxsplit=1)[0].upper()
        if first_token in write_verbs:
            statements.append(statement)

    sa.event.listen(database.engine, "before_cursor_execute", capture_write)
    try:
        with TestClient(create_app(database=database)) as client:
            response = client.get("/api/v1/status")
    finally:
        sa.event.remove(database.engine, "before_cursor_execute", capture_write)

    assert response.status_code == 200
    assert statements == []


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
        response = client.get("/api/v1/tickets")

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
        response = client.get("/api/v1/tickets?status=needs_human_decision")

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
        response = client.get("/api/v1/tickets?status=not_a_status")

    assert response.status_code == 422


def _lesson(
    product_id: UUID,
    *,
    lesson_id: UUID,
    title: str,
    status: EntityStatus = EntityStatus.DRAFT,
    category: LessonCategory = LessonCategory.DELIVERY,
    created_at: datetime = datetime(2026, 7, 25, 10, tzinfo=UTC),
    updated_at: datetime = datetime(2026, 7, 25, 10, tzinfo=UTC),
    **overrides: Any,
) -> Lesson:
    return Lesson(
        **lesson_kwargs()
        | {
            "id": lesson_id,
            "product_id": product_id,
            "status": status,
            "category": category,
            "title": title,
            "created_at": created_at,
            "updated_at": updated_at,
        }
        | overrides
    )


def _lesson_item_json(lesson: Lesson) -> dict[str, Any]:
    return LessonItemSchema(
        id=lesson.id,
        product_id=lesson.product_id,
        status=lesson.status,
        category=lesson.category,
        title=lesson.title,
        problem=lesson.problem,
        solution=lesson.solution,
        outcome=lesson.outcome,
        confidence=lesson.confidence,
        source_ticket_id=lesson.source_ticket_id,
        related_ticket_ids=lesson.related_ticket_ids,
        related_adr_ids=lesson.related_adr_ids,
        tags=lesson.tags,
        created_by_type=lesson.created_by_type,
        created_by_id=lesson.created_by_id,
        created_at=lesson.created_at,
        updated_at=lesson.updated_at,
    ).model_dump(mode="json")


def test_lessons_returns_stored_lesson_projection(database: Database) -> None:
    product = ProductRepo(database).get_by_key("ATLAS")
    assert product is not None
    lessons = [
        _lesson(
            product.id,
            lesson_id=UUID("00000000-0000-0000-0000-000000000002"),
            title="Later stored lesson",
            status=EntityStatus.ACTIVE,
            category=LessonCategory.TESTING,
            confidence=0.8,
            source_ticket_id=uuid4(),
            related_ticket_ids=[uuid4()],
            related_adr_ids=[uuid4()],
            tags=["api", "lessons"],
            created_by_type=ActorType.SYSTEM,
            created_by_id="atlas",
        ),
        _lesson(
            product.id,
            lesson_id=UUID("00000000-0000-0000-0000-000000000001"),
            title="Earlier stored lesson",
            confidence=None,
            source_ticket_id=uuid4(),
            related_ticket_ids=[],
            related_adr_ids=[],
            tags=[],
        ),
    ]
    lesson_repo = LessonRepo(database)
    for lesson in lessons:
        lesson_repo.add(lesson)

    with TestClient(create_app(database=database)) as client:
        response = client.get("/api/v1/lessons")

    assert response.status_code == 200
    assert response.json() == LessonsResponse(
        lessons=[
            LessonItemSchema(**_lesson_item_json(lesson))
            for lesson in sorted(lessons, key=lambda record: record.id)
        ]
    ).model_dump(mode="json")


def test_lessons_filters_status_and_preserves_repository_order(
    database: Database,
) -> None:
    product = ProductRepo(database).get_by_key("ATLAS")
    assert product is not None
    older_active = _lesson(
        product.id,
        lesson_id=UUID("00000000-0000-0000-0000-000000000010"),
        title="Older active lesson",
        status=EntityStatus.ACTIVE,
        created_at=datetime(2026, 7, 25, 9, tzinfo=UTC),
    )
    draft = _lesson(
        product.id,
        lesson_id=UUID("00000000-0000-0000-0000-000000000011"),
        title="Draft lesson",
        status=EntityStatus.DRAFT,
        confidence=None,
    )
    newer_active = _lesson(
        product.id,
        lesson_id=UUID("00000000-0000-0000-0000-000000000012"),
        title="Newer active lesson",
        status=EntityStatus.ACTIVE,
        created_at=datetime(2026, 7, 25, 11, tzinfo=UTC),
    )
    lesson_repo = LessonRepo(database)
    for lesson in (newer_active, draft, older_active):
        lesson_repo.add(lesson)

    with TestClient(create_app(database=database)) as client:
        response = client.get("/api/v1/lessons?status=active")

    assert response.status_code == 200
    assert response.json() == {
        "lessons": [_lesson_item_json(older_active), _lesson_item_json(newer_active)]
    }


def test_lessons_rejects_invalid_status(database: Database) -> None:
    with TestClient(create_app(database=database)) as client:
        response = client.get("/api/v1/lessons?status=not_a_status")

    assert response.status_code == 422


def test_lessons_route_performs_no_lesson_state_writes(database: Database) -> None:
    product = ProductRepo(database).get_by_key("ATLAS")
    assert product is not None
    lesson = _lesson(
        product.id,
        lesson_id=UUID("00000000-0000-0000-0000-000000000020"),
        title="Read-only lesson",
        confidence=None,
    )
    lesson_repo = LessonRepo(database)
    lesson_repo.add(lesson)
    before = lesson_repo.get(lesson.id)

    with TestClient(create_app(database=database)) as client:
        response = client.get("/api/v1/lessons?status=draft")

    assert response.status_code == 200
    after = lesson_repo.get(lesson.id)
    assert after == before


def test_ticket_detail_returns_operator_facing_stored_state(
    database: Database,
) -> None:
    product = ProductRepo(database).get_by_key("ATLAS")
    assert product is not None
    epic = Epic(**_epic_model_kwargs(product.id, key="ATLAS-E1"))
    ticket = Ticket(
        **(
            _ticket_model_kwargs(product.id, epic.id, key="ATLAS-037M")
            | {
                "title": "Expose ticket detail",
                "objective": "Give the operator one complete ticket definition.",
                "context": "The board card is intentionally lean.",
                "status": TicketStatus.IN_PROGRESS,
                "ticket_type": TicketType.FEATURE,
                "risk_level": RiskLevel.MEDIUM,
                "priority": 37,
                "estimated_effort": 5,
                "relevant_docs": ["docs/atlas/operator-api.md"],
                "acceptance_criteria": ["Known keys return detail."],
                "non_goals": ["Evidence projection."],
                "implementation_notes": ["Keep the read single-source."],
                "test_requirements": ["Exercise the HTTP route."],
                "documentation_requirements": ["Record the 404 convention."],
                "definition_of_done": ["The full gate sweep passes."],
                "tags": ["api", "projection"],
                "component": "operator-api",
                "external_linear_id": "linear-037m",
                "external_github_issue_id": "237",
                "source_anchor": "operator-api#the-v1-contract",
            }
        )
    )
    EpicRepo(database).add(epic)
    TicketRepo(database).add(ticket)

    with TestClient(create_app(database=database)) as client:
        response = client.get("/api/v1/tickets/ATLAS-037M")

    assert response.status_code == 200
    assert response.json() == TicketDetailResponse(
        key=ticket.key,
        title=ticket.title,
        objective=ticket.objective,
        context=ticket.context,
        status=ticket.status,
        ticket_type=ticket.ticket_type,
        risk_level=ticket.risk_level,
        priority=ticket.priority,
        estimated_effort=ticket.estimated_effort,
        relevant_docs=ticket.relevant_docs,
        acceptance_criteria=ticket.acceptance_criteria,
        non_goals=ticket.non_goals,
        implementation_notes=ticket.implementation_notes,
        test_requirements=ticket.test_requirements,
        documentation_requirements=ticket.documentation_requirements,
        definition_of_done=ticket.definition_of_done,
        tags=ticket.tags,
        component=ticket.component,
        external_linear_id=ticket.external_linear_id,
        external_github_issue_id=ticket.external_github_issue_id,
        source_anchor=ticket.source_anchor,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
        completed_at=ticket.completed_at,
    ).model_dump(mode="json")


def test_ticket_detail_returns_native_404_for_unknown_key(
    database: Database,
) -> None:
    with TestClient(create_app(database=database)) as client:
        response = client.get("/api/v1/tickets/ATLAS-404")

    assert response.status_code == 404
    assert response.json() == {"detail": "Ticket ATLAS-404 not found"}


def _evidence(
    ticket: Ticket,
    *,
    created_by_type: ActorType,
    evidence_type: EvidenceType = EvidenceType.TEST_RESULT,
    status: EvidenceStatus = EvidenceStatus.PASSED,
    raw_payload: dict[str, Any] | None = None,
    created_at: datetime | None = None,
    offset: int = 0,
) -> Evidence:
    is_system = created_by_type is ActorType.SYSTEM
    return Evidence(
        id=uuid4(),
        product_id=ticket.product_id,
        ticket_id=ticket.id,
        evidence_type=evidence_type,
        status=status,
        summary=evidence_type.value,
        commit_sha="abc123" if is_system else None,
        external_run_id="run-123" if is_system else None,
        payload_hash="hash-123" if is_system else None,
        raw_payload=raw_payload or {},
        created_by_type=created_by_type,
        created_by_id="api-test",
        created_at=created_at or datetime(2026, 7, 25, 9, offset, tzinfo=UTC),
    )


def test_ticket_evidence_returns_type_tier_status_and_pin_completeness(
    database: Database,
) -> None:
    product = ProductRepo(database).get_by_key("ATLAS")
    assert product is not None
    epic = Epic(**_epic_model_kwargs(product.id, key="ATLAS-E1"))
    ticket = Ticket(**_ticket_model_kwargs(product.id, epic.id, key="ATLAS-200"))
    EpicRepo(database).add(epic)
    TicketRepo(database).add(ticket)
    EvidenceRepo(database).add(
        _evidence(
            ticket,
            created_by_type=ActorType.SYSTEM,
            evidence_type=EvidenceType.TEST_RESULT,
            raw_payload={"secret": "do-not-expose"},
        )
    )
    EvidenceRepo(database).add(
        _evidence(
            ticket,
            created_by_type=ActorType.HUMAN,
            evidence_type=EvidenceType.MANUAL_APPROVAL,
            offset=1,
        )
    )

    with TestClient(create_app(database=database)) as client:
        response = client.get("/api/v1/tickets/ATLAS-200/evidence")

    assert response.status_code == 200
    assert response.json() == {
        "evidence": [
            {
                "type": "test_result",
                "tier": "system",
                "status": "passed",
                "has_system_pin_triple": True,
            },
            {
                "type": "manual_approval",
                "tier": "human",
                "status": "passed",
                "has_system_pin_triple": False,
            },
        ]
    }
    assert "raw_payload" not in response.text
    assert "do-not-expose" not in response.text


def test_ticket_evidence_returns_empty_collection_for_known_ticket_without_evidence(
    database: Database,
) -> None:
    product = ProductRepo(database).get_by_key("ATLAS")
    assert product is not None
    epic = Epic(**_epic_model_kwargs(product.id, key="ATLAS-E1"))
    ticket = Ticket(**_ticket_model_kwargs(product.id, epic.id, key="ATLAS-201"))
    EpicRepo(database).add(epic)
    TicketRepo(database).add(ticket)

    with TestClient(create_app(database=database)) as client:
        response = client.get("/api/v1/tickets/ATLAS-201/evidence")

    assert response.status_code == 200
    assert response.json() == {"evidence": []}


def test_ticket_evidence_returns_native_404_for_unknown_key(
    database: Database,
) -> None:
    with TestClient(create_app(database=database)) as client:
        response = client.get("/api/v1/tickets/ATLAS-404/evidence")

    assert response.status_code == 404
    assert response.json() == {"detail": "Ticket ATLAS-404 not found"}


def _dependency(source: Ticket, target: Ticket) -> TicketDependency:
    return TicketDependency(
        **(
            dependency_kwargs()
            | {
                "id": uuid4(),
                "source_ticket_id": source.id,
                "target_entity_type": "ticket",
                "target_entity_id": target.id,
            }
        )
    )


def test_ticket_dependencies_returns_blockers_blocked_by_and_readiness(
    database: Database,
) -> None:
    product = ProductRepo(database).get_by_key("ATLAS")
    assert product is not None
    epic = Epic(**_epic_model_kwargs(product.id, key="ATLAS-E1"))
    blocker = Ticket(
        **(
            _ticket_model_kwargs(product.id, epic.id, key="ATLAS-200")
            | {"id": uuid4(), "status": TicketStatus.IN_PROGRESS}
        )
    )
    ticket = Ticket(
        **(
            _ticket_model_kwargs(product.id, epic.id, key="ATLAS-199")
            | {
                "id": uuid4(),
                "status": TicketStatus.IN_PROGRESS,
                "acceptance_criteria": [],
            }
        )
    )
    dependent = Ticket(
        **(_ticket_model_kwargs(product.id, epic.id, key="ATLAS-201") | {"id": uuid4()})
    )
    EpicRepo(database).add(epic)
    ticket_repo = TicketRepo(database)
    for record in (blocker, ticket, dependent):
        ticket_repo.add(record)
    dependency_repo = TicketDependencyRepo(database)
    dependency_repo.add(_dependency(ticket, blocker))
    dependency_repo.add(_dependency(dependent, ticket))

    with TestClient(create_app(database=database)) as client:
        response = client.get("/api/v1/tickets/ATLAS-199/dependencies")

    assert response.status_code == 200
    assert response.json() == {
        "key": "ATLAS-199",
        "blockers": [
            {"key": "ATLAS-200", "code": "dependency_not_done"},
        ],
        "blocked_by": ["ATLAS-201"],
        "readiness": TicketReadinessSchema(
            ready=False,
            reasons=[
                NotReadyReasonSchema(
                    code=NotReadyCode.WRONG_STATUS,
                    message="status 'in_progress' is not one of ['backlog', 'planned']",
                    target=None,
                    status="in_progress",
                ),
                NotReadyReasonSchema(
                    code=NotReadyCode.DEPENDENCY_NOT_DONE,
                    message="depends_on ticket 'ATLAS-200' has status "
                    "'in_progress', not 'done'",
                    target="ATLAS-200",
                    status="in_progress",
                ),
                NotReadyReasonSchema(
                    code=NotReadyCode.NO_ACCEPTANCE_CRITERIA,
                    message="ticket has no acceptance criteria",
                    target=None,
                    status=None,
                ),
            ],
        ).model_dump(mode="json"),
    }


def test_ticket_dependencies_returns_native_404_for_unknown_key(
    database: Database,
) -> None:
    with TestClient(create_app(database=database)) as client:
        response = client.get("/api/v1/tickets/ATLAS-404/dependencies")

    assert response.status_code == 404
    assert response.json() == {"detail": "Ticket ATLAS-404 not found"}


def test_dependency_critical_path_returns_ordered_existing_projection(
    database: Database,
) -> None:
    product = ProductRepo(database).get_by_key("ATLAS")
    assert product is not None
    epic = Epic(**_epic_model_kwargs(product.id, key="ATLAS-E1"))
    last = Ticket(
        **(
            _ticket_model_kwargs(product.id, epic.id, key="ATLAS-201")
            | {"id": uuid4(), "estimated_effort": 5}
        )
    )
    middle = Ticket(
        **(
            _ticket_model_kwargs(product.id, epic.id, key="ATLAS-202")
            | {"id": uuid4(), "estimated_effort": 3}
        )
    )
    first = Ticket(
        **(
            _ticket_model_kwargs(product.id, epic.id, key="ATLAS-203")
            | {"id": uuid4(), "estimated_effort": 2}
        )
    )
    EpicRepo(database).add(epic)
    ticket_repo = TicketRepo(database)
    for record in (last, middle, first):
        ticket_repo.add(record)
    dependency_repo = TicketDependencyRepo(database)
    dependency_repo.add(_dependency(last, middle))
    dependency_repo.add(_dependency(middle, first))

    with TestClient(create_app(database=database)) as client:
        response = client.get("/api/v1/dependencies/critical-path")

    assert response.status_code == 200
    assert response.json() == DependencyCriticalPathResponse(
        keys=["ATLAS-203", "ATLAS-202", "ATLAS-201"],
        steps=[
            CriticalPathStepSchema(
                key="ATLAS-203",
                effort=2,
                cumulative_effort=2,
            ),
            CriticalPathStepSchema(
                key="ATLAS-202",
                effort=3,
                cumulative_effort=5,
            ),
            CriticalPathStepSchema(
                key="ATLAS-201",
                effort=5,
                cumulative_effort=10,
            ),
        ],
        total_effort=10,
    ).model_dump(mode="json")


def test_ticket_count_static_route_precedes_ticket_key_route(
    database: Database,
) -> None:
    with TestClient(create_app(database=database)) as client:
        response = client.get("/api/v1/tickets/count")

    assert response.status_code == 200
    assert response.json() == {"count": 0}


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
        response = client.get("/api/v1/reviews")

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
