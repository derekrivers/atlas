"""ATL-411: authenticated lesson disposition HTTP command contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_lesson_model import lesson_kwargs
from test_plan_pipeline import fresh_db

from atlas.api.app import create_app
from atlas.api.dependencies import get_lesson_disposition_service
from atlas.api.security import CSRF_HEADER_NAME
from atlas.core.enums import ActorType, EntityStatus
from atlas.core.models import Lesson
from atlas.learning import (
    LessonDispositionCommand,
    RejectLesson,
)
from atlas.orchestration import (
    LessonDispositionCommandContext,
    LessonDispositionResult,
    LessonDispositionService,
    LessonDispositionStatus,
)
from atlas.storage import (
    Database,
    LessonRepo,
    OperatorActionReceiptRepo,
    ProductRepo,
)

GOOD_TOKEN = "atlas-operator-token-0123456789ABCDEFGHJKLMNPQRSTxyz!@#"
LOOPBACK_HOST = "127.0.0.1:4173"
LOOPBACK_ORIGIN = f"http://{LOOPBACK_HOST}"
NOW = datetime(2026, 8, 3, 12, tzinfo=UTC)


@pytest.fixture
def database(tmp_path: Path) -> Database:
    return fresh_db(tmp_path)


def _lesson(
    database: Database,
    *,
    status: EntityStatus = EntityStatus.DRAFT,
    confidence: float | None = None,
) -> Lesson:
    product = ProductRepo(database).get_by_key("ATLAS")
    assert product is not None
    return LessonRepo(database).add(
        Lesson(
            **lesson_kwargs()
            | {
                "id": uuid4(),
                "product_id": product.id,
                "status": status,
                "confidence": confidence,
                "created_at": NOW,
                "updated_at": NOW,
            }
        )
    )


def _writable_app(
    database: Database,
    service: RecordingDispositionService | None = None,
) -> FastAPI:
    app = create_app(
        database=database,
        enable_writes=True,
        operator_token=GOOD_TOKEN,
        bind_host="127.0.0.1",
        clock=lambda: NOW,
    )
    if service is not None:
        app.dependency_overrides[get_lesson_disposition_service] = lambda: cast(
            LessonDispositionService,
            service,
        )
    return app


def _login(client: TestClient) -> str:
    response = client.post(
        "/api/v1/session",
        json={"token": GOOD_TOKEN},
        headers={"host": LOOPBACK_HOST},
    )
    assert response.status_code == 200
    return str(response.json()["csrf_token"])


def _headers(
    csrf_token: str,
    *,
    idempotency_key: str | None = "atlas-command-key",
    host: str = LOOPBACK_HOST,
    origin: str = LOOPBACK_ORIGIN,
    content_type: str = "application/json",
) -> dict[str, str]:
    headers = {
        "host": host,
        "origin": origin,
        "content-type": content_type,
        CSRF_HEADER_NAME: csrf_token,
    }
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return headers


@dataclass
class RecordingDispositionService:
    result: LessonDispositionResult
    calls: list[tuple[LessonDispositionCommand, LessonDispositionCommandContext]] = (
        field(default_factory=list)
    )

    def execute(
        self,
        command: LessonDispositionCommand,
        context: LessonDispositionCommandContext,
    ) -> LessonDispositionResult:
        self.calls.append((command, context))
        return self.result


def test_ac1_writable_route_inventory_adds_only_two_explicit_lesson_commands(
    database: Database,
) -> None:
    app = _writable_app(database)
    lesson_operations = {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        if path.startswith("/api/v1/lessons")
        for method in operations
    }

    assert lesson_operations == {
        ("GET", "/api/v1/lessons"),
        ("POST", "/api/v1/lessons/{lesson_id}/promote"),
        ("POST", "/api/v1/lessons/{lesson_id}/reject"),
    }

    lesson_id = uuid4()
    with TestClient(app) as client:
        for method, path in (
            ("PATCH", f"/api/v1/lessons/{lesson_id}"),
            ("PUT", f"/api/v1/lessons/{lesson_id}"),
            ("POST", f"/api/v1/lessons/{lesson_id}/archive"),
            ("POST", f"/api/lessons/{lesson_id}/promote"),
        ):
            assert client.request(method, path).status_code == 404


def test_ac1_read_only_app_does_not_mount_resource_commands(
    database: Database,
) -> None:
    app = create_app(database=database)

    assert set(app.openapi()["paths"]) >= {"/api/v1/lessons"}
    assert all(
        path == "/api/v1/lessons"
        for path in app.openapi()["paths"]
        if path.startswith("/api/v1/lessons")
    )


def test_ac1_executable_route_inventory_proves_only_governed_writes_exist(
    database: Database,
) -> None:
    document = _writable_app(database).openapi()
    write_operations = {
        (method.upper(), path)
        for path, operations in document["paths"].items()
        for method in operations
        if method.upper() in {"POST", "PUT", "PATCH", "DELETE"}
    }

    assert write_operations == {
        ("POST", "/api/v1/session"),
        ("DELETE", "/api/v1/session"),
        ("POST", "/api/v1/lessons/{lesson_id}/promote"),
        ("POST", "/api/v1/lessons/{lesson_id}/reject"),
        ("POST", "/api/v1/reviews/{pr_number}/acceptance-sessions"),
        ("POST", "/api/v1/acceptance-sessions/{session_id}/evidence"),
        ("POST", "/api/v1/acceptance-sessions/{session_id}/confirm"),
        ("POST", "/api/v1/acceptance-sessions/{session_id}/verify"),
    }


def test_ac1_openapi_pins_security_header_and_strict_command_schemas(
    database: Database,
) -> None:
    document = _writable_app(database).openapi()
    promote = document["paths"]["/api/v1/lessons/{lesson_id}/promote"]["post"]
    reject = document["paths"]["/api/v1/lessons/{lesson_id}/reject"]["post"]
    schemas = document["components"]["schemas"]

    for operation in (promote, reject):
        assert operation["security"] == [
            {"AtlasSessionCookie": [], "AtlasCSRFToken": []}
        ]
        idempotency = [
            parameter
            for parameter in operation["parameters"]
            if parameter["name"] == "Idempotency-Key"
        ]
        assert len(idempotency) == 1
        assert idempotency[0]["in"] == "header"
        assert idempotency[0]["required"] is True
        assert "409" in operation["responses"]

    promote_schema = schemas["PromoteLessonRequest"]
    assert promote_schema["additionalProperties"] is False
    assert set(promote_schema["properties"]) == {"confidence"}
    assert promote_schema["properties"]["confidence"] == {
        "maximum": 1.0,
        "minimum": 0.0,
        "title": "Confidence",
        "type": "number",
    }
    reject_schema = schemas["RejectLessonRequest"]
    assert reject_schema["additionalProperties"] is False
    assert reject_schema["properties"] == {}


@pytest.mark.parametrize(
    ("case", "request_changes", "expected_status"),
    [
        ("missing-session", {"session": False}, 401),
        ("missing-csrf", {"csrf": ""}, 403),
        ("hostile-origin", {"origin": "http://evil.test"}, 403),
        ("host-confusion", {"host": "evil.test"}, 403),
        ("missing-idempotency", {"idempotency_key": None}, 422),
        ("blank-idempotency", {"idempotency_key": "   "}, 422),
        ("non-strict-json", {"content_type": "application/json; charset=utf-8"}, 415),
    ],
)
def test_ac2_security_preconditions_fail_before_the_service_is_invoked(
    database: Database,
    case: str,
    request_changes: dict[str, Any],
    expected_status: int,
) -> None:
    service = RecordingDispositionService(
        LessonDispositionResult(status=LessonDispositionStatus.COMMAND_FAILED)
    )
    with TestClient(_writable_app(database, service)) as client:
        csrf_token = _login(client)
        if request_changes.get("session") is False:
            client.cookies.clear()
        csrf_token = cast(str, request_changes.get("csrf", csrf_token))
        response = client.post(
            f"/api/v1/lessons/{uuid4()}/promote",
            content='{"confidence": 0.8}',
            headers=_headers(
                csrf_token,
                idempotency_key=request_changes.get(
                    "idempotency_key", f"security-{case}"
                ),
                host=request_changes.get("host", LOOPBACK_HOST),
                origin=request_changes.get("origin", LOOPBACK_ORIGIN),
                content_type=request_changes.get("content_type", "application/json"),
            ),
        )

    assert response.status_code == expected_status
    assert service.calls == []


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"confidence": "0.8"},
        {"confidence": True},
        {"confidence": -0.01},
        {"confidence": 1.01},
        {"confidence": 0.8, "actor": "agent"},
        {"confidence": 0.8, "status": "active"},
        {"confidence": 0.8, "content": "replacement"},
        {"confidence": 0.8, "unknown": "field"},
    ],
    ids=[
        "missing-confidence",
        "string-confidence",
        "boolean-confidence",
        "below-range",
        "above-range",
        "actor",
        "status",
        "content",
        "unknown",
    ],
)
def test_ac3_promote_schema_rejects_noncanonical_input_before_service(
    database: Database,
    body: dict[str, Any],
) -> None:
    service = RecordingDispositionService(
        LessonDispositionResult(status=LessonDispositionStatus.COMMAND_FAILED)
    )
    with TestClient(_writable_app(database, service)) as client:
        csrf_token = _login(client)
        response = client.post(
            f"/api/v1/lessons/{uuid4()}/promote",
            json=body,
            headers=_headers(csrf_token, idempotency_key=f"invalid-{uuid4()}"),
        )

    assert response.status_code == 422
    assert service.calls == []


@pytest.mark.parametrize(
    "rendered",
    ['{"confidence": NaN}', '{"confidence": Infinity}'],
)
def test_ac3_promote_schema_rejects_non_finite_json_before_service(
    database: Database,
    rendered: str,
) -> None:
    service = RecordingDispositionService(
        LessonDispositionResult(status=LessonDispositionStatus.COMMAND_FAILED)
    )
    with TestClient(_writable_app(database, service)) as client:
        csrf_token = _login(client)
        response = client.post(
            f"/api/v1/lessons/{uuid4()}/promote",
            content=rendered,
            headers=_headers(csrf_token, idempotency_key=f"finite-{uuid4()}"),
        )

    assert response.status_code == 422
    assert service.calls == []


@pytest.mark.parametrize(
    "body",
    [
        {"actor": "agent"},
        {"status": "archived"},
        {"content": "replacement"},
        {"unknown": "field"},
    ],
)
def test_ac3_reject_schema_accepts_only_an_empty_object_before_service(
    database: Database,
    body: dict[str, Any],
) -> None:
    service = RecordingDispositionService(
        LessonDispositionResult(status=LessonDispositionStatus.COMMAND_FAILED)
    )
    with TestClient(_writable_app(database, service)) as client:
        csrf_token = _login(client)
        response = client.post(
            f"/api/v1/lessons/{uuid4()}/reject",
            json=body,
            headers=_headers(csrf_token, idempotency_key=f"reject-{uuid4()}"),
        )

    assert response.status_code == 422
    assert service.calls == []


@pytest.mark.parametrize(
    ("action", "body", "expected_status", "expected_confidence"),
    [
        ("promote", {"confidence": 0.8}, EntityStatus.ACTIVE, 0.8),
        ("reject", {}, EntityStatus.ARCHIVED, None),
    ],
)
def test_ac5_success_returns_updated_safe_lesson_and_bounded_receipt(
    database: Database,
    action: str,
    body: dict[str, Any],
    expected_status: EntityStatus,
    expected_confidence: float | None,
) -> None:
    lesson = _lesson(database)
    idempotency_key = f"raw-key-{action}-must-not-leak"
    with TestClient(_writable_app(database)) as client:
        csrf_token = _login(client)
        response = client.post(
            f"/api/v1/lessons/{lesson.id}/{action}",
            json=body,
            headers=_headers(csrf_token, idempotency_key=idempotency_key),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["lesson"]["status"] == expected_status.value
    assert payload["lesson"]["confidence"] == expected_confidence
    assert payload["receipt"]["action"] == f"lesson.{action}"
    assert payload["receipt"]["actor"] == {"type": "human", "id": "operator"}
    assert payload["receipt"]["target"] == {
        "type": "lesson",
        "id": str(lesson.id),
    }
    assert idempotency_key not in response.text
    assert csrf_token not in response.text
    assert GOOD_TOKEN not in response.text
    assert set(payload["receipt"]) == {
        "receipt_id",
        "correlation_id",
        "action",
        "target",
        "actor",
        "idempotency_key_identity",
        "request_fingerprint",
        "outcome",
        "result_code",
        "result_metadata",
        "before_status",
        "after_status",
        "created_at",
        "completed_at",
    }


def test_ac4_unknown_lesson_maps_real_service_result_to_404(
    database: Database,
) -> None:
    with TestClient(_writable_app(database)) as client:
        csrf_token = _login(client)
        response = client.post(
            f"/api/v1/lessons/{uuid4()}/reject",
            json={},
            headers=_headers(csrf_token, idempotency_key="unknown-lesson"),
        )

    assert response.status_code == 404
    assert response.json() == {"detail": "lesson was not found"}
    assert len(OperatorActionReceiptRepo(database).list()) == 1


@pytest.mark.parametrize(
    ("result_status", "expected_status", "expected_detail", "with_lesson"),
    [
        (LessonDispositionStatus.NOT_FOUND, 404, "lesson was not found", False),
        (
            LessonDispositionStatus.INVALID,
            422,
            "lesson disposition request was invalid",
            False,
        ),
        (LessonDispositionStatus.NOT_DRAFT, 409, "lesson is not DRAFT", True),
        (
            LessonDispositionStatus.STALE_STATE,
            409,
            "lesson state changed before disposition committed",
            True,
        ),
        (
            LessonDispositionStatus.IDEMPOTENCY_CONFLICT,
            409,
            "idempotency key conflicts with an existing command",
            False,
        ),
        (
            LessonDispositionStatus.IN_PROGRESS,
            409,
            "idempotent command is still in progress",
            False,
        ),
    ],
)
def test_ac4_presenter_maps_typed_non_success_outcomes_without_domain_reparse(
    database: Database,
    result_status: LessonDispositionStatus,
    expected_status: int,
    expected_detail: str,
    with_lesson: bool,
) -> None:
    current = _lesson(database, status=EntityStatus.ACTIVE, confidence=0.7)
    service = RecordingDispositionService(
        LessonDispositionResult(
            status=result_status,
            lesson=current if with_lesson else None,
            message="private internal exception and credential material",
        )
    )
    with TestClient(_writable_app(database, service)) as client:
        csrf_token = _login(client)
        response = client.post(
            f"/api/v1/lessons/{current.id}/reject",
            json={},
            headers=_headers(csrf_token, idempotency_key=f"mapping-{result_status}"),
        )

    assert response.status_code == expected_status
    assert response.json()["detail"] == expected_detail
    assert (response.json().get("lesson") is not None) is with_lesson
    assert "private internal exception" not in response.text
    assert len(service.calls) == 1
    command, context = service.calls[0]
    assert isinstance(command, RejectLesson)
    assert context.created_by_type is ActorType.HUMAN
    assert context.created_by_id == "operator"


@pytest.mark.parametrize(
    "result_status",
    [
        LessonDispositionStatus.COMMAND_FAILED,
        LessonDispositionStatus.RECEIPT_PERSISTENCE_FAILED,
        LessonDispositionStatus.STORAGE_FAILED,
    ],
)
def test_ac4_failure_mapping_is_secret_free(
    database: Database,
    result_status: LessonDispositionStatus,
) -> None:
    service = RecordingDispositionService(
        LessonDispositionResult(
            status=result_status,
            message=f"{GOOD_TOKEN} raw request body and traceback",
        )
    )
    with TestClient(_writable_app(database, service)) as client:
        csrf_token = _login(client)
        response = client.post(
            f"/api/v1/lessons/{uuid4()}/promote",
            json={"confidence": 0.8},
            headers=_headers(csrf_token, idempotency_key=f"failure-{result_status}"),
        )

    assert response.status_code == 500
    assert response.json() == {"detail": "lesson disposition failed"}
    assert GOOD_TOKEN not in response.text
    assert csrf_token not in response.text
    assert "traceback" not in response.text
    assert len(service.calls) == 1


def test_ac4_stale_cli_race_returns_safe_current_lesson_and_no_route_retry(
    database: Database,
) -> None:
    winner = _lesson(database, status=EntityStatus.ACTIVE, confidence=0.9)
    service = RecordingDispositionService(
        LessonDispositionResult(
            status=LessonDispositionStatus.STALE_STATE,
            lesson=winner,
        )
    )
    with TestClient(_writable_app(database, service)) as client:
        csrf_token = _login(client)
        response = client.post(
            f"/api/v1/lessons/{winner.id}/reject",
            json={},
            headers=_headers(csrf_token, idempotency_key="stale-cli-race"),
        )

    assert response.status_code == 409
    assert response.json()["lesson"]["id"] == str(winner.id)
    assert response.json()["lesson"]["status"] == "active"
    assert len(service.calls) == 1


def test_ac6_same_key_same_body_replays_byte_equivalent_success_without_mutation(
    database: Database,
) -> None:
    lesson = _lesson(database)
    with TestClient(_writable_app(database)) as client:
        csrf_token = _login(client)
        headers = _headers(csrf_token, idempotency_key="same-command")
        first = client.post(
            f"/api/v1/lessons/{lesson.id}/promote",
            json={"confidence": 0.8},
            headers=headers,
        )
        archived = LessonRepo(database).archive(
            lesson.id,
            now=NOW.replace(hour=13),
        )
        replay = client.post(
            f"/api/v1/lessons/{lesson.id}/promote",
            json={"confidence": 0.8},
            headers=headers,
        )

    assert first.status_code == replay.status_code == 200
    assert replay.content == first.content
    assert len(OperatorActionReceiptRepo(database).list()) == 1
    stored = LessonRepo(database).get(lesson.id)
    assert stored is not None
    assert stored == archived
    assert stored.status is EntityStatus.ARCHIVED
    assert stored.confidence == 0.8


@pytest.mark.parametrize(
    ("submitted", "canonical"),
    [
        (0.0004, 0.0),
        (0.123456, 0.123),
        (0.9999, 1.0),
    ],
    ids=["lower-bound-rounding", "fractional-scale", "upper-bound-rounding"],
)
def test_ac6_high_precision_confidence_has_byte_equivalent_canonical_replay(
    database: Database,
    submitted: float,
    canonical: float,
) -> None:
    lesson = _lesson(database)
    with TestClient(_writable_app(database)) as client:
        csrf_token = _login(client)
        headers = _headers(
            csrf_token,
            idempotency_key=f"canonical-confidence-{submitted}",
        )
        first = client.post(
            f"/api/v1/lessons/{lesson.id}/promote",
            json={"confidence": submitted},
            headers=headers,
        )
        replay = client.post(
            f"/api/v1/lessons/{lesson.id}/promote",
            json={"confidence": submitted},
            headers=headers,
        )

    assert first.status_code == replay.status_code == 200
    assert replay.content == first.content
    assert first.json()["lesson"]["confidence"] == canonical
    assert first.json()["receipt"]["result_metadata"]["confidence"] == canonical
    stored = LessonRepo(database).get(lesson.id)
    assert stored is not None
    assert stored.confidence == canonical
    assert len(OperatorActionReceiptRepo(database).list()) == 1


@pytest.mark.parametrize(
    ("action", "body", "terminal_status"),
    [
        ("promote", {"confidence": 0.8}, EntityStatus.ACTIVE),
        ("reject", {}, EntityStatus.ARCHIVED),
    ],
)
def test_ac6_same_key_replay_ignores_later_citation_and_preserves_canonical_state(
    database: Database,
    action: str,
    body: dict[str, Any],
    terminal_status: EntityStatus,
) -> None:
    lesson = _lesson(database)
    later_ticket_id = uuid4()
    with TestClient(_writable_app(database)) as client:
        csrf_token = _login(client)
        headers = _headers(csrf_token, idempotency_key=f"citation-{action}")
        first = client.post(
            f"/api/v1/lessons/{lesson.id}/{action}",
            json=body,
            headers=headers,
        )
        [cited] = LessonRepo(database).record_ticket_citation(
            lesson_ids=[lesson.id],
            ticket_id=later_ticket_id,
        )
        replay = client.post(
            f"/api/v1/lessons/{lesson.id}/{action}",
            json=body,
            headers=headers,
        )

    assert first.status_code == replay.status_code == 200
    assert replay.content == first.content
    assert first.json()["lesson"]["status"] == terminal_status.value
    assert first.json()["lesson"]["related_ticket_ids"] == []
    assert cited.related_ticket_ids == [later_ticket_id]
    assert LessonRepo(database).get(lesson.id) == cited
    assert len(OperatorActionReceiptRepo(database).list()) == 1


@pytest.mark.parametrize(
    ("altered_action", "altered_body"),
    [
        ("promote", {"confidence": 0.9}),
        ("reject", {}),
    ],
    ids=["different-confidence", "different-action"],
)
def test_ac6_same_key_altered_command_conflicts_without_mutation(
    database: Database,
    altered_action: str,
    altered_body: dict[str, Any],
) -> None:
    lesson = _lesson(database)
    with TestClient(_writable_app(database)) as client:
        csrf_token = _login(client)
        headers = _headers(csrf_token, idempotency_key="altered-command")
        first = client.post(
            f"/api/v1/lessons/{lesson.id}/promote",
            json={"confidence": 0.8},
            headers=headers,
        )
        altered = client.post(
            f"/api/v1/lessons/{lesson.id}/{altered_action}",
            json=altered_body,
            headers=headers,
        )

    assert first.status_code == 200
    assert altered.status_code == 409
    assert altered.json() == {
        "detail": "idempotency key conflicts with an existing command",
        "lesson": None,
    }
    assert len(OperatorActionReceiptRepo(database).list()) == 1
    stored = LessonRepo(database).get(lesson.id)
    assert stored is not None
    assert stored.status is EntityStatus.ACTIVE
    assert stored.confidence == 0.8


def test_ac7_writable_mode_keeps_get_lessons_public_and_filter_compatible(
    database: Database,
) -> None:
    draft = _lesson(database)
    with TestClient(_writable_app(database)) as client:
        response = client.get("/api/v1/lessons?status=draft")
        invalid = client.get("/api/v1/lessons?status=not_a_status")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["lessons"]] == [str(draft.id)]
    assert invalid.status_code == 422
