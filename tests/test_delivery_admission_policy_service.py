"""Governed delivery admission policy revisions, races and side effects."""

from __future__ import annotations

import inspect
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import sqlalchemy as sa
from test_agent_run_model import agent_run_kwargs
from test_delivery_admission_policy_model import policy_spec
from test_models_validation import product_kwargs, ticket_kwargs

import atlas.orchestration.operator_actions as operator_actions
from atlas.core.models import AgentRun, Product, Ticket
from atlas.orchestration import (
    DeliveryAdmissionPolicyChangeStatus,
    DeliveryAdmissionPolicyConflictCode,
    DeliveryAdmissionPolicyService,
    OperatorActionFailureCode,
)
from atlas.storage import (
    AgentRunRepo,
    Database,
    DeliveryAdmissionPolicyRepo,
    OperatorActionReceiptRepo,
    ProductRepo,
    TicketRepo,
)

NOW = datetime(2026, 8, 2, 14, tzinfo=UTC)


class FrozenClock:
    def __call__(self) -> datetime:
        return NOW


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{tmp_path}/atlas.db")
    database.create_all()
    return database


def seed_product(db: Database) -> Product:
    return ProductRepo(db).add(
        Product(**product_kwargs() | {"id": uuid4(), "key": "ATLAS"})
    )


def service(db: Database) -> DeliveryAdmissionPolicyService:
    return DeliveryAdmissionPolicyService(db, clock=FrozenClock())


def create_policy(
    db: Database,
    product: Product,
    *,
    key: str = "policy-create",
) -> Any:
    return service(db).revise(
        product_id=product.id,
        expected_revision=0,
        idempotency_key=key,
        policy=policy_spec(),
    )


def test_ac3_policy_write_uses_server_actor_and_expected_revision(db: Database) -> None:
    product = seed_product(db)

    result = create_policy(db, product)

    assert result.status is DeliveryAdmissionPolicyChangeStatus.APPLIED
    assert result.policy is not None
    assert result.policy.revision == 1
    assert result.policy.created_by_type.value == "human"
    assert result.policy.created_by_id == "operator"
    assert result.receipt is not None
    assert result.receipt.created_by_type.value == "human"
    assert result.receipt.created_by_id == "operator"
    assert (
        "actor"
        not in inspect.signature(DeliveryAdmissionPolicyService.revise).parameters
    )


def test_ac3_exact_replay_is_stable_and_altered_replay_conflicts(
    db: Database,
) -> None:
    product = seed_product(db)
    application = service(db)
    first = application.revise(
        product_id=product.id,
        expected_revision=0,
        idempotency_key="one-key",
        policy=policy_spec(),
    )
    replay = application.revise(
        product_id=product.id,
        expected_revision=0,
        idempotency_key="one-key",
        policy=policy_spec(),
    )
    altered = application.revise(
        product_id=product.id,
        expected_revision=1,
        idempotency_key="one-key",
        policy=policy_spec(review_budget=3),
    )

    assert first.status is DeliveryAdmissionPolicyChangeStatus.APPLIED
    assert replay.status is DeliveryAdmissionPolicyChangeStatus.REPLAYED
    assert replay.policy == first.policy
    assert replay.receipt == first.receipt
    assert altered.status is DeliveryAdmissionPolicyChangeStatus.CONFLICT
    assert (
        altered.conflict_code
        is DeliveryAdmissionPolicyConflictCode.IDEMPOTENCY_KEY_REUSED
    )
    assert [
        item.revision
        for item in DeliveryAdmissionPolicyRepo(db).list_revisions(product.id)
    ] == [1]


def test_ac3_stale_compare_and_set_returns_conflict_without_revision(
    db: Database,
) -> None:
    product = seed_product(db)
    create_policy(db, product)

    stale = service(db).revise(
        product_id=product.id,
        expected_revision=0,
        idempotency_key="stale-update",
        policy=policy_spec(working_budget=2),
    )

    assert stale.status is DeliveryAdmissionPolicyChangeStatus.CONFLICT
    assert stale.conflict_code is DeliveryAdmissionPolicyConflictCode.STALE_REVISION
    assert stale.receipt is not None
    assert stale.receipt.outcome.value == "conflict"
    assert [
        item.revision
        for item in DeliveryAdmissionPolicyRepo(db).list_revisions(product.id)
    ] == [1]


