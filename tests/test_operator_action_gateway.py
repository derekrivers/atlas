"""Operator action gateway idempotency, transactions and redaction."""

from __future__ import annotations

import json
import math
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session
from test_models_validation import product_kwargs
from test_operator_action_receipt_model import operator_action_receipt_kwargs

from atlas.core.enums import ActorType, EntityStatus
from atlas.core.models import OperatorActionOutcome, OperatorActionReceipt, Product
from atlas.orchestration import (
    CanonicalFingerprintError,
    OperatorActionCommandContext,
    OperatorActionCommandResult,
    OperatorActionConflictCode,
    OperatorActionEnvelope,
    OperatorActionFailureCode,
    OperatorActionGateway,
    OperatorActionGatewayStatus,
    canonical_request_fingerprint,
    idempotency_key_identity,
    present_operator_action_receipt,
)
from atlas.storage import Database, OperatorActionReceiptRepo, ProductRepo
from atlas.storage.tables import (
    OperatorActionKeyRow,
    OperatorActionReceiptRow,
    ProductRow,
)

NOW = datetime(2026, 8, 2, 13, tzinfo=UTC)


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{tmp_path}/atlas.db")
    database.create_all()
    return database


class FrozenClock:
    def __call__(self) -> datetime:
        return NOW


class UUIDSequence:
    def __init__(self, *values: UUID) -> None:
        self._values = iter(values)
        self._lock = threading.Lock()

    def __call__(self) -> UUID:
        with self._lock:
            return next(self._values)


def envelope(
    *,
    key: str = "idem-key-1",
    action: str = "product.deprecate",
    target_type: str = "product",
    target_id: str = "ATLAS",
    payload: dict[str, Any] | None = None,
) -> OperatorActionEnvelope:
    body = {"reason": "operator requested"} if payload is None else payload
    return OperatorActionEnvelope(
        action=action,
        target_type=target_type,
        target_id=target_id,
        created_by_type=ActorType.HUMAN,
        created_by_id="operator",
        idempotency_key=key,
        request_fingerprint=canonical_request_fingerprint(
            action=action,
            target_type=target_type,
            target_id=target_id,
            payload=body,
        ),
    )


def seed_product(db: Database, **overrides: Any) -> Product:
    product = Product(
        **product_kwargs()
        | {"id": uuid4(), "key": "ATLAS", "status": "active"}
        | overrides
    )
    return ProductRepo(db).add(product)


def mutate_product_command(
    product_id: UUID,
    calls: list[str],
    *,
    status: str = "deprecated",
) -> Any:
    def _command(context: OperatorActionCommandContext) -> OperatorActionCommandResult:
        calls.append(str(context.correlation_id))
        row = context.unit_of_work.get(ProductRow, product_id)
        assert row is not None
        before = row.status
        row.status = status
        return OperatorActionCommandResult(
            outcome=OperatorActionOutcome.SUCCEEDED,
            result_code="product_deprecated",
            result_metadata={"changed": True},
            before_status=before,
            after_status=status,
        )

    return _command


def seed_in_progress_reservation(db: Database, request: OperatorActionEnvelope) -> UUID:
    correlation_id = uuid4()
    with db.session() as session, session.begin():
        session.add(
            OperatorActionKeyRow(
                idempotency_key_identity=idempotency_key_identity(
                    request.idempotency_key
                ),
                request_fingerprint=request.request_fingerprint,
                receipt_id=uuid4(),
                correlation_id=correlation_id,
                action=request.action,
                target_type=request.target_type,
                target_id=request.target_id,
                created_by_type=request.created_by_type.value,
                created_by_id=request.created_by_id,
                created_at=NOW,
            )
        )
    return correlation_id


def seed_terminal_receipt(db: Database, receipt: OperatorActionReceipt) -> None:
    with db.session() as session, session.begin():
        session.add(
            OperatorActionKeyRow(
                idempotency_key_identity=receipt.idempotency_key_identity,
                request_fingerprint=receipt.request_fingerprint,
                receipt_id=receipt.id,
                correlation_id=receipt.correlation_id,
                action=receipt.action,
                target_type=receipt.target_type,
                target_id=receipt.target_id,
                created_by_type=receipt.created_by_type.value,
                created_by_id=receipt.created_by_id,
                created_at=receipt.created_at,
            )
        )
        payload = receipt.model_dump()
        payload["result_metadata"] = receipt.model_dump(mode="json")["result_metadata"]
        session.add(OperatorActionReceiptRow(**payload))


