"""Idempotent command gateway for governed operator writes."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Protocol, TypeVar, cast
from uuid import UUID, uuid4

import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy.orm import Session

from atlas.core.enums import ActorType, EntityStatus
from atlas.core.models import (
    Evidence,
    OperatorActionOutcome,
    OperatorActionReceipt,
    OperatorActionResultCode,
)
from atlas.core.models.operator_action_receipt import (
    APPROVED_OPERATOR_ACTION_METADATA_FIELDS,
    OperatorActionMetadataKey,
    OperatorActionMetadataValue,
)
from atlas.storage import Database
from atlas.storage.repositories import (
    NaiveDatetimeError,
    _add_evidence,
    _add_operator_action_receipt,
    _add_operator_action_reservation,
    _get_operator_action_receipt_by_identity,
    _get_operator_action_reservation,
    compare_and_set_entity,
)

_HASH_PREFIX = "sha256:"
_Entity = TypeVar("_Entity")


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
    """Typed replay, ownership, and compare-and-set conflict reasons."""

    IDEMPOTENCY_KEY_REUSED = "idempotency_key_reused"
    IN_PROGRESS = "in_progress"
    STALE_STATE = "stale_state"


class OperatorActionFailureCode(StrEnum):
    """Typed failures that do not report mutation success."""

    COMMAND_FAILED = "command_failed"
    RECEIPT_COMMIT_FAILED = "receipt_commit_failed"
    STORAGE_FAILED = "storage_failed"


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
class OperatorActionEntityLoad:
    """One gateway-owned load to expose to a command as a detached value."""

    name: str
    entity_type: type[Any]
    entity_id: object
    for_update: bool = False


@dataclass(frozen=True)
class OperatorActionMutation:
    """One detached ORM mutation for the gateway to apply transactionally.

    Empty compare-and-set fields retain the original unconditional merge
    behaviour for commands without an observed-state contract.  Supplying
    both fields makes the repository update conditional at the SQL boundary.
    """

    entity: object
    expected_values: Mapping[str, object] = field(default_factory=dict)
    updated_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class OperatorActionCommandContext:
    """Session-free, detached inputs supplied to the injected command."""

    loaded_entities: Mapping[str, object]
    receipt_id: UUID
    correlation_id: UUID
    created_at: datetime
    created_by_type: ActorType
    created_by_id: str

    def entity(self, name: str, entity_type: type[_Entity]) -> _Entity | None:
        """Return one named detached value with a runtime type check."""

        entity = self.loaded_entities.get(name)
        if entity is None:
            return None
        if not isinstance(entity, entity_type):
            raise TypeError("loaded entity has an unexpected type")
        return entity


@dataclass(frozen=True)
class OperatorActionCommandResult:
    """Bounded terminal outcome returned by a domain command."""

    outcome: OperatorActionOutcome
    result_code: OperatorActionResultCode
    result_metadata: Mapping[str, Any] = field(default_factory=dict)
    before_status: EntityStatus | None = None
    after_status: EntityStatus | None = None
    mutations: tuple[OperatorActionMutation, ...] = ()
    evidence_appends: tuple[Evidence, ...] = ()


@dataclass(frozen=True)
class OperatorActionConflict:
    code: OperatorActionConflictCode
    correlation_id: UUID | None = None
    current_entity: object | None = None


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
    loaded_entities: Mapping[str, object] = field(default_factory=dict)


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


class _ReservationStorageFailed(Exception):
    pass


class _MutationStale(Exception):
    def __init__(self, entity_type: type[Any], entity_id: object) -> None:
        super().__init__("compare-and-set predicate did not match")
        self.entity_type = entity_type
        self.entity_id = entity_id


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

    receipt = OperatorActionReceipt.model_validate(
        {
            field_name: getattr(receipt, field_name)
            for field_name in OperatorActionReceipt.model_fields
        }
    )
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
        "result_metadata": dict(receipt.result_metadata),
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
        *,
        loads: tuple[OperatorActionEntityLoad, ...] = (),
    ) -> OperatorActionGatewayResult:
        """Execute ``command`` once for one idempotency key identity."""

        if not isinstance(envelope.created_by_type, ActorType):
            raise ValueError("created_by_type must be a server-resolved ActorType")
        key_identity = idempotency_key_identity(envelope.idempotency_key)
        try:
            return self._execute_owned(envelope, key_identity, command, loads)
        except _ReservationConflict:
            return self._result_for_existing_reservation(envelope, key_identity, loads)
        except _MutationStale as stale:
            return self._stale_result(stale)
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
        except _ReservationStorageFailed:
            return self._result_for_existing_reservation(
                envelope,
                key_identity,
                loads,
            )

    def _execute_owned(
        self,
        envelope: OperatorActionEnvelope,
        key_identity: str,
        command: Command,
        loads: tuple[OperatorActionEntityLoad, ...],
    ) -> OperatorActionGatewayResult:
        created_at = self._clock()
        _reject_naive_datetime(created_at, "OperatorActionReceipt", "created_at")
        receipt_id = self._receipt_id_factory()
        correlation_id = self._correlation_id_factory()
        receipt: OperatorActionReceipt | None = None
        reservation_owned = False

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
                    except sa.exc.SQLAlchemyError as exc:
                        raise _ReservationStorageFailed from exc

                    reservation_owned = True

                    try:
                        loaded_entities = _load_detached_entities(session, loads)
                        context = OperatorActionCommandContext(
                            loaded_entities=MappingProxyType(loaded_entities),
                            receipt_id=receipt_id,
                            correlation_id=correlation_id,
                            created_at=created_at,
                            created_by_type=envelope.created_by_type,
                            created_by_id=envelope.created_by_id,
                        )
                        command_result = command(context)
                        _apply_operator_action_mutations(
                            session, command_result.mutations
                        )
                        for evidence in command_result.evidence_appends:
                            _require_server_resolved_evidence_actor(evidence, context)
                            _add_evidence(session, evidence)
                        session.flush()
                    except _MutationStale:
                        raise
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
            except (
                _ReservationConflict,
                _ReservationStorageFailed,
                _CommandFailed,
                _ReceiptFailed,
                _MutationStale,
            ):
                raise
            except sa.exc.SQLAlchemyError as exc:
                if reservation_owned:
                    raise _ReceiptFailed from exc
                raise _ReservationStorageFailed from exc

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
            result_metadata=_approved_operator_action_metadata(
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
        loads: tuple[OperatorActionEntityLoad, ...],
    ) -> OperatorActionGatewayResult:
        try:
            with self._db.session() as session:
                key_row = _get_operator_action_reservation(session, key_identity)
                if key_row is None:
                    return _storage_failure_result()
                if key_row.request_fingerprint != envelope.request_fingerprint:
                    return OperatorActionGatewayResult(
                        status=OperatorActionGatewayStatus.CONFLICT,
                        conflict=OperatorActionConflict(
                            code=OperatorActionConflictCode.IDEMPOTENCY_KEY_REUSED,
                            correlation_id=key_row.correlation_id,
                        ),
                    )

                receipt = _get_operator_action_receipt_by_identity(
                    session, key_identity
                )
                if receipt is None:
                    return OperatorActionGatewayResult(
                        status=OperatorActionGatewayStatus.IN_PROGRESS,
                        conflict=OperatorActionConflict(
                            code=OperatorActionConflictCode.IN_PROGRESS,
                            correlation_id=key_row.correlation_id,
                        ),
                    )

                loaded_entities = _load_detached_entities(session, loads)
                return OperatorActionGatewayResult(
                    status=OperatorActionGatewayStatus.REPLAYED,
                    receipt=receipt,
                    loaded_entities=MappingProxyType(loaded_entities),
                )
        except sa.exc.SQLAlchemyError:
            return _storage_failure_result()

    def _stale_result(self, stale: _MutationStale) -> OperatorActionGatewayResult:
        """Reload the winner's safe current row after rolling back the loser."""

        try:
            with self._db.session() as session:
                current = session.get(stale.entity_type, stale.entity_id)
                if current is None:
                    return _storage_failure_result()
                session.expunge(current)
        except sa.exc.SQLAlchemyError:
            return _storage_failure_result()
        return OperatorActionGatewayResult(
            status=OperatorActionGatewayStatus.CONFLICT,
            conflict=OperatorActionConflict(
                code=OperatorActionConflictCode.STALE_STATE,
                current_entity=current,
            ),
        )


