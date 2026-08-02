"""Loopback operator-session security contract for writable API entry."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_plan_pipeline import fresh_db

from atlas.api.app import CONTENT_SECURITY_POLICY, create_app
from atlas.api.dependencies import MutationContextDependency
from atlas.api.schemas import SessionLoginRequest
from atlas.api.security import (
    CSRF_HEADER_NAME,
    SESSION_COOKIE_NAME,
    InMemoryOperatorSessionStore,
    LoginAttemptThrottle,
    MutationContext,
    OperatorTokenVerifier,
    session_cookie_from_set_cookie,
)
from atlas.core.enums import ActorType
from atlas.orchestration.operator_security import (
    OPERATOR_TOKEN_ENV,
    WritableApiPreconditionCode,
    WritableApiPreconditionError,
)
from atlas.storage import Database

GOOD_TOKEN = "atlas-operator-token-0123456789ABCDEFGHJKLMNPQRSTxyz!@#"
WRONG_TOKEN = "wrong-token-value-that-is-long-enough-for-a-request-body"
LOOPBACK_HOST = "127.0.0.1:4173"
LOOPBACK_ORIGIN = f"http://{LOOPBACK_HOST}"
NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)


class FrozenClock:
    """Mutable deterministic clock for session expiry tests."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


@dataclass
class FakeCommandService:
    calls: list[MutationContext]


@pytest.fixture
def database(tmp_path: Path) -> Database:
    return fresh_db(tmp_path)


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(NOW)


def _writable_app(
    database: Database,
    clock: Callable[[], datetime],
    *,
    token: str = GOOD_TOKEN,
    bind_host: str = "127.0.0.1",
) -> FastAPI:
    return create_app(
        database=database,
        enable_writes=True,
        operator_token=token,
        bind_host=bind_host,
        clock=clock,
        session_store=InMemoryOperatorSessionStore(clock=clock),
        login_throttle=LoginAttemptThrottle(clock=clock),
    )


def _protected_mutation_app(
    database: Database,
    clock: Callable[[], datetime],
) -> tuple[FastAPI, FakeCommandService]:
    app = _writable_app(database, clock)
    service = FakeCommandService(calls=[])

    @app.post("/api/v1/test-mutation", include_in_schema=False)
    def protected_mutation(context: MutationContextDependency) -> dict[str, str]:
        service.calls.append(context)
        return {
            "created_by_type": context.actor.created_by_type.value,
            "created_by_id": context.actor.created_by_id,
        }

    return app, service


def _login(client: TestClient, *, token: str = GOOD_TOKEN) -> tuple[str, str]:
    response = client.post(
        "/api/v1/session",
        json={"token": token},
        headers={"host": LOOPBACK_HOST},
    )
    assert response.status_code == 200
    session_id = session_cookie_from_set_cookie(response.headers["set-cookie"])
    assert session_id is not None
    return session_id, str(response.json()["csrf_token"])


def _mutation_headers(
    *,
    session_id: str,
    csrf_token: str,
    host: str = LOOPBACK_HOST,
    origin: str = LOOPBACK_ORIGIN,
    content_type: str = "application/json",
) -> dict[str, str]:
    return {
        "host": host,
        "origin": origin,
        "content-type": content_type,
        CSRF_HEADER_NAME: csrf_token,
        "cookie": f"{SESSION_COOKIE_NAME}={session_id}",
    }


