"""Idempotent command gateway for governed operator writes."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, cast
from uuid import UUID, uuid4

import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy.orm import Session

from atlas.core.enums import ActorType
from atlas.core.models import OperatorActionOutcome, OperatorActionReceipt
from atlas.core.models.operator_action_receipt import (
    MAX_OPERATOR_ACTION_METADATA_BYTES,
)
from atlas.storage import Database
from atlas.storage.repositories import (
    NaiveDatetimeError,
    _add_operator_action_receipt,
    _add_operator_action_reservation,
    _get_operator_action_receipt_by_identity,
    _get_operator_action_reservation,
)

_HASH_PREFIX = "sha256:"
_REDACTED = "[REDACTED]"
_MAX_METADATA_STRING_BYTES = 512
_SECRET_KEY_RE = re.compile(
    r"(authorization|cookie|csrf|session|token|secret|password|credential|api[_-]?key)",
    re.IGNORECASE,
)
_FORBIDDEN_PAYLOAD_KEY_RE = re.compile(
    r"(request[_-]?body|raw[_-]?payload|raw[_-]?evidence|evidence[_-]?payload|"
    r"lesson[_-]?content|traceback|stack[_-]?trace|exception|problem|solution|"
    r"lesson[_-]?text)",
    re.IGNORECASE,
)
_SECRET_VALUE_RES = (
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{8,}"),
    re.compile(r"\bcsrf[-_A-Za-z0-9]{6,}", re.IGNORECASE),
    re.compile(r"\bsession[-_A-Za-z0-9]{6,}", re.IGNORECASE),
)


class CanonicalFingerprintError(ValueError):
    """A command payload cannot be represented as canonical JSON."""


class OperatorActionIdempotencyKeyError(ValueError):
    """An idempotency key was missing or unsupported."""


class OperatorActionGatewayStatus(StrEnum):
    """Typed gateway outcome before transport mapping."""

    EXECUTED = "executed"
    REPLAYED = "replayed"
    CONFLICT = "conflict"
    IN_PROGRESS = "in_progress"
    FAILED = "failed"


class OperatorActionConflictCode(StrEnum):
    """Conflict reasons that never invoke the command."""

    IDEMPOTENCY_KEY_REUSED = "idempotency_key_reused"
    IN_PROGRESS = "in_progress"


class OperatorActionFailureCode(StrEnum):
    """Typed failures that do not report mutation success."""

    COMMAND_FAILED = "command_failed"
    RECEIPT_COMMIT_FAILED = "receipt_commit_failed"
    RESERVATION_UNAVAILABLE = "reservation_unavailable"


@dataclass(frozen=True)
class OperatorActionEnvelope:
    """Server-resolved command identity passed into the gateway."""

    action: str
    target_type: str
    target_id: str
    created_by_type: ActorType
    created_by_id: str
    idempotency_key: str
    request_fingerprint: str


@dataclass(frozen=True)
class OperatorActionCommandContext:
    """Transaction context supplied to the injected command."""

    session: Session
    receipt_id: UUID
    correlation_id: UUID
    created_at: datetime


@dataclass(frozen=True)
class OperatorActionCommandResult:
    """Bounded terminal outcome returned by a domain command."""

    outcome: OperatorActionOutcome
    result_code: str
    result_metadata: Mapping[str, Any] = field(default_factory=dict)
    before_status: str | None = None
    after_status: str | None = None


@dataclass(frozen=True)
class OperatorActionConflict:
    code: OperatorActionConflictCode
    correlation_id: UUID | None = None


@dataclass(frozen=True)
class OperatorActionFailure:
    code: OperatorActionFailureCode


@dataclass(frozen=True)
class OperatorActionGatewayResult:
    """Gateway result; terminal receipt presentation is identical on replay."""

    status: OperatorActionGatewayStatus
    receipt: OperatorActionReceipt | None = None
    conflict: OperatorActionConflict | None = None
    failure: OperatorActionFailure | None = None


class Clock(Protocol):
    def __call__(self) -> datetime:
        """Return a timezone-aware timestamp."""


class IdFactory(Protocol):
    def __call__(self) -> UUID:
        """Return a stable generated UUID."""


Command = Callable[[OperatorActionCommandContext], OperatorActionCommandResult]


class _ReservationConflict(Exception):
    pass


class _CommandFailed(Exception):
    pass


class _ReceiptFailed(Exception):
    pass


def canonical_request_fingerprint(
    *,
    action: str,
    target_type: str,
    target_id: str,
    payload: Mapping[str, Any],
) -> str:
    """Return the stable fingerprint for action, target and full payload."""

    canonical_payload = {
        "action": _json_string(action, "action"),
        "target": {
            "type": _json_string(target_type, "target_type"),
            "id": _json_string(target_id, "target_id"),
        },
        "payload": _canonical_json_value(payload, "payload"),
    }
    rendered = json.dumps(
        canonical_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return _HASH_PREFIX + hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def idempotency_key_identity(idempotency_key: str) -> str:
    """Hash the caller-supplied idempotency key before persistence."""

    if not isinstance(idempotency_key, str) or not idempotency_key.strip():
        raise OperatorActionIdempotencyKeyError("idempotency key must be non-empty")
    return _HASH_PREFIX + hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()


def present_operator_action_receipt(
    receipt: OperatorActionReceipt,
) -> dict[str, Any]:
    """Render a receipt without credentials, request bodies or raw payloads."""

    return {
        "receipt_id": str(receipt.id),
        "correlation_id": str(receipt.correlation_id),
        "action": receipt.action,
        "target": {
            "type": receipt.target_type,
            "id": receipt.target_id,
        },
        "actor": {
            "type": receipt.created_by_type.value,
            "id": receipt.created_by_id,
        },
        "idempotency_key_identity": receipt.idempotency_key_identity,
        "request_fingerprint": receipt.request_fingerprint,
        "outcome": receipt.outcome.value,
        "result_code": receipt.result_code,
        "result_metadata": receipt.result_metadata,
        "before_status": receipt.before_status,
        "after_status": receipt.after_status,
        "created_at": receipt.created_at.isoformat(),
        "completed_at": receipt.completed_at.isoformat(),
    }


class OperatorActionGateway:
    """Reserve idempotency, run a command, and append a terminal receipt."""

    def __init__(
        self,
        db: Database,
        *,
        clock: Clock,
        receipt_id_factory: IdFactory = uuid4,
        correlation_id_factory: IdFactory = uuid4,
    ) -> None:
        self._db = db
        self._clock = clock
        self._receipt_id_factory = receipt_id_factory
        self._correlation_id_factory = correlation_id_factory

    def execute(
        self,
        envelope: OperatorActionEnvelope,
        command: Command,
    ) -> OperatorActionGatewayResult:
        """Execute ``command`` once for one idempotency key identity."""

        if not isinstance(envelope.created_by_type, ActorType):
            raise ValueError("created_by_type must be a server-resolved ActorType")
        key_identity = idempotency_key_identity(envelope.idempotency_key)
        try:
            return self._execute_owned(envelope, key_identity, command)
        except _ReservationConflict:
            return self._result_for_existing_reservation(envelope, key_identity)
        except _CommandFailed:
            return OperatorActionGatewayResult(
                status=OperatorActionGatewayStatus.FAILED,
                failure=OperatorActionFailure(OperatorActionFailureCode.COMMAND_FAILED),
            )
        except _ReceiptFailed:
            return OperatorActionGatewayResult(
                status=OperatorActionGatewayStatus.FAILED,
                failure=OperatorActionFailure(
                    OperatorActionFailureCode.RECEIPT_COMMIT_FAILED
                ),
            )
        except sa.exc.OperationalError:
            return OperatorActionGatewayResult(
                status=OperatorActionGatewayStatus.IN_PROGRESS,
                conflict=OperatorActionConflict(OperatorActionConflictCode.IN_PROGRESS),
            )

    def _execute_owned(
        self,
        envelope: OperatorActionEnvelope,
        key_identity: str,
        command: Command,
    ) -> OperatorActionGatewayResult:
        created_at = self._clock()
        _reject_naive_datetime(created_at, "OperatorActionReceipt", "created_at")
        receipt_id = self._receipt_id_factory()
        correlation_id = self._correlation_id_factory()
        receipt: OperatorActionReceipt | None = None

        with self._db.session() as session:
            try:
                with session.begin():
                    _add_operator_action_reservation(
                        session,
                        idempotency_key_identity=key_identity,
                        request_fingerprint=envelope.request_fingerprint,
                        receipt_id=receipt_id,
                        correlation_id=correlation_id,
                        action=envelope.action,
                        target_type=envelope.target_type,
                        target_id=envelope.target_id,
                        created_by_type=envelope.created_by_type.value,
                        created_by_id=envelope.created_by_id,
                        created_at=created_at,
                    )
                    try:
                        session.flush()
                    except sa.exc.IntegrityError as exc:
                        raise _ReservationConflict from exc

                    context = OperatorActionCommandContext(
                        session=session,
                        receipt_id=receipt_id,
                        correlation_id=correlation_id,
                        created_at=created_at,
                    )
                    try:
                        command_result = command(context)
                    except Exception as exc:
                        raise _CommandFailed from exc

                    try:
                        receipt = self._receipt_from_command_result(
                            envelope=envelope,
                            key_identity=key_identity,
                            receipt_id=receipt_id,
                            correlation_id=correlation_id,
                            created_at=created_at,
                            command_result=command_result,
                        )
                        _add_operator_action_receipt(session, receipt)
                        session.flush()
                    except (
                        sa.exc.SQLAlchemyError,
                        TypeError,
                        ValueError,
                        ValidationError,
                    ) as exc:
                        raise _ReceiptFailed from exc
            except (_ReservationConflict, _CommandFailed, _ReceiptFailed):
                raise

        assert receipt is not None
        return OperatorActionGatewayResult(
            status=OperatorActionGatewayStatus.EXECUTED,
            receipt=receipt,
        )

    def _receipt_from_command_result(
        self,
        *,
        envelope: OperatorActionEnvelope,
        key_identity: str,
        receipt_id: UUID,
        correlation_id: UUID,
        created_at: datetime,
        command_result: OperatorActionCommandResult,
    ) -> OperatorActionReceipt:
        completed_at = self._clock()
        _reject_naive_datetime(completed_at, "OperatorActionReceipt", "completed_at")
        return OperatorActionReceipt(
            id=receipt_id,
            correlation_id=correlation_id,
            action=envelope.action,
            target_type=envelope.target_type,
            target_id=envelope.target_id,
            created_by_type=envelope.created_by_type,
            created_by_id=envelope.created_by_id,
            idempotency_key_identity=key_identity,
            request_fingerprint=envelope.request_fingerprint,
            outcome=command_result.outcome,
            result_code=command_result.result_code,
            result_metadata=redact_operator_action_metadata(
                command_result.result_metadata
            ),
            before_status=command_result.before_status,
            after_status=command_result.after_status,
            created_at=created_at,
            completed_at=completed_at,
        )

    def _result_for_existing_reservation(
        self,
        envelope: OperatorActionEnvelope,
        key_identity: str,
    ) -> OperatorActionGatewayResult:
        with self._db.session() as session:
            key_row = _get_operator_action_reservation(session, key_identity)
            if key_row is None:
                return OperatorActionGatewayResult(
                    status=OperatorActionGatewayStatus.FAILED,
                    failure=OperatorActionFailure(
                        OperatorActionFailureCode.RESERVATION_UNAVAILABLE
                    ),
                )
            if key_row.request_fingerprint != envelope.request_fingerprint:
                return OperatorActionGatewayResult(
                    status=OperatorActionGatewayStatus.CONFLICT,
                    conflict=OperatorActionConflict(
                        code=OperatorActionConflictCode.IDEMPOTENCY_KEY_REUSED,
                        correlation_id=key_row.correlation_id,
                    ),
                )

            receipt = _get_operator_action_receipt_by_identity(session, key_identity)
            if receipt is None:
                return OperatorActionGatewayResult(
                    status=OperatorActionGatewayStatus.IN_PROGRESS,
                    conflict=OperatorActionConflict(
                        code=OperatorActionConflictCode.IN_PROGRESS,
                        correlation_id=key_row.correlation_id,
                    ),
                )

            return OperatorActionGatewayResult(
                status=OperatorActionGatewayStatus.REPLAYED,
                receipt=receipt,
            )


def redact_operator_action_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Return bounded JSON metadata with secret-shaped values redacted."""

    redacted = cast(dict[str, Any], _redact_json(metadata))
    rendered = _render_metadata(redacted)
    size = len(rendered.encode("utf-8"))
    if size <= MAX_OPERATOR_ACTION_METADATA_BYTES:
        return redacted
    return {"_truncated": True, "_original_bytes": size}


