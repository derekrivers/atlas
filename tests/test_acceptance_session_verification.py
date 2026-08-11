"""ATLAS-241 exact-head verification and live-readiness acceptance contract.

The required canaries were first seeded red with ``assert 1 == 2`` (B011), then
replaced by the named behavioural assertions below.
"""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import alembic.command
import pytest
import sqlalchemy as sa

import atlas.orchestration.acceptance_verification as verification_module
from atlas.core.enums import ActorType, EntityStatus, EvidenceStatus, RiskLevel
from atlas.core.models import (
    AcceptanceSession,
    AcceptanceSessionLifecycle,
    AcceptanceSessionStep,
    AcceptanceSessionStepState,
    Evidence,
    EvidenceType,
    OperatorActionOutcome,
    OperatorActionReceipt,
    OperatorActionResultCode,
    Product,
    Ticket,
    TicketStatus,
    TicketType,
    VerificationCheck,
)
from atlas.core.models import (
    AcceptanceSessionBlockingReason as Reason,
)
from atlas.core.models.acceptance_session import AcceptanceStepSummary
from atlas.github import (
    GitHubAPIError,
    GitHubCompare,
    GitHubCompareStatus,
    GitHubMalformedResponseError,
)
from atlas.linear.client import LinearGraphQLClient
from atlas.orchestration import (
    AcceptanceSessionCreationService,
    AcceptanceSessionLiveReadinessService,
    AcceptanceSessionVerificationService,
    AcceptanceVerificationContext,
    AcceptanceVerificationResult,
    AcceptanceVerificationStatus,
    OperatorActionFailureCode,
    OperatorActionGateway,
)
from atlas.orchestration.pr_context import PRContext
from atlas.orchestration.pr_integration import (
    PRAncestryStatus,
    PRBaseSHASource,
    PRIntegrationAssessment,
    PRIntegrationEligibility,
    PRIntegrationStatus,
    PRMergeabilityStatus,
)
from atlas.orchestration.verify import VerifyResult
from atlas.pm import sync as pm_sync
from atlas.storage import (
    AcceptanceSessionRepo,
    Database,
    EvidenceRepo,
    OperatorActionReceiptRepo,
    ProductRepo,
    TicketRepo,
    TicketStatusTransitionRepo,
    VerificationCheckRepo,
)
from atlas.storage.repositories import TicketRepo as TicketRepoClass
from atlas.storage.tables import AcceptanceSessionRow
from atlas.verification import (
    CheckOutcome,
    PRVerification,
    TicketVerification,
    acceptance_criterion_hash,
    required_checks,
)

OWNER = "acme"
REPO = "atlas"
SLUG = f"{OWNER}/{REPO}"
PR = 417
HEAD = "a" * 40
OTHER_HEAD = "b" * 40
BASE = "c" * 40
OTHER_BASE = "d" * 40
NOW = datetime(2026, 8, 10, 9, tzinfo=UTC)
VERDICT_ID = UUID("10000000-0000-4000-8000-000000000241")


class FrozenClock:
    def __call__(self) -> datetime:
        return NOW + timedelta(minutes=1)


class AssessmentFake:
    def __init__(self, *results: PRIntegrationAssessment | Exception | object) -> None:
        self._results = iter(results)
        self.calls = 0

    def __call__(self, *_args: Any) -> PRIntegrationAssessment:
        self.calls += 1
        result = next(self._results)
        if isinstance(result, Exception):
            raise result
        return result  # type: ignore[return-value]


class ContextFake:
    def __init__(self, context: PRContext | Exception | None = None) -> None:
        self.context = context or pr_context()
        self.calls = 0

    def __call__(self, *_args: Any) -> PRContext:
        self.calls += 1
        if isinstance(self.context, Exception):
            raise self.context
        return self.context


class VerificationFake:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[tuple[PRContext, tuple[str, ...], Database]] = []

    def __call__(
        self,
        context: PRContext,
        close_set: tuple[str, ...],
        db: Database,
    ) -> PRVerification:
        self.calls.append((context, close_set, db))
        return self.result  # type: ignore[return-value]


