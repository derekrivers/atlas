"""Exact-proof and fresh-process tests for retrospective merged completion."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from github_fakes import FakeGitHubClient
from pm_temporal_harness import (
    ExternalRequest,
    FaultPoint,
    MutableProviderWorld,
    SimulatedProcessDeath,
    TemporalHarness,
    WorkflowTick,
)
from test_models_validation import product_kwargs, ticket_kwargs
from test_pm_sync import (
    CI_PENDING_STATE,
    DONE_STATE,
    PROJECT_ID,
    STARTED,
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
from atlas.linear.client import LinearClient, LinearIssue, LinearMergedGitHubPublication
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
from atlas.pm.workflow_write import PMWorkflowWriteGuard, WorkflowWriteWindowClosed
from atlas.storage import (
    AcceptanceSessionRepo,
    AdmissionCoordinationRepo,
    CIHandoffCoordinationRepo,
    CIHandoffReconciliationRepo,
    CIHandoffWriteFenceError,
    Database,
    EvidenceRepo,
    PmRecoveryRepo,
    ProductRepo,
    RetrospectiveCompletionCoordinationRepo,
    RetrospectiveCompletionFencePresentError,
    RetrospectiveCompletionReconciliationRepo,
    TicketRepo,
    VerificationCheckRepo,
)
from atlas.storage.tables import RetrospectiveCompletionReconciliationRow
from atlas.verification import (
    build_acceptance_confirmation,
    build_blanket_approval,
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


class TemporalLinearClient:
    """Fresh client over the harness's process-independent provider world."""

    def __init__(
        self,
        *,
        world: MutableProviderWorld,
        tick: WorkflowTick,
        issue_ids: tuple[str, ...],
    ) -> None:
        self._world = world
        self._tick = tick
        self._issue_ids = issue_ids

    @staticmethod
    def _decode(value: Mapping[str, Any]) -> LinearIssue:
        publications = tuple(
            LinearMergedGitHubPublication(**item)
            for item in value["merged_github_publications"]
        )
        return LinearIssue(
            id=str(value["id"]),
            title=str(value["title"]),
            state_id=str(value["state_id"]),
            state_name=str(value["state_name"]),
            state_type=str(value["state_type"]),
            description=str(value["description"]),
            merged_github_publications=publications,
            github_publications_complete=True,
        )

    def fetch_project_issues(self, _project_id: str) -> list[LinearIssue]:
        return [
            self._decode(self._world.resource("linear", f"issue:{issue_id}"))
            for issue_id in self._issue_ids
        ]

    def fetch_issue(self, issue_id: str) -> LinearIssue | None:
        if issue_id not in self._issue_ids:
            return None
        return self._decode(self._world.resource("linear", f"issue:{issue_id}"))

    def set_state(self, issue_id: str, state_id: str) -> LinearIssue:
        assert state_id == DONE_STATE.id
        result = self._tick.external_write(_done_request(issue_id))
        return self._decode(result.value)


class ContradictoryStateClient(RecordingClient):
    """Expose a valid source id with a contradictory workflow-state type."""

    contradictory = False

    def fetch_project_issues(self, project_id: str) -> list[LinearIssue]:
        issues = super().fetch_project_issues(project_id)
        if not self.contradictory:
            return issues
        return [
            replace(issue, state_type="completed")
            if issue.state_id == CI_PENDING_STATE.id
            else issue
            for issue in issues
        ]


def _done_request(issue_id: str) -> ExternalRequest:
    return ExternalRequest(
        provider="linear",
        operation="set_state",
        resource=f"issue:{issue_id}",
        payload={
            "state_id": DONE_STATE.id,
            "state_name": DONE_STATE.name,
            "state_type": DONE_STATE.type,
        },
    )


def _merge_provider_state(
    current: Mapping[str, object], payload: Mapping[str, object]
) -> Mapping[str, Any]:
    def mutable(value: object) -> Any:
        if isinstance(value, Mapping):
            return {str(key): mutable(child) for key, child in value.items()}
        if isinstance(value, tuple):
            return [mutable(child) for child in value]
        return value

    return {**mutable(current), **mutable(payload)}