def test_fingerprint_is_stable_across_json_key_order() -> None:
    first = canonical_request_fingerprint(
        action="lesson.promote",
        target_type="lesson",
        target_id="abc",
        payload={"confidence": 0.8, "metadata": {"b": 2, "a": 1}},
    )
    second = canonical_request_fingerprint(
        action="lesson.promote",
        target_type="lesson",
        target_id="abc",
        payload={"metadata": {"a": 1, "b": 2}, "confidence": 0.8},
    )

    assert first == second


@pytest.mark.parametrize(
    "changed",
    [
        {"action": "lesson.reject"},
        {"target_type": "ticket"},
        {"target_id": "def"},
        {"payload": {"confidence": 0.8, "reason": None}},
        {"payload": {"confidence": 0.9}},
        {"payload": {"confidence": 0.8, "created_by_id": "spoofed"}},
    ],
    ids=[
        "action",
        "target_type",
        "target_id",
        "omitted-field",
        "payload-value",
        "actor-shaped-payload-field",
    ],
)
def test_fingerprint_changes_when_semantic_command_identity_changes(
    changed: dict[str, Any],
) -> None:
    action = changed.get("action", "lesson.promote")
    target_type = changed.get("target_type", "lesson")
    target_id = changed.get("target_id", "abc")
    payload = changed.get("payload", {"confidence": 0.8})

    assert canonical_request_fingerprint(
        action="lesson.promote",
        target_type="lesson",
        target_id="abc",
        payload={"confidence": 0.8},
    ) != canonical_request_fingerprint(
        action=action,
        target_type=target_type,
        target_id=target_id,
        payload=payload,
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"confidence": math.nan},
        {"confidence": math.inf},
        {"confidence": -math.inf},
        {"unsupported": ("tuple",)},
        {1: "non-string key"},
    ],
    ids=["nan", "inf", "negative-inf", "tuple", "non-string-key"],
)
def test_fingerprint_rejects_unsupported_or_non_finite_payload_values(
    payload: dict[Any, Any],
) -> None:
    with pytest.raises(CanonicalFingerprintError):
        canonical_request_fingerprint(
            action="lesson.promote",
            target_type="lesson",
            target_id="abc",
            payload=payload,
        )


def test_same_key_same_fingerprint_replays_terminal_success_without_command(
    db: Database,
) -> None:
    product = seed_product(db)
    calls: list[str] = []
    request = envelope(target_id=product.key)
    gateway = OperatorActionGateway(
        db,
        clock=FrozenClock(),
        receipt_id_factory=UUIDSequence(
            UUID("10000000-0000-4000-8000-000000000001"),
            UUID("10000000-0000-4000-8000-000000000002"),
        ),
        correlation_id_factory=UUIDSequence(
            UUID("20000000-0000-4000-8000-000000000001"),
            UUID("20000000-0000-4000-8000-000000000002"),
        ),
    )

    first = gateway.execute(request, mutate_product_command(product.id, calls))
    second = gateway.execute(
        request,
        mutate_product_command(product.id, calls, status="archived"),
    )

    assert first.status is OperatorActionGatewayStatus.EXECUTED
    assert second.status is OperatorActionGatewayStatus.REPLAYED
    assert first.receipt == second.receipt
    assert calls == ["20000000-0000-4000-8000-000000000001"]
    stored = ProductRepo(db).get(product.id)
    assert stored is not None
    assert stored.status is EntityStatus.DEPRECATED


def test_same_key_different_fingerprint_returns_typed_conflict_without_command(
    db: Database,
) -> None:
    product = seed_product(db)
    calls: list[str] = []
    gateway = OperatorActionGateway(
        db,
        clock=FrozenClock(),
        receipt_id_factory=uuid4,
        correlation_id_factory=uuid4,
    )
    first = envelope(key="idem-key-conflict", target_id=product.key)
    changed = envelope(
        key="idem-key-conflict",
        target_id=product.key,
        payload={"reason": "different command"},
    )

    assert gateway.execute(first, mutate_product_command(product.id, calls)).receipt
    conflict = gateway.execute(changed, mutate_product_command(product.id, calls))

    assert conflict.status is OperatorActionGatewayStatus.CONFLICT
    assert conflict.conflict is not None
    assert conflict.conflict.code is OperatorActionConflictCode.IDEMPOTENCY_KEY_REUSED
    assert len(calls) == 1