class MappingLookup:
    def __init__(self, *tickets: Ticket) -> None:
        self.tickets = {ticket.key: ticket for ticket in tickets}

    def get_by_key(self, key: str) -> Ticket | None:
        return self.tickets.get(key)


class TicketLookupSequence:
    def __init__(self, *tickets: Ticket) -> None:
        self._tickets = iter(tickets)

    def get_by_key(self, _key: str) -> Ticket:
        return next(self._tickets)


class ReadOnlyGitHubSpy:
    """Read methods are executable; any mutation is a hard test failure."""

    def __init__(self) -> None:
        self.reads: list[str] = []
        self.mutations: list[str] = []

    def fetch_pull_request(
        self, owner: str, repo: str, pr_number: int
    ) -> dict[str, Any]:
        self.reads.append("fetch_pull_request")
        return {
            "number": pr_number,
            "title": "ATLAS-241 exact-head readiness",
            "body": None,
            "state": "open",
            "draft": False,
            "merged": False,
            "mergeable": True,
            "head": {
                "ref": "agent/atlas-241",
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
        self.reads.append("fetch_branch_head")
        return BASE

    def compare_commits(self, *_args: Any) -> GitHubCompare:
        self.reads.append("compare_commits")
        return GitHubCompare(
            status=GitHubCompareStatus.AHEAD,
            ahead_by=1,
            behind_by=0,
            merge_base_sha=BASE,
        )

    def fetch_pr_files(self, *_args: Any) -> list[dict[str, Any]]:
        self.reads.append("fetch_pr_files")
        return [{"filename": "atlas/orchestration/acceptance_verification.py"}]

    def fetch_workflow_runs(self, *_args: Any) -> list[dict[str, Any]]:
        self.reads.append("fetch_workflow_runs")
        return []

    def fetch_check_runs(self, *_args: Any) -> list[dict[str, Any]]:
        self.reads.append("fetch_check_runs")
        return []

    def fetch_pr_reviews(self, *_args: Any) -> list[dict[str, Any]]:
        self.reads.append("fetch_pr_reviews")
        return []

    def merge_pull_request(self, *_args: Any) -> None:
        self.mutations.append("merge_pull_request")
        raise AssertionError("verification must never merge a PR")

    def update_pull_request(self, *_args: Any) -> None:
        self.mutations.append("update_pull_request")
        raise AssertionError("verification must never mutate a PR")


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
        vision="Governed delivery.",
        status=EntityStatus.ACTIVE,
        created_by_type=ActorType.HUMAN,
        created_by_id="operator",
        created_at=NOW,
        updated_at=NOW,
    )


def ticket(product_id: UUID, *criteria: str) -> Ticket:
    return Ticket(
        id=uuid4(),
        product_id=product_id,
        key="ATLAS-241",
        title="Exact-head verification readiness",
        objective="Admit only the exact verified head.",
        context="Phase 14.",
        status=TicketStatus.REVIEW_REQUIRED,
        ticket_type=TicketType.BUG,
        risk_level=RiskLevel.LOW,
        priority=1,
        relevant_docs=["atlas/orchestration/acceptance_verification.py"],
        acceptance_criteria=list(criteria or ("exact head is ready",)),
        documentation_requirements=[],
        source_anchor="atlas/orchestration/acceptance_verification.py#contract",
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
        "pr_title": "ATLAS-241 exact-head readiness",
        "pr_body": None,
        "pr_state": "open",
        "pr_draft": False,
        "pr_merged": False,
        "head_ref": "agent/atlas-241",
        "head_sha": HEAD,
        "head_repository": SLUG,
        "base_ref": "main",
        "base_sha": BASE,
        "base_repository": SLUG,
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


def pr_context(**overrides: Any) -> PRContext:
    values: dict[str, Any] = {
        "owner": OWNER,
        "repo": REPO,
        "pull_request": {
            "title": "ATLAS-241 exact-head readiness",
            "body": None,
            "head": {"sha": HEAD},
            "merged": False,
        },
        "head_commit": HEAD,
        "pr_files": ["atlas/orchestration/acceptance_verification.py"],
    }
    values.update(overrides)
    return PRContext(**values)


def verification(
    stored_ticket: Ticket,
    *,
    status: EvidenceStatus = EvidenceStatus.PASSED,
    head: str = HEAD,
    checks: tuple[CheckOutcome, ...] = (),
) -> PRVerification:
    return PRVerification(
        head_commit=head,
        status=status,
        tickets=(
            TicketVerification(
                ticket_id=stored_ticket.id,
                status=status,
                checks=checks,
            ),
        ),
    )


def seed_session(
    db: Database,
    *,
    confirmations_ready: bool = True,
) -> tuple[AcceptanceSession, Ticket]:
    stored_product = ProductRepo(db).add(product())
    stored_ticket = TicketRepo(db).add(ticket(stored_product.id))
    created = AcceptanceSessionCreationService(
        github_client=ReadOnlyGitHubSpy(),
        ticket_lookup=TicketRepo(db),
        repository=AcceptanceSessionRepo(db),
        clock=lambda: NOW,
        assessment_service=lambda *_args: assessment(),
    ).create(
        repository_owner=OWNER,
        repository_name=REPO,
        pr_number=PR,
        idempotency_key="create-session",
        created_by_type=ActorType.HUMAN,
        created_by_id="operator",
    )
    assert created.session is not None
    if confirmations_ready:
        with db.session() as sql_session, sql_session.begin():
            row = sql_session.get(AcceptanceSessionRow, created.session.id)
            assert row is not None
            summaries = dict(row.step_summaries)
            for step in (
                AcceptanceSessionStep.EVIDENCE,
                AcceptanceSessionStep.CONFIRMATIONS,
            ):
                summaries[step.value] = AcceptanceStepSummary(
                    state=AcceptanceSessionStepState.COMPLETE,
                    occurred_at=NOW,
                ).model_dump(mode="json")
            row.lifecycle = AcceptanceSessionLifecycle.CONFIRMATIONS_READY.value
            row.step_summaries = summaries
            row.historical_readiness_reasons = [Reason.VERIFICATION_NOT_PASSED.value]
    session = AcceptanceSessionRepo(db).get(created.session.id)
    assert session is not None
    return session, stored_ticket


def action_context(key: str = "verify-session") -> AcceptanceVerificationContext:
    return AcceptanceVerificationContext(
        idempotency_key=key,
        created_by_type=ActorType.HUMAN,
        created_by_id="operator",
    )


def action_service(
    db: Database,
    stored_ticket: Ticket,
    assessments: AssessmentFake,
    verifier: VerificationFake | None,
    *,
    ticket_lookup: Any | None = None,
    context_service: Callable[..., PRContext] | None = None,
    gateway: OperatorActionGateway | None = None,
    github_client: Any | None = None,
) -> AcceptanceSessionVerificationService:
    return AcceptanceSessionVerificationService(
        db=db,
        github_client=github_client or ReadOnlyGitHubSpy(),
        ticket_lookup=ticket_lookup or MappingLookup(stored_ticket),
        gateway=gateway or OperatorActionGateway(db, clock=FrozenClock()),
        clock=FrozenClock(),
        assessment_service=assessments,
        pr_context_service=context_service or ContextFake(),
        verification_service=verifier,
        verdict_id_factory=lambda: VERDICT_ID,
    )


def test_ac1_all_prerequisites_are_returned_and_no_verifier_call_occurs(
    db: Database,
) -> None:
    session, stored_ticket = seed_session(db, confirmations_ready=False)
    drifted_ticket = stored_ticket.model_copy(
        update={"acceptance_criteria": ["drifted criterion"]}
    )
    verifier = VerificationFake(verification(stored_ticket))
    service = action_service(
        db,
        stored_ticket,
        AssessmentFake(assessment(head_sha=OTHER_HEAD, base_sha=OTHER_BASE)),
        verifier,
        ticket_lookup=MappingLookup(drifted_ticket),
    )

    result = service.execute(session.id, action_context())

    assert not result.merge_ready
    assert set(result.reasons) >= {
        Reason.EVIDENCE_NOT_READY,
        Reason.CONFIRMATIONS_NOT_READY,
        Reason.SESSION_NOT_VERIFIABLE,
        Reason.HEAD_SHA_MISMATCH,
        Reason.BASE_SHA_MISMATCH,
        Reason.CRITERIA_MISMATCH,
    }
    assert verifier.calls == []
    assert result.session is not None
    assert result.session.lifecycle is AcceptanceSessionLifecycle.STALE


@pytest.mark.parametrize(
    ("status", "head", "expected"),
    [
        (EvidenceStatus.PENDING, HEAD, Reason.VERIFICATION_PENDING),
        (EvidenceStatus.FAILED, HEAD, Reason.VERIFICATION_FAILED),
        (EvidenceStatus.WARNING, HEAD, Reason.VERIFICATION_WARNING),
        (
            EvidenceStatus.NOT_APPLICABLE,
            HEAD,
            Reason.VERIFICATION_NOT_APPLICABLE,
        ),
        (EvidenceStatus.PASSED, "not-a-sha", Reason.VERIFIED_HEAD_INVALID),
        (EvidenceStatus.PASSED, OTHER_HEAD, Reason.VERIFIED_HEAD_MISMATCH),
    ],
)
def test_ac2_only_explicit_passed_exact_head_verdict_is_accepted(
    db: Database,
    status: EvidenceStatus,
    head: str,
    expected: Reason,
) -> None:
    session, stored_ticket = seed_session(db)
    verifier = VerificationFake(verification(stored_ticket, status=status, head=head))
    service = action_service(
        db,
        stored_ticket,
        AssessmentFake(assessment(), assessment()),
        verifier,
    )

    result = service.execute(session.id, action_context())

    assert not result.merge_ready
    assert expected in result.reasons
    assert len(verifier.calls) == 1
    assert result.session is not None
    assert result.session.lifecycle in {
        AcceptanceSessionLifecycle.BLOCKED,
        AcceptanceSessionLifecycle.FAILED,
    }


@pytest.mark.parametrize(
    ("malformed", "expected"),
    [
        (object(), Reason.VERIFICATION_MALFORMED),
        (
            PRVerification(
                head_commit=HEAD,
                status=EvidenceStatus.PASSED,
                tickets=(),
            ),
            Reason.VERIFICATION_CLOSE_SET_MISMATCH,
        ),
    ],
)
def test_ac2_malformed_or_wrong_close_set_verdict_never_advances(
    db: Database,
    malformed: object,
    expected: Reason,
) -> None:
    session, stored_ticket = seed_session(db)
    verifier = VerificationFake(malformed)
    service = action_service(
        db,
        stored_ticket,
        AssessmentFake(assessment(), assessment()),
        verifier,
    )

    result = service.execute(session.id, action_context())

    assert not result.merge_ready
    assert expected in result.reasons
    assert len(verifier.calls) == 1


def test_ac2_canonical_verification_engine_is_invoked_in_process(
    db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, stored_ticket = seed_session(db)
    calls: list[tuple[PRContext, tuple[str, ...], Database]] = []

    def canonical_spy(
        context: PRContext,
        close_set: tuple[str, ...],
        database: Database,
    ) -> VerifyResult:
        calls.append((context, close_set, database))
        return VerifyResult(
            verification(stored_ticket),
            {stored_ticket.id: stored_ticket.key},
            [],
        )

    monkeypatch.setattr(verification_module, "run_verify", canonical_spy)
    service = action_service(
        db,
        stored_ticket,
        AssessmentFake(assessment(), assessment(), assessment()),
        None,
    )

    result = service.execute(session.id, action_context())

    assert result.merge_ready
    assert len(calls) == 1
    assert calls[0][1] == session.close_set
    assert calls[0][2] is db


def test_ac3_head_and_main_race_before_verification_prevents_verifier_call(
    db: Database,
) -> None:
    session, stored_ticket = seed_session(db)
    verifier = VerificationFake(verification(stored_ticket))
    service = action_service(
        db,
        stored_ticket,
        AssessmentFake(
            assessment(),
            assessment(head_sha=OTHER_HEAD, base_sha=OTHER_BASE),
        ),
        verifier,
    )

    result = service.execute(session.id, action_context())

    assert not result.merge_ready
    assert {Reason.HEAD_SHA_MISMATCH, Reason.BASE_SHA_MISMATCH} <= set(result.reasons)
    assert verifier.calls == []


def test_ac3_fresh_post_pass_assessment_preserves_every_identity_reason(
    db: Database,
) -> None:
    session, stored_ticket = seed_session(db)
    drifted_ticket = stored_ticket.model_copy(
        update={"acceptance_criteria": ["criteria moved after verification"]}
    )
    final = assessment(
        owner="other",
        repo="repository",
        pr_number=PR + 1,
        head_ref="moved-head-ref",
        head_sha=OTHER_HEAD,
        head_repository="other/repository",
        base_ref="release",
        base_sha=OTHER_BASE,
        base_repository="other/repository",
        eligibility=PRIntegrationEligibility.CLOSED,
        integration_status=PRIntegrationStatus.INELIGIBLE,
    )
    verifier = VerificationFake(verification(stored_ticket))
    service = action_service(
        db,
        stored_ticket,
        AssessmentFake(assessment(), assessment(), final),
        verifier,
        ticket_lookup=TicketLookupSequence(
            stored_ticket,
            stored_ticket,
            drifted_ticket,
        ),
    )

    result = service.execute(session.id, action_context())

    assert not result.merge_ready
    assert set(result.reasons) >= {
        Reason.REPOSITORY_MISMATCH,
        Reason.PR_NUMBER_MISMATCH,
        Reason.HEAD_REF_MISMATCH,
        Reason.HEAD_SHA_MISMATCH,
        Reason.HEAD_REPOSITORY_MISMATCH,
        Reason.BASE_REF_MISMATCH,
        Reason.BASE_REPOSITORY_MISMATCH,
        Reason.ELIGIBILITY_MISMATCH,
        Reason.INTEGRATION_STATUS_MISMATCH,
        Reason.CRITERIA_MISMATCH,
    }
    assert len(verifier.calls) == 1
    assert result.session is not None
    verification_step = result.session.step_summaries[
        AcceptanceSessionStep.VERIFICATION
    ]
    assert verification_step.state is AcceptanceSessionStepState.COMPLETE
    assert verification_step.verification is not None
    assert verification_step.verification.head_commit == HEAD
    assert result.session.lifecycle is AcceptanceSessionLifecycle.STALE


def test_ac4_success_persists_verified_head_verdict_assessment_and_receipt(
    db: Database,
) -> None:
    session, stored_ticket = seed_session(db)
    verifier = VerificationFake(verification(stored_ticket))
    service = action_service(
        db,
        stored_ticket,
        AssessmentFake(assessment(), assessment(), assessment()),
        verifier,
    )

    result = service.execute(session.id, action_context())

    assert result.status is AcceptanceVerificationStatus.MERGE_READY
    assert result.merge_ready
    assert result.reasons == ()
    assert result.receipt is not None
    assert result.receipt.outcome is OperatorActionOutcome.SUCCEEDED
    assert result.session is not None
    stored = result.session
    assert stored.lifecycle is AcceptanceSessionLifecycle.MERGE_READY
    assert stored.stored_merge_ready is True
    verification_step = stored.step_summaries[AcceptanceSessionStep.VERIFICATION]
    readiness_step = stored.step_summaries[AcceptanceSessionStep.READINESS]
    assert verification_step.verification is not None
    assert verification_step.verification.verdict_id == VERDICT_ID
    assert verification_step.verification.head_commit == HEAD
    assert verification_step.verification.status is EvidenceStatus.PASSED
    assert readiness_step.readiness is not None
    assert readiness_step.readiness.verdict_id == VERDICT_ID
    assert readiness_step.readiness.head_sha == HEAD
    assert readiness_step.readiness.base_sha == BASE
    assert readiness_step.readiness.criteria_fingerprint == stored.criteria_fingerprint
    assert (
        verification_step.receipt_ids
        == readiness_step.receipt_ids
        == (result.receipt.id,)
    )


def test_ac4_same_key_replay_never_repeats_verifier_or_live_reads(
    db: Database,
) -> None:
    session, stored_ticket = seed_session(db)
    assessments = AssessmentFake(assessment(), assessment(), assessment())
    verifier = VerificationFake(verification(stored_ticket))
    service = action_service(db, stored_ticket, assessments, verifier)

    first = service.execute(session.id, action_context("same-key"))
    replay = service.execute(session.id, action_context("same-key"))

    assert first.merge_ready and replay.merge_ready
    assert replay.status is AcceptanceVerificationStatus.REPLAYED
    assert replay.receipt is not None and first.receipt is not None
    assert replay.receipt.id == first.receipt.id
    assert len(verifier.calls) == 1
    assert assessments.calls == 3


def test_ac4_concurrent_different_keys_verify_once_and_return_typed_conflict(
    db: Database,
) -> None:
    session, stored_ticket = seed_session(db)
    assessments = AssessmentFake(assessment(), assessment(), assessment())
    verifier = VerificationFake(verification(stored_ticket))
    service = action_service(db, stored_ticket, assessments, verifier)
    results: dict[str, AcceptanceVerificationResult] = {}
    result_lock = threading.Lock()

    def run(key: str) -> None:
        result = service.execute(session.id, action_context(key))
        with result_lock:
            results[key] = result

    threads = [
        threading.Thread(target=run, args=(f"concurrent-{index}",))
        for index in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert all(not thread.is_alive() for thread in threads)
    assert {result.status for result in results.values()} == {
        AcceptanceVerificationStatus.MERGE_READY,
        AcceptanceVerificationStatus.CONFLICT,
    }
    conflict_key, conflict = next(
        (key, result)
        for key, result in results.items()
        if result.status is AcceptanceVerificationStatus.CONFLICT
    )
    assert not conflict.merge_ready
    assert conflict.receipt is not None
    assert conflict.receipt.outcome is OperatorActionOutcome.CONFLICT
    assert conflict.receipt.result_code is OperatorActionResultCode.ACTION_CONFLICT
    assert len(verifier.calls) == 1
    assert assessments.calls == 3

    replay = service.execute(session.id, action_context(conflict_key))

    assert replay.status is AcceptanceVerificationStatus.CONFLICT
    assert not replay.merge_ready
    assert replay.receipt == conflict.receipt
    assert len(verifier.calls) == 1
    assert assessments.calls == 3


def existing_receipt(receipt_id: UUID) -> OperatorActionReceipt:
    return OperatorActionReceipt(
        id=receipt_id,
        correlation_id=uuid4(),
        action="existing.action",
        target_type="existing",
        target_id="existing",
        created_by_type=ActorType.HUMAN,
        created_by_id="operator",
        idempotency_key_identity="sha256:" + "1" * 64,
        request_fingerprint="sha256:" + "2" * 64,
        outcome=OperatorActionOutcome.SUCCEEDED,
        result_code=OperatorActionResultCode.ACTION_SUCCEEDED,
        result_metadata={"changed": True, "affected_count": 1},
        created_at=NOW,
        completed_at=NOW,
    )


def test_ac5_receipt_failure_rolls_back_all_readiness_success(
    db: Database,
) -> None:
    session, stored_ticket = seed_session(db)
    duplicate_id = UUID("20000000-0000-4000-8000-000000000241")
    OperatorActionReceiptRepo(db).record(existing_receipt(duplicate_id))
    gateway = OperatorActionGateway(
        db,
        clock=FrozenClock(),
        receipt_id_factory=lambda: duplicate_id,
        correlation_id_factory=uuid4,
    )
    verifier = VerificationFake(verification(stored_ticket))
    service = action_service(
        db,
        stored_ticket,
        AssessmentFake(assessment(), assessment(), assessment()),
        verifier,
        gateway=gateway,
    )

    result = service.execute(session.id, action_context("receipt-collision"))

    assert result.status is AcceptanceVerificationStatus.FAILED
    assert not result.merge_ready
    assert result.failure is not None
    assert result.failure.code is OperatorActionFailureCode.RECEIPT_COMMIT_FAILED
    assert Reason.READINESS_PERSISTENCE_FAILED in result.reasons
    stored = AcceptanceSessionRepo(db).get(session.id)
    assert stored is not None
    assert stored.lifecycle is AcceptanceSessionLifecycle.CONFIRMATIONS_READY
    assert stored.stored_merge_ready is False


def successful_session(
    db: Database,
) -> tuple[AcceptanceSession, Ticket]:
    session, stored_ticket = seed_session(db)
    result = action_service(
        db,
        stored_ticket,
        AssessmentFake(assessment(), assessment(), assessment()),
        VerificationFake(verification(stored_ticket)),
    ).execute(session.id, action_context())
    assert result.session is not None and result.merge_ready
    return result.session, stored_ticket


def test_ac6_live_readiness_is_fresh_and_strictly_non_mutating(
    db: Database,
) -> None:
    stored, stored_ticket = successful_session(db)
    writes: list[str] = []

    def inspect_sql(
        _conn: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            writes.append(statement)

    service = AcceptanceSessionLiveReadinessService(
        github_client=ReadOnlyGitHubSpy(),
        ticket_lookup=MappingLookup(stored_ticket),
        session_repository=AcceptanceSessionRepo(db),
        assessment_service=AssessmentFake(assessment()),
    )
    sa.event.listen(db.engine, "before_cursor_execute", inspect_sql)
    try:
        result = service.evaluate(stored.id)
    finally:
        sa.event.remove(db.engine, "before_cursor_execute", inspect_sql)

    assert result.merge_ready
    assert result.reasons == ()
    assert writes == []
    assert AcceptanceSessionRepo(db).get(stored.id) == stored


def test_ac6_later_movement_and_criteria_drift_revoke_cached_true_without_write(
    db: Database,
) -> None:
    stored, stored_ticket = successful_session(db)
    drifted = stored_ticket.model_copy(
        update={"acceptance_criteria": ["live criteria moved"]}
    )
    service = AcceptanceSessionLiveReadinessService(
        github_client=ReadOnlyGitHubSpy(),
        ticket_lookup=MappingLookup(drifted),
        session_repository=AcceptanceSessionRepo(db),
        assessment_service=AssessmentFake(
            assessment(head_sha=OTHER_HEAD, base_sha=OTHER_BASE)
        ),
    )

    result = service.evaluate(stored.id)

    assert not result.merge_ready
    assert {
        Reason.HEAD_SHA_MISMATCH,
        Reason.BASE_SHA_MISMATCH,
        Reason.CRITERIA_MISMATCH,
    } <= set(result.reasons)
    assert AcceptanceSessionRepo(db).get(stored.id) == stored


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (TimeoutError("timeout canary"), Reason.EXTERNAL_READ_TIMEOUT),
        (
            GitHubMalformedResponseError("malformed canary"),
            Reason.EXTERNAL_RESPONSE_MALFORMED,
        ),
        (GitHubAPIError("transport canary"), Reason.EXTERNAL_READ_FAILED),
    ],
)
def test_ac6_external_read_failures_revoke_cached_true_and_preserve_history(
    db: Database,
    failure: Exception,
    expected: Reason,
) -> None:
    stored, stored_ticket = successful_session(db)
    service = AcceptanceSessionLiveReadinessService(
        github_client=ReadOnlyGitHubSpy(),
        ticket_lookup=MappingLookup(stored_ticket),
        session_repository=AcceptanceSessionRepo(db),
        assessment_service=AssessmentFake(failure),
    )

    result = service.evaluate(stored.id)

    assert not result.merge_ready
    assert {expected, Reason.EXTERNAL_STATE_INDETERMINATE} <= set(result.reasons)
    assert AcceptanceSessionRepo(db).get(stored.id) == stored


def _old_head_evidence(
    stored_ticket: Ticket,
    evidence_type: EvidenceType,
) -> Evidence:
    system = evidence_type in {EvidenceType.TEST_RESULT, EvidenceType.LINT_RESULT}
    return Evidence(
        id=uuid4(),
        product_id=stored_ticket.product_id,
        ticket_id=None if system else stored_ticket.id,
        evidence_type=evidence_type,
        status=EvidenceStatus.PASSED,
        summary="old-head proof",
        commit_sha=OTHER_HEAD,
        external_run_id=f"old-{evidence_type.value}" if system else None,
        job_name=evidence_type.value if system else None,
        source_event_at=NOW if system else None,
        payload_hash=f"hash-{evidence_type.value}" if system else None,
        raw_payload=(
            {
                "acceptance_criterion_hash": acceptance_criterion_hash(
                    "exact head is ready"
                )
            }
            if evidence_type is EvidenceType.MANUAL_APPROVAL
            else {}
        ),
        created_by_type=ActorType.SYSTEM if system else ActorType.HUMAN,
        created_by_id="ci" if system else "operator",
        created_at=NOW,
    )


def test_old_head_evidence_confirmations_and_stored_verdict_cannot_produce_readiness(
    db: Database,
) -> None:
    session, stored_ticket = seed_session(db)
    evidence_repo = EvidenceRepo(db)
    for evidence_type in (
        EvidenceType.TEST_RESULT,
        EvidenceType.LINT_RESULT,
        EvidenceType.MANUAL_APPROVAL,
    ):
        evidence_repo.add(_old_head_evidence(stored_ticket, evidence_type))
    for check in required_checks(stored_ticket):
        if check.required:
            VerificationCheckRepo(db).add(
                VerificationCheck(
                    id=uuid4(),
                    ticket_id=stored_ticket.id,
                    check_type=check.check_type,
                    status=EvidenceStatus.PASSED,
                    summary="stale stored verdict row",
                    required=True,
                    evidence_ids=[],
                    created_at=NOW,
                    completed_at=NOW,
                )
            )
    github = ReadOnlyGitHubSpy()
    service = AcceptanceSessionVerificationService(
        db=db,
        github_client=github,
        ticket_lookup=TicketRepo(db),
        gateway=OperatorActionGateway(db, clock=FrozenClock()),
        clock=FrozenClock(),
        verdict_id_factory=lambda: VERDICT_ID,
    )

    result = service.execute(session.id, action_context("old-head-regression"))

    assert not result.merge_ready
    assert Reason.VERIFICATION_PENDING in result.reasons
    assert result.session is not None
    assert result.session.stored_merge_ready is False
    latest = VerificationCheckRepo(db).list_for_ticket(stored_ticket.id)
    assert any(row.status is EvidenceStatus.PENDING for row in latest)


def test_ac7_actions_use_only_read_clients_and_never_mutate_external_systems(
    db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, stored_ticket = seed_session(db)
    github = ReadOnlyGitHubSpy()

    def forbidden(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("forbidden external or workflow mutation was called")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(LinearGraphQLClient, "update_issue", forbidden)
    monkeypatch.setattr(TicketRepoClass, "apply_linear_status", forbidden)
    monkeypatch.setattr(Database, "create_all", forbidden)
    monkeypatch.setattr(alembic.command, "upgrade", forbidden)
    monkeypatch.setattr(pm_sync, "sync_tick", forbidden)
    verifier = VerificationFake(verification(stored_ticket))
    service = AcceptanceSessionVerificationService(
        db=db,
        github_client=github,
        ticket_lookup=TicketRepo(db),
        gateway=OperatorActionGateway(db, clock=FrozenClock()),
        clock=FrozenClock(),
        verification_service=verifier,
        verdict_id_factory=lambda: VERDICT_ID,
    )

    result = service.execute(session.id, action_context("mutation-spies"))

    assert result.merge_ready
    assert github.reads
    assert github.mutations == []
    assert TicketRepo(db).get_by_key(stored_ticket.key) == stored_ticket
    assert TicketStatusTransitionRepo(db).list_all() == []