def test_ac3_concurrent_compare_and_set_has_one_winner(db: Database) -> None:
    product = seed_product(db)
    create_policy(db, product)
    barrier = threading.Barrier(2)
    results: list[Any] = []

    def revise(key: str, review_budget: int) -> None:
        barrier.wait()
        results.append(
            service(db).revise(
                product_id=product.id,
                expected_revision=1,
                idempotency_key=key,
                policy=policy_spec(review_budget=review_budget),
            )
        )

    threads = [
        threading.Thread(target=revise, args=("race-a", 1)),
        threading.Thread(target=revise, args=("race-b", 3)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(result.status.value for result in results) == [
        "applied",
        "conflict",
    ]
    history = DeliveryAdmissionPolicyRepo(db).list_revisions(product.id)
    assert [item.revision for item in history] == [1, 2]
    assert DeliveryAdmissionPolicyRepo(db).get_active(product.id) == history[-1]


@pytest.mark.parametrize(
    ("failure_site", "expected_code"),
    [
        ("store", OperatorActionFailureCode.COMMAND_FAILED),
        ("receipt", OperatorActionFailureCode.RECEIPT_COMMIT_FAILED),
    ],
)
def test_ac4_store_or_receipt_failure_preserves_prior_authority(
    db: Database,
    monkeypatch: pytest.MonkeyPatch,
    failure_site: str,
    expected_code: OperatorActionFailureCode,
) -> None:
    product = seed_product(db)
    create_policy(db, product)
    prior = DeliveryAdmissionPolicyRepo(db).get_active(product.id)
    prior_receipts = OperatorActionReceiptRepo(db).list()

    def fail(*args: object, **kwargs: object) -> None:
        raise sa.exc.IntegrityError("seeded failure", {}, Exception("seeded"))

    target = (
        "_apply_operator_action_mutations"
        if failure_site == "store"
        else "_add_operator_action_receipt"
    )
    monkeypatch.setattr(operator_actions, target, fail)

    result = service(db).revise(
        product_id=product.id,
        expected_revision=1,
        idempotency_key=f"failed-{failure_site}",
        policy=policy_spec(review_budget=3),
    )

    assert result.status is DeliveryAdmissionPolicyChangeStatus.FAILED
    assert result.failure_code is expected_code
    assert DeliveryAdmissionPolicyRepo(db).get_active(product.id) == prior
    assert [
        item.revision
        for item in DeliveryAdmissionPolicyRepo(db).list_revisions(product.id)
    ] == [1]
    assert OperatorActionReceiptRepo(db).list() == prior_receipts


@pytest.mark.parametrize("mode", ["paused", "draining"])
def test_ac6_pause_and_drain_only_change_policy(
    db: Database,
    tmp_path: Path,
    mode: str,
) -> None:
    product = seed_product(db)
    create_policy(db, product)
    ticket = TicketRepo(db).add(
        Ticket(
            **ticket_kwargs()
            | {
                "id": uuid4(),
                "product_id": product.id,
                "key": "ATLAS-246",
                "status": "in_progress",
            }
        )
    )
    agent_run = AgentRunRepo(db).add(
        AgentRun(
            **agent_run_kwargs()
            | {
                "id": uuid4(),
                "product_id": product.id,
                "ticket_id": ticket.id,
                "provider": "codex",
                "status": "running",
            }
        )
    )
    workspace = tmp_path / "active-workspace"
    workspace.mkdir()
    marker = workspace / "work.txt"
    marker.write_text("preserve me", encoding="utf-8")

    result = service(db).revise(
        product_id=product.id,
        expected_revision=1,
        idempotency_key=f"mode-{mode}",
        policy=policy_spec(mode=mode),
    )

    assert result.status is DeliveryAdmissionPolicyChangeStatus.APPLIED
    assert result.policy is not None
    assert result.policy.permits_new_admission is False
    assert TicketRepo(db).get(ticket.id) == ticket
    assert AgentRunRepo(db).get(agent_run.id) == agent_run
    assert marker.read_text(encoding="utf-8") == "preserve me"