def test_terminal_refusal_is_recorded_and_replayed_without_success_masquerade(
    db: Database,
) -> None:
    calls = 0
    request = envelope(key="idem-refusal", payload={"expected": "draft"})
    gateway = OperatorActionGateway(db, clock=FrozenClock())

    def refuse(_: OperatorActionCommandContext) -> OperatorActionCommandResult:
        nonlocal calls
        calls += 1
        return OperatorActionCommandResult(
            outcome=OperatorActionOutcome.REFUSED,
            result_code="stale_state",
            before_status="active",
        )

    first = gateway.execute(request, refuse)
    second = gateway.execute(request, refuse)

    assert first.status is OperatorActionGatewayStatus.EXECUTED
    assert first.receipt is not None
    assert first.receipt.outcome is OperatorActionOutcome.REFUSED
    assert second.status is OperatorActionGatewayStatus.REPLAYED
    assert second.receipt == first.receipt
    assert calls == 1


def test_in_progress_recovery_returns_named_conflict_and_invokes_no_command(
    db: Database,
) -> None:
    request = envelope(key="idem-in-progress")
    correlation_id = seed_in_progress_reservation(db, request)
    gateway = OperatorActionGateway(db, clock=FrozenClock())
    calls = 0

    def command(_: OperatorActionCommandContext) -> OperatorActionCommandResult:
        nonlocal calls
        calls += 1
        return OperatorActionCommandResult(
            outcome=OperatorActionOutcome.SUCCEEDED,
            result_code="should_not_run",
        )

    result = gateway.execute(request, command)

    assert result.status is OperatorActionGatewayStatus.IN_PROGRESS
    assert result.conflict is not None
    assert result.conflict.code is OperatorActionConflictCode.IN_PROGRESS
    assert result.conflict.correlation_id == correlation_id
    assert calls == 0


def test_mutation_failure_rolls_back_state_and_leaves_no_replayable_success(
    db: Database,
) -> None:
    product = seed_product(db)
    request = envelope(key="idem-command-failure", target_id=product.key)
    gateway = OperatorActionGateway(db, clock=FrozenClock())

    def failing_command(
        context: OperatorActionCommandContext,
    ) -> OperatorActionCommandResult:
        row = context.unit_of_work.get(ProductRow, product.id)
        assert row is not None
        row.status = EntityStatus.DEPRECATED.value
        raise RuntimeError("domain mutation failed")

    result = gateway.execute(request, failing_command)

    assert result.status is OperatorActionGatewayStatus.FAILED
    assert result.failure is not None
    assert result.failure.code is OperatorActionFailureCode.COMMAND_FAILED
    stored = ProductRepo(db).get(product.id)
    assert stored is not None
    assert stored.status is EntityStatus.ACTIVE
    assert (
        OperatorActionReceiptRepo(db).get_by_idempotency_key_identity(
            idempotency_key_identity(request.idempotency_key)
        )
        is None
    )


@pytest.mark.parametrize("lifecycle_method", ["commit", "rollback", "close", "flush"])
def test_command_cannot_end_or_flush_gateway_owned_transaction(
    db: Database,
    lifecycle_method: str,
) -> None:
    product = seed_product(db)
    request = envelope(key=f"idem-premature-{lifecycle_method}", target_id=product.key)
    gateway = OperatorActionGateway(db, clock=FrozenClock())

    def escaping_command(
        context: OperatorActionCommandContext,
    ) -> OperatorActionCommandResult:
        row = context.unit_of_work.get(ProductRow, product.id)
        assert row is not None
        row.status = EntityStatus.DEPRECATED.value
        getattr(context.unit_of_work, lifecycle_method)()
        return OperatorActionCommandResult(
            outcome=OperatorActionOutcome.SUCCEEDED,
            result_code="must_not_commit",
        )

    result = gateway.execute(request, escaping_command)

    assert result.status is OperatorActionGatewayStatus.FAILED
    assert result.failure is not None
    assert result.failure.code is OperatorActionFailureCode.COMMAND_FAILED
    stored = ProductRepo(db).get(product.id)
    assert stored is not None
    assert stored.status is EntityStatus.ACTIVE
    with db.session() as session:
        assert (
            session.get(
                OperatorActionKeyRow,
                idempotency_key_identity(request.idempotency_key),
            )
            is None
        )


