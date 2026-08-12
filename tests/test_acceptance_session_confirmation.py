"""ATLAS-240 governed acceptance-session confirmation contract.

The caller-shape and old-head canaries were seeded red with ``assert 1 == 2``
first (B011), then replaced with the behaviour assertions below.
"""

from __future__ import annotations

import inspect
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from github_fakes import FakeGitHubClient
from pydantic import ValidationError

import atlas.orchestration.acceptance_confirmation as confirmation_module
import atlas.orchestration.confirm as cli_confirmation_module
from atlas.core.enums import ActorType, EntityStatus, EvidenceStatus, RiskLevel
from atlas.core.models import (
    AcceptanceSession,
    AcceptanceSessionBlockingReason,
    AcceptanceSessionLifecycle,
    AcceptanceSessionStep,
    Evidence,
    OperatorActionOutcome,
    Product,
    Ticket,
    TicketStatus,
    TicketType,
)
from atlas.github import GitHubCompareStatus
from atlas.orchestration import (
    AcceptanceConfirmationRequest,
    AcceptanceConfirmationStatus,
    AcceptanceConfirmationValidationCode,
    AcceptanceSessionConfirmationService,
    AcceptanceSessionCreationService,
    AcceptanceSessionCreationStatus,
    OperatorActionCommandResult,
    OperatorActionEnvelope,
    OperatorActionFailureCode,
    OperatorActionGateway,
    OperatorActionResultCode,
    canonical_request_fingerprint,
    capture_ticket_result,
)
from atlas.orchestration.pr_integration import (
    PRAncestryStatus,
    PRBaseSHASource,
    PRIntegrationAssessment,
    PRIntegrationEligibility,
    PRIntegrationStatus,
    PRMergeabilityStatus,
)
from atlas.storage import (
    AcceptanceSessionRepo,
    Database,
    EvidenceRepo,
    OperatorActionReceiptRepo,
    ProductRepo,
    TicketRepo,
    VerificationCheckRepo,
)
from atlas.storage.tables import AcceptanceSessionRow, OperatorActionKeyRow, TicketRow
from atlas.verification import (
    build_acceptance_confirmation,
    evaluate_acceptance_criteria,
    evaluate_human_approval,
)

OWNER = "acme"
REPO = "atlas"
PR = 419
HEAD = "2" * 40
OLD_HEAD = "1" * 40
BASE = "0" * 40
NOW = datetime(2026, 8, 3, 10, tzinfo=UTC)


class FrozenClock:
    def __call__(self) -> datetime:
        return NOW + timedelta(minutes=1)


class UUIDSequence:
    def __init__(self, *values: UUID) -> None:
        self._values = iter(values)
        self._lock = threading.Lock()

    def __call__(self) -> UUID:
        with self._lock:
            return next(self._values)


class ApproveEverything:
    def acceptance(self, _criterion: str) -> bool:
        return True

    def scope(self, _path: str) -> Literal["waive", "fail", "skip"]:
        return "skip"

    def approval(self) -> Literal["approve", "reject", "skip"]:
        return "approve"


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{tmp_path}/atlas.db")
    database.create_all()
    return database


def product() -> Product:
    return Product(
        id=uuid4(),
        key="ATLAS",
        name="Atlas",
        description="Organisational operating system.",
        vision="Governed software delivery.",
        status=EntityStatus.ACTIVE,
        created_by_type=ActorType.HUMAN,
        created_by_id="operator",
        created_at=NOW,
        updated_at=NOW,
    )


def ticket(product_id: UUID, key: str, *criteria: str) -> Ticket:
    return Ticket(
        id=uuid4(),
        product_id=product_id,
        key=key,
        title=f"Ticket {key}",
        objective="Prove governed confirmation.",
        context="Phase 14.",
        status=TicketStatus.REVIEW_REQUIRED,
        ticket_type=TicketType.FEATURE,
        risk_level=RiskLevel.HIGH,
        priority=1,
        acceptance_criteria=list(criteria),
        source_anchor="docs/atlas/review-acceptance-console.md#confirmation-action",
        created_by_type=ActorType.AGENT,
        created_by_id="planner",
        created_at=NOW,
        updated_at=NOW,
    )