def _require_server_resolved_evidence_actor(
    evidence: Evidence,
    context: OperatorActionCommandContext,
) -> None:
    """Prevent an injected command from changing server-owned attribution."""

    if (
        evidence.created_by_type is not context.created_by_type
        or evidence.created_by_id != context.created_by_id
    ):
        raise ValueError("evidence actor must match server-resolved command actor")


def _approved_operator_action_metadata(
    metadata: Mapping[str, Any],
) -> dict[OperatorActionMetadataKey, OperatorActionMetadataValue]:
    """Select only structurally approved metadata without inspecting denials."""

    return cast(
        dict[OperatorActionMetadataKey, OperatorActionMetadataValue],
        {
            key: metadata[key]
            for key in APPROVED_OPERATOR_ACTION_METADATA_FIELDS
            if key in metadata
        },
    )


def _load_detached_entities(
    session: Session,
    loads: tuple[OperatorActionEntityLoad, ...],
) -> dict[str, object]:
    """Load declared command inputs without retaining or exposing ``session``."""

    loaded: dict[str, object] = {}
    for item in loads:
        if not item.name or item.name in loaded:
            raise ValueError("operator action load names must be unique and non-empty")
        entity = session.get(
            item.entity_type,
            item.entity_id,
            with_for_update=item.for_update,
        )
        if entity is not None:
            session.expunge(entity)
            loaded[item.name] = entity
    return loaded


def _apply_operator_action_mutations(
    session: Session,
    mutations: tuple[OperatorActionMutation, ...],
) -> None:
    """Validate and merge a command's detached mutation plan."""

    for mutation in mutations:
        state = sa.inspect(mutation.entity, raiseerr=False)
        if state is None:
            raise ValueError("operator action mutations must be mapped entities")
        if getattr(state, "session", None) is not None:
            raise ValueError("operator action mutations must be detached")
        uses_compare_and_set = bool(mutation.expected_values or mutation.updated_fields)
        if uses_compare_and_set:
            if not mutation.expected_values or not mutation.updated_fields:
                raise ValueError(
                    "compare-and-set requires predicates and updated fields"
                )
            if not compare_and_set_entity(
                session,
                mutation.entity,
                expected_values=mutation.expected_values,
                updated_fields=mutation.updated_fields,
            ):
                mapper = state.mapper
                if len(mapper.primary_key) != 1:
                    raise ValueError("compare-and-set requires one primary-key column")
                primary_key = mapper.primary_key[0]
                raise _MutationStale(
                    type(mutation.entity),
                    getattr(mutation.entity, primary_key.key),
                )
            continue
        session.merge(mutation.entity)


def _storage_failure_result() -> OperatorActionGatewayResult:
    return OperatorActionGatewayResult(
        status=OperatorActionGatewayStatus.FAILED,
        failure=OperatorActionFailure(OperatorActionFailureCode.STORAGE_FAILED),
    )


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


def _reject_naive_datetime(value: datetime, model_name: str, field: str) -> None:
    if value.utcoffset() is None:
        raise NaiveDatetimeError(model_name, field)