def test_receipt_insert_failure_rolls_back_mutation_and_leaves_no_success(
    db: Database,
) -> None:
    product = seed_product(db)
    duplicate_receipt_id = UUID("30000000-0000-4000-8000-000000000001")
    existing = OperatorActionReceipt(
        **operator_action_receipt_kwargs()
        | {
            "id": duplicate_receipt_id,
            "correlation_id": UUID("40000000-0000-4000-8000-000000000001"),
            "idempotency_key_identity": idempotency_key_identity("already-used"),
        }
    )
    seed_terminal_receipt(db, existing)
    request = envelope(key="idem-receipt-failure", target_id=product.key)
    gateway = OperatorActionGateway(
        db,
        clock=FrozenClock(),
        receipt_id_factory=UUIDSequence(duplicate_receipt_id),
        correlation_id_factory=UUIDSequence(
            UUID("40000000-0000-4000-8000-000000000002")
        ),
    )

    result = gateway.execute(request, mutate_product_command(product.id, []))

    assert result.status is OperatorActionGatewayStatus.FAILED
    assert result.failure is not None
    assert result.failure.code is OperatorActionFailureCode.RECEIPT_COMMIT_FAILED
    stored = ProductRepo(db).get(product.id)
    assert stored is not None
    assert stored.status is EntityStatus.ACTIVE
    assert (
        OperatorActionReceiptRepo(db).get_by_idempotency_key_identity(
            idempotency_key_identity(request.idempotency_key)
        )
        is None
    )
    with db.session() as session:
        assert (
            session.get(
                OperatorActionKeyRow,
                idempotency_key_identity(request.idempotency_key),
            )
            is None
        )


def test_actual_transaction_commit_failure_rolls_back_mutation_and_receipt(
    db: Database,
) -> None:
    product = seed_product(db)
    request = envelope(key="idem-commit-failure", target_id=product.key)
    gateway = OperatorActionGateway(db, clock=FrozenClock())
    armed = True

    def fail_commit(_: Session) -> None:
        nonlocal armed
        if armed:
            armed = False
            raise sa.exc.OperationalError(
                "COMMIT", {}, RuntimeError("seeded commit failure")
            )

    sa.event.listen(Session, "before_commit", fail_commit)
    try:
        result = gateway.execute(request, mutate_product_command(product.id, []))
    finally:
        sa.event.remove(Session, "before_commit", fail_commit)

    assert result.status is OperatorActionGatewayStatus.FAILED
    assert result.failure is not None
    assert result.failure.code is OperatorActionFailureCode.RECEIPT_COMMIT_FAILED
    stored = ProductRepo(db).get(product.id)
    assert stored is not None
    assert stored.status is EntityStatus.ACTIVE
    with db.session() as session:
        assert (
            session.get(
                OperatorActionKeyRow,
                idempotency_key_identity(request.idempotency_key),
            )
            is None
        )


def test_unproven_operational_error_is_typed_storage_failure(
    db: Database,
) -> None:
    request = envelope(key="idem-unproven-storage-error")
    gateway = OperatorActionGateway(db, clock=FrozenClock())
    calls = 0

    def fail_reservation_flush(session: Session, *_: Any) -> None:
        if any(isinstance(row, OperatorActionKeyRow) for row in session.new):
            raise sa.exc.OperationalError(
                "INSERT", {}, RuntimeError("seeded storage failure")
            )

    def command(_: OperatorActionCommandContext) -> OperatorActionCommandResult:
        nonlocal calls
        calls += 1
        return OperatorActionCommandResult(
            outcome=OperatorActionOutcome.SUCCEEDED,
            result_code="must_not_run",
        )

    sa.event.listen(Session, "before_flush", fail_reservation_flush)
    try:
        result = gateway.execute(request, command)
    finally:
        sa.event.remove(Session, "before_flush", fail_reservation_flush)

    assert result.status is OperatorActionGatewayStatus.FAILED
    assert result.failure is not None
    assert result.failure.code is OperatorActionFailureCode.STORAGE_FAILED
    assert calls == 0