def assessment(**overrides: Any) -> PRIntegrationAssessment:
    values: dict[str, Any] = {
        "owner": OWNER,
        "repo": REPO,
        "pr_number": PR,
        "pr_title": "ATLAS-2 and ATLAS-1",
        "pr_body": None,
        "pr_state": "open",
        "pr_draft": False,
        "pr_merged": False,
        "head_ref": "atl-419-acceptance-session-confirmation",
        "head_sha": HEAD,
        "head_repository": f"{OWNER}/{REPO}",
        "base_ref": "main",
        "base_sha": BASE,
        "base_repository": f"{OWNER}/{REPO}",
        "base_sha_source": PRBaseSHASource.LIVE_BRANCH,
        "merge_base_sha": BASE,
        "ahead_by": 1,
        "behind_by": 0,
        "compare_status": GitHubCompareStatus.AHEAD,
        "mergeability": PRMergeabilityStatus.MERGEABLE,
        "ancestry": PRAncestryStatus.CURRENT,
        "eligibility": PRIntegrationEligibility.ELIGIBLE,
        "integration_status": PRIntegrationStatus.CURRENT,
    }
    values.update(overrides)
    return PRIntegrationAssessment(**values)


def seed_session(db: Database) -> tuple[AcceptanceSession, tuple[Ticket, Ticket]]:
    stored_product = ProductRepo(db).add(product())
    tickets = (
        TicketRepo(db).add(ticket(stored_product.id, "ATLAS-1", "first", "second")),
        TicketRepo(db).add(ticket(stored_product.id, "ATLAS-2", "third")),
    )
    service = AcceptanceSessionCreationService(
        github_client=FakeGitHubClient(),
        ticket_lookup=TicketRepo(db),
        repository=AcceptanceSessionRepo(db),
        clock=lambda: NOW,
        assessment_service=lambda *_args: assessment(),
    )
    created = service.create(
        repository_owner=OWNER,
        repository_name=REPO,
        pr_number=PR,
        idempotency_key="create-session",
        created_by_type=ActorType.HUMAN,
        created_by_id="operator",
    )
    assert created.status is AcceptanceSessionCreationStatus.CREATED
    assert created.session is not None

    with db.session() as sql_session, sql_session.begin():
        row = sql_session.get(AcceptanceSessionRow, created.session.id)
        assert row is not None
        summaries = dict(row.step_summaries)
        summaries[AcceptanceSessionStep.EVIDENCE.value] = {
            "state": "complete",
            "reasons": [],
            "receipt_ids": [],
            "occurred_at": NOW.isoformat(),
        }
        row.lifecycle = AcceptanceSessionLifecycle.EVIDENCE_READY.value
        row.step_summaries = summaries
        row.historical_readiness_reasons = [
            reason
            for reason in row.historical_readiness_reasons
            if reason != AcceptanceSessionBlockingReason.EVIDENCE_NOT_READY.value
        ]
    session = AcceptanceSessionRepo(db).get(created.session.id)
    assert session is not None
    return session, tickets


def request(
    session: AcceptanceSession,
    *,
    indexes: tuple[int, ...] = (0, 1, 2),
    fingerprint: str | None = None,
    manual_approval: bool = True,
) -> AcceptanceConfirmationRequest:
    return AcceptanceConfirmationRequest(
        session_id=session.id,
        criteria_fingerprint=fingerprint or session.criteria_fingerprint,
        criterion_indexes=indexes,
        manual_approval=manual_approval,
    )


def service(
    db: Database,
    *,
    live_assessment: PRIntegrationAssessment | None = None,
    evidence_id_factory: Callable[[], UUID] = uuid4,
    gateway: OperatorActionGateway | None = None,
) -> AcceptanceSessionConfirmationService:
    return AcceptanceSessionConfirmationService(
        db=db,
        github_client=FakeGitHubClient(),
        ticket_lookup=TicketRepo(db),
        clock=FrozenClock(),
        evidence_id_factory=evidence_id_factory,
        assessment_service=lambda *_args: live_assessment or assessment(),
        gateway=gateway,
    )


