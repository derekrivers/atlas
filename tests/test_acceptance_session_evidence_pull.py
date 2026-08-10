"""ATLAS-239 exact-head acceptance-session evidence action contract."""

from __future__ import annotations

import inspect
import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from test_acceptance_sessions import (
    HEAD,
    NOW,
    FrozenClock,
    TicketFake,
    assessment,
    create,
    creator,
    ticket,
)
from test_operator_action_gateway import seed_product, seed_terminal_receipt
from test_operator_action_receipt_model import operator_action_receipt_kwargs

from atlas.core.enums import ActorType, EvidenceStatus
from atlas.core.models import (
    AcceptanceSessionBlockingReason as Reason,
)
from atlas.core.models import (
    AcceptanceSessionLifecycle,
    AcceptanceSessionStep,
    Evidence,
    EvidenceType,
    OperatorActionOutcome,
    OperatorActionReceipt,
    OperatorActionResultCode,
)
from atlas.evidence import EvidencePullMalformedSourceError, PullResult
from atlas.github import (
    GitHubAuthenticationError,
    GitHubRateLimitError,
    GitHubRESTClient,
    GitHubTransportError,
)
from atlas.orchestration import (
    AcceptanceEvidencePullContext,
    AcceptanceSessionEvidencePullService,
    OperatorActionFailureCode,
    OperatorActionGateway,
    OperatorActionGatewayStatus,
    present_operator_action_receipt,
)
from atlas.storage import AcceptanceSessionRepo, Database, EvidenceRepo

OTHER_HEAD = "3" * 40
OTHER_BASE = "4" * 40
SOURCE_AT = datetime(2026, 8, 2, 12, 58, tzinfo=UTC)
TOKEN_CANARY = "ghp_atlas239_token_canary_do_not_persist"
PAYLOAD_CANARY = "raw-job-log-atlas239-do-not-copy"


class _RESTResponse:
    """Minimal urllib response used by real-client action regressions."""

    def __init__(self, body: bytes, *, link: str | None = None) -> None:
        self._body = body
        self.headers = {} if link is None else {"Link": link}

    def __enter__(self) -> _RESTResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{tmp_path}/atlas239.db")
    database.create_all()
    return database


class AssessmentFake:
    def __init__(self, *values: Any) -> None:
        self.values = list(values)
        self.calls = 0

    def __call__(self, *_args: Any) -> Any:
        index = min(self.calls, len(self.values) - 1)
        self.calls += 1
        value = self.values[index]
        if isinstance(value, Exception):
            raise value
        return value