def test_operational_error_is_in_progress_only_with_proven_reservation(
    db: Database,
) -> None:
    request = envelope(key="idem-proven-storage-owner")
    correlation_id = seed_in_progress_reservation(db, request)
    gateway = OperatorActionGateway(db, clock=FrozenClock())
    calls = 0

    def fail_reservation_flush(session: Session, *_: Any) -> None:
        if any(isinstance(row, OperatorActionKeyRow) for row in session.new):
            raise sa.exc.OperationalError(
                "INSERT", {}, RuntimeError("seeded storage contention")
            )

    def command(_: OperatorActionCommandContext) -> OperatorActionCommandResult:
        nonlocal calls
        calls += 1
        return OperatorActionCommandResult(
            outcome=OperatorActionOutcome.SUCCEEDED,
            result_code="must_not_run",
        )

    sa.event.listen(Session, "before_flush", fail_reservation_flush)
    try:
        result = gateway.execute(request, command)
    finally:
        sa.event.remove(Session, "before_flush", fail_reservation_flush)

    assert result.status is OperatorActionGatewayStatus.IN_PROGRESS
    assert result.conflict is not None
    assert result.conflict.code is OperatorActionConflictCode.IN_PROGRESS
    assert result.conflict.correlation_id == correlation_id
    assert calls == 0


def test_concurrent_duplicate_calls_invoke_command_once(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{tmp_path}/concurrent.db")
    database.create_all()
    request = envelope(key="idem-concurrent", action="system.noop")
    gateway = OperatorActionGateway(database, clock=FrozenClock())
    start = threading.Barrier(2)
    invocation_lock = threading.Lock()
    invocations = 0
    results: list[OperatorActionGatewayStatus] = []

    def command(_: OperatorActionCommandContext) -> OperatorActionCommandResult:
        nonlocal invocations
        with invocation_lock:
            invocations += 1
        time.sleep(0.1)
        return OperatorActionCommandResult(
            outcome=OperatorActionOutcome.SUCCEEDED,
            result_code="noop_ok",
        )

    def run() -> None:
        start.wait()
        results.append(gateway.execute(request, command).status)

    threads = [threading.Thread(target=run), threading.Thread(target=run)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert invocations == 1
    assert sorted(status.value for status in results) in [
        ["executed", "in_progress"],
        ["executed", "replayed"],
    ]


def test_receipt_default_deny_excludes_neutral_key_opaque_content(
    db: Database, caplog: pytest.LogCaptureFixture
) -> None:
    opaque_credential = "mF9kQ7vLc2xP8nR4wT6yB3dH5jS1aG0z"
    lesson_content = "Promote this private lesson narrative verbatim."
    request_content = '{"private_command":"do not copy"}'
    evidence_content = "raw-test-output-with-customer-data"
    request = envelope(key=opaque_credential, payload={"safe": True})
    gateway = OperatorActionGateway(db, clock=FrozenClock())

    def command(_: OperatorActionCommandContext) -> OperatorActionCommandResult:
        return OperatorActionCommandResult(
            outcome=OperatorActionOutcome.SUCCEEDED,
            result_code="metadata_filtered",
            result_metadata={
                "alpha": opaque_credential,
                "bravo": lesson_content,
                "charlie": request_content,
                "delta": evidence_content,
                "changed": True,
            },
        )

    result = gateway.execute(request, command)

    assert result.receipt is not None
    persisted = OperatorActionReceiptRepo(db).get(result.receipt.id)
    assert persisted is not None
    persisted_json = json.dumps(persisted.model_dump(mode="json"), sort_keys=True)
    rendered_json = json.dumps(
        present_operator_action_receipt(persisted), sort_keys=True
    )
    assert persisted.result_metadata == {"changed": True}
    for forbidden in (
        opaque_credential,
        lesson_content,
        request_content,
        evidence_content,
    ):
        assert forbidden not in persisted_json
        assert forbidden not in rendered_json
        assert forbidden not in caplog.text
    assert request.idempotency_key not in persisted_json
    assert request.idempotency_key not in rendered_json
    assert result.receipt.idempotency_key_identity == idempotency_key_identity(
        opaque_credential
    )
