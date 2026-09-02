"""Exact-proof and fresh-process tests for retrospective merged completion."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from github_fakes import FakeGitHubClient
from pm_temporal_harness import SimulatedProcessDeath, TemporalHarness
from test_models_validation import product_kwargs, ticket_kwargs
from test_pm_sync import (
    CI_PENDING_STATE,
    DONE_STATE,
    PROJECT_ID,
    TEAM_ID,
    RecordingClient,
    seed_default_admission_policy,
    status_map,
)

from atlas.core.enums import ActorType, EvidenceStatus
from atlas.core.models import (
    AcceptanceSession,
    AcceptanceSessionLifecycle,
    AcceptanceSessionStep,
    AcceptanceSessionStepState,
    CIHandoffClassification,
    CIHandoffDecision,
    CIHandoffReason,
    CIHandoffReconciliation,
    Product,
    Ticket,
    TicketStatus,
    VerificationCheck,
)
from atlas.core.models.acceptance_session import (
    AcceptanceAssessmentSnapshot,
    AcceptanceReadinessAssessment,
    AcceptanceStepSummary,
    AcceptanceVerificationSummary,
)
from atlas.core.models.ci_handoff_reconciliation import CIHandoffCheckResult
from atlas.core.models.evidence import Evidence, EvidenceType
from atlas.evidence import build_merge_evidence
from atlas.github import GitHubCompare, GitHubCompareStatus
from atlas.linear.client import LinearClient
from atlas.orchestration.acceptance_sessions import (
    acceptance_criteria_fingerprint,
    acceptance_criteria_snapshot,
)
from atlas.pm import (
    RetrospectiveCompletionHooks,
    RetrospectiveCompletionResult,
    reconcile_retrospective_completion,
    reconcile_retrospective_completion_fence,
    sync_tick,
)
from atlas.pm.ci_handoff_adapter import resolve_issue_bound_publication
from atlas.pm.ci_handoff_fairness import select_fair_ci_handoff_candidate
from atlas.storage import (
    AcceptanceSessionRepo,
    CIHandoffReconciliationRepo,
    Database,
    EvidenceRepo,
    PmRecoveryRepo,
    ProductRepo,
    RetrospectiveCompletionCoordinationRepo,
    RetrospectiveCompletionReconciliationRepo,
    TicketRepo,
    VerificationCheckRepo,
)
from atlas.verification import (
    build_acceptance_confirmation,
    evaluate_ticket,
    required_checks,
)

NOW = datetime(2026, 9, 2, 12, tzinfo=UTC)
HEAD = "a" * 40
OTHER_HEAD = "b" * 40
MERGE = "c" * 40
MAIN = "d" * 40
OWNER = "derekrivers"
REPO = "atlas"
PR = 335
ATTACHMENT = "merged-attachment-335"
SOURCE_PATH = "atlas/pm/retrospective_completion.py"


class DelegatingLinearClient:
    """Fresh process object over one provider world that survives restarts."""

    def __init__(self, provider: RecordingClient) -> None:
        self.provider = provider

    def __getattr__(self, name: str) -> Any:
        return getattr(self.provider, name)


def _database(path: Path) -> Database:
    database = Database(f"sqlite:///{path}")
    database.create_all()
    return database


def _ticket(product_id: UUID, issue_id: str, *, key: str = "ATLAS-335") -> Ticket:
    return Ticket(
        **ticket_kwargs()
        | {
            "id": uuid4(),
            "product_id": product_id,
            "key": key,
            "status": TicketStatus.CI_PENDING,
            "external_linear_id": issue_id,
            "acceptance_criteria": ["Retrospective proof is exact."],
            "source_anchor": SOURCE_PATH + "#owner",
            "created_at": NOW,
            "updated_at": NOW,
        }
    )


def _system_evidence(
    ticket: Ticket, evidence_type: EvidenceType, *, job_name: str
) -> Evidence:
    return Evidence(
        id=uuid4(),
        product_id=ticket.product_id,
        ticket_id=ticket.id,
        evidence_type=evidence_type,
        status=EvidenceStatus.PASSED,
        summary=f"{job_name} passed",
        commit_sha=HEAD,
        external_run_id=f"run:{ticket.key}:{job_name}:{HEAD}",
        job_name=job_name,
        source_event_at=NOW - timedelta(minutes=10),
        payload_hash=(
            "sha256:" + hashlib.sha256(f"{ticket.key}:{job_name}".encode()).hexdigest()
        ),
        raw_payload={"name": job_name},
        created_by_type=ActorType.SYSTEM,
        created_by_id="github-actions",
        created_at=NOW - timedelta(minutes=10),
    )


def _acceptance_session(
    ticket: Ticket, verdict_id: UUID, *, pr_number: int = PR
) -> AcceptanceSession:
    snapshot = acceptance_criteria_snapshot((ticket.key,), (ticket,))
    fingerprint = acceptance_criteria_fingerprint(snapshot)
    receipt_id = uuid4()
    return AcceptanceSession(
        id=uuid4(),
        repository_owner=OWNER,
        repository_name=REPO,
        pr_number=pr_number,
        close_set=(ticket.key,),
        head_ref="codex/exact-head",
        head_sha=HEAD,
        head_repository=f"{OWNER}/{REPO}",
        base_ref="main",
        base_sha="1" * 40,
        base_repository=f"{OWNER}/{REPO}",
        initial_assessment=AcceptanceAssessmentSnapshot(
            pr_state="open",
            pr_draft=False,
            pr_merged=False,
            base_sha_source="live_branch",
            merge_base_sha="1" * 40,
            ahead_by=1,
            behind_by=0,
            compare_status="ahead",
            mergeability="mergeable",
            ancestry="current",
            eligibility="eligible",
            integration_status="current",
        ),
        criteria_snapshot=snapshot,
        criteria_fingerprint=fingerprint,
        creation_idempotency_key_identity=f"sha256:{pr_number:064x}",
        created_by_type=ActorType.HUMAN,
        created_by_id="operator",
        lifecycle=AcceptanceSessionLifecycle.MERGE_READY,
        step_summaries={
            AcceptanceSessionStep.PREFLIGHT: AcceptanceStepSummary(
                state=AcceptanceSessionStepState.COMPLETE,
                occurred_at=NOW - timedelta(hours=2),
            ),
            AcceptanceSessionStep.EVIDENCE: AcceptanceStepSummary(
                state=AcceptanceSessionStepState.COMPLETE,
                occurred_at=NOW - timedelta(hours=2),
            ),
            AcceptanceSessionStep.CONFIRMATIONS: AcceptanceStepSummary(
                state=AcceptanceSessionStepState.COMPLETE,
                receipt_ids=(receipt_id,),
                occurred_at=NOW - timedelta(hours=1),
            ),
            AcceptanceSessionStep.VERIFICATION: AcceptanceStepSummary(
                state=AcceptanceSessionStepState.COMPLETE,
                receipt_ids=(receipt_id,),
                occurred_at=NOW - timedelta(hours=1),
                verification=AcceptanceVerificationSummary(
                    verdict_id=verdict_id,
                    status=EvidenceStatus.PASSED,
                    head_commit=HEAD,
                    ticket_count=1,
                    blocking_check_count=0,
                ),
            ),
            AcceptanceSessionStep.READINESS: AcceptanceStepSummary(
                state=AcceptanceSessionStepState.COMPLETE,
                receipt_ids=(receipt_id,),
                occurred_at=NOW - timedelta(hours=1),
                readiness=AcceptanceReadinessAssessment(
                    verdict_id=verdict_id,
                    repository_owner=OWNER,
                    repository_name=REPO,
                    pr_number=pr_number,
                    head_ref="codex/exact-head",
                    head_sha=HEAD,
                    head_repository=f"{OWNER}/{REPO}",
                    base_ref="main",
                    base_sha="1" * 40,
                    base_repository=f"{OWNER}/{REPO}",
                    eligibility="eligible",
                    integration_status="current",
                    criteria_fingerprint=fingerprint,
                ),
            ),
        },
        stored_merge_ready=True,
        historical_readiness_reasons=(),
        created_at=NOW - timedelta(hours=2),
        updated_at=NOW - timedelta(hours=1),
    )


def _github(
    *,
    compare: GitHubCompare | None = None,
    head: str = HEAD,
    pr_number: int = PR,
    main: str = MAIN,
) -> FakeGitHubClient:
    return FakeGitHubClient(
        pull_request={
            "number": pr_number,
            "state": "closed",
            "merged": True,
            "merge_commit_sha": MERGE,
            "head": {
                "ref": "codex/exact-head",
                "sha": head,
                "repo": {"full_name": f"{OWNER}/{REPO}"},
            },
            "base": {
                "ref": "main",
                "sha": "1" * 40,
                "repo": {"full_name": f"{OWNER}/{REPO}"},
            },
        },
        branch_head_sha=main,
        compare=compare
        or GitHubCompare(
            status=GitHubCompareStatus.AHEAD,
            ahead_by=1,
            behind_by=0,
            merge_base_sha=MERGE,
        ),
        pr_files=[{"filename": SOURCE_PATH}],
    )


def _seed_complete_world(
    db: Database,
    linear: RecordingClient,
    *,
    include_acceptance: bool = True,
    ci_head: str = HEAD,
    key: str = "ATLAS-335",
    pr_number: int = PR,
    product_id: UUID | None = None,
) -> tuple[Ticket, UUID]:
    if product_id is None:
        product = Product(**product_kwargs() | {"id": uuid4(), "key": f"P-{key}"})
        ProductRepo(db).add(product)
        seed_default_admission_policy(db, product.id)
    else:
        stored_product = ProductRepo(db).get(product_id)
        assert stored_product is not None
        product = stored_product
    issue = linear.create_issue(
        {"title": "Historical", "description": "historical"},
        team_id=TEAM_ID,
        project_id=PROJECT_ID,
    )
    linear.simulate_linear_state(issue.id, CI_PENDING_STATE)
    linear.seed_merged_github_publication(
        issue.id,
        owner=OWNER,
        repo=REPO,
        pr_number=pr_number,
        attachment_id=f"{ATTACHMENT}-{pr_number}",
    )
    ticket = _ticket(product.id, issue.id, key=key)
    TicketRepo(db).add(ticket)
    evidence_repo = EvidenceRepo(db)
    evidence_repo.add(
        _system_evidence(ticket, EvidenceType.TEST_RESULT, job_name="test")
    )
    evidence_repo.add(
        _system_evidence(ticket, EvidenceType.LINT_RESULT, job_name="lint")
    )
    if include_acceptance:
        evidence_repo.add(
            build_acceptance_confirmation(
                ticket.acceptance_criteria[0],
                ticket_id=ticket.id,
                head_commit=HEAD,
                product_id=ticket.product_id,
                operator_id="operator",
                evidence_id=uuid4(),
                now=NOW - timedelta(hours=1),
            )
        )
    merge_evidence = build_merge_evidence(
        _github(pr_number=pr_number).fetch_pull_request(OWNER, REPO, pr_number),
        head_commit=HEAD,
        ticket_id=ticket.id,
        product_id=ticket.product_id,
        evidence_id=uuid4(),
        now=NOW - timedelta(minutes=30),
    )
    assert merge_evidence is not None
    evidence_repo.add(merge_evidence)
    evidence = evidence_repo.list_for_product_commit(ticket.product_id, HEAD)
    verification = evaluate_ticket(
        ticket,
        pr_files=[SOURCE_PATH],
        head_commit=HEAD,
        evidence=evidence,
    )
    checks = VerificationCheckRepo(db)
    for outcome in verification.checks:
        if outcome.required:
            checks.add(
                VerificationCheck(
                    id=uuid4(),
                    ticket_id=ticket.id,
                    check_type=outcome.check_type,
                    status=outcome.status,
                    summary=outcome.reason,
                    required=True,
                    evidence_ids=list(outcome.evidence_ids),
                    created_at=NOW - timedelta(minutes=20),
                    completed_at=NOW - timedelta(minutes=20),
                )
            )
    verdict_id = uuid4()
    AcceptanceSessionRepo(db).add(
        _acceptance_session(ticket, verdict_id, pr_number=pr_number)
    )
    first_required = next(item for item in required_checks(ticket) if item.required)
    CIHandoffReconciliationRepo(db).record(
        CIHandoffReconciliation(
            id=uuid4(),
            product_id=ticket.product_id,
            ticket_id=ticket.id,
            ticket_key=ticket.key,
            linear_issue_id=ticket.external_linear_id,
            repository_owner=OWNER,
            repository_name=REPO,
            pr_number=pr_number,
            head_commit=ci_head,
            classification=CIHandoffClassification.PASSED,
            reason=CIHandoffReason.COMPLETE_REQUIRED_CHECKS_PASSED,
            decision=CIHandoffDecision.REVIEW_REQUIRED,
            check_results=(
                CIHandoffCheckResult(
                    check_type=first_required.check_type,
                    status=EvidenceStatus.PASSED,
                    classification=CIHandoffClassification.PASSED,
                    evidence_ids=(),
                ),
            ),
            observed_at=NOW - timedelta(hours=3),
            created_by_type=ActorType.SYSTEM,
            created_by_id="ci-handoff-reconciler",
        )
    )
    selection = select_fair_ci_handoff_candidate(
        db=db,
        tickets=TicketRepo(db),
        initial_issues=linear.fetch_project_issues(PROJECT_ID),
        now=NOW - timedelta(minutes=1),
    )
    assert selection.episode is not None
    linear.state_writes.clear()
    return ticket, selection.episode.id


def _run(
    db: Database,
    linear: LinearClient,
    ticket: Ticket,
    episode_id: UUID,
    github: FakeGitHubClient,
    *,
    hooks: RetrospectiveCompletionHooks | None = None,
) -> RetrospectiveCompletionResult:
    issue = next(
        item
        for item in linear.fetch_project_issues(PROJECT_ID)
        if item.id == ticket.external_linear_id
    )
    publication = issue.merged_github_publications[0]
    return reconcile_retrospective_completion(
        db=db,
        tickets=TicketRepo(db),
        github=github,
        linear=linear,
        status_map=status_map(),
        project_id=PROJECT_ID,
        initial_issues=linear.fetch_project_issues(PROJECT_ID),
        ticket_key=ticket.key,
        publication=publication,
        now=NOW,
        recovery_episode_id=episode_id,
        hooks=hooks,
    )


def test_historical_completion_requires_separate_ordinary_rejection_and_exact_proof(
    tmp_path: Path,
) -> None:
    db = _database(tmp_path / "atlas.db")
    linear = RecordingClient()
    ticket, episode_id = _seed_complete_world(db, linear)
    issues = linear.fetch_project_issues(PROJECT_ID)

    publication, ordinary_reason = resolve_issue_bound_publication(ticket, issues)
    assert publication is None
    assert ordinary_reason is not None

    result = _run(db, linear, ticket, episode_id, _github())

    assert result.linear_mutations == 1
    assert linear.state_writes == [(ticket.external_linear_id, DONE_STATE.id)]
    retained = TicketRepo(db).get_by_key(ticket.key)
    assert retained is not None
    assert retained.status is TicketStatus.CI_PENDING
    [decision] = RetrospectiveCompletionReconciliationRepo(db).list_for_ticket(
        ticket.id
    )
    assert decision.contributor_head == HEAD
    assert decision.merge_commit == MERGE
    assert decision.canonical_main == MAIN
    assert decision.policy_id is not None
    assert decision.policy_revision == 1
    assert decision.policy_fingerprint is not None
    assert decision.snapshot_fingerprint is not None
    assert decision.acceptance_session_id is not None
    assert decision.verification_check_ids
    assert decision.deciding_evidence_ids
    assert decision.merged_evidence_id is not None


def test_sync_cadence_routes_merged_candidate_and_persists_typed_hold(
    tmp_path: Path,
) -> None:
    db = _database(tmp_path / "sync-hold.db")
    linear = RecordingClient()
    ticket, _episode_id = _seed_complete_world(db, linear, include_acceptance=False)

    result = sync_tick(
        tickets=TicketRepo(db),
        db=db,
        client=linear,
        status_map=status_map(),
        team_id=TEAM_ID,
        project_id=PROJECT_ID,
        inbox_dir=tmp_path / "inbox",
        documents=lambda: [],
        now=NOW,
        github_client=_github(),
        completion_clock=lambda: NOW + timedelta(seconds=1),
    )

    assert result.ci_handoff_evaluated == 1
    assert result.ci_handoff_held == 1
    assert result.ci_handoff_mutations == 0
    assert linear.state_writes == []
    [blocker] = PmRecoveryRepo(db).list_blockers(
        product_id=ticket.product_id,
        active_only=True,
    )
    assert blocker.code.value == "retrospective_proof_incomplete"


def test_sync_cadence_complete_proof_consumes_the_only_workflow_effect(
    tmp_path: Path,
) -> None:
    db = _database(tmp_path / "sync-complete.db")
    linear = RecordingClient()
    ticket, _episode_id = _seed_complete_world(db, linear)

    result = sync_tick(
        tickets=TicketRepo(db),
        db=db,
        client=linear,
        status_map=status_map(),
        team_id=TEAM_ID,
        project_id=PROJECT_ID,
        inbox_dir=tmp_path / "inbox",
        documents=lambda: [],
        now=NOW,
        github_client=_github(),
        completion_clock=lambda: NOW + timedelta(seconds=1),
    )

    assert result.ci_handoff_mutations == 1
    assert linear.state_writes == [(ticket.external_linear_id, DONE_STATE.id)]
    assert result.completed == 0


def test_held_history_does_not_starve_later_independent_candidate(
    tmp_path: Path,
) -> None:
    db = _database(tmp_path / "independent-progress.db")
    linear = RecordingClient()
    poison, _ = _seed_complete_world(
        db, linear, include_acceptance=False, key="ATLAS-335"
    )

    first = sync_tick(
        tickets=TicketRepo(db),
        db=db,
        client=linear,
        status_map=status_map(),
        team_id=TEAM_ID,
        project_id=PROJECT_ID,
        inbox_dir=tmp_path / "inbox",
        documents=lambda: [],
        now=NOW,
        github_client=_github(),
        completion_clock=lambda: NOW + timedelta(seconds=1),
    )
    assert first.ci_handoff_held == 1
    assert linear.state_writes == []

    independent, _ = _seed_complete_world(
        db,
        linear,
        include_acceptance=True,
        key="ATLAS-336",
        pr_number=PR + 1,
        product_id=poison.product_id,
    )
    second = sync_tick(
        tickets=TicketRepo(db),
        db=db,
        client=linear,
        status_map=status_map(),
        team_id=TEAM_ID,
        project_id=PROJECT_ID,
        inbox_dir=tmp_path / "inbox",
        documents=lambda: [],
        now=NOW + timedelta(minutes=1),
        github_client=_github(),
        completion_clock=lambda: NOW + timedelta(minutes=1, seconds=1),
    )
    assert second.ci_handoff_held == 1

    third = sync_tick(
        tickets=TicketRepo(db),
        db=db,
        client=linear,
        status_map=status_map(),
        team_id=TEAM_ID,
        project_id=PROJECT_ID,
        inbox_dir=tmp_path / "inbox",
        documents=lambda: [],
        now=NOW + timedelta(minutes=2),
        github_client=_github(pr_number=PR + 1),
        completion_clock=lambda: NOW + timedelta(minutes=2, seconds=1),
    )

    assert third.ci_handoff_mutations == 1, third.safe_ci_handoff_summaries(
        verbose=True
    )
    assert linear.state_writes == [(independent.external_linear_id, DONE_STATE.id)]
    retained_poison = TicketRepo(db).get_by_key(poison.key)
    assert retained_poison is not None
    assert retained_poison.status is TicketStatus.CI_PENDING


@pytest.mark.parametrize("defect", ["merge_only", "wrong_head", "off_main"])
def test_incomplete_or_wrong_exact_proof_holds_without_workflow_write(
    tmp_path: Path, defect: str
) -> None:
    db = _database(tmp_path / f"{defect}.db")
    linear = RecordingClient()
    ticket, episode_id = _seed_complete_world(
        db,
        linear,
        include_acceptance=defect != "merge_only",
        ci_head=OTHER_HEAD if defect == "wrong_head" else HEAD,
    )
    github = _github(
        compare=(
            GitHubCompare(
                status=GitHubCompareStatus.DIVERGED,
                ahead_by=1,
                behind_by=1,
                merge_base_sha="e" * 40,
            )
            if defect == "off_main"
            else None
        )
    )

    result = _run(db, linear, ticket, episode_id, github)

    assert result.linear_mutations == 0
    assert linear.state_writes == []
    retained = TicketRepo(db).get_by_key(ticket.key)
    assert retained is not None
    assert retained.status is TicketStatus.CI_PENDING
    [decision] = RetrospectiveCompletionReconciliationRepo(db).list_for_ticket(
        ticket.id
    )
    assert decision.decision.value == "hold"


def test_publication_cardinality_ambiguity_holds_before_retrospective_owner(
    tmp_path: Path,
) -> None:
    db = _database(tmp_path / "ambiguous.db")
    linear = RecordingClient()
    ticket, _episode_id = _seed_complete_world(db, linear)
    assert ticket.external_linear_id is not None
    linear.seed_merged_github_publication(
        ticket.external_linear_id,
        owner=OWNER,
        repo=REPO,
        pr_number=PR + 1,
        attachment_id="replacement",
        append=True,
    )
    issues = linear.fetch_project_issues(PROJECT_ID)

    publication, ordinary_reason = resolve_issue_bound_publication(ticket, issues)

    assert publication is None
    assert ordinary_reason is not None
    assert linear.state_writes == []


@pytest.mark.parametrize("crash_point", ["before", "after"])
def test_fence_recovers_across_fresh_process_without_duplicate_write(
    crash_point: str,
) -> None:
    provider = RecordingClient()
    with TemporalHarness(initial_time=NOW) as harness:
        seed_db = _database(harness.db_path)
        ticket, episode_id = _seed_complete_world(seed_db, provider)
        harness.register_generation_resource(
            "db", lambda _generation: _database(harness.db_path)
        )
        harness.register_generation_resource(
            "linear", lambda _generation: DelegatingLinearClient(provider)
        )
        first = harness.new_generation()
        db1 = first.resource("db")
        linear1 = first.resource("linear")
        assert isinstance(db1, Database)
        hooks = RetrospectiveCompletionHooks(
            before_provider_write=(
                (lambda: (_ for _ in ()).throw(SimulatedProcessDeath()))
                if crash_point == "before"
                else lambda: None
            ),
            after_provider_write=(
                (lambda: (_ for _ in ()).throw(SimulatedProcessDeath()))
                if crash_point == "after"
                else lambda: None
            ),
        )
        with pytest.raises(SimulatedProcessDeath):
            _run(
                db1,
                cast(LinearClient, linear1),
                ticket,
                episode_id,
                _github(),
                hooks=hooks,
            )
        first.close()
        writes_after_crash = len(provider.state_writes)
        assert writes_after_crash == (0 if crash_point == "before" else 1)

        second = harness.new_generation()
        db2 = second.resource("db")
        linear2 = second.resource("linear")
        assert isinstance(db2, Database)
        recovered = reconcile_retrospective_completion_fence(
            db=db2,
            tickets=TicketRepo(db2),
            github=_github(main=OTHER_HEAD if crash_point == "before" else MAIN),
            linear=cast(LinearClient, linear2),
            status_map=status_map(),
            project_id=PROJECT_ID,
            product_id=ticket.product_id,
            now=NOW + timedelta(seconds=1),
        )
        assert recovered is not None
        assert recovered.linear_mutations == (1 if crash_point == "before" else 0)
        assert len(provider.state_writes) == 1
        assert (
            RetrospectiveCompletionCoordinationRepo(db2).get_fence(ticket.product_id)
            is None
        )
        assert (
            len(
                RetrospectiveCompletionReconciliationRepo(db2).list_for_ticket(
                    ticket.id
                )
            )
            == 1
        )
        second.close()
