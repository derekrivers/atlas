"""Reusable restart and fault-injection primitives for PM resilience tests.

This module deliberately models infrastructure mechanics, not PM production
behaviour.  A test supplies the workflow policy while the harness supplies a
durable SQLite store, reconstructable process generations, provider worlds,
fault boundaries, and assertions over externally visible writes.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import tempfile
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, cast

JsonObject = dict[str, Any]
ReadonlyJsonObject = Mapping[str, object]
OperationHandler = Callable[[ReadonlyJsonObject, ReadonlyJsonObject], Mapping[str, Any]]


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _clone_json_object(value: object) -> JsonObject:
    decoded = json.loads(_canonical_json(value))
    if not isinstance(decoded, dict):
        raise TypeError("expected a JSON object")
    return cast(JsonObject, decoded)


def _freeze_json(value: object) -> object:
    """Deep-clone JSON into containers a provider handler cannot mutate."""
    cloned = json.loads(_canonical_json(value))

    def freeze(item: object) -> object:
        if isinstance(item, dict):
            return MappingProxyType(
                {str(key): freeze(child) for key, child in item.items()}
            )
        if isinstance(item, list):
            return tuple(freeze(child) for child in item)
        return item

    return freeze(cloned)


class SimulatedProcessDeath(BaseException):
    """A fatal boundary crossing that ordinary ``except Exception`` misses."""


class FaultPoint(StrEnum):
    BEFORE_PROVIDER_CALL = "before_provider_call"
    AFTER_EFFECT_BEFORE_RETURN = "after_effect_before_return"
    AFTER_RETURN_BEFORE_LOCAL_ACK = "after_return_before_local_ack"
    BEFORE_DURABLE_ACK = "before_durable_ack"
    AFTER_DURABLE_ACK = "after_durable_ack"
    BEFORE_RECEIPT = "before_receipt"
    AFTER_RECEIPT = "after_receipt"


class TypedHold(Exception):
    """A safe, inspectable stop outcome rather than an untyped timeout."""

    def __init__(self, code: str, subject: str, *, retryable: bool = True) -> None:
        self.code = code
        self.subject = subject
        self.retryable = retryable
        super().__init__(f"{code}: {subject}")


class IdempotencyConflict(TypedHold):
    def __init__(self, effect_identity: str) -> None:
        super().__init__("idempotency-conflict", effect_identity, retryable=False)


class DurableReplayConflict(TypedHold):
    def __init__(self, subject: str) -> None:
        super().__init__("durable-replay-conflict", subject, retryable=False)


class DeterministicClock:
    def __init__(self, initial: datetime | None = None) -> None:
        value = initial or datetime(2026, 1, 1, tzinfo=UTC)
        if value.tzinfo is None:
            raise ValueError("deterministic clock requires a timezone-aware value")
        self._now = value

    def __call__(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> datetime:
        if delta < timedelta(0):
            raise ValueError("deterministic clock cannot move backwards")
        self._now += delta
        return self._now


@dataclass(frozen=True, init=False)
class ExternalRequest:
    """One exact request plus its logical provider-effect identity."""

    provider: str
    operation: str
    resource: str
    payload_json: str
    idempotency_key: str | None

    def __init__(
        self,
        *,
        provider: str,
        operation: str,
        resource: str,
        payload: Mapping[str, Any],
        idempotency_key: str | None = None,
    ) -> None:
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "operation", operation)
        object.__setattr__(self, "resource", resource)
        object.__setattr__(self, "payload_json", _canonical_json(payload))
        object.__setattr__(self, "idempotency_key", idempotency_key)

    @property
    def payload(self) -> JsonObject:
        value = json.loads(self.payload_json)
        if not isinstance(value, dict):  # pragma: no cover - constructor guarantees it
            raise TypeError("external request payload is not an object")
        return value

    @property
    def request_fingerprint(self) -> str:
        canonical = _canonical_json(
            {
                "idempotency_key": self.idempotency_key,
                "operation": self.operation,
                "payload": json.loads(self.payload_json),
                "provider": self.provider,
                "resource": self.resource,
            }
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    @property
    def effect_identity(self) -> str:
        if self.idempotency_key is None:
            return self.request_fingerprint
        canonical = _canonical_json(
            {
                "idempotency_key": self.idempotency_key,
                "operation": self.operation,
                "provider": self.provider,
                "resource": self.resource,
            }
        )
        return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True)
class ExternalResult:
    request_fingerprint: str
    effect_identity: str
    value: JsonObject
    applied: bool


@dataclass(frozen=True)
class ExternalWriteEvent:
    sequence: int
    kind: Literal["attempt", "effect"]
    tick_id: str
    request_fingerprint: str
    effect_identity: str
    provider: str
    operation: str
    resource: str


class ExternalWriteLedger:
    """Process-independent record separating calls from provider effects."""

    def __init__(self) -> None:
        self.events: list[ExternalWriteEvent] = []

    def record(
        self,
        kind: Literal["attempt", "effect"],
        *,
        tick_id: str,
        request: ExternalRequest,
    ) -> None:
        self.events.append(
            ExternalWriteEvent(
                sequence=len(self.events) + 1,
                kind=kind,
                tick_id=tick_id,
                request_fingerprint=request.request_fingerprint,
                effect_identity=request.effect_identity,
                provider=request.provider,
                operation=request.operation,
                resource=request.resource,
            )
        )

    def count(
        self, kind: Literal["attempt", "effect"], request_fingerprint: str
    ) -> int:
        return sum(
            event.kind == kind and event.request_fingerprint == request_fingerprint
            for event in self.events
        )

    def effects_in_tick(self, tick_id: str) -> int:
        return sum(
            event.kind == "effect" and event.tick_id == tick_id for event in self.events
        )

    def assert_counts(
        self, request_fingerprint: str, *, attempts: int, effects: int
    ) -> None:
        assert self.count("attempt", request_fingerprint) == attempts
        assert self.count("effect", request_fingerprint) == effects

    def assert_no_duplicate_harmful_effects(self) -> None:
        counts = Counter(
            event.effect_identity for event in self.events if event.kind == "effect"
        )
        assert all(count == 1 for count in counts.values()), counts

    def assert_at_most_one_workflow_effect_per_tick(self) -> None:
        counts = Counter(
            event.tick_id for event in self.events if event.kind == "effect"
        )
        assert all(count <= 1 for count in counts.values()), counts


class MutableProviderWorld:
    """Linear/GitHub-like state that outlives every simulated process."""

    def __init__(self) -> None:
        self.ledger = ExternalWriteLedger()
        self._resources: dict[str, dict[str, JsonObject]] = {}
        self._handlers: dict[tuple[str, str], OperationHandler] = {}
        self._results: dict[str, tuple[str, JsonObject]] = {}

    def register_operation(
        self, provider: str, operation: str, handler: OperationHandler
    ) -> None:
        self._handlers[(provider, operation)] = handler

    def set_resource(
        self, provider: str, resource: str, value: Mapping[str, Any]
    ) -> None:
        """Advance authoritative external state without simulating an Atlas write."""
        self._resources.setdefault(provider, {})[resource] = _clone_json_object(value)

    def resource(self, provider: str, resource: str) -> JsonObject:
        value = self._resources.setdefault(provider, {}).setdefault(resource, {})
        return _clone_json_object(value)

    def would_create_effect(self, request: ExternalRequest) -> bool:
        if request.idempotency_key is None:
            return True
        return request.effect_identity not in self._results

    def execute(
        self,
        request: ExternalRequest,
        *,
        tick_id: str,
        after_effect: Callable[[], None],
    ) -> ExternalResult:
        self.ledger.record("attempt", tick_id=tick_id, request=request)
        prior = self._results.get(request.effect_identity)
        if request.idempotency_key is not None and prior is not None:
            prior_fingerprint, prior_result = prior
            if prior_fingerprint != request.request_fingerprint:
                raise IdempotencyConflict(request.effect_identity)
            return ExternalResult(
                request.request_fingerprint,
                request.effect_identity,
                _clone_json_object(prior_result),
                applied=False,
            )

        current = self._resources.setdefault(request.provider, {}).setdefault(
            request.resource, {}
        )
        handler = self._handlers.get((request.provider, request.operation))
        proposed: Mapping[str, Any]
        if handler is None:
            if request.operation != "patch":
                raise KeyError(
                    f"no provider handler for {request.provider}:{request.operation}"
                )
            default_proposed = _clone_json_object(current)
            default_proposed.update(request.payload)
            proposed = default_proposed
        else:
            frozen_current = cast(ReadonlyJsonObject, _freeze_json(current))
            frozen_payload = cast(ReadonlyJsonObject, _freeze_json(request.payload))
            proposed = handler(frozen_current, frozen_payload)

        stable_result = _clone_json_object(proposed)
        self._resources[request.provider][request.resource] = _clone_json_object(
            stable_result
        )
        if request.idempotency_key is not None:
            self._results[request.effect_identity] = (
                request.request_fingerprint,
                _clone_json_object(stable_result),
            )
        self.ledger.record("effect", tick_id=tick_id, request=request)
        after_effect()
        return ExternalResult(
            request.request_fingerprint,
            request.effect_identity,
            _clone_json_object(stable_result),
            applied=True,
        )


@dataclass
class _FaultRule:
    point: FaultPoint
    request_fingerprint: str | None
    remaining: int
    error_factory: Callable[[], BaseException]


class FaultInjector:
    def __init__(self) -> None:
        self._rules: list[_FaultRule] = []

    def arm(
        self,
        point: FaultPoint,
        *,
        request_fingerprint: str | None = None,
        times: int = 1,
        error_factory: Callable[[], BaseException] = SimulatedProcessDeath,
    ) -> None:
        if times < 1:
            raise ValueError("fault count must be positive")
        self._rules.append(_FaultRule(point, request_fingerprint, times, error_factory))

    def trip(self, point: FaultPoint, request_fingerprint: str) -> None:
        for rule in self._rules:
            if (
                rule.remaining
                and rule.point == point
                and rule.request_fingerprint in (None, request_fingerprint)
            ):
                rule.remaining -= 1
                raise rule.error_factory()


@dataclass(frozen=True)
class _ResourceSpec:
    factory: Callable[[ProcessGeneration], object]
    disposer: Callable[[object], None] | None


class ProcessGeneration:
    """All process-scoped objects; closing it disposes every one of them."""

    def __init__(self, harness: TemporalHarness, generation_id: int) -> None:
        self.harness = harness
        self.generation_id = generation_id
        self.connection = sqlite3.connect(harness.db_path)
        self.connection.row_factory = sqlite3.Row
        self._resources: dict[str, tuple[object, Callable[[object], None] | None]] = {}
        self._closed = False
        try:
            for name, spec in harness._resource_specs.items():
                resource = spec.factory(self)
                self._resources[name] = (resource, spec.disposer)
        except BaseException:
            self.close(suppress_errors=True)
            raise

    def __enter__(self) -> ProcessGeneration:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close(suppress_errors=exc is not None)

    def resource(self, name: str) -> object:
        return self._resources[name][0]

    def tick(self, tick_id: str) -> WorkflowTick:
        return WorkflowTick(self, tick_id)

    def is_acknowledged(self, request_fingerprint: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM temporal_acknowledgements WHERE request_fingerprint = ?",
            (request_fingerprint,),
        ).fetchone()
        return row is not None

    def acknowledge_external(
        self, request: ExternalRequest, result: ExternalResult, *, tick_id: str
    ) -> None:
        fingerprint = request.request_fingerprint
        if (
            result.request_fingerprint != fingerprint
            or result.effect_identity != request.effect_identity
        ):
            raise DurableReplayConflict(f"ack-identity:{fingerprint}")
        result_json = _canonical_json(result.value)
        expected = (
            request.effect_identity,
            request.provider,
            request.operation,
            request.resource,
            result_json,
        )
        self.harness.faults.trip(FaultPoint.BEFORE_DURABLE_ACK, fingerprint)
        existing = self.connection.execute(
            """
            SELECT effect_identity, provider, operation, resource, result_json
            FROM temporal_acknowledgements WHERE request_fingerprint = ?
            """,
            (fingerprint,),
        ).fetchone()
        if existing is None:
            self.connection.execute(
                """
                INSERT INTO temporal_acknowledgements (
                    request_fingerprint, effect_identity, provider, operation,
                    resource, result_json, tick_id, acknowledged_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fingerprint,
                    *expected,
                    tick_id,
                    self.harness.clock().isoformat(),
                ),
            )
            self.connection.commit()
        elif tuple(existing) != expected:
            raise DurableReplayConflict(f"ack-replay:{fingerprint}")
        self.harness.faults.trip(FaultPoint.AFTER_DURABLE_ACK, fingerprint)

    def has_receipt(self, request_fingerprint: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM temporal_receipts WHERE request_fingerprint = ?",
            (request_fingerprint,),
        ).fetchone()
        return row is not None

    def record_receipt(
        self,
        request_fingerprint: str,
        *,
        tick_id: str,
        status: str,
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        payload_json = _canonical_json(payload or {})
        expected = (status, payload_json)
        self.harness.faults.trip(FaultPoint.BEFORE_RECEIPT, request_fingerprint)
        existing = self.connection.execute(
            """
            SELECT status, payload_json FROM temporal_receipts
            WHERE request_fingerprint = ?
            """,
            (request_fingerprint,),
        ).fetchone()
        if existing is None:
            self.connection.execute(
                """
                INSERT INTO temporal_receipts (
                    request_fingerprint, tick_id, status, payload_json, recorded_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    request_fingerprint,
                    tick_id,
                    status,
                    payload_json,
                    self.harness.clock().isoformat(),
                ),
            )
            self.connection.commit()
        elif tuple(existing) != expected:
            raise DurableReplayConflict(f"receipt-replay:{request_fingerprint}")
        self.harness.faults.trip(FaultPoint.AFTER_RECEIPT, request_fingerprint)

    def close(self, *, suppress_errors: bool = False) -> None:
        if self._closed:
            return
        errors: list[BaseException] = []
        for resource, disposer in reversed(self._resources.values()):
            try:
                if disposer is not None:
                    disposer(resource)
                else:
                    close = getattr(resource, "close", None)
                    if callable(close):
                        close()
            except BaseException as error:
                errors.append(error)
        self._resources.clear()
        try:
            self.connection.close()
        except BaseException as error:
            errors.append(error)
        self._closed = True
        self.harness._generation_closed(self)
        if errors and not suppress_errors:
            raise errors[0]


class WorkflowTick:
    def __init__(self, generation: ProcessGeneration, tick_id: str) -> None:
        self.generation = generation
        self.tick_id = tick_id

    def external_write(self, request: ExternalRequest) -> ExternalResult:
        harness = self.generation.harness
        if (
            harness.providers.would_create_effect(request)
            and harness.providers.ledger.effects_in_tick(self.tick_id)
            >= harness.max_workflow_effects_per_tick
        ):
            raise TypedHold("workflow-effect-limit", self.tick_id)

        fingerprint = request.request_fingerprint
        harness.faults.trip(FaultPoint.BEFORE_PROVIDER_CALL, fingerprint)
        result = harness.providers.execute(
            request,
            tick_id=self.tick_id,
            after_effect=lambda: harness.faults.trip(
                FaultPoint.AFTER_EFFECT_BEFORE_RETURN, fingerprint
            ),
        )
        harness.faults.trip(FaultPoint.AFTER_RETURN_BEFORE_LOCAL_ACK, fingerprint)
        return result


@dataclass(frozen=True)
class ConvergenceResult:
    outcome: Literal["converged", "held"]
    ticks: int
    hold: TypedHold | None = None


class TemporalHarness:
    """Own durable test state and reconstruct complete process generations."""

    def __init__(
        self,
        *,
        db_path: Path | None = None,
        initial_time: datetime | None = None,
        max_workflow_effects_per_tick: int = 1,
    ) -> None:
        if max_workflow_effects_per_tick < 1:
            raise ValueError("workflow effect limit must be positive")
        if db_path is None:
            self._temporary_root: Path | None = Path(
                tempfile.mkdtemp(prefix="atlas-pm-temporal-")
            )
            self.db_path = self._temporary_root / "pm-temporal.sqlite3"
        else:
            self._temporary_root = None
            self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = DeterministicClock(initial_time)
        self.providers = MutableProviderWorld()
        self.faults = FaultInjector()
        self.max_workflow_effects_per_tick = max_workflow_effects_per_tick
        self._resource_specs: dict[str, _ResourceSpec] = {}
        self._active_generation: ProcessGeneration | None = None
        self._generation_count = 0
        self._closed = False
        self._initialize_store()

    def __enter__(self) -> TemporalHarness:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close(suppress_errors=exc is not None)

    @property
    def generation_count(self) -> int:
        return self._generation_count

    def register_generation_resource(
        self,
        name: str,
        factory: Callable[[ProcessGeneration], object],
        *,
        disposer: Callable[[object], None] | None = None,
    ) -> None:
        if self._generation_count > 0:
            raise RuntimeError("register generation resources before process creation")
        if name in self._resource_specs:
            raise ValueError(f"duplicate generation resource: {name}")
        self._resource_specs[name] = _ResourceSpec(factory, disposer)

    def new_generation(self) -> ProcessGeneration:
        if self._closed:
            raise RuntimeError("temporal harness is closed")
        if self._active_generation is not None:
            raise RuntimeError(
                "dispose the active process generation before rebuilding"
            )
        self._generation_count += 1
        generation = ProcessGeneration(self, self._generation_count)
        self._active_generation = generation
        return generation

    def restart(self) -> ProcessGeneration:
        if self._active_generation is not None:
            self._active_generation.close()
        return self.new_generation()

    def run_until_converged_or_held(
        self,
        step: Callable[[WorkflowTick], bool],
        *,
        max_ticks: int,
        tick_interval: timedelta = timedelta(seconds=1),
    ) -> ConvergenceResult:
        """Rebuild per tick; retry retryable holds and return the final bound hold.

        A non-retryable hold returns immediately.  A retryable hold advances the
        deterministic clock and reconstructs the next process; if it is still
        present at ``max_ticks``, that final typed hold is returned.  Exhausting
        the bound with only ordinary non-progress raises ``AssertionError``.
        """
        if max_ticks < 1:
            raise ValueError("max_ticks must be positive")
        for tick_number in range(1, max_ticks + 1):
            try:
                with self.new_generation() as generation:
                    if step(generation.tick(f"tick-{tick_number}")):
                        return ConvergenceResult("converged", tick_number)
            except TypedHold as hold:
                if not hold.retryable or tick_number == max_ticks:
                    return ConvergenceResult("held", tick_number, hold)
            if tick_number < max_ticks:
                self.clock.advance(tick_interval)
        raise AssertionError(
            f"workflow did not converge or hold within {max_ticks} ticks"
        )

    def durable_rows(
        self, table: Literal["temporal_acknowledgements", "temporal_receipts"]
    ) -> list[JsonObject]:
        if not re.fullmatch(r"[a-z_]+", table):  # defensive if typing is bypassed
            raise ValueError("invalid durable table name")
        with sqlite3.connect(self.db_path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(f"SELECT * FROM {table}").fetchall()
        return [dict(row) for row in rows]

    def assert_durable_row(
        self,
        table: Literal["temporal_acknowledgements", "temporal_receipts"],
        **expected: object,
    ) -> None:
        rows = self.durable_rows(table)
        matches = [
            row
            for row in rows
            if all(row.get(key) == value for key, value in expected.items())
        ]
        assert len(matches) == 1, {"expected": expected, "rows": rows}

    def close(self, *, suppress_errors: bool = False) -> None:
        if self._closed:
            return
        errors: list[BaseException] = []
        if self._active_generation is not None:
            try:
                self._active_generation.close(suppress_errors=False)
            except BaseException as error:
                errors.append(error)
        if self._temporary_root is not None:
            try:
                shutil.rmtree(self._temporary_root)
            except BaseException as error:
                errors.append(error)
        self._closed = True
        if errors and not suppress_errors:
            raise errors[0]

    def _generation_closed(self, generation: ProcessGeneration) -> None:
        if self._active_generation is generation:
            self._active_generation = None

    def _initialize_store(self) -> None:
        with sqlite3.connect(self.db_path) as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS temporal_acknowledgements (
                    request_fingerprint TEXT PRIMARY KEY,
                    effect_identity TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    resource TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    tick_id TEXT NOT NULL,
                    acknowledged_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS temporal_receipts (
                    request_fingerprint TEXT PRIMARY KEY,
                    tick_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                );
                """
            )
