"""ATL-425 authenticated acceptance-session HTTP contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_plan_pipeline import fresh_db

from atlas.api.app import create_app
from atlas.api.dependencies import (
    get_acceptance_session_confirmation_service,
    get_acceptance_session_creation_service,
    get_acceptance_session_evidence_service,
    get_acceptance_session_readiness_service,
    get_acceptance_session_verification_service,
)
from atlas.api.schemas import (
    AcceptanceConfirmationRequestSchema,
    AcceptanceEvidenceRequest,
    AcceptanceVerificationRequest,
    CreateAcceptanceSessionRequest,
)
from atlas.api.security import CSRF_HEADER_NAME, SESSION_COOKIE_NAME
from atlas.core.enums import ActorType, EntityStatus, RiskLevel
from atlas.core.models import (
    AcceptanceSessionBlockingReason,
    AcceptanceSessionLifecycle,
    Product,
    Ticket,
    TicketStatus,
    TicketType,
)
from atlas.github import GitHubCompare, GitHubCompareStatus, GitHubTimeoutError
from atlas.storage import AcceptanceSessionRepo, Database, ProductRepo, TicketRepo

GOOD_TOKEN = "atlas-operator-token-0123456789ABCDEFGHJKLMNPQRSTxyz!@#"
HOST = "127.0.0.1:4173"
ORIGIN = f"http://{HOST}"
OWNER = "acme"
REPO = "atlas"
PR = 425
NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
HEAD = "a" * 40
BASE = "b" * 40


@dataclass
class FakeGitHubClient:
    timeout: bool = False
    calls: int = 0

    def _observe(self) -> None:
        self.calls += 1
        if self.timeout:
            raise GitHubTimeoutError("fixture deadline")

    def fetch_pull_request(
        self, owner: str, repo: str, pr_number: int
    ) -> dict[str, Any]:
        self._observe()
        return {
            "number": pr_number,
            "title": "ATLAS-425 acceptance API",
            "body": None,
            "state": "open",
            "draft": False,
            "merged": False,
            "mergeable": True,
            "head": {
                "ref": "agent/atl-425-acceptance-api",
                "sha": HEAD,
                "repo": {"full_name": f"{owner}/{repo}"},
            },
            "base": {
                "ref": "main",
                "sha": BASE,
                "repo": {"full_name": f"{owner}/{repo}"},
            },
        }

    def fetch_branch_head(self, *_args: Any) -> str:
        self._observe()
        return BASE

    def compare_commits(self, *_args: Any) -> GitHubCompare:
        self._observe()
        return GitHubCompare(
            status=GitHubCompareStatus.AHEAD,
            ahead_by=1,
            behind_by=0,
            merge_base_sha=BASE,
        )

    def fetch_workflow_runs(self, *_args: Any) -> list[dict[str, Any]]:
        self._observe()
        return []

    def fetch_check_runs(self, *_args: Any) -> list[dict[str, Any]]:
        self._observe()
        return []

    def fetch_pr_reviews(self, *_args: Any) -> list[dict[str, Any]]:
        self._observe()
        return []

    def fetch_pr_files(self, *_args: Any) -> list[dict[str, Any]]:
        self._observe()
        return []


@pytest.fixture
def database(tmp_path: Path) -> Database:
    return fresh_db(tmp_path)


def _seed_ticket(database: Database) -> Ticket:
    products = ProductRepo(database).list()
    product = (
        products[0]
        if products
        else ProductRepo(database).add(
            Product(
                id=uuid4(),
                key="ATLAS",
                name="Atlas",
                description="Organisational operating system.",
                vision="Governed delivery.",
                status=EntityStatus.ACTIVE,
                created_by_type=ActorType.HUMAN,
                created_by_id="operator",
                created_at=NOW,
                updated_at=NOW,
            )
        )
    )
    return TicketRepo(database).add(
        Ticket(
            id=uuid4(),
            product_id=product.id,
            key="ATLAS-425",
            title="Acceptance workflow API",
            objective="Expose one authenticated exact-head resource.",
            context="Phase 14.",
            status=TicketStatus.REVIEW_REQUIRED,
            ticket_type=TicketType.FEATURE,
            risk_level=RiskLevel.HIGH,
            priority=1,
            acceptance_criteria=["The authenticated route is exact-head."],
            source_anchor="docs/atlas/review-acceptance-console.md#http-contract",
            created_by_type=ActorType.AGENT,
            created_by_id="planner",
            created_at=NOW,
            updated_at=NOW,
        )
    )


def _app(database: Database, github: FakeGitHubClient) -> FastAPI:
    return create_app(
        database=database,
        enable_writes=True,
        operator_token=GOOD_TOKEN,
        clock=lambda: NOW,
        acceptance_repositories=(f"{OWNER}/{REPO}",),
        acceptance_github_client=github,
        acceptance_external_timeout_seconds=0.25,
    )


def _login(client: TestClient) -> tuple[str, str]:
    response = client.post(
        "/api/v1/session",
        json={"token": GOOD_TOKEN},
        headers={"host": HOST},
    )
    assert response.status_code == 200
    session_id = client.cookies.get(SESSION_COOKIE_NAME)
    assert session_id is not None
    return session_id, str(response.json()["csrf_token"])


def _headers(session_id: str, csrf: str, *, key: str = "command-1") -> dict[str, str]:
    return {
        "host": HOST,
        "origin": ORIGIN,
        "content-type": "application/json",
        "cookie": f"{SESSION_COOKIE_NAME}={session_id}",
        CSRF_HEADER_NAME: csrf,
        "Idempotency-Key": key,
    }


def _create_session(client: TestClient, session_id: str, csrf: str) -> UUID:
    response = client.post(
        f"/api/v1/reviews/{PR}/acceptance-sessions",
        json={"repository": f"{OWNER}/{REPO}"},
        headers=_headers(session_id, csrf, key="create"),
    )
    assert response.status_code == 200, response.text
    assert response.json()["receipt"]["action"] == "acceptance_session.create"
    return UUID(response.json()["session"]["session_id"])


def test_ac1_route_inventory_is_exact_and_forbidden_commands_are_absent(
    database: Database,
) -> None:
    app = _app(database, FakeGitHubClient())
    operations = {
        (method.upper(), path)
        for path, methods in app.openapi()["paths"].items()
        for method in methods
    }
    acceptance = {
        operation for operation in operations if "acceptance-sessions" in operation[1]
    }
    assert acceptance == {
        ("POST", "/api/v1/reviews/{pr_number}/acceptance-sessions"),
        ("GET", "/api/v1/acceptance-sessions/{session_id}"),
        ("POST", "/api/v1/acceptance-sessions/{session_id}/evidence"),
        ("POST", "/api/v1/acceptance-sessions/{session_id}/confirm"),
        ("POST", "/api/v1/acceptance-sessions/{session_id}/verify"),
    }
    assert not any(
        method in {"PATCH", "PUT"}
        or path.endswith(("/merge", "/rebase", "/actions", "/commands"))
        for method, path in operations
    )


def test_ac2_security_idempotency_and_authenticated_no_store_get(
    database: Database,
) -> None:
    _seed_ticket(database)
    app = _app(database, FakeGitHubClient())
    with TestClient(app) as client:
        unauthenticated = client.get(
            f"/api/v1/acceptance-sessions/{uuid4()}",
            headers={"host": HOST},
        )
        session_id, csrf = _login(client)
        missing_key = client.post(
            f"/api/v1/reviews/{PR}/acceptance-sessions",
            json={"repository": f"{OWNER}/{REPO}"},
            headers={
                key: value
                for key, value in _headers(session_id, csrf).items()
                if key != "Idempotency-Key"
            },
        )
        created_id = _create_session(client, session_id, csrf)
        read = client.get(
            f"/api/v1/acceptance-sessions/{created_id}",
            headers={
                "host": HOST,
                "cookie": f"{SESSION_COOKIE_NAME}={session_id}",
            },
        )

    assert unauthenticated.status_code == 401
    assert missing_key.status_code == 422
    assert read.status_code == 200
    assert read.headers["cache-control"] == "no-store"
    assert read.json()["merge_ready"] is False
    assert read.json()["reasons"]


@pytest.mark.parametrize(
    "payload",
    [
        {"repository": "https://github.com/acme/atlas"},
        {"repository": "acme/other"},
        {"repository": "acme/atlas", "github_token": "secret"},
        {"repository": "acme/atlas", "actor": "attacker"},
        {"repository": "acme/atlas", "sha": HEAD},
    ],
)
def test_ac3_create_policy_and_strict_schema_reject_browser_authority(
    database: Database,
    payload: dict[str, str],
) -> None:
    app = _app(database, FakeGitHubClient())
    with TestClient(app) as client:
        session_id, csrf = _login(client)
        response = client.post(
            f"/api/v1/reviews/{PR}/acceptance-sessions",
            json=payload,
            headers=_headers(session_id, csrf),
        )
    assert response.status_code == 422


def test_ac3_step_models_expose_only_minimal_strict_fields() -> None:
    assert set(CreateAcceptanceSessionRequest.model_fields) == {"repository"}
    assert set(AcceptanceEvidenceRequest.model_fields) == set()
    assert set(AcceptanceVerificationRequest.model_fields) == set()
    assert set(AcceptanceConfirmationRequestSchema.model_fields) == {
        "criteria_fingerprint",
        "criterion_indexes",
        "manual_approval",
    }


def test_ac4_each_dependency_calls_one_typed_service_once(
    database: Database,
) -> None:
    _seed_ticket(database)
    github = FakeGitHubClient()
    app = _app(database, github)
    creation = _CreationSpy()
    readiness = _ReadinessSpy()
    evidence = _EvidenceSpy()
    confirmation = _ConfirmationSpy()
    verification = _VerificationSpy()
    app.dependency_overrides[get_acceptance_session_creation_service] = lambda: creation
    app.dependency_overrides[get_acceptance_session_readiness_service] = lambda: (
        readiness
    )
    app.dependency_overrides[get_acceptance_session_evidence_service] = lambda: evidence
    app.dependency_overrides[get_acceptance_session_confirmation_service] = lambda: (
        confirmation
    )
    app.dependency_overrides[get_acceptance_session_verification_service] = lambda: (
        verification
    )

    with TestClient(app) as client:
        session_id, csrf = _login(client)
        target_id = uuid4()
        created = client.post(
            f"/api/v1/reviews/{PR}/acceptance-sessions",
            json={"repository": f"{OWNER}/{REPO}"},
            headers=_headers(session_id, csrf),
        )
        read = client.get(
            f"/api/v1/acceptance-sessions/{target_id}",
            headers={
                "host": HOST,
                "cookie": f"{SESSION_COOKIE_NAME}={session_id}",
            },
        )
        pulled = client.post(
            f"/api/v1/acceptance-sessions/{target_id}/evidence",
            json={},
            headers=_headers(session_id, csrf, key="evidence"),
        )
        confirmed = client.post(
            f"/api/v1/acceptance-sessions/{target_id}/confirm",
            json={
                "criteria_fingerprint": "sha256:" + "a" * 64,
                "criterion_indexes": [],
                "manual_approval": True,
            },
            headers=_headers(session_id, csrf, key="confirm"),
        )
        verified = client.post(
            f"/api/v1/acceptance-sessions/{target_id}/verify",
            json={},
            headers=_headers(session_id, csrf, key="verify"),
        )

    assert created.status_code == 409
    assert read.status_code == 404
    assert pulled.status_code == 404
    assert confirmed.status_code == 404
    assert verified.status_code == 404
    assert creation.calls == 1
    assert readiness.calls == 1
    assert evidence.calls == 1
    assert confirmation.calls == 1
    assert verification.calls == 1


class _CreationSpy:
    def __init__(self) -> None:
        self.calls = 0

    def create(self, **_kwargs: Any) -> Any:
        from atlas.orchestration import (
            AcceptanceSessionCreationResult,
            AcceptanceSessionCreationStatus,
        )

        self.calls += 1
        return AcceptanceSessionCreationResult(
            status=AcceptanceSessionCreationStatus.REFUSED,
            reasons=(AcceptanceSessionBlockingReason.PR_DRAFT,),
        )


class _ReadinessSpy:
    def __init__(self) -> None:
        self.calls = 0

    def evaluate(self, _session_id: UUID) -> Any:
        from atlas.orchestration import LiveAcceptanceReadinessResult

        self.calls += 1
        return LiveAcceptanceReadinessResult(
            merge_ready=False,
            reasons=(AcceptanceSessionBlockingReason.SESSION_UNKNOWN,),
        )


class _EvidenceSpy:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, _session_id: UUID, _context: Any) -> Any:
        from atlas.orchestration import (
            AcceptanceEvidencePullResult,
            OperatorActionGatewayStatus,
        )

        self.calls += 1
        return AcceptanceEvidencePullResult(
            status=OperatorActionGatewayStatus.EXECUTED,
        )


class _ConfirmationSpy:
    def __init__(self) -> None:
        self.calls = 0

    def confirm(self, _request: Any, *, idempotency_key: str) -> Any:
        from atlas.orchestration import (
            AcceptanceConfirmationResult,
            AcceptanceConfirmationStatus,
            AcceptanceConfirmationValidationCode,
        )

        assert idempotency_key == "confirm"
        self.calls += 1
        return AcceptanceConfirmationResult(
            status=AcceptanceConfirmationStatus.VALIDATION_FAILED,
            validation_errors=(AcceptanceConfirmationValidationCode.SESSION_UNKNOWN,),
        )


class _VerificationSpy:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, _session_id: UUID, _context: Any) -> Any:
        from atlas.orchestration import (
            AcceptanceVerificationResult,
            AcceptanceVerificationStatus,
        )

        self.calls += 1
        return AcceptanceVerificationResult(
            status=AcceptanceVerificationStatus.REFUSED,
            reasons=(AcceptanceSessionBlockingReason.SESSION_UNKNOWN,),
        )


def _successful_receipt(session_id: UUID, action: str) -> Any:
    from atlas.core.models import (
        OperatorActionOutcome,
        OperatorActionReceipt,
        OperatorActionResultCode,
    )

    return OperatorActionReceipt(
        id=uuid4(),
        correlation_id=uuid4(),
        action=action,
        target_type="acceptance_session",
        target_id=str(session_id),
        created_by_type=ActorType.HUMAN,
        created_by_id="operator",
        idempotency_key_identity="sha256:" + "1" * 64,
        request_fingerprint="sha256:" + "2" * 64,
        outcome=OperatorActionOutcome.SUCCEEDED,
        result_code=OperatorActionResultCode.ACTION_SUCCEEDED,
        result_metadata={"affected_count": 1, "changed": True},
        created_at=NOW,
        completed_at=NOW,
    )


def _timeout_receipt(session_id: UUID, action: str) -> Any:
    from atlas.core.models import (
        OperatorActionOutcome,
        OperatorActionReceipt,
        OperatorActionResultCode,
    )

    return OperatorActionReceipt(
        id=uuid4(),
        correlation_id=uuid4(),
        action=action,
        target_type="acceptance_session",
        target_id=str(session_id),
        created_by_type=ActorType.HUMAN,
        created_by_id="operator",
        idempotency_key_identity="sha256:" + "3" * 64,
        request_fingerprint="sha256:" + "4" * 64,
        outcome=OperatorActionOutcome.FAILED,
        result_code=OperatorActionResultCode.EXTERNAL_TIMEOUT,
        result_metadata={"affected_count": 0, "changed": False},
        created_at=NOW,
        completed_at=NOW,
    )


class _EvidenceSuccess:
    def __init__(self, session: Any) -> None:
        self._session = session

    def execute(self, session_id: UUID, _context: Any) -> Any:
        from atlas.orchestration import (
            AcceptanceEvidencePullResult,
            OperatorActionGatewayStatus,
        )

        return AcceptanceEvidencePullResult(
            status=OperatorActionGatewayStatus.EXECUTED,
            session=self._session,
            receipt=_successful_receipt(
                session_id,
                "acceptance_session.pull_evidence",
            ),
        )


class _ConfirmationSuccess:
    def __init__(self, session: Any) -> None:
        self._session = session

    def confirm(self, request: Any, *, idempotency_key: str) -> Any:
        from atlas.orchestration import (
            AcceptanceConfirmationResult,
            AcceptanceConfirmationStatus,
        )

        assert idempotency_key == "confirm-success"
        return AcceptanceConfirmationResult(
            status=AcceptanceConfirmationStatus.CONFIRMED,
            session=self._session,
            receipt=_successful_receipt(
                request.session_id,
                "acceptance_session.confirm",
            ),
        )


class _VerificationSuccess:
    def __init__(self, session: Any) -> None:
        self._session = session

    def execute(self, session_id: UUID, _context: Any) -> Any:
        from atlas.orchestration import (
            AcceptanceVerificationResult,
            AcceptanceVerificationStatus,
        )

        return AcceptanceVerificationResult(
            status=AcceptanceVerificationStatus.MERGE_READY,
            merge_ready=True,
            session=self._session,
            receipt=_successful_receipt(
                session_id,
                "acceptance_session.verify",
            ),
        )


class _EvidenceTimeout:
    def __init__(self, session: Any) -> None:
        self._session = session

    def execute(self, session_id: UUID, _context: Any) -> Any:
        from atlas.orchestration import (
            AcceptanceEvidencePullResult,
            OperatorActionGatewayStatus,
        )

        return AcceptanceEvidencePullResult(
            status=OperatorActionGatewayStatus.EXECUTED,
            session=self._session,
            receipt=_timeout_receipt(
                session_id,
                "acceptance_session.pull_evidence",
            ),
        )


class _ConfirmationTimeout:
    def __init__(self, session: Any) -> None:
        self._session = session

    def confirm(self, request: Any, *, idempotency_key: str) -> Any:
        from atlas.orchestration import (
            AcceptanceConfirmationResult,
            AcceptanceConfirmationStatus,
        )

        assert idempotency_key == "confirm-timeout"
        return AcceptanceConfirmationResult(
            status=AcceptanceConfirmationStatus.REFUSED,
            session=self._session,
            receipt=_timeout_receipt(
                request.session_id,
                "acceptance_session.confirm",
            ),
        )


class _VerificationTimeout:
    def __init__(self, session: Any) -> None:
        self._session = session

    def execute(self, session_id: UUID, _context: Any) -> Any:
        from atlas.orchestration import (
            AcceptanceVerificationResult,
            AcceptanceVerificationStatus,
        )

        return AcceptanceVerificationResult(
            status=AcceptanceVerificationStatus.FAILED,
            session=self._session,
            receipt=_timeout_receipt(
                session_id,
                "acceptance_session.verify",
            ),
            reasons=(
                AcceptanceSessionBlockingReason.EXTERNAL_READ_TIMEOUT,
                AcceptanceSessionBlockingReason.EXTERNAL_STATE_INDETERMINATE,
            ),
        )


def test_ac1_ac5_all_five_routes_present_typed_success(
    database: Database,
) -> None:
    ticket = _seed_ticket(database)
    app = _app(database, FakeGitHubClient())
    with TestClient(app) as client:
        session_cookie, csrf = _login(client)
        session_id = _create_session(client, session_cookie, csrf)
        session = AcceptanceSessionRepo(database).get(session_id)
        assert session is not None
        app.dependency_overrides.update(
            {
                get_acceptance_session_evidence_service: lambda: _EvidenceSuccess(
                    session
                ),
                get_acceptance_session_confirmation_service: lambda: (
                    _ConfirmationSuccess(session)
                ),
                get_acceptance_session_verification_service: lambda: (
                    _VerificationSuccess(session)
                ),
            }
        )

        read = client.get(
            f"/api/v1/acceptance-sessions/{session_id}",
            headers={
                "host": HOST,
                "cookie": f"{SESSION_COOKIE_NAME}={session_cookie}",
            },
        )
        evidence = client.post(
            f"/api/v1/acceptance-sessions/{session_id}/evidence",
            json={},
            headers=_headers(session_cookie, csrf, key="evidence-success"),
        )
        confirmation = client.post(
            f"/api/v1/acceptance-sessions/{session_id}/confirm",
            json={
                "criteria_fingerprint": session.criteria_fingerprint,
                "criterion_indexes": list(range(len(ticket.acceptance_criteria))),
                "manual_approval": True,
            },
            headers=_headers(session_cookie, csrf, key="confirm-success"),
        )
        verification = client.post(
            f"/api/v1/acceptance-sessions/{session_id}/verify",
            json={},
            headers=_headers(session_cookie, csrf, key="verify-success"),
        )

    assert read.status_code == 200
    assert evidence.status_code == 200
    assert evidence.json()["receipt"]["action"] == ("acceptance_session.pull_evidence")
    assert confirmation.status_code == 200
    assert confirmation.json()["receipt"]["action"] == "acceptance_session.confirm"
    assert verification.status_code == 200
    assert verification.json()["receipt"]["action"] == "acceptance_session.verify"
    assert verification.json()["merge_ready"] is True


def test_ac5_step_timeouts_are_named_non_advancing_504_outcomes(
    database: Database,
) -> None:
    ticket = _seed_ticket(database)
    app = _app(database, FakeGitHubClient())
    with TestClient(app) as client:
        session_cookie, csrf = _login(client)
        session_id = _create_session(client, session_cookie, csrf)
        session = AcceptanceSessionRepo(database).get(session_id)
        assert session is not None
        app.dependency_overrides.update(
            {
                get_acceptance_session_evidence_service: lambda: _EvidenceTimeout(
                    session
                ),
                get_acceptance_session_confirmation_service: lambda: (
                    _ConfirmationTimeout(session)
                ),
                get_acceptance_session_verification_service: lambda: (
                    _VerificationTimeout(session)
                ),
            }
        )

        evidence = client.post(
            f"/api/v1/acceptance-sessions/{session_id}/evidence",
            json={},
            headers=_headers(session_cookie, csrf, key="evidence-timeout"),
        )
        confirmation = client.post(
            f"/api/v1/acceptance-sessions/{session_id}/confirm",
            json={
                "criteria_fingerprint": session.criteria_fingerprint,
                "criterion_indexes": list(range(len(ticket.acceptance_criteria))),
                "manual_approval": True,
            },
            headers=_headers(session_cookie, csrf, key="confirm-timeout"),
        )
        verification = client.post(
            f"/api/v1/acceptance-sessions/{session_id}/verify",
            json={},
            headers=_headers(session_cookie, csrf, key="verify-timeout"),
        )

    assert evidence.status_code == 504
    assert confirmation.status_code == 504
    assert verification.status_code == 504
    assert verification.json()["reasons"] == [
        "external_read_timeout",
        "external_state_indeterminate",
    ]
    assert AcceptanceSessionRepo(database).get(session_id) == session


def test_ac5_timeout_is_named_and_creation_does_not_advance(
    database: Database,
) -> None:
    _seed_ticket(database)
    github = FakeGitHubClient(timeout=True)
    app = _app(database, github)
    with TestClient(app) as client:
        session_id, csrf = _login(client)
        response = client.post(
            f"/api/v1/reviews/{PR}/acceptance-sessions",
            json={"repository": f"{OWNER}/{REPO}"},
            headers=_headers(session_id, csrf),
        )

    assert response.status_code == 504
    assert response.json()["reasons"] == ["external_read_timeout"]
    assert AcceptanceSessionRepo(database).list_for_pr(OWNER, REPO, PR) == []


def test_ac5_live_get_external_failure_returns_false_without_history_write(
    database: Database,
) -> None:
    _seed_ticket(database)
    github = FakeGitHubClient()
    app = _app(database, github)
    with TestClient(app) as client:
        session_id, csrf = _login(client)
        created_id = _create_session(client, session_id, csrf)
        before = AcceptanceSessionRepo(database).get(created_id)
        assert before is not None
        github.timeout = True
        response = client.get(
            f"/api/v1/acceptance-sessions/{created_id}",
            headers={
                "host": HOST,
                "cookie": f"{SESSION_COOKIE_NAME}={session_id}",
            },
        )
        after = AcceptanceSessionRepo(database).get(created_id)

    assert response.status_code == 200
    assert response.json()["merge_ready"] is False
    assert "external_read_timeout" in response.json()["reasons"]
    assert before == after
    assert after is not None
    assert after.lifecycle is AcceptanceSessionLifecycle.PREFLIGHT_PASSED


def test_ac6_openapi_is_finite_synchronous_and_publishes_security_and_enums(
    database: Database,
) -> None:
    document = _app(database, FakeGitHubClient()).openapi()
    get_operation = document["paths"]["/api/v1/acceptance-sessions/{session_id}"]["get"]
    post_operation = document["paths"][
        "/api/v1/acceptance-sessions/{session_id}/verify"
    ]["post"]
    assert get_operation["security"] == [{"AtlasSessionCookie": []}]
    assert post_operation["security"] == [
        {"AtlasSessionCookie": [], "AtlasCSRFToken": []}
    ]
    serialized = str(document).lower()
    assert not any(
        forbidden in serialized
        for forbidden in (
            "job_id",
            "polling",
            "websocket",
            "server-sent event",
            "background completion",
        )
    )
    schemas = document["components"]["schemas"]
    assert "AcceptanceSessionBlockingReason" in schemas
    assert "AcceptanceSessionLifecycle" in schemas
    assert "AcceptanceSessionStep" in schemas


def test_ac7_read_only_application_keeps_existing_route_inventory(
    database: Database,
) -> None:
    paths = create_app(database=database, enable_writes=False).openapi()["paths"]
    assert not any("acceptance-sessions" in path for path in paths)
    assert "/api/v1/reviews" in paths
    assert set(paths["/api/v1/reviews"]) == {"get"}