class PullFake:
    def __init__(self, *outcomes: PullResult | Exception) -> None:
        self.outcomes = list(outcomes) or [PullResult([], [], [])]
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        client: Any,
        owner: str,
        repo: str,
        pr_number: int,
        *,
        evidence_repo: EvidenceRepo,
        product_id: UUID,
        now: datetime,
    ) -> PullResult:
        self.calls.append(
            {
                "client": client,
                "owner": owner,
                "repo": repo,
                "pr_number": pr_number,
                "evidence_repo": evidence_repo,
                "product_id": product_id,
                "now": now,
            }
        )
        outcome = self.outcomes[min(len(self.calls) - 1, len(self.outcomes) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        stored: dict[UUID, Evidence] = {}
        for record in [*outcome.checks, *outcome.reviews, *outcome.docs]:
            stored[record.id] = evidence_repo.add(record)
        return PullResult(
            [stored[record.id] for record in outcome.checks],
            [stored[record.id] for record in outcome.reviews],
            [stored[record.id] for record in outcome.docs],
        )


class UnpersistedPullFake(PullFake):
    """Return a claimed pull result without adding it to canonical storage."""

    def __call__(
        self,
        client: Any,
        owner: str,
        repo: str,
        pr_number: int,
        *,
        evidence_repo: EvidenceRepo,
        product_id: UUID,
        now: datetime,
    ) -> PullResult:
        self.calls.append(
            {
                "client": client,
                "owner": owner,
                "repo": repo,
                "pr_number": pr_number,
                "evidence_repo": evidence_repo,
                "product_id": product_id,
                "now": now,
            }
        )
        outcome = self.outcomes[min(len(self.calls) - 1, len(self.outcomes) - 1)]
        assert isinstance(outcome, PullResult)
        return outcome


def an_evidence(
    product_id: UUID,
    *,
    head: str = HEAD,
    evidence_type: EvidenceType = EvidenceType.TEST_RESULT,
    status: EvidenceStatus = EvidenceStatus.PASSED,
    raw_payload: dict[str, Any] | None = None,
) -> Evidence:
    return Evidence(
        id=uuid4(),
        product_id=product_id,
        evidence_type=evidence_type,
        status=status,
        summary="bounded source summary",
        commit_sha=head,
        external_run_id=f"run:{uuid4()}",
        job_name="test-python" if evidence_type in _CHECK_TYPES else None,
        source_event_at=SOURCE_AT,
        payload_hash=f"sha256:{uuid4().hex}",
        source_uri="https://github.com/acme/atlas/actions/runs/1",
        raw_payload=raw_payload or {},
        created_by_type=ActorType.SYSTEM,
        created_by_id="github-actions",
        created_at=NOW,
    )


_CHECK_TYPES = {
    EvidenceType.TEST_RESULT,
    EvidenceType.BUILD_RESULT,
    EvidenceType.LINT_RESULT,
    EvidenceType.COVERAGE_REPORT,
}


def acceptance_fixture(db: Database) -> tuple[Any, TicketFake, UUID]:
    product = seed_product(db)
    tickets = TicketFake(
        ticket("ATLAS-1", "first criterion").model_copy(
            update={"product_id": product.id}
        ),
        ticket("ATLAS-2", "second criterion").model_copy(
            update={"product_id": product.id}
        ),
    )
    created = create(creator(db, tickets, exact_assessment=assessment()))
    assert created.session is not None
    return created.session, tickets, product.id


def action_service(
    db: Database,
    tickets: TicketFake,
    assessment_fake: AssessmentFake,
    pull_fake: PullFake,
    *,
    gateway: OperatorActionGateway | None = None,
) -> AcceptanceSessionEvidencePullService:
    return AcceptanceSessionEvidencePullService(
        github_client=object(),  # type: ignore[arg-type]
        ticket_lookup=tickets,
        session_repository=AcceptanceSessionRepo(db),
        evidence_repository=EvidenceRepo(db),
        gateway=gateway or OperatorActionGateway(db, clock=FrozenClock()),
        clock=FrozenClock(),
        assessment_service=assessment_fake,
        evidence_pull_service=pull_fake,
    )


def action_context(key: str = "pull-evidence-1") -> AcceptanceEvidencePullContext:
    return AcceptanceEvidencePullContext(
        idempotency_key=key,
        created_by_type=ActorType.HUMAN,
        created_by_id="operator",
    )


def test_ac1_action_accepts_only_session_identity_and_authenticated_context(
    db: Database,
) -> None:
    session, tickets, product_id = acceptance_fixture(db)
    pulled = PullFake(PullResult([an_evidence(product_id)], [], []))
    assessments = AssessmentFake(assessment(), assessment())
    service = action_service(db, tickets, assessments, pulled)

    assert list(
        inspect.signature(AcceptanceSessionEvidencePullService.execute).parameters
    ) == [
        "self",
        "session_id",
        "context",
    ]
    result = service.execute(session.id, action_context())

    assert result.status is OperatorActionGatewayStatus.EXECUTED
    assert result.receipt is not None
    assert result.receipt.result_code is OperatorActionResultCode.ACTION_SUCCEEDED
    assert result.session is not None
    assert result.session.lifecycle is AcceptanceSessionLifecycle.EVIDENCE_READY
    assert pulled.calls[0] | {"client": None, "evidence_repo": None, "now": None} == {
        "client": None,
        "owner": session.repository_owner,
        "repo": session.repository_name,
        "pr_number": session.pr_number,
        "evidence_repo": None,
        "product_id": product_id,
        "now": None,
    }
    second = service.execute(session.id, action_context("new-key-after-complete"))
    assert second.receipt is not None
    assert second.receipt.result_code is OperatorActionResultCode.ACTION_REFUSED
    assert len(pulled.calls) == 1
    assert assessments.calls == 2


def test_ac2_pre_pull_freshness_returns_all_reasons_and_performs_no_pull(
    db: Database,
) -> None:
    session, tickets, _ = acceptance_fixture(db)
    moved = assessment(
        head_ref="moved-ref",
        head_sha=OTHER_HEAD,
        base_sha=OTHER_BASE,
        base_repository="acme/other",
    )
    pulled = PullFake()
    service = action_service(db, tickets, AssessmentFake(moved), pulled)

    result = service.execute(session.id, action_context())

    assert pulled.calls == []
    assert result.receipt is not None
    assert result.receipt.result_code is OperatorActionResultCode.STALE_STATE
    assert result.session is not None
    assert result.session.lifecycle is AcceptanceSessionLifecycle.STALE
    assert result.reasons == (
        Reason.HEAD_REF_MISMATCH,
        Reason.HEAD_SHA_MISMATCH,
        Reason.BASE_SHA_MISMATCH,
        Reason.BASE_REPOSITORY_MISMATCH,
    )


def test_ac3_action_invokes_shared_evidence_pull_service_directly(
    db: Database,
) -> None:
    session, tickets, product_id = acceptance_fixture(db)
    pulled = PullFake(
        PullResult(
            [an_evidence(product_id)],
            [an_evidence(product_id, evidence_type=EvidenceType.PR_REVIEW)],
            [an_evidence(product_id, evidence_type=EvidenceType.DOCUMENTATION_UPDATE)],
        )
    )
    service = action_service(
        db, tickets, AssessmentFake(assessment(), assessment()), pulled
    )

    result = service.execute(session.id, action_context())

    assert len(pulled.calls) == 1
    assert pulled.calls[0]["client"] is not None
    assert isinstance(pulled.calls[0]["evidence_repo"], EvidenceRepo)
    assert result.session is not None
    summary = result.session.step_summaries[AcceptanceSessionStep.EVIDENCE].evidence
    assert summary is not None
    assert (summary.checks_count, summary.reviews_count, summary.docs_count) == (
        1,
        1,
        1,
    )
    module_source = inspect.getsource(type(service))
    assert "subprocess" not in module_source
    assert "normalise_" not in module_source


def test_ac4_post_pull_movement_stales_session_and_keeps_old_head_history(
    db: Database,
) -> None:
    session, tickets, product_id = acceptance_fixture(db)
    old_head_record = an_evidence(product_id)
    pulled = PullFake(PullResult([old_head_record], [], []))
    service = action_service(
        db,
        tickets,
        AssessmentFake(assessment(), assessment(head_sha=OTHER_HEAD)),
        pulled,
    )

    result = service.execute(session.id, action_context())

    assert result.session is not None
    assert result.session.lifecycle is AcceptanceSessionLifecycle.STALE
    assert result.reasons == (Reason.HEAD_SHA_MISMATCH,)
    assert EvidenceRepo(db).list_for_product_commit(product_id, HEAD) == [
        old_head_record
    ]
    assert EvidenceRepo(db).list_for_product_commit(product_id, OTHER_HEAD) == []
    evidence_step = result.session.step_summaries[AcceptanceSessionStep.EVIDENCE]
    assert evidence_step.evidence is None


def test_ac5_success_advances_once_with_bounded_secret_free_summary(
    db: Database,
) -> None:
    session, tickets, product_id = acceptance_fixture(db)
    record = an_evidence(
        product_id,
        raw_payload={"token": TOKEN_CANARY, "job_log": PAYLOAD_CANARY},
    )
    pulled = PullFake(PullResult([record], [], []))
    service = action_service(
        db, tickets, AssessmentFake(assessment(), assessment()), pulled
    )

    result = service.execute(session.id, action_context(TOKEN_CANARY))

    assert result.session is not None
    summary = result.session.step_summaries[AcceptanceSessionStep.EVIDENCE].evidence
    assert summary is not None
    assert summary.model_dump() == {
        "total_count": 1,
        "new_count": 1,
        "checks_count": 1,
        "reviews_count": 0,
        "docs_count": 0,
        "system_count": 1,
        "human_count": 0,
        "agent_count": 0,
        "pending_count": 0,
        "passed_count": 1,
        "failed_count": 0,
        "warning_count": 0,
        "not_applicable_count": 0,
        "complete_pin_count": 1,
        "exact_head_pin_count": 1,
        "pin_complete": True,
        "exact_head_pin_complete": True,
        "oldest_source_event_at": SOURCE_AT,
        "latest_source_event_at": SOURCE_AT,
    }
    assert result.receipt is not None
    retained = json.dumps(
        {
            "session": result.session.model_dump(mode="json"),
            "receipt": present_operator_action_receipt(result.receipt),
        },
        sort_keys=True,
    )
    assert TOKEN_CANARY not in retained
    assert PAYLOAD_CANARY not in retained
    assert "raw_payload" not in retained
    assert "job_log" not in retained


def test_ac5_receipt_failure_rolls_back_advance_but_not_canonical_evidence(
    db: Database,
) -> None:
    session, tickets, product_id = acceptance_fixture(db)
    duplicate_id = UUID("30000000-0000-4000-8000-000000000001")
    existing = OperatorActionReceipt(
        **operator_action_receipt_kwargs()
        | {
            "id": duplicate_id,
            "correlation_id": UUID("40000000-0000-4000-8000-000000000001"),
        }
    )
    seed_terminal_receipt(db, existing)
    gateway = OperatorActionGateway(
        db,
        clock=FrozenClock(),
        receipt_id_factory=lambda: duplicate_id,
        correlation_id_factory=lambda: UUID("40000000-0000-4000-8000-000000000002"),
    )
    record = an_evidence(product_id)
    pulled = PullFake(PullResult([record], [], []))
    service = action_service(
        db,
        tickets,
        AssessmentFake(assessment(), assessment()),
        pulled,
        gateway=gateway,
    )

    result = service.execute(session.id, action_context())

    assert result.status is OperatorActionGatewayStatus.FAILED
    assert result.failure is not None
    assert result.failure.code is OperatorActionFailureCode.RECEIPT_COMMIT_FAILED
    stored = AcceptanceSessionRepo(db).get(session.id)
    assert stored is not None
    assert stored.lifecycle is AcceptanceSessionLifecycle.PREFLIGHT_PASSED
    assert EvidenceRepo(db).list_for_product_commit(product_id, HEAD) == [record]


def test_ac6_replay_concurrency_and_altered_replay_never_repeat_external_work(
    db: Database,
) -> None:
    session, tickets, product_id = acceptance_fixture(db)
    pulled = PullFake(PullResult([an_evidence(product_id)], [], []))
    assessments = AssessmentFake(assessment(), assessment())
    service = action_service(db, tickets, assessments, pulled)
    start = threading.Barrier(2)
    results: list[Any] = []

    def run() -> None:
        start.wait()
        results.append(service.execute(session.id, action_context("shared-key")))

    threads = [threading.Thread(target=run), threading.Thread(target=run)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert len(pulled.calls) == 1
    assert sorted(result.status.value for result in results) == ["executed", "replayed"]
    replay = service.execute(session.id, action_context("shared-key"))
    assert replay.status is OperatorActionGatewayStatus.REPLAYED
    altered = service.execute(uuid4(), action_context("shared-key"))
    assert altered.status is OperatorActionGatewayStatus.CONFLICT
    assert len(pulled.calls) == 1
    assert assessments.calls == 2


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (
            GitHubTransportError("transport canary that must not persist"),
            OperatorActionResultCode.EVIDENCE_TRANSPORT_FAILED,
        ),
        (
            GitHubAuthenticationError("authentication canary that must not persist"),
            OperatorActionResultCode.EVIDENCE_AUTHENTICATION_FAILED,
        ),
        (
            GitHubRateLimitError("rate-limit canary that must not persist"),
            OperatorActionResultCode.EVIDENCE_RATE_LIMIT_FAILED,
        ),
        (
            EvidencePullMalformedSourceError("malformed canary that must not persist"),
            OperatorActionResultCode.EVIDENCE_MALFORMED_SOURCE,
        ),
    ],
)
def test_ac7_external_failures_are_distinct_nonadvancing_and_retryable(
    db: Database,
    failure: Exception,
    code: OperatorActionResultCode,
) -> None:
    session, tickets, _ = acceptance_fixture(db)
    pulled = PullFake(failure, PullResult([], [], []))
    service = action_service(
        db,
        tickets,
        AssessmentFake(assessment(), assessment(), assessment()),
        pulled,
    )

    failed = service.execute(session.id, action_context("failed-key"))

    assert failed.receipt is not None
    assert failed.receipt.result_code is code
    assert failed.session is not None
    assert failed.session.lifecycle is AcceptanceSessionLifecycle.PREFLIGHT_PASSED
    assert len(pulled.calls) == 1
    replay = service.execute(session.id, action_context("failed-key"))
    assert replay.status is OperatorActionGatewayStatus.REPLAYED
    assert replay.receipt is not None and replay.receipt.result_code is code
    assert len(pulled.calls) == 1

    retried = service.execute(session.id, action_context("retry-key"))
    assert retried.receipt is not None
    assert retried.receipt.result_code is OperatorActionResultCode.ACTION_SUCCEEDED
    assert retried.session is not None
    assert retried.session.lifecycle is AcceptanceSessionLifecycle.EVIDENCE_READY
    assert len(pulled.calls) == 2


@pytest.mark.parametrize(
    "scenario",
    [
        "empty-envelope",
        "top-level-list-envelope",
        "missing-list-field",
        "wrong-list-field",
        "malformed-pagination",
        "out-of-origin-pagination",
        "cyclic-pagination",
        "object-for-bare-array",
    ],
)
def test_real_client_malformed_source_is_terminal_and_replays_without_requests(
    db: Database,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
) -> None:
    session, tickets, product_id = acceptance_fixture(db)
    requests: list[str] = []
    cycle_url = "https://api.github.com/atlas-test-cycle?page=2"

    def _urlopen(request: Any, *args: Any, **kwargs: Any) -> _RESTResponse:
        url = request.full_url
        requests.append(url)
        pr_url = (
            f"https://api.github.com/repos/{session.repository_owner}/"
            f"{session.repository_name}/pulls/{session.pr_number}"
        )
        if url == pr_url:
            return _RESTResponse(json.dumps({"head": {"sha": HEAD}}).encode())
        if url == cycle_url:
            return _RESTResponse(
                b'{"workflow_runs": []}', link=f'<{cycle_url}>; rel="next"'
            )
        if "/actions/runs?" in url:
            if scenario == "empty-envelope":
                return _RESTResponse(b"{}")
            if scenario == "top-level-list-envelope":
                return _RESTResponse(b"[]")
            if scenario == "missing-list-field":
                return _RESTResponse(b'{"unexpected": []}')
            if scenario == "wrong-list-field":
                return _RESTResponse(b'{"workflow_runs": {}}')
            if scenario == "malformed-pagination":
                return _RESTResponse(
                    b'{"workflow_runs": []}',
                    link='https://api.github.com/page/2; rel="next"',
                )
            if scenario == "out-of-origin-pagination":
                return _RESTResponse(
                    b'{"workflow_runs": []}',
                    link='<https://example.com/page/2>; rel="next"',
                )
            if scenario == "cyclic-pagination":
                return _RESTResponse(
                    b'{"workflow_runs": []}', link=f'<{cycle_url}>; rel="next"'
                )
            return _RESTResponse(b'{"workflow_runs": []}')
        if "/check-runs?" in url:
            return _RESTResponse(b'{"check_runs": []}')
        if "/reviews?" in url:
            if scenario == "object-for-bare-array":
                return _RESTResponse(b"{}")
            return _RESTResponse(b"[]")
        if "/files?" in url:
            return _RESTResponse(b"[]")
        raise AssertionError(f"unexpected GitHub request: {url}")

    monkeypatch.setattr("atlas.github.client.urllib_request.urlopen", _urlopen)
    service = AcceptanceSessionEvidencePullService(
        github_client=GitHubRESTClient(token=TOKEN_CANARY),
        ticket_lookup=tickets,
        session_repository=AcceptanceSessionRepo(db),
        evidence_repository=EvidenceRepo(db),
        gateway=OperatorActionGateway(db, clock=FrozenClock()),
        clock=FrozenClock(),
        assessment_service=AssessmentFake(assessment()),
    )
    context = action_context(f"malformed-source-{scenario}")

    result = service.execute(session.id, context)

    assert result.status is OperatorActionGatewayStatus.EXECUTED
    assert result.failure is None
    assert result.receipt is not None
    assert result.receipt.outcome is OperatorActionOutcome.FAILED
    assert (
        result.receipt.result_code is OperatorActionResultCode.EVIDENCE_MALFORMED_SOURCE
    )
    assert result.session is not None
    assert result.session.lifecycle is AcceptanceSessionLifecycle.PREFLIGHT_PASSED
    assert EvidenceRepo(db).list_for_product_commit(product_id, HEAD) == []
    retained = json.dumps(
        present_operator_action_receipt(result.receipt), sort_keys=True
    )
    assert TOKEN_CANARY not in retained

    request_count = len(requests)
    replay = service.execute(session.id, context)

    assert replay.status is OperatorActionGatewayStatus.REPLAYED
    assert replay.receipt is not None
    assert replay.receipt.id == result.receipt.id
    assert replay.receipt.outcome is OperatorActionOutcome.FAILED
    assert len(requests) == request_count


def test_old_head_evidence_cannot_satisfy_a_new_exact_head_summary(
    db: Database,
) -> None:
    session, tickets, product_id = acceptance_fixture(db)
    EvidenceRepo(db).add(an_evidence(product_id, head=OTHER_HEAD))
    pulled = PullFake(PullResult([], [], []))
    service = action_service(
        db, tickets, AssessmentFake(assessment(), assessment()), pulled
    )

    result = service.execute(session.id, action_context())

    assert result.session is not None
    summary = result.session.step_summaries[AcceptanceSessionStep.EVIDENCE].evidence
    assert summary is not None
    assert summary.total_count == 0
    assert summary.new_count == 0
    assert summary.exact_head_pin_complete is True


def test_mixed_pull_counts_only_new_exact_head_rows_and_preserves_history(
    db: Database,
) -> None:
    session, tickets, product_id = acceptance_fixture(db)
    existing_check = EvidenceRepo(db).add(an_evidence(product_id))
    unchanged_check = existing_check.model_copy(update={"id": uuid4()})
    new_docs = an_evidence(
        product_id,
        evidence_type=EvidenceType.DOCUMENTATION_UPDATE,
    )
    historical_review = an_evidence(
        product_id,
        head=OTHER_HEAD,
        evidence_type=EvidenceType.PR_REVIEW,
    )
    pulled = PullFake(PullResult([unchanged_check], [historical_review], [new_docs]))
    service = action_service(
        db, tickets, AssessmentFake(assessment(), assessment()), pulled
    )

    result = service.execute(session.id, action_context("mixed-history"))
    replay = service.execute(session.id, action_context("mixed-history"))

    assert result.session is not None
    assert result.session.lifecycle is AcceptanceSessionLifecycle.EVIDENCE_READY
    summary = result.session.step_summaries[AcceptanceSessionStep.EVIDENCE].evidence
    assert summary is not None
    assert (summary.total_count, summary.new_count) == (2, 1)
    assert (summary.checks_count, summary.reviews_count, summary.docs_count) == (
        1,
        0,
        1,
    )
    assert {
        record.id
        for record in EvidenceRepo(db).list_for_product_commit(product_id, HEAD)
    } == {existing_check.id, new_docs.id}
    assert EvidenceRepo(db).list_for_product_commit(product_id, OTHER_HEAD) == [
        historical_review
    ]
    assert replay.status is OperatorActionGatewayStatus.REPLAYED
    assert len(pulled.calls) == 1


@pytest.mark.parametrize(
    "record",
    [
        an_evidence(uuid4()).model_copy(update={"external_run_id": None}),
        an_evidence(uuid4()),
    ],
    ids=["incomplete-pin", "absent-canonical-current-head"],
)
def test_malformed_or_falsely_current_pull_result_does_not_advance(
    db: Database,
    record: Evidence,
) -> None:
    session, tickets, product_id = acceptance_fixture(db)
    claimed = record.model_copy(update={"product_id": product_id})
    pulled = UnpersistedPullFake(PullResult([claimed], [], []))
    service = action_service(
        db, tickets, AssessmentFake(assessment(), assessment()), pulled
    )

    result = service.execute(session.id, action_context("invalid-result"))

    assert result.receipt is not None
    assert (
        result.receipt.result_code is OperatorActionResultCode.EVIDENCE_MALFORMED_SOURCE
    )
    assert result.session is not None
    assert result.session.lifecycle is AcceptanceSessionLifecycle.PREFLIGHT_PASSED
    assert EvidenceRepo(db).list_for_product_commit(product_id, HEAD) == []


def test_source_idempotent_return_of_existing_row_reports_zero_new(
    db: Database,
) -> None:
    session, tickets, product_id = acceptance_fixture(db)
    existing = EvidenceRepo(db).add(an_evidence(product_id))
    unchanged_source = existing.model_copy(update={"id": uuid4()})
    pulled = PullFake(PullResult([unchanged_source], [], []))
    service = action_service(
        db, tickets, AssessmentFake(assessment(), assessment()), pulled
    )

    result = service.execute(session.id, action_context())

    assert result.session is not None
    summary = result.session.step_summaries[AcceptanceSessionStep.EVIDENCE].evidence
    assert summary is not None
    assert summary.total_count == 1
    assert summary.new_count == 0
    assert EvidenceRepo(db).list_for_product_commit(product_id, HEAD) == [existing]


def test_genuinely_appended_current_head_record_reports_one_new(
    db: Database,
) -> None:
    session, tickets, product_id = acceptance_fixture(db)
    record = an_evidence(product_id)
    pulled = PullFake(PullResult([record], [], []))
    service = action_service(
        db, tickets, AssessmentFake(assessment(), assessment()), pulled
    )

    result = service.execute(session.id, action_context())

    assert result.session is not None
    summary = result.session.step_summaries[AcceptanceSessionStep.EVIDENCE].evidence
    assert summary is not None
    assert summary.total_count == 1
    assert summary.new_count == 1
    assert EvidenceRepo(db).list_for_product_commit(product_id, HEAD) == [record]
