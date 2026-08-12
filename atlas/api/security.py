"""Loopback operator-session security primitives for the HTTP adapter."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http.cookies import SimpleCookie
from typing import Final
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, Response, status

from atlas.api.schemas import (
    SessionLoginRequest,
    SessionLoginResponse,
    SessionStateResponse,
)
from atlas.core.enums import ActorType
from atlas.orchestration.operator_security import (
    assert_writable_startup_preconditions,
    is_loopback_host,
    validate_operator_token,
)

SESSION_COOKIE_NAME: Final = "atlas_session"
CSRF_HEADER_NAME: Final = "X-Atlas-CSRF"
SESSION_TTL: Final = timedelta(minutes=30)
LOGIN_FAILURE_LIMIT: Final = 3
LOGIN_FAILURE_WINDOW: Final = timedelta(minutes=1)

Clock = Callable[[], datetime]


@dataclass(frozen=True)
class OperatorActor:
    """Server-owned command actor resolved from a live operator session."""

    created_by_type: ActorType = ActorType.HUMAN
    created_by_id: str = "operator"


@dataclass(frozen=True)
class MutationContext:
    """Authenticated command context passed to future writable services."""

    actor: OperatorActor
    session_digest: bytes
    expires_at: datetime


@dataclass(frozen=True)
class AuthenticatedSessionContext:
    """Live shared-session context for an authenticated observational read."""

    actor: OperatorActor
    expires_at: datetime


@dataclass(frozen=True)
class CreatedOperatorSession:
    """Opaque browser credentials emitted once at login."""

    session_id: str
    csrf_token: str
    expires_at: datetime


@dataclass(frozen=True)
class StoredOperatorSession:
    """Digest-only session record retained server-side."""

    session_digest: bytes
    csrf_digest: bytes
    expires_at: datetime
    revoked_at: datetime | None = None


def utc_now() -> datetime:
    """Return an aware UTC timestamp for session expiry decisions."""
    return datetime.now(UTC)


def _secret_digest(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


class OperatorTokenVerifier:
    """Constant-time verifier for the configured bootstrap credential."""

    def __init__(self, token: str) -> None:
        self._token_digest = _secret_digest(validate_operator_token(token))

    def verify(self, presented_token: str) -> bool:
        return hmac.compare_digest(
            _secret_digest(presented_token),
            self._token_digest,
        )


class LoginAttemptThrottle:
    """Bound failed bootstrap-token attempts over a short in-memory window."""

    def __init__(
        self,
        *,
        clock: Clock = utc_now,
        limit: int = LOGIN_FAILURE_LIMIT,
        window: timedelta = LOGIN_FAILURE_WINDOW,
    ) -> None:
        self._clock = clock
        self._limit = limit
        self._window = window
        self._failures: list[datetime] = []

    def _prune(self, now: datetime) -> None:
        window_start = now - self._window
        self._failures = [
            failed_at for failed_at in self._failures if failed_at > window_start
        ]

    def before_attempt(self) -> None:
        now = self._clock()
        self._prune(now)
        if len(self._failures) >= self._limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="operator login temporarily throttled",
            )

    def record_failure(self) -> None:
        now = self._clock()
        self._prune(now)
        self._failures.append(now)

    def record_success(self) -> None:
        self._failures = []


class InMemoryOperatorSessionStore:
    """Digest-only in-process store for loopback operator browser sessions."""

    def __init__(self, *, clock: Clock = utc_now, ttl: timedelta = SESSION_TTL) -> None:
        self._clock = clock
        self._ttl = ttl
        self._sessions: dict[bytes, StoredOperatorSession] = {}

    def create(self) -> CreatedOperatorSession:
        session_id = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        expires_at = self._clock() + self._ttl
        session_digest = _secret_digest(session_id)
        self._sessions[session_digest] = StoredOperatorSession(
            session_digest=session_digest,
            csrf_digest=_secret_digest(csrf_token),
            expires_at=expires_at,
        )
        return CreatedOperatorSession(
            session_id=session_id,
            csrf_token=csrf_token,
            expires_at=expires_at,
        )

    def resolve_session(self, session_id: str | None) -> StoredOperatorSession | None:
        if not session_id:
            return None
        session_digest = _secret_digest(session_id)
        session = self._sessions.get(session_digest)
        if session is None:
            return None
        if session.revoked_at is not None or session.expires_at <= self._clock():
            return None
        return session

    def resolve_with_csrf(
        self,
        *,
        session_id: str | None,
        csrf_token: str | None,
    ) -> tuple[StoredOperatorSession | None, bool]:
        session = self.resolve_session(session_id)
        if session is None:
            return None, False
        if not csrf_token:
            return session, False
        return session, hmac.compare_digest(
            _secret_digest(csrf_token),
            session.csrf_digest,
        )

    def revoke(self, session_digest: bytes) -> None:
        session = self._sessions.get(session_digest)
        if session is None:
            return
        self._sessions[session_digest] = StoredOperatorSession(
            session_digest=session.session_digest,
            csrf_digest=session.csrf_digest,
            expires_at=session.expires_at,
            revoked_at=self._clock(),
        )


def _require_strict_json(request: Request) -> None:
    if request.headers.get("content-type") != "application/json":
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="strict application/json required",
        )


def _origin_matches_host(origin: str, host: str) -> bool:
    parsed = urlsplit(origin)
    return (
        parsed.scheme == "http"
        and parsed.netloc == host
        and parsed.path == ""
        and parsed.query == ""
        and parsed.fragment == ""
        and is_loopback_host(host)
    )


def _require_loopback_host_and_origin(request: Request) -> None:
    host = request.headers.get("host", "")
    origin = request.headers.get("origin", "")
    if origin == "null" or not _origin_matches_host(origin, host):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="operator mutation origin rejected",
        )


def _set_session_cookie(
    response: Response,
    *,
    session_id: str,
    expires_at: datetime,
) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        session_id,
        max_age=int(SESSION_TTL.total_seconds()),
        expires=expires_at,
        path="/",
        secure=False,
        httponly=True,
        samesite="strict",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        path="/",
        secure=False,
        httponly=True,
        samesite="strict",
    )


def session_cookie_from_set_cookie(set_cookie: str) -> str | None:
    """Parse a session cookie value from a Set-Cookie header for tests/tools."""
    cookie = SimpleCookie()
    cookie.load(set_cookie)
    morsel = cookie.get(SESSION_COOKIE_NAME)
    return None if morsel is None else morsel.value


class OperatorSessionService:
    """Injected service owning login, live-session resolution, and revocation."""

    def __init__(
        self,
        *,
        verifier: OperatorTokenVerifier,
        store: InMemoryOperatorSessionStore,
        throttle: LoginAttemptThrottle,
    ) -> None:
        self._verifier = verifier
        self._store = store
        self._throttle = throttle

    def login(
        self,
        *,
        request: Request,
        response: Response,
        body: SessionLoginRequest,
    ) -> SessionLoginResponse:
        _require_strict_json(request)
        self._throttle.before_attempt()
        if not self._verifier.verify(body.token):
            self._throttle.record_failure()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid operator credential",
            )
        self._throttle.record_success()
        created = self._store.create()
        _set_session_cookie(
            response,
            session_id=created.session_id,
            expires_at=created.expires_at,
        )
        return SessionLoginResponse(
            authenticated=True,
            expires_at=created.expires_at,
            csrf_token=created.csrf_token,
        )

    def read_state(self, *, request: Request) -> SessionStateResponse:
        session = self._store.resolve_session(request.cookies.get(SESSION_COOKIE_NAME))
        if session is None:
            return SessionStateResponse(authenticated=False, expires_at=None)
        return SessionStateResponse(
            authenticated=True,
            expires_at=session.expires_at,
        )

    def resolve_authenticated_context(
        self, *, session_id: str | None
    ) -> AuthenticatedSessionContext:
        """Require one live cookie without applying mutation-only CSRF checks."""

        session = self._store.resolve_session(session_id)
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="operator session required",
            )
        return AuthenticatedSessionContext(
            actor=OperatorActor(),
            expires_at=session.expires_at,
        )

    def resolve_mutation_context(
        self,
        *,
        request: Request,
        session_id: str | None,
        csrf_token: str | None,
    ) -> MutationContext:
        _require_loopback_host_and_origin(request)
        _require_strict_json(request)
        session, csrf_matches = self._store.resolve_with_csrf(
            session_id=session_id,
            csrf_token=csrf_token,
        )
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="operator session required",
            )
        if not csrf_matches:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="operator csrf token rejected",
            )
        return MutationContext(
            actor=OperatorActor(),
            session_digest=session.session_digest,
            expires_at=session.expires_at,
        )

    def revoke(
        self,
        *,
        context: MutationContext,
        response: Response,
    ) -> SessionStateResponse:
        self._store.revoke(context.session_digest)
        _clear_session_cookie(response)
        return SessionStateResponse(authenticated=False, expires_at=None)


def build_operator_session_service(
    *,
    operator_token: str | None,
    bind_host: str,
    clock: Clock = utc_now,
    store: InMemoryOperatorSessionStore | None = None,
    throttle: LoginAttemptThrottle | None = None,
) -> OperatorSessionService:
    """Validate startup preconditions and build the injected session service."""
    token = assert_writable_startup_preconditions(
        operator_token=operator_token,
        bind_host=bind_host,
    )
    session_store = store or InMemoryOperatorSessionStore(clock=clock)
    login_throttle = throttle or LoginAttemptThrottle(clock=clock)
    return OperatorSessionService(
        verifier=OperatorTokenVerifier(token),
        store=session_store,
        throttle=login_throttle,
    )