def _seed_provider_issue(world: MutableProviderWorld, issue: LinearIssue) -> None:
    world.set_resource(
        "linear",
        f"issue:{issue.id}",
        {
            "id": issue.id,
            "title": issue.title,
            "state_id": issue.state_id,
            "state_name": issue.state_name,
            "state_type": issue.state_type,
            "description": issue.description or "",
            "merged_github_publications": [
                {
                    "attachment_id": publication.attachment_id,
                    "repository_owner": publication.repository_owner,
                    "repository_name": publication.repository_name,
                    "pr_number": publication.pr_number,
                }
                for publication in issue.merged_github_publications
            ],
        },
    )


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
    ticket: Ticket,
    verdict_id: UUID,
    *,
    pr_number: int = PR,
    verification_ticket_count: int = 1,
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
                    ticket_count=verification_ticket_count,
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
    include_blanket_approval: bool = True,
    acceptance_ticket_count: int = 1,
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
        if include_blanket_approval:
            evidence_repo.add(
                build_blanket_approval(
                    approved=True,
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
        _acceptance_session(
            ticket,
            verdict_id,
            pr_number=pr_number,
            verification_ticket_count=acceptance_ticket_count,
        )
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
    assert retained.status is TicketStatus.DONE
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
    with (
        pytest.raises(sa.exc.IntegrityError, match="append-only"),
        db.session() as session,
        session.begin(),
    ):
        session.execute(
            sa.update(RetrospectiveCompletionReconciliationRow)
            .where(RetrospectiveCompletionReconciliationRow.id == decision.id)
            .values(reason="tampered")
        )
    with (
        pytest.raises(sa.exc.IntegrityError, match="append-only"),
        db.session() as session,
        session.begin(),
    ):
        session.execute(
            sa.delete(RetrospectiveCompletionReconciliationRow).where(
                RetrospectiveCompletionReconciliationRow.id == decision.id
            )
        )


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


@pytest.mark.parametrize(
    "defect",
    ["merge_only", "missing_blanket", "invalid_session", "wrong_head", "off_main"],
)
def test_incomplete_or_wrong_exact_proof_holds_without_workflow_write(
    tmp_path: Path, defect: str
) -> None:
    db = _database(tmp_path / f"{defect}.db")
    linear = RecordingClient()
    ticket, episode_id = _seed_complete_world(
        db,
        linear,
        include_acceptance=defect != "merge_only",
        include_blanket_approval=defect != "missing_blanket",
        acceptance_ticket_count=2 if defect == "invalid_session" else 1,
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

    assert result.ci_handoff_held == 1
    assert linear.state_writes == []
    [blocker] = PmRecoveryRepo(db).list_blockers(
        product_id=ticket.product_id,
        active_only=True,
    )
    assert blocker.code.value == "publication_ambiguous"


def test_prior_ci_decision_cannot_authorize_a_later_ci_pending_episode(
    tmp_path: Path,
) -> None:
    db = _database(tmp_path / "later-episode.db")
    linear = RecordingClient()
    ticket, _old_episode = _seed_complete_world(db, linear)
    tickets = TicketRepo(db)
    tickets.apply_linear_status(
        ticket.key,
        TicketStatus.REVIEW_REQUIRED,
        now=NOW - timedelta(hours=2),
        created_by_id="test",
    )
    tickets.apply_linear_status(
        ticket.key,
        TicketStatus.CI_PENDING,
        now=NOW - timedelta(hours=1),
        created_by_id="test",
    )
    selection = select_fair_ci_handoff_candidate(
        db=db,
        tickets=tickets,
        initial_issues=linear.fetch_project_issues(PROJECT_ID),
        now=NOW,
    )
    assert selection.episode is not None

    result = _run(db, linear, ticket, selection.episode.id, _github())

    assert result.reason.value == "contributor_head_unavailable"
    assert result.linear_mutations == 0
    assert linear.state_writes == []


def test_contradictory_source_state_type_blocks_final_revalidation(
    tmp_path: Path,
) -> None:
    db = _database(tmp_path / "contradictory-state.db")
    linear = ContradictoryStateClient()
    ticket, episode_id = _seed_complete_world(db, linear)
    hooks = RetrospectiveCompletionHooks(
        after_proof_evaluated=lambda: setattr(linear, "contradictory", True)
    )

    result = _run(db, linear, ticket, episode_id, _github(), hooks=hooks)

    assert result.reason.value == "board_state_moved"
    assert result.linear_mutations == 0
    assert linear.state_writes == []


def test_crash_before_rejects_replaced_attachment_for_same_pr(tmp_path: Path) -> None:
    db = _database(tmp_path / "replacement-after-fence.db")
    linear = RecordingClient()
    ticket, episode_id = _seed_complete_world(db, linear)
    with pytest.raises(SimulatedProcessDeath):
        _run(
            db,
            linear,
            ticket,
            episode_id,
            _github(),
            hooks=RetrospectiveCompletionHooks(
                before_provider_write=lambda: (_ for _ in ()).throw(
                    SimulatedProcessDeath()
                )
            ),
        )
    assert ticket.external_linear_id is not None
    linear.seed_merged_github_publication(
        ticket.external_linear_id,
        owner=OWNER,
        repo=REPO,
        pr_number=PR,
        attachment_id="replacement-same-pr",
    )

    result = reconcile_retrospective_completion_fence(
        db=db,
        tickets=TicketRepo(db),
        github=_github(),
        linear=linear,
        status_map=status_map(),
        project_id=PROJECT_ID,
        product_id=ticket.product_id,
        now=NOW + timedelta(seconds=1),
    )

    assert result is not None
    assert result.linear_mutations == 0
    assert linear.state_writes == []
    assert RetrospectiveCompletionCoordinationRepo(db).get_fence(ticket.product_id)


def test_retrospective_fence_blocks_ordinary_admission_and_ci_writers(
    tmp_path: Path,
) -> None:
    db = _database(tmp_path / "retrospective-exclusion.db")
    linear = RecordingClient()
    ticket, episode_id = _seed_complete_world(db, linear)
    with pytest.raises(SimulatedProcessDeath):
        _run(
            db,
            linear,
            ticket,
            episode_id,
            _github(),
            hooks=RetrospectiveCompletionHooks(
                before_provider_write=lambda: (_ for _ in ()).throw(
                    SimulatedProcessDeath()
                )
            ),
        )

    with pytest.raises(WorkflowWriteWindowClosed):
        PMWorkflowWriteGuard(db=db, observed_at=NOW + timedelta(seconds=1)).execute(
            product_id=ticket.product_id,
            call=lambda: "forbidden",
        )

    lease = AdmissionCoordinationRepo(db)
    owner = uuid4()
    assert lease.try_acquire(
        product_id=ticket.product_id,
        owner_id=owner,
        acquired_at=NOW + timedelta(seconds=1),
        ttl=timedelta(minutes=1),
    )
    with pytest.raises(RetrospectiveCompletionFencePresentError):
        lease.begin_write(
            product_id=ticket.product_id,
            owner_id=owner,
            admission_run_id=uuid4(),
            ticket_id=ticket.id,
            ticket_key=ticket.key,
            issue_id=ticket.external_linear_id or "",
            source_state_id=CI_PENDING_STATE.id,
            target_state_id=DONE_STATE.id,
            policy_revision=1,
            created_at=NOW + timedelta(seconds=1),
        )
    [ci_decision] = CIHandoffReconciliationRepo(db).list_for_ticket(ticket.id)
    with pytest.raises(CIHandoffWriteFenceError):
        CIHandoffCoordinationRepo(db).begin_write(
            product_id=ticket.product_id,
            owner_id=owner,
            reconciliation_id=ci_decision.id,
            ticket_id=ticket.id,
            ticket_key=ticket.key,
            issue_id=ticket.external_linear_id or "",
            source_state_id=CI_PENDING_STATE.id,
            target_state_id="state-review-required",
            target_status=TicketStatus.REVIEW_REQUIRED,
            created_at=NOW + timedelta(seconds=1),
        )
    lease.release(product_id=ticket.product_id, owner_id=owner)


@pytest.mark.parametrize("competing_fence", ["admission", "ci"])
def test_existing_competing_fence_blocks_retrospective_preparation(
    tmp_path: Path, competing_fence: str
) -> None:
    db = _database(tmp_path / f"{competing_fence}-blocks-retrospective.db")
    linear = RecordingClient()
    ticket, episode_id = _seed_complete_world(db, linear)
    lease = AdmissionCoordinationRepo(db)
    owner = uuid4()
    assert lease.try_acquire(
        product_id=ticket.product_id,
        owner_id=owner,
        acquired_at=NOW,
        ttl=timedelta(minutes=1),
    )
    if competing_fence == "admission":
        lease.begin_write(
            product_id=ticket.product_id,
            owner_id=owner,
            admission_run_id=uuid4(),
            ticket_id=ticket.id,
            ticket_key=ticket.key,
            issue_id=ticket.external_linear_id or "",
            source_state_id=CI_PENDING_STATE.id,
            target_state_id=DONE_STATE.id,
            policy_revision=1,
            created_at=NOW,
        )
    else:
        [ci_decision] = CIHandoffReconciliationRepo(db).list_for_ticket(ticket.id)
        CIHandoffCoordinationRepo(db).begin_write(
            product_id=ticket.product_id,
            owner_id=owner,
            reconciliation_id=ci_decision.id,
            ticket_id=ticket.id,
            ticket_key=ticket.key,
            issue_id=ticket.external_linear_id or "",
            source_state_id=CI_PENDING_STATE.id,
            target_state_id="state-review-required",
            target_status=TicketStatus.REVIEW_REQUIRED,
            created_at=NOW,
        )
    lease.release(product_id=ticket.product_id, owner_id=owner)

    result = _run(db, linear, ticket, episode_id, _github())

    assert result.reason.value == "concurrent_write_fence"
    assert result.linear_mutations == 0
    assert linear.state_writes == []
    assert (
        RetrospectiveCompletionCoordinationRepo(db).get_fence(ticket.product_id) is None
    )


def test_missing_github_for_fenced_product_does_not_stop_independent_pull(
    tmp_path: Path,
) -> None:
    db = _database(tmp_path / "missing-github-independent.db")
    linear = RecordingClient()
    fenced, episode_id = _seed_complete_world(db, linear)
    with pytest.raises(SimulatedProcessDeath):
        _run(
            db,
            linear,
            fenced,
            episode_id,
            _github(),
            hooks=RetrospectiveCompletionHooks(
                before_provider_write=lambda: (_ for _ in ()).throw(
                    SimulatedProcessDeath()
                )
            ),
        )

    product = Product(**product_kwargs() | {"id": uuid4(), "key": "P-INDEPENDENT"})
    ProductRepo(db).add(product)
    seed_default_admission_policy(db, product.id)
    issue = linear.create_issue(
        {"title": "Independent", "description": "independent"},
        team_id=TEAM_ID,
        project_id=PROJECT_ID,
    )
    linear.simulate_linear_state(issue.id, STARTED)
    independent = _ticket(product.id, issue.id, key="ATLAS-336").model_copy(
        update={"status": TicketStatus.PLANNED}
    )
    TicketRepo(db).add(independent)
    linear.creates.clear()
    linear.state_writes.clear()

    sync_tick(
        tickets=TicketRepo(db),
        db=db,
        client=linear,
        status_map=status_map(),
        team_id=TEAM_ID,
        project_id=PROJECT_ID,
        inbox_dir=tmp_path / "inbox",
        documents=lambda: [],
        now=NOW + timedelta(seconds=1),
        github_client=None,
        completion_clock=lambda: NOW + timedelta(seconds=2),
    )

    retained = TicketRepo(db).get_by_key(independent.key)
    assert retained is not None
    assert retained.status is TicketStatus.IN_PROGRESS
    assert RetrospectiveCompletionCoordinationRepo(db).get_fence(fenced.product_id)


def test_retry_deferred_fence_does_not_stop_independent_product(
    tmp_path: Path,
) -> None:
    db = _database(tmp_path / "deferred-fence-independent.db")
    linear = RecordingClient()
    fenced, episode_id = _seed_complete_world(db, linear)
    with pytest.raises(SimulatedProcessDeath):
        _run(
            db,
            linear,
            fenced,
            episode_id,
            _github(),
            hooks=RetrospectiveCompletionHooks(
                before_provider_write=lambda: (_ for _ in ()).throw(
                    SimulatedProcessDeath()
                )
            ),
        )
    product = Product(**product_kwargs() | {"id": uuid4(), "key": "P-DEFERRED"})
    ProductRepo(db).add(product)
    seed_default_admission_policy(db, product.id)
    issue = linear.create_issue(
        {"title": "Independent", "description": "independent"},
        team_id=TEAM_ID,
        project_id=PROJECT_ID,
    )
    linear.simulate_linear_state(issue.id, STARTED)
    independent = _ticket(product.id, issue.id, key="ATLAS-337").model_copy(
        update={"status": TicketStatus.PLANNED}
    )
    TicketRepo(db).add(independent)
    lease = AdmissionCoordinationRepo(db)
    competing_owner = uuid4()
    assert lease.try_acquire(
        product_id=fenced.product_id,
        owner_id=competing_owner,
        acquired_at=NOW + timedelta(seconds=1),
        ttl=timedelta(minutes=5),
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
        now=NOW + timedelta(seconds=1),
        github_client=_github(),
        completion_clock=lambda: NOW + timedelta(seconds=2),
    )
    assert first.ci_handoff_held == 1
    before_retry = TicketRepo(db).get_by_key(independent.key)
    assert before_retry is not None
    assert before_retry.status is TicketStatus.PLANNED

    sync_tick(
        tickets=TicketRepo(db),
        db=db,
        client=linear,
        status_map=status_map(),
        team_id=TEAM_ID,
        project_id=PROJECT_ID,
        inbox_dir=tmp_path / "inbox",
        documents=lambda: [],
        now=NOW + timedelta(seconds=2),
        github_client=_github(),
        completion_clock=lambda: NOW + timedelta(seconds=3),
    )

    retained = TicketRepo(db).get_by_key(independent.key)
    assert retained is not None
    assert retained.status is TicketStatus.IN_PROGRESS
    assert RetrospectiveCompletionCoordinationRepo(db).get_fence(fenced.product_id)
    lease.release(product_id=fenced.product_id, owner_id=competing_owner)


@pytest.mark.parametrize("crash_point", ["before", "after"])
def test_fence_recovers_across_fresh_process_without_duplicate_write(
    crash_point: str,
) -> None:
    with TemporalHarness(initial_time=NOW) as harness:
        seeder = RecordingClient()
        seed_db = _database(harness.db_path)
        ticket, episode_id = _seed_complete_world(seed_db, seeder)
        issue_id = ticket.external_linear_id
        assert issue_id is not None
        [seed_issue] = seeder.fetch_project_issues(PROJECT_ID)
        _seed_provider_issue(harness.providers, seed_issue)
        harness.providers.register_operation(
            "linear", "set_state", _merge_provider_state
        )
        harness.register_generation_resource(
            "db", lambda _generation: _database(harness.db_path)
        )
        harness.register_generation_resource(
            "linear",
            lambda generation: TemporalLinearClient(
                world=harness.providers,
                tick=generation.tick(f"process-{generation.generation_id}"),
                issue_ids=(issue_id,),
            ),
        )
        request = _done_request(issue_id)
        harness.faults.arm(
            (
                FaultPoint.BEFORE_PROVIDER_CALL
                if crash_point == "before"
                else FaultPoint.AFTER_EFFECT_BEFORE_RETURN
            ),
            request_fingerprint=request.request_fingerprint,
        )
        with harness.new_generation() as first:
            db1 = first.resource("db")
            linear1 = first.resource("linear")
            assert isinstance(db1, Database)
            with pytest.raises(SimulatedProcessDeath):
                _run(
                    db1,
                    cast(LinearClient, linear1),
                    ticket,
                    episode_id,
                    _github(),
                )
        harness.providers.ledger.assert_counts(
            request.request_fingerprint,
            attempts=0 if crash_point == "before" else 1,
            effects=0 if crash_point == "before" else 1,
        )

        with harness.new_generation() as second:
            db2 = second.resource("db")
            linear2 = second.resource("linear")
            assert isinstance(db2, Database)
            recovered = sync_tick(
                tickets=TicketRepo(db2),
                db=db2,
                client=cast(LinearClient, linear2),
                status_map=status_map(),
                team_id=TEAM_ID,
                project_id=PROJECT_ID,
                inbox_dir=harness.db_path.parent / "inbox",
                documents=lambda: [],
                now=NOW + timedelta(seconds=1),
                github_client=_github(
                    main=OTHER_HEAD if crash_point == "before" else MAIN
                ),
                completion_clock=lambda: NOW + timedelta(seconds=2),
            )
            assert recovered.ci_handoff_mutations == (
                1 if crash_point == "before" else 0
            )
            retained = TicketRepo(db2).get_by_key(ticket.key)
            assert retained is not None
            assert retained.status is TicketStatus.DONE
            episode = PmRecoveryRepo(db2).get_episode(episode_id)
            assert episode is not None
            assert episode.closed_at is not None
            assert (
                RetrospectiveCompletionCoordinationRepo(db2).get_fence(
                    ticket.product_id
                )
                is None
            )
        with harness.new_generation() as third:
            db3 = third.resource("db")
            linear3 = third.resource("linear")
            assert isinstance(db3, Database)
            follow_up = sync_tick(
                tickets=TicketRepo(db3),
                db=db3,
                client=cast(LinearClient, linear3),
                status_map=status_map(),
                team_id=TEAM_ID,
                project_id=PROJECT_ID,
                inbox_dir=harness.db_path.parent / "inbox",
                documents=lambda: [],
                now=NOW + timedelta(seconds=2),
                github_client=_github(),
                completion_clock=lambda: NOW + timedelta(seconds=3),
            )
            assert follow_up.ci_handoff_mutations == 0
            assert (
                RetrospectiveCompletionCoordinationRepo(db3).get_fence(
                    ticket.product_id
                )
                is None
            )
            assert (
                len(
                    RetrospectiveCompletionReconciliationRepo(db3).list_for_ticket(
                        ticket.id
                    )
                )
                == 1
            )
        harness.providers.ledger.assert_counts(
            request.request_fingerprint, attempts=1, effects=1
        )
        harness.providers.ledger.assert_no_duplicate_harmful_effects()
        harness.providers.ledger.assert_at_most_one_workflow_effect_per_tick()