def test_ac1_request_is_api_independent_and_contains_only_operator_intent(
    db: Database,
) -> None:
    session, _tickets = seed_session(db)

    assert set(AcceptanceConfirmationRequest.model_fields) == {
        "session_id",
        "criteria_fingerprint",
        "criterion_indexes",
        "manual_approval",
    }
    parameters = inspect.signature(
        AcceptanceSessionConfirmationService.confirm
    ).parameters
    assert set(parameters) == {"self", "request", "idempotency_key"}
    result = service(db).confirm(
        request(session, indexes=(2, 0, 1)),
        idempotency_key="complete-reordered-set",
    )

    assert result.status is AcceptanceConfirmationStatus.CONFIRMED
    assert result.session is not None
    assert result.session.lifecycle is AcceptanceSessionLifecycle.CONFIRMATIONS_READY


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"criterion_indexes": (0, 1)}, {"missing_criterion_index"}),
        (
            {"criterion_indexes": (0, 1, 1)},
            {"missing_criterion_index", "duplicate_criterion_index"},
        ),
        (
            {"criterion_indexes": (0, 1, 99)},
            {"missing_criterion_index", "unknown_criterion_index"},
        ),
        (
            {"criterion_indexes": (0, 1, 2, 99)},
            {"unknown_criterion_index", "extra_criterion_index"},
        ),
        (
            {"criteria_fingerprint": "sha256:" + "f" * 64},
            {"criteria_fingerprint_mismatch"},
        ),
        ({"manual_approval": False}, {"manual_approval_required"}),
    ],
    ids=["missing", "duplicate", "unknown", "extra", "fingerprint", "manual"],
)
def test_ac2_incomplete_or_altered_intent_is_validation_only(
    db: Database,
    overrides: dict[str, Any],
    expected: set[str],
) -> None:
    session, _tickets = seed_session(db)
    values: dict[str, Any] = {
        "session_id": session.id,
        "criteria_fingerprint": session.criteria_fingerprint,
        "criterion_indexes": (0, 1, 2),
        "manual_approval": True,
    }
    values.update(overrides)

    result = service(db).confirm(
        AcceptanceConfirmationRequest(**values),
        idempotency_key=f"invalid-{next(iter(expected))}",
    )

    assert result.status is AcceptanceConfirmationStatus.VALIDATION_FAILED
    assert expected <= {error.value for error in result.validation_errors}
    assert EvidenceRepo(db).list() == []
    assert OperatorActionReceiptRepo(db).list() == []
    assert AcceptanceSessionRepo(db).get(session.id) == session


@pytest.mark.parametrize(
    ("live", "expected_reason"),
    [
        (
            assessment(head_sha="3" * 40),
            AcceptanceSessionBlockingReason.HEAD_SHA_MISMATCH,
        ),
        (
            assessment(base_sha="4" * 40, merge_base_sha="4" * 40),
            AcceptanceSessionBlockingReason.BASE_SHA_MISMATCH,
        ),
        (
            assessment(
                eligibility=PRIntegrationEligibility.DRAFT,
                integration_status=PRIntegrationStatus.INELIGIBLE,
                pr_draft=True,
            ),
            AcceptanceSessionBlockingReason.ELIGIBILITY_MISMATCH,
        ),
    ],
    ids=["head", "main", "eligibility"],
)
def test_ac3_live_exact_head_drift_atomically_stales_without_confirmation(
    db: Database,
    live: PRIntegrationAssessment,
    expected_reason: AcceptanceSessionBlockingReason,
) -> None:
    session, _tickets = seed_session(db)

    key = f"drift-{expected_reason.value}"
    result = service(db, live_assessment=live).confirm(
        request(session),
        idempotency_key=key,
    )
    replay = service(db, live_assessment=live).confirm(
        request(session),
        idempotency_key=key,
    )

    assert result.status is AcceptanceConfirmationStatus.STALE
    assert result.receipt is not None
    assert result.receipt.outcome is OperatorActionOutcome.REFUSED
    assert result.session is not None
    assert result.session.lifecycle is AcceptanceSessionLifecycle.STALE
    assert expected_reason in result.session.blocking_reasons
    assert result.reasons == result.session.blocking_reasons
    assert replay.status is AcceptanceConfirmationStatus.REPLAYED
    assert replay.reasons == result.reasons
    assert EvidenceRepo(db).list() == []


def test_ac3_live_criteria_drift_stales_in_same_receipt_transaction(
    db: Database,
) -> None:
    session, _tickets = seed_session(db)
    with db.session() as sql_session, sql_session.begin():
        row = sql_session.scalars(
            sa.select(TicketRow).where(TicketRow.key == "ATLAS-1")
        ).one()
        row.acceptance_criteria = ["changed live", "second"]

    result = service(db).confirm(request(session), idempotency_key="criteria-drift")

    assert result.status is AcceptanceConfirmationStatus.STALE
    assert result.session is not None
    assert result.session.blocking_reasons == (
        AcceptanceSessionBlockingReason.CRITERIA_MISMATCH,
    )
    assert result.reasons == (AcceptanceSessionBlockingReason.CRITERIA_MISMATCH,)
    assert EvidenceRepo(db).list() == []