def _json_string(value: str, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise CanonicalFingerprintError(f"{path} must be a non-empty string")
    return value


def _canonical_json_value(value: Any, path: str) -> Any:
    if value is None or isinstance(value, str | bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalFingerprintError(f"{path} contains non-finite float")
        return value
    if isinstance(value, list):
        return [
            _canonical_json_value(item, f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    if isinstance(value, dict):
        normalised: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalFingerprintError(
                    f"{path} contains non-string object key {key!r}"
                )
            normalised[key] = _canonical_json_value(item, f"{path}.{key}")
        return normalised
    raise CanonicalFingerprintError(
        f"{path} contains unsupported value {type(value).__name__}"
    )


def _redact_json(value: Any) -> Any:
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        if _looks_secret_value(value):
            return _REDACTED
        if len(value.encode("utf-8")) > _MAX_METADATA_STRING_BYTES:
            return value.encode("utf-8")[:_MAX_METADATA_STRING_BYTES].decode(
                "utf-8", errors="ignore"
            )
        return value
    if isinstance(value, list):
        return [_redact_json(item) for item in value[:20]]
    if isinstance(value, Mapping):
        safe: dict[str, Any] = {}
        redacted_index = 0
        for raw_key, item in list(value.items())[:40]:
            key = str(raw_key)
            if _looks_secret_value(key):
                redacted_index += 1
                safe[f"_redacted_key_{redacted_index}"] = _REDACTED
                continue
            if _SECRET_KEY_RE.search(key) or _FORBIDDEN_PAYLOAD_KEY_RE.search(key):
                safe[key[:128]] = _REDACTED
                continue
            safe[key[:128]] = _redact_json(item)
        return safe
    return f"<{type(value).__name__}>"


def _looks_secret_value(value: str) -> bool:
    return any(pattern.search(value) for pattern in _SECRET_VALUE_RES)


def _render_metadata(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _reject_naive_datetime(value: datetime, model_name: str, field: str) -> None:
    if value.utcoffset() is None:
        raise NaiveDatetimeError(model_name, field)