def test_ac1_writable_startup_preconditions_are_named_and_read_only_still_starts(
    database: Database,
    clock: FrozenClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(OPERATOR_TOKEN_ENV, raising=False)

    with (
        pytest.raises(WritableApiPreconditionError) as missing,
        TestClient(create_app(database=database, enable_writes=True, clock=clock)),
    ):
        pass
    assert missing.value.code is WritableApiPreconditionCode.TOKEN_MISSING
    assert OPERATOR_TOKEN_ENV in str(missing.value)
    assert GOOD_TOKEN not in str(missing.value)

    with (
        pytest.raises(WritableApiPreconditionError) as weak,
        TestClient(_writable_app(database, clock, token="a" * 64)),
    ):
        pass
    assert weak.value.code is WritableApiPreconditionCode.TOKEN_WEAK
    assert "a" * 64 not in str(weak.value)

    with (
        pytest.raises(WritableApiPreconditionError) as remote,
        TestClient(_writable_app(database, clock, bind_host="0.0.0.0")),
    ):
        pass
    assert remote.value.code is WritableApiPreconditionCode.REMOTE_UNSUPPORTED

    with TestClient(
        create_app(database=database, enable_writes=False, bind_host="0.0.0.0")
    ) as client:
        response = client.get("/api/v1/status")
    assert response.status_code == 200


def test_ac2_login_uses_strict_json_constant_time_throttling_and_secret_redaction(
    database: Database,
    clock: FrozenClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compare_calls: list[tuple[bytes, bytes]] = []

    def capture_compare(left: bytes, right: bytes) -> bool:
        compare_calls.append((left, right))
        return False

    with monkeypatch.context() as patch:
        patch.setattr("atlas.api.security.hmac.compare_digest", capture_compare)
        verifier = OperatorTokenVerifier(GOOD_TOKEN)
        assert not verifier.verify(WRONG_TOKEN)
    assert len(compare_calls) == 1
    assert {len(value) for pair in compare_calls for value in pair} == {32}

    app = _writable_app(database, clock)
    with TestClient(app) as client:
        form_response = client.post(
            "/api/v1/session",
            data={"token": GOOD_TOKEN},
            headers={"host": LOOPBACK_HOST},
        )
        assert form_response.status_code in {415, 422}
        assert GOOD_TOKEN not in form_response.text

        for _attempt in range(3):
            response = client.post(
                "/api/v1/session",
                json={"token": WRONG_TOKEN},
                headers={"host": LOOPBACK_HOST},
            )
            assert response.status_code == 401
            assert GOOD_TOKEN not in response.text
        throttled = client.post(
            "/api/v1/session",
            json={"token": WRONG_TOKEN},
            headers={"host": LOOPBACK_HOST},
        )
        assert throttled.status_code == 429
        assert GOOD_TOKEN not in throttled.text

        clock.advance(timedelta(minutes=2))
        success = client.post(
            "/api/v1/session",
            json={"token": GOOD_TOKEN},
            headers={"host": LOOPBACK_HOST},
        )

    assert success.status_code == 200
    body = success.json()
    assert set(body) == {"authenticated", "expires_at", "csrf_token"}
    assert body["authenticated"] is True
    assert GOOD_TOKEN not in success.text
    set_cookie = success.headers["set-cookie"]
    assert SESSION_COOKIE_NAME in set_cookie
    assert "HttpOnly" in set_cookie
    assert "samesite=strict" in set_cookie.lower()
    assert "Domain=" not in set_cookie
    assert "Secure" not in set_cookie
    session_id = session_cookie_from_set_cookie(set_cookie)
    assert session_id is not None
    assert session_id not in success.text


def test_ac3_session_state_expiry_revocation_and_subsequent_401(
    database: Database,
    clock: FrozenClock,
) -> None:
    app, service = _protected_mutation_app(database, clock)
    with TestClient(app) as client:
        session_id, csrf_token = _login(client)

        state = client.get(
            "/api/v1/session",
            headers={
                "host": LOOPBACK_HOST,
                "cookie": f"{SESSION_COOKIE_NAME}={session_id}",
            },
        )
        assert state.status_code == 200
        assert state.json() == {
            "authenticated": True,
            "expires_at": (NOW + timedelta(minutes=30))
            .isoformat()
            .replace("+00:00", "Z"),
        }
        assert "csrf" not in state.text.lower()

        revoked = client.request(
            "DELETE",
            "/api/v1/session",
            headers=_mutation_headers(session_id=session_id, csrf_token=csrf_token),
        )
        assert revoked.status_code == 200
        assert revoked.json() == {"authenticated": False, "expires_at": None}

        after_revoke = client.post(
            "/api/v1/test-mutation",
            json={},
            headers=_mutation_headers(session_id=session_id, csrf_token=csrf_token),
        )
        assert after_revoke.status_code == 401
        assert service.calls == []

        expired_session_id, expired_csrf = _login(client)
        clock.advance(timedelta(minutes=31))
        expired = client.post(
            "/api/v1/test-mutation",
            json={},
            headers=_mutation_headers(
                session_id=expired_session_id,
                csrf_token=expired_csrf,
            ),
        )
    assert expired.status_code == 401
    assert service.calls == []


@pytest.mark.parametrize(
    ("name", "headers", "expected_status"),
    [
        (
            "missing_origin",
            {"origin": None},
            403,
        ),
        (
            "hostile_origin",
            {"origin": "http://evil.test"},
            403,
        ),
        (
            "null_origin",
            {"origin": "null"},
            403,
        ),
        (
            "host_confusion",
            {"host": "evil.test", "origin": "http://evil.test"},
            403,
        ),
        (
            "csrf_mismatch",
            {CSRF_HEADER_NAME: "wrong-csrf"},
            403,
        ),
        (
            "simple_form_content_type",
            {"content-type": "application/x-www-form-urlencoded"},
            415,
        ),
        (
            "malformed_content_type",
            {"content-type": "application/json; charset=utf-8"},
            415,
        ),
    ],
)
def test_ac4_mutation_dependency_rejects_hostile_csrf_and_content_type_before_service(
    database: Database,
    clock: FrozenClock,
    name: str,
    headers: dict[str, str | None],
    expected_status: int,
) -> None:
    app, service = _protected_mutation_app(database, clock)
    with TestClient(app) as client:
        session_id, csrf_token = _login(client)
        request_headers = _mutation_headers(
            session_id=session_id,
            csrf_token=csrf_token,
        )
        for key, value in headers.items():
            if value is None:
                request_headers.pop(key, None)
            else:
                request_headers[key] = value

        response = client.post(
            "/api/v1/test-mutation",
            content=json.dumps({"probe": name}),
            headers=request_headers,
        )

    assert response.status_code == expected_status
    assert service.calls == []


def test_ac5_mutation_context_resolves_immutable_operator_actor_and_rejects_actor_input(
    database: Database,
    clock: FrozenClock,
) -> None:
    assert set(SessionLoginRequest.model_fields) == {"token"}

    app, service = _protected_mutation_app(database, clock)
    with TestClient(app) as client:
        injected_login = client.post(
            "/api/v1/session",
            json={
                "token": GOOD_TOKEN,
                "actor": "attacker",
                "created_by_type": "agent",
                "created_by_id": "malicious",
            },
            headers={"host": LOOPBACK_HOST},
        )
        assert injected_login.status_code == 422

        session_id, csrf_token = _login(client)
        response = client.post(
            "/api/v1/test-mutation",
            json={},
            headers=_mutation_headers(
                session_id=session_id,
                csrf_token=csrf_token,
            )
            | {
                "X-Atlas-Actor": "agent/malicious",
                "X-Atlas-Created-By-Type": "agent",
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "created_by_type": "human",
        "created_by_id": "operator",
    }
    assert len(service.calls) == 1
    assert service.calls[0].actor.created_by_type is ActorType.HUMAN
    assert service.calls[0].actor.created_by_id == "operator"


def test_ac6_session_and_mutation_headers_are_no_store_and_cors_denied(
    database: Database,
    clock: FrozenClock,
) -> None:
    app, _service = _protected_mutation_app(database, clock)
    assert all(
        cast(type[object], middleware.cls).__name__ != "CORSMiddleware"
        for middleware in app.user_middleware
    )

    with TestClient(app) as client:
        login = client.post(
            "/api/v1/session",
            json={"token": GOOD_TOKEN},
            headers={"host": LOOPBACK_HOST},
        )
        session_id = session_cookie_from_set_cookie(login.headers["set-cookie"])
        assert session_id is not None
        csrf_token = str(login.json()["csrf_token"])
        mutation = client.post(
            "/api/v1/test-mutation",
            json={},
            headers=_mutation_headers(session_id=session_id, csrf_token=csrf_token),
        )
        cors_probe = client.options(
            "/api/v1/session",
            headers={
                "origin": "http://evil.test",
                "access-control-request-method": "POST",
            },
        )

    for response in (login, mutation):
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    assert "access-control-allow-origin" not in cors_probe.headers


def test_ac7_openapi_declares_session_routes_security_and_no_usable_secret(
    database: Database,
    clock: FrozenClock,
) -> None:
    del database, clock
    document = create_app(
        enable_writes=True,
        operator_token=GOOD_TOKEN,
        bind_host="127.0.0.1",
    ).openapi()

    assert set(document["paths"]["/api/v1/session"]) == {"get", "post", "delete"}
    schemes = document["components"]["securitySchemes"]
    assert schemes["AtlasSessionCookie"] == {
        "type": "apiKey",
        "in": "cookie",
        "name": SESSION_COOKIE_NAME,
    }
    assert schemes["AtlasCSRFToken"] == {
        "type": "apiKey",
        "in": "header",
        "name": CSRF_HEADER_NAME,
    }
    assert document["paths"]["/api/v1/session"]["delete"]["security"] == [
        {
            "AtlasSessionCookie": [],
            "AtlasCSRFToken": [],
        }
    ]
    serialized = json.dumps(document, sort_keys=True)
    assert GOOD_TOKEN not in serialized
    assert WRONG_TOKEN not in serialized