def test_ac4_success_uses_human_operator_records_that_existing_evaluators_pass(
    db: Database,
) -> None:
    session, tickets = seed_session(db)

    result = service(db).confirm(request(session), idempotency_key="success")

    assert result.status is AcceptanceConfirmationStatus.CONFIRMED
    assert result.receipt is not None
    assert result.receipt.result_metadata == {"affected_count": 5, "changed": True}
    records = EvidenceRepo(db).list()
    assert len(records) == 5
    assert all(record.commit_sha == HEAD for record in records)
    assert all(record.created_by_type is ActorType.HUMAN for record in records)
    assert all(record.created_by_id == "operator" for record in records)
    for stored_ticket in tickets:
        assert (
            evaluate_acceptance_criteria(
                stored_ticket.acceptance_criteria,
                ticket_id=stored_ticket.id,
                head_commit=HEAD,
                evidence=records,
            ).status
            is EvidenceStatus.PASSED
        )
        assert (
            evaluate_human_approval(
                ticket_id=stored_ticket.id,
                head_commit=HEAD,
                evidence=records,
            ).status
            is EvidenceStatus.PASSED
        )
    assert VerificationCheckRepo(db).list() == []


@pytest.mark.parametrize("failure_kind", ["evidence", "receipt"])
def test_ac5_write_or_receipt_failure_rolls_back_complete_confirmation_set(
    db: Database,
    failure_kind: str,
) -> None:
    session, _tickets = seed_session(db)
    fixed = UUID("50000000-0000-4000-8000-000000000001")
    evidence_ids: Callable[[], UUID] = uuid4
    gateway: OperatorActionGateway | None = None
    baseline_receipts = 0
    if failure_kind == "evidence":
        evidence_ids = UUIDSequence(fixed, fixed)
    else:
        seed_gateway = OperatorActionGateway(
            db,
            clock=FrozenClock(),
            receipt_id_factory=lambda: fixed,
        )
        seed_envelope = OperatorActionEnvelope(
            action="test.seed",
            target_type="test",
            target_id="receipt",
            created_by_type=ActorType.HUMAN,
            created_by_id="operator",
            idempotency_key="seed-receipt",
            request_fingerprint=canonical_request_fingerprint(
                action="test.seed",
                target_type="test",
                target_id="receipt",
                payload={"seed": True},
            ),
        )
        seeded = seed_gateway.execute(
            seed_envelope,
            lambda _context: OperatorActionCommandResult(
                outcome=OperatorActionOutcome.SUCCEEDED,
                result_code=OperatorActionResultCode.ACTION_SUCCEEDED,
            ),
        )
        assert seeded.receipt is not None
        baseline_receipts = 1
        gateway = OperatorActionGateway(
            db,
            clock=FrozenClock(),
            receipt_id_factory=lambda: fixed,
        )

    result = service(
        db,
        evidence_id_factory=evidence_ids,
        gateway=gateway,
    ).confirm(request(session), idempotency_key=f"fail-{failure_kind}")

    assert result.status is AcceptanceConfirmationStatus.FAILED
    assert result.failure is not None
    expected_failure = (
        OperatorActionFailureCode.COMMAND_FAILED
        if failure_kind == "evidence"
        else OperatorActionFailureCode.RECEIPT_COMMIT_FAILED
    )
    assert result.failure.code is expected_failure
    assert EvidenceRepo(db).list() == []
    assert len(OperatorActionReceiptRepo(db).list()) == baseline_receipts
    assert AcceptanceSessionRepo(db).get(session.id) == session


def test_ac6_same_key_reordered_set_replays_without_duplicate_or_replacement(
    db: Database,
) -> None:
    session, _tickets = seed_session(db)
    action = service(db)

    first = action.confirm(request(session), idempotency_key="replay-key")
    replay = action.confirm(request(session), idempotency_key="replay-key")
    reordered = action.confirm(
        request(session, indexes=(2, 1, 0)),
        idempotency_key="replay-key",
    )

    assert first.status is AcceptanceConfirmationStatus.CONFIRMED
    assert first.receipt is not None
    assert replay.status is AcceptanceConfirmationStatus.REPLAYED
    assert replay.receipt == first.receipt
    assert reordered.status is AcceptanceConfirmationStatus.REPLAYED
    assert reordered.receipt == first.receipt
    assert len(EvidenceRepo(db).list()) == 5
    stored = AcceptanceSessionRepo(db).get(session.id)
    assert stored is not None
    assert stored.step_summaries[AcceptanceSessionStep.CONFIRMATIONS].receipt_ids == (
        first.receipt.id,
    )


def test_ac6_concurrent_keys_lock_session_and_only_one_action_advances(
    db: Database,
) -> None:
    session, _tickets = seed_session(db)
    db_url = str(db.engine.url)
    results: list[AcceptanceConfirmationStatus] = []
    result_lock = threading.Lock()

    def run(key: str) -> None:
        database = Database(db_url)
        result = service(database).confirm(request(session), idempotency_key=key)
        with result_lock:
            results.append(result.status)

    threads = [
        threading.Thread(target=run, args=(f"concurrent-{index}",))
        for index in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert sorted(results) == sorted(
        [AcceptanceConfirmationStatus.CONFIRMED, AcceptanceConfirmationStatus.CONFLICT]
    )
    assert len(EvidenceRepo(db).list()) == 5
    stored = AcceptanceSessionRepo(db).get(session.id)
    assert stored is not None
    assert (
        len(stored.step_summaries[AcceptanceSessionStep.CONFIRMATIONS].receipt_ids) == 1
    )


def test_ac7_cli_and_session_action_share_confirmation_domain_service_and_writer(
    db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, tickets = seed_session(db)
    calls: list[tuple[str, tuple[str, ...], bool | None]] = []
    original = cli_confirmation_module.build_confirmation_records

    def spy(
        stored_ticket: Ticket,
        *,
        confirmed_criteria: tuple[str, ...],
        manual_approval: bool | None,
        **kwargs: Any,
    ) -> tuple[Evidence, ...]:
        calls.append((stored_ticket.key, confirmed_criteria, manual_approval))
        return original(
            stored_ticket,
            confirmed_criteria=confirmed_criteria,
            manual_approval=manual_approval,
            **kwargs,
        )

    monkeypatch.setattr(cli_confirmation_module, "build_confirmation_records", spy)
    capture_ticket_result(
        tickets[0],
        prompts=ApproveEverything(),
        head_commit=OLD_HEAD,
        pr_files=[],
        evidence=[],
        product_id=tickets[0].product_id,
        operator_id="operator",
        evidence_repo=EvidenceRepo(db),
        now=NOW,
        new_id=uuid4,
    )
    monkeypatch.setattr(confirmation_module, "build_confirmation_records", spy)

    result = service(db).confirm(request(session), idempotency_key="shared-service")

    assert result.status is AcceptanceConfirmationStatus.CONFIRMED
    assert ("ATLAS-1", ("first",), None) in calls
    assert ("ATLAS-1", ("second",), None) in calls
    assert ("ATLAS-1", (), True) in calls
    assert ("ATLAS-1", ("first", "second"), True) in calls
    assert ("ATLAS-2", ("third",), True) in calls
    assert len(EvidenceRepo(db).list()) == 8


@pytest.mark.parametrize(
    "forbidden",
    [
        {"criterion_text": "caller-authored"},
        {"actor": "spoofed"},
        {"head_sha": "f" * 40},
        {"repository": "other/repo"},
        {"ticket_key": "ATLAS-999"},
    ],
    ids=["text", "actor", "head", "repository", "ticket"],
)
def test_canary_caller_authored_identity_fields_are_rejected(
    db: Database,
    forbidden: dict[str, str],
) -> None:
    session, _tickets = seed_session(db)
    payload: dict[str, Any] = request(session).model_dump(mode="json") | forbidden

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AcceptanceConfirmationRequest.model_validate(payload)

    assert EvidenceRepo(db).list() == []


def test_canary_old_head_confirmations_cannot_satisfy_new_session(
    db: Database,
) -> None:
    session, tickets = seed_session(db)
    old_records = [
        build_acceptance_confirmation(
            criterion,
            ticket_id=tickets[0].id,
            head_commit=OLD_HEAD,
            product_id=tickets[0].product_id,
            operator_id="operator",
            evidence_id=uuid4(),
            now=NOW,
        )
        for criterion in tickets[0].acceptance_criteria
    ]
    for record in old_records:
        EvidenceRepo(db).add(record)

    evaluation = evaluate_acceptance_criteria(
        tickets[0].acceptance_criteria,
        ticket_id=tickets[0].id,
        head_commit=session.head_sha,
        evidence=EvidenceRepo(db).list(),
    )

    assert evaluation.status is EvidenceStatus.PENDING
    assert set(evaluation.evidence_ids).isdisjoint(
        {record.id for record in old_records}
    )


def test_unknown_session_is_validation_only_and_reserves_no_action_key(
    db: Database,
) -> None:
    result = service(db).confirm(
        AcceptanceConfirmationRequest(
            session_id=uuid4(),
            criteria_fingerprint="sha256:" + "0" * 64,
            criterion_indexes=(),
            manual_approval=True,
        ),
        idempotency_key="unknown-session",
    )

    assert result.status is AcceptanceConfirmationStatus.VALIDATION_FAILED
    assert result.validation_errors == (
        AcceptanceConfirmationValidationCode.SESSION_UNKNOWN,
    )
    with db.session() as sql_session:
        assert sql_session.scalars(sa.select(OperatorActionKeyRow)).all() == []
