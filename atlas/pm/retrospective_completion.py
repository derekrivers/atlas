"""Separate exact-proof owner for retrospective merged-publication completion."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from time import monotonic
from typing import Any, cast
from uuid import UUID, uuid4

from atlas.core.acceptance_criteria import (
    acceptance_criteria_fingerprint,
    acceptance_criteria_snapshot,
)
from atlas.core.enums import ActorType, EvidenceStatus
from atlas.core.models import (
    AcceptanceSession,
    AcceptanceSessionLifecycle,
    AcceptanceSessionStep,
    AcceptanceSessionStepState,
    CIHandoffClassification,
    CIHandoffDecision,
    DeliveryAdmissionPolicyRevision,
    RetrospectiveCompletionDecision,
    RetrospectiveCompletionReason,
    RetrospectiveCompletionReconciliation,
    Ticket,
    TicketStatus,
    VerificationCheck,
    VerificationCheckType,
)
from atlas.core.models.evidence import Evidence, EvidenceType
from atlas.dependencies.graph import build_dependency_graph
from atlas.evidence import canonical_merged_pr_identity
from atlas.github import (
    GitHubAPIError,
    GitHubClient,
    GitHubCompareStatus,
    GitHubMalformedResponseError,
)
from atlas.linear import LinearRetrospectiveCompletionWriter
from atlas.linear.client import (
    LinearAPIError,
    LinearClient,
    LinearIssue,
    LinearMergedGitHubPublication,
)
from atlas.linear.ownership import LinearStatusMap, status_from_issue
from atlas.pm.delivery_snapshot import (
    DeliverySnapshot,
    LinearBoardPull,
    build_delivery_snapshot,
    delivery_policy_fingerprint,
)
from atlas.storage import (
    AcceptanceSessionRepo,
    AdmissionCoordinationRepo,
    AdmissionLeaseLostError,
    CIHandoffReconciliationRepo,
    CompetingWorkflowFencePresentError,
    Database,
    DeliveryAdmissionPolicyRepo,
    EvidenceRepo,
    RetrospectiveCompletionCoordinationRepo,
    RetrospectiveCompletionReconciliationRepo,
    RetrospectiveCompletionWriteFenceError,
    RetrospectiveProviderCallIndeterminateError,
    TicketDependencyRepo,
    TicketRepo,
    VerificationCheckRepo,
)
from atlas.verification import (
    evaluate_ticket,
    proof_evidence_ids,
    required_checks,
    ticket_verdict_from_checks,
)

RETROSPECTIVE_COMPLETION_LEASE_TTL = timedelta(minutes=5)
CREATED_BY = "retrospective-completion-reconciler"


def _noop() -> None:
    return None


@dataclass(frozen=True)
class RetrospectiveCompletionHooks:
    """Deterministic process-death seams around the one external effect."""

    after_proof_evaluated: Callable[[], None] = _noop
    after_fence_persisted: Callable[[], None] = _noop
    before_provider_write: Callable[[], None] = _noop
    after_provider_write: Callable[[], None] = _noop
    monotonic_clock: Callable[[], float] = monotonic


@dataclass(frozen=True)
class RetrospectiveCompletionProof:
    """Complete exact identities retained in a transition-authorising decision."""

    publication: LinearMergedGitHubPublication
    contributor_head: str
    merge_commit: str
    canonical_main: str
    policy_id: UUID
    policy_revision: int
    policy_fingerprint: str
    snapshot_fingerprint: str
    acceptance_session_id: UUID
    verification_verdict_id: UUID
    criteria_fingerprint: str
    verification_check_ids: tuple[UUID, ...]
    deciding_evidence_ids: tuple[UUID, ...]
    merged_evidence_id: UUID


@dataclass(frozen=True)
class RetrospectiveCompletionResult:
    """Bounded result returned to the cadence adapter."""

    reason: RetrospectiveCompletionReason
    ticket_key: str
    decision: RetrospectiveCompletionDecision = RetrospectiveCompletionDecision.HOLD
    reconciliation_id: UUID | None = None
    linear_mutations: int = 0
    fence_reconciliation_attempted: bool = False

    @property
    def held(self) -> bool:
        return self.decision is RetrospectiveCompletionDecision.HOLD

    @property
    def ends_workflow_write_window(self) -> bool:
        return bool(
            self.linear_mutations
            or self.fence_reconciliation_attempted
            or self.reason
            in {
                RetrospectiveCompletionReason.LEASE_UNAVAILABLE,
                RetrospectiveCompletionReason.LEASE_LOST,
                RetrospectiveCompletionReason.WRITE_INDETERMINATE,
                RetrospectiveCompletionReason.CONCURRENT_WRITE_FENCE,
            }
        )

    @property
    def safe_summary(self) -> str:
        return (
            f"retrospective completion {self.ticket_key}: "
            f"decision={self.decision.value} reason={self.reason.value} "
            f"mutations={self.linear_mutations}"
        )


def _full_sha(value: object) -> str | None:
    if (
        not isinstance(value, str)
        or len(value) != 40
        or any(character not in "0123456789abcdefABCDEF" for character in value)
    ):
        return None
    return value.lower()


def _issue_by_id(issues: Sequence[LinearIssue], issue_id: str) -> LinearIssue | None:
    matches = [issue for issue in issues if issue.id == issue_id]
    return matches[0] if len(matches) == 1 else None


def _complete_board(issues: Sequence[LinearIssue]) -> bool:
    """Read completeness from the list-compatible bounded page object."""

    page = cast(Any, issues)
    return bool(getattr(page, "complete", True)) and not bool(
        getattr(page, "pagination_gaps", ())
    )


def _delivery_snapshot(
    *,
    db: Database,
    ticket: Ticket,
    policy: DeliveryAdmissionPolicyRevision,
    project_id: str,
    status_map: LinearStatusMap,
    issues: Sequence[LinearIssue],
    now: datetime,
) -> DeliverySnapshot:
    page = cast(Any, issues)
    return build_delivery_snapshot(
        product_id=ticket.product_id,
        linear_project_id=project_id,
        policy=policy,
        status_map=status_map,
        board_pull=LinearBoardPull(
            issues=tuple(issues),
            complete=bool(getattr(page, "complete", True)),
            pagination_gaps=tuple(getattr(page, "pagination_gaps", ())),
        ),
        tickets=tuple(TicketRepo(db).list()),
        dependencies=tuple(TicketDependencyRepo(db).list()),
        graph=build_dependency_graph(db),
        clock=lambda: now,
    )


def resolve_historical_publication(
    ticket: Ticket, issues: Sequence[LinearIssue]
) -> tuple[
    LinearMergedGitHubPublication | None,
    RetrospectiveCompletionReason | None,
]:
    """Resolve the separate merged-only issue-bound projection exactly once."""

    if ticket.external_linear_id is None:
        return None, RetrospectiveCompletionReason.PUBLICATION_UNAVAILABLE
    joined = [issue for issue in issues if issue.id == ticket.external_linear_id]
    if len(joined) != 1:
        return None, RetrospectiveCompletionReason.PUBLICATION_UNAVAILABLE
    issue = joined[0]
    if not issue.github_publications_complete:
        return None, RetrospectiveCompletionReason.PUBLICATION_AMBIGUOUS
    # Ordinary and historical projections are mutually exclusive for this edge.
    if issue.github_publications or len(issue.merged_github_publications) != 1:
        return (
            None,
            RetrospectiveCompletionReason.PUBLICATION_AMBIGUOUS
            if issue.github_publications or issue.merged_github_publications
            else RetrospectiveCompletionReason.PUBLICATION_UNAVAILABLE,
        )
    return issue.merged_github_publications[0], None


def _historical_contributor_head(
    *,
    db: Database,
    ticket: Ticket,
    publication: LinearMergedGitHubPublication,
) -> str | None:
    """Recover the episode head only from the append-only ordinary CI decision."""

    matching = [
        decision
        for decision in CIHandoffReconciliationRepo(db).list_for_ticket(ticket.id)
        if decision.repository_owner.casefold()
        == publication.repository_owner.casefold()
        and decision.repository_name.casefold()
        == publication.repository_name.casefold()
        and decision.pr_number == publication.pr_number
        and decision.classification is CIHandoffClassification.PASSED
        and decision.decision is CIHandoffDecision.REVIEW_REQUIRED
    ]
    if not matching:
        return None
    latest = max(matching, key=lambda item: (item.observed_at, str(item.id)))
    return latest.head_commit


def _current_acceptance_session(
    *,
    db: Database,
    tickets: TicketRepo,
    ticket: Ticket,
    publication: LinearMergedGitHubPublication,
    contributor_head: str,
) -> AcceptanceSession | None:
    candidates = []
    for session in AcceptanceSessionRepo(db).list_for_pr(
        publication.repository_owner,
        publication.repository_name,
        publication.pr_number,
    ):
        if (
            session.head_sha == contributor_head
            and ticket.key in session.close_set
            and session.lifecycle is AcceptanceSessionLifecycle.MERGE_READY
            and session.stored_merge_ready
            and not session.historical_readiness_reasons
        ):
            candidates.append(session)
    if len(candidates) != 1:
        return None
    session = candidates[0]
    close_tickets = [tickets.get_by_key(key) for key in session.close_set]
    if any(item is None for item in close_tickets):
        return None
    complete_tickets = [item for item in close_tickets if item is not None]
    snapshot = acceptance_criteria_snapshot(session.close_set, complete_tickets)
    fingerprint = acceptance_criteria_fingerprint(snapshot)
    if fingerprint != session.criteria_fingerprint:
        return None
    if tuple(snapshot) != session.criteria_snapshot:
        return None
    confirmations = session.step_summaries[AcceptanceSessionStep.CONFIRMATIONS]
    verification = session.step_summaries[AcceptanceSessionStep.VERIFICATION]
    readiness = session.step_summaries[AcceptanceSessionStep.READINESS]
    if (
        confirmations.state is not AcceptanceSessionStepState.COMPLETE
        or not confirmations.receipt_ids
        or verification.state is not AcceptanceSessionStepState.COMPLETE
        or verification.verification is None
        or verification.verification.status is not EvidenceStatus.PASSED
        or verification.verification.head_commit != contributor_head
        or readiness.state is not AcceptanceSessionStepState.COMPLETE
        or readiness.readiness is None
        or readiness.readiness.verdict_id != verification.verification.verdict_id
        or readiness.readiness.criteria_fingerprint != fingerprint
        or readiness.readiness.repository_owner.casefold()
        != publication.repository_owner.casefold()
        or readiness.readiness.repository_name.casefold()
        != publication.repository_name.casefold()
        or readiness.readiness.pr_number != publication.pr_number
        or readiness.readiness.head_sha != contributor_head
        or readiness.readiness.base_ref != "main"
    ):
        return None
    return session


def _latest_required_checks(
    ticket: Ticket, checks: Sequence[VerificationCheck]
) -> tuple[VerificationCheck, ...]:
    required = {item.check_type for item in required_checks(ticket) if item.required}
    latest: dict[VerificationCheckType, VerificationCheck] = {}
    for check in checks:
        if check.check_type not in required:
            continue
        current = latest.get(check.check_type)
        if current is None or check.created_at > current.created_at:
            latest[check.check_type] = check
    return tuple(latest[key] for key in sorted(latest, key=lambda item: item.value))


def _structured_merge_evidence(
    evidence: Sequence[Evidence],
    *,
    ticket: Ticket,
    publication: LinearMergedGitHubPublication,
    contributor_head: str,
    merge_commit: str,
) -> Evidence | None:
    expected = {
        "schema_version": "pr-merged-evidence-v2",
        "repository_owner": publication.repository_owner.casefold(),
        "repository_name": publication.repository_name.casefold(),
        "pr_number": publication.pr_number,
        "contributor_head": contributor_head,
        "merge_commit": merge_commit,
    }
    matches = [
        item
        for item in evidence
        if item.ticket_id == ticket.id
        and item.evidence_type is EvidenceType.PR_MERGED
        and item.status is EvidenceStatus.PASSED
        and item.created_by_type is ActorType.SYSTEM
        and item.commit_sha == contributor_head
        and item.raw_payload == expected
    ]
    return matches[0] if len(matches) == 1 else None


def evaluate_retrospective_proof(
    *,
    db: Database,
    tickets: TicketRepo,
    github: GitHubClient,
    ticket: Ticket,
    publication: LinearMergedGitHubPublication,
    status_map: LinearStatusMap,
    project_id: str,
    board_issues: Sequence[LinearIssue],
    now: datetime,
    expected_contributor_head: str | None = None,
) -> tuple[RetrospectiveCompletionProof | None, RetrospectiveCompletionReason]:
    """Freshly prove every canonical fact without writing any verdict or evidence."""

    policy = DeliveryAdmissionPolicyRepo(db).get_active(ticket.product_id)
    if policy is None:
        return None, RetrospectiveCompletionReason.POLICY_UNAVAILABLE
    snapshot = _delivery_snapshot(
        db=db,
        ticket=ticket,
        policy=policy,
        project_id=project_id,
        status_map=status_map,
        issues=board_issues,
        now=now,
    )
    if snapshot.incompleteness_reasons:
        return None, RetrospectiveCompletionReason.SNAPSHOT_INCOMPLETE

    contributor_head = expected_contributor_head or _historical_contributor_head(
        db=db, ticket=ticket, publication=publication
    )
    contributor_head = _full_sha(contributor_head)
    if contributor_head is None:
        return None, RetrospectiveCompletionReason.CONTRIBUTOR_HEAD_UNAVAILABLE
    try:
        pull_request = github.fetch_pull_request(
            publication.repository_owner,
            publication.repository_name,
            publication.pr_number,
        )
    except (GitHubAPIError, GitHubMalformedResponseError):
        return None, RetrospectiveCompletionReason.MERGED_PR_UNPROVEN
    identity = canonical_merged_pr_identity(pull_request)
    if identity is None:
        return None, RetrospectiveCompletionReason.MERGED_PR_UNPROVEN
    if (
        identity.repository_owner != publication.repository_owner.casefold()
        or identity.repository_name != publication.repository_name.casefold()
        or identity.pr_number != publication.pr_number
        or identity.contributor_head != contributor_head
    ):
        return None, RetrospectiveCompletionReason.MERGED_PR_IDENTITY_MISMATCH
    try:
        canonical_main = _full_sha(
            github.fetch_branch_head(
                publication.repository_owner,
                publication.repository_name,
                "main",
            )
        )
    except (GitHubAPIError, GitHubMalformedResponseError):
        canonical_main = None
    if canonical_main is None:
        return None, RetrospectiveCompletionReason.CANONICAL_MAIN_UNPROVEN
    try:
        ancestry = github.compare_commits(
            publication.repository_owner,
            publication.repository_name,
            identity.merge_commit,
            canonical_main,
        )
    except (GitHubAPIError, GitHubMalformedResponseError):
        return None, RetrospectiveCompletionReason.MERGE_ANCESTRY_UNPROVEN
    if not (
        ancestry.status in {GitHubCompareStatus.AHEAD, GitHubCompareStatus.IDENTICAL}
        and ancestry.behind_by == 0
        and ancestry.merge_base_sha.lower() == identity.merge_commit
    ):
        return None, RetrospectiveCompletionReason.MERGE_ANCESTRY_UNPROVEN

    session = _current_acceptance_session(
        db=db,
        tickets=tickets,
        ticket=ticket,
        publication=publication,
        contributor_head=contributor_head,
    )
    if session is None:
        return None, RetrospectiveCompletionReason.ACCEPTANCE_UNPROVEN
    checks = VerificationCheckRepo(db).list_for_ticket(ticket.id)
    latest_checks = _latest_required_checks(ticket, checks)
    if (
        len(latest_checks)
        != len([item for item in required_checks(ticket) if item.required])
        or ticket_verdict_from_checks(ticket, checks) is not EvidenceStatus.PASSED
    ):
        return None, RetrospectiveCompletionReason.VERIFICATION_UNPROVEN
    proof_ids = proof_evidence_ids(ticket, checks)
    evidence = EvidenceRepo(db).list_for_product_commit(
        ticket.product_id, contributor_head
    )
    evidence_by_id = {item.id: item for item in evidence}
    if not proof_ids or not proof_ids <= set(evidence_by_id):
        return None, RetrospectiveCompletionReason.VERIFICATION_UNPROVEN
    try:
        raw_files = github.fetch_pr_files(
            publication.repository_owner,
            publication.repository_name,
            publication.pr_number,
        )
    except (GitHubAPIError, GitHubMalformedResponseError):
        return None, RetrospectiveCompletionReason.VERIFICATION_UNPROVEN
    filenames: list[str] = []
    for item in raw_files:
        filename = item.get("filename") if isinstance(item, Mapping) else None
        if not isinstance(filename, str) or not filename:
            return None, RetrospectiveCompletionReason.VERIFICATION_UNPROVEN
        filenames.append(filename)
    fresh = evaluate_ticket(
        ticket,
        pr_files=filenames,
        head_commit=contributor_head,
        evidence=evidence,
    )
    fresh_ids = frozenset(
        evidence_id for outcome in fresh.checks for evidence_id in outcome.evidence_ids
    )
    if fresh.status is not EvidenceStatus.PASSED or fresh_ids != proof_ids:
        return None, RetrospectiveCompletionReason.VERIFICATION_UNPROVEN
    verification_summary = session.step_summaries[
        AcceptanceSessionStep.VERIFICATION
    ].verification
    assert verification_summary is not None
    merge_evidence = _structured_merge_evidence(
        evidence,
        ticket=ticket,
        publication=publication,
        contributor_head=contributor_head,
        merge_commit=identity.merge_commit,
    )
    if merge_evidence is None:
        return None, RetrospectiveCompletionReason.MERGED_EVIDENCE_UNPROVEN
    return (
        RetrospectiveCompletionProof(
            publication=publication,
            contributor_head=contributor_head,
            merge_commit=identity.merge_commit,
            canonical_main=canonical_main,
            policy_id=policy.id,
            policy_revision=policy.revision,
            policy_fingerprint=delivery_policy_fingerprint(policy),
            snapshot_fingerprint=snapshot.fingerprint,
            acceptance_session_id=session.id,
            verification_verdict_id=verification_summary.verdict_id,
            criteria_fingerprint=session.criteria_fingerprint,
            verification_check_ids=tuple(check.id for check in latest_checks),
            deciding_evidence_ids=tuple(sorted(proof_ids, key=str)),
            merged_evidence_id=merge_evidence.id,
        ),
        RetrospectiveCompletionReason.COMPLETE_DELIVERY_PROOF,
    )


def _decision_record(
    *,
    ticket: Ticket,
    publication: LinearMergedGitHubPublication | None,
    proof: RetrospectiveCompletionProof | None,
    reason: RetrospectiveCompletionReason,
    now: datetime,
    reconciliation_id: UUID,
    recovery_episode_id: UUID | None,
) -> RetrospectiveCompletionReconciliation:
    return RetrospectiveCompletionReconciliation(
        id=reconciliation_id,
        product_id=ticket.product_id,
        ticket_id=ticket.id,
        ticket_key=ticket.key,
        linear_issue_id=ticket.external_linear_id,
        recovery_episode_id=recovery_episode_id,
        publication_attachment_id=(
            None if publication is None else publication.attachment_id
        ),
        repository_owner=(
            None if publication is None else publication.repository_owner.casefold()
        ),
        repository_name=(
            None if publication is None else publication.repository_name.casefold()
        ),
        pr_number=None if publication is None else publication.pr_number,
        contributor_head=None if proof is None else proof.contributor_head,
        merge_commit=None if proof is None else proof.merge_commit,
        canonical_main=None if proof is None else proof.canonical_main,
        policy_id=None if proof is None else proof.policy_id,
        policy_revision=None if proof is None else proof.policy_revision,
        policy_fingerprint=None if proof is None else proof.policy_fingerprint,
        snapshot_fingerprint=None if proof is None else proof.snapshot_fingerprint,
        acceptance_session_id=(None if proof is None else proof.acceptance_session_id),
        verification_verdict_id=(
            None if proof is None else proof.verification_verdict_id
        ),
        criteria_fingerprint=None if proof is None else proof.criteria_fingerprint,
        verification_check_ids=(() if proof is None else proof.verification_check_ids),
        deciding_evidence_ids=(() if proof is None else proof.deciding_evidence_ids),
        merged_evidence_id=None if proof is None else proof.merged_evidence_id,
        reason=reason,
        decision=(
            RetrospectiveCompletionDecision.DONE
            if reason is RetrospectiveCompletionReason.COMPLETE_DELIVERY_PROOF
            else RetrospectiveCompletionDecision.HOLD
        ),
        observed_at=now,
        created_by_type=ActorType.SYSTEM,
        created_by_id=CREATED_BY,
    )


def _confirmed_target(issue: LinearIssue, *, target_state_id: str) -> bool:
    return issue.state_id == target_state_id


def reconcile_retrospective_completion(
    *,
    db: Database,
    tickets: TicketRepo,
    github: GitHubClient,
    linear: LinearClient,
    status_map: LinearStatusMap,
    project_id: str,
    initial_issues: list[LinearIssue],
    ticket_key: str,
    publication: LinearMergedGitHubPublication,
    now: datetime,
    recovery_episode_id: UUID | None = None,
    hooks: RetrospectiveCompletionHooks | None = None,
    uuid_factory: Callable[[], UUID] = uuid4,
) -> RetrospectiveCompletionResult:
    """Prove and perform at most one fenced ci_pending -> done provider write."""

    if now.utcoffset() is None:
        raise ValueError("retrospective completion clock must be timezone-aware")
    hooks = hooks or RetrospectiveCompletionHooks()
    ticket = tickets.get_by_key(ticket_key)
    if ticket is None:
        raise ValueError(f"unknown ticket {ticket_key!r}")
    if ticket.status is not TicketStatus.CI_PENDING:
        reason = RetrospectiveCompletionReason.TICKET_NOT_CI_PENDING
        record = _decision_record(
            ticket=ticket,
            publication=publication,
            proof=None,
            reason=reason,
            now=now,
            reconciliation_id=uuid_factory(),
            recovery_episode_id=recovery_episode_id,
        )
        RetrospectiveCompletionReconciliationRepo(db).record(record)
        return RetrospectiveCompletionResult(reason=reason, ticket_key=ticket.key)

    initial_proof, initial_reason = evaluate_retrospective_proof(
        db=db,
        tickets=tickets,
        github=github,
        ticket=ticket,
        publication=publication,
        status_map=status_map,
        project_id=project_id,
        board_issues=initial_issues,
        now=now,
    )
    hooks.after_proof_evaluated()
    if initial_proof is None:
        record = _decision_record(
            ticket=ticket,
            publication=publication,
            proof=None,
            reason=initial_reason,
            now=now,
            reconciliation_id=uuid_factory(),
            recovery_episode_id=recovery_episode_id,
        )
        RetrospectiveCompletionReconciliationRepo(db).record(record)
        return RetrospectiveCompletionResult(
            reason=initial_reason,
            ticket_key=ticket.key,
            reconciliation_id=record.id,
        )

    lease = AdmissionCoordinationRepo(db)
    owner_id = uuid_factory()
    lease_started = hooks.monotonic_clock()
    if not lease.try_acquire(
        product_id=ticket.product_id,
        owner_id=owner_id,
        acquired_at=now,
        ttl=RETROSPECTIVE_COMPLETION_LEASE_TTL,
    ):
        return RetrospectiveCompletionResult(
            reason=RetrospectiveCompletionReason.LEASE_UNAVAILABLE,
            ticket_key=ticket.key,
        )
    try:
        try:
            fresh_issues = linear.fetch_project_issues(project_id)
        except LinearAPIError:
            return RetrospectiveCompletionResult(
                reason=RetrospectiveCompletionReason.BOARD_REVALIDATION_FAILED,
                ticket_key=ticket.key,
            )
        if not _complete_board(fresh_issues):
            return RetrospectiveCompletionResult(
                reason=RetrospectiveCompletionReason.BOARD_REVALIDATION_FAILED,
                ticket_key=ticket.key,
            )
        fresh_ticket = tickets.get_by_key(ticket.key)
        issue = (
            None
            if ticket.external_linear_id is None
            else _issue_by_id(fresh_issues, ticket.external_linear_id)
        )
        if (
            fresh_ticket is None
            or fresh_ticket.id != ticket.id
            or fresh_ticket.status is not TicketStatus.CI_PENDING
            or issue is None
            or status_from_issue(issue, status_map) is not TicketStatus.CI_PENDING
        ):
            return RetrospectiveCompletionResult(
                reason=RetrospectiveCompletionReason.BOARD_STATE_MOVED,
                ticket_key=ticket.key,
            )
        current_publication, publication_reason = resolve_historical_publication(
            fresh_ticket, fresh_issues
        )
        if current_publication != publication:
            return RetrospectiveCompletionResult(
                reason=(
                    publication_reason
                    or RetrospectiveCompletionReason.PUBLICATION_AMBIGUOUS
                ),
                ticket_key=ticket.key,
            )
        final_proof, final_reason = evaluate_retrospective_proof(
            db=db,
            tickets=tickets,
            github=github,
            ticket=fresh_ticket,
            publication=current_publication,
            status_map=status_map,
            project_id=project_id,
            board_issues=fresh_issues,
            now=now,
            expected_contributor_head=initial_proof.contributor_head,
        )
        if final_proof != initial_proof:
            reason = (
                final_reason
                if final_proof is None
                else RetrospectiveCompletionReason.PROOF_CHANGED
            )
            record = _decision_record(
                ticket=fresh_ticket,
                publication=current_publication,
                proof=None,
                reason=reason,
                now=now,
                reconciliation_id=uuid_factory(),
                recovery_episode_id=recovery_episode_id,
            )
            RetrospectiveCompletionReconciliationRepo(db).record(record)
            return RetrospectiveCompletionResult(
                reason=reason,
                ticket_key=ticket.key,
                reconciliation_id=record.id,
            )
        lease_age = hooks.monotonic_clock() - lease_started
        if (
            lease_age < 0
            or lease_age >= RETROSPECTIVE_COMPLETION_LEASE_TTL.total_seconds()
        ):
            return RetrospectiveCompletionResult(
                reason=RetrospectiveCompletionReason.LEASE_LOST,
                ticket_key=ticket.key,
            )
        assert final_proof is not None
        assert issue.state_id is not None
        reconciliation = _decision_record(
            ticket=fresh_ticket,
            publication=current_publication,
            proof=final_proof,
            reason=RetrospectiveCompletionReason.COMPLETE_DELIVERY_PROOF,
            now=now,
            reconciliation_id=uuid_factory(),
            recovery_episode_id=recovery_episode_id,
        )
        writer = LinearRetrospectiveCompletionWriter(linear, status_map)
        target_state_id = writer.target_state_id()
        coordination = RetrospectiveCompletionCoordinationRepo(db)
        try:
            coordination.record_and_begin_write(
                owner_id=owner_id,
                reconciliation=reconciliation,
                source_state_id=issue.state_id,
                target_state_id=target_state_id,
            )
        except CompetingWorkflowFencePresentError:
            return RetrospectiveCompletionResult(
                reason=RetrospectiveCompletionReason.CONCURRENT_WRITE_FENCE,
                ticket_key=ticket.key,
            )
        hooks.after_fence_persisted()
        hooks.before_provider_write()
        try:
            returned = coordination.execute_owned_call(
                product_id=ticket.product_id,
                owner_id=owner_id,
                reconciliation_id=reconciliation.id,
                observed_at=now + timedelta(seconds=lease_age),
                call=lambda: writer.transition(
                    issue.id, observed_source=TicketStatus.CI_PENDING
                ),
            )
        except (
            RetrospectiveProviderCallIndeterminateError,
            AdmissionLeaseLostError,
        ):
            return RetrospectiveCompletionResult(
                reason=RetrospectiveCompletionReason.WRITE_INDETERMINATE,
                ticket_key=ticket.key,
                reconciliation_id=reconciliation.id,
            )
        hooks.after_provider_write()
        if not _confirmed_target(returned, target_state_id=target_state_id):
            coordination.defer_unresolved(
                product_id=ticket.product_id,
                reconciliation_id=reconciliation.id,
                observed_at=now + timedelta(seconds=lease_age),
            )
            return RetrospectiveCompletionResult(
                reason=RetrospectiveCompletionReason.WRITE_INDETERMINATE,
                ticket_key=ticket.key,
                reconciliation_id=reconciliation.id,
            )
        coordination.clear_owned_fence(
            product_id=ticket.product_id,
            owner_id=owner_id,
            reconciliation_id=reconciliation.id,
            observed_at=now + timedelta(seconds=lease_age),
        )
        return RetrospectiveCompletionResult(
            reason=RetrospectiveCompletionReason.WRITE_CONFIRMED,
            decision=RetrospectiveCompletionDecision.DONE,
            ticket_key=ticket.key,
            reconciliation_id=reconciliation.id,
            linear_mutations=1,
        )
    except (AdmissionLeaseLostError, RetrospectiveCompletionWriteFenceError):
        return RetrospectiveCompletionResult(
            reason=RetrospectiveCompletionReason.LEASE_LOST,
            ticket_key=ticket.key,
        )
    finally:
        lease.release(product_id=ticket.product_id, owner_id=owner_id)


def reconcile_retrospective_completion_fence(
    *,
    db: Database,
    tickets: TicketRepo,
    github: GitHubClient,
    linear: LinearClient,
    status_map: LinearStatusMap,
    project_id: str,
    product_id: UUID,
    now: datetime,
    hooks: RetrospectiveCompletionHooks | None = None,
    uuid_factory: Callable[[], UUID] = uuid4,
) -> RetrospectiveCompletionResult | None:
    """Fresh-process owner of one prepared/indeterminate retrospective fence."""

    coordination = RetrospectiveCompletionCoordinationRepo(db)
    fence = coordination.get_fence(product_id)
    if fence is None:
        return None
    ticket = tickets.get_by_key(fence.ticket_key)
    if ticket is None or ticket.id != fence.ticket_id:
        raise RetrospectiveCompletionWriteFenceError(
            "retrospective fence no longer resolves to its exact ticket"
        )
    hooks = hooks or RetrospectiveCompletionHooks()
    lease = AdmissionCoordinationRepo(db)
    owner_id = uuid_factory()
    if not lease.try_acquire(
        product_id=product_id,
        owner_id=owner_id,
        acquired_at=now,
        ttl=RETROSPECTIVE_COMPLETION_LEASE_TTL,
    ):
        return RetrospectiveCompletionResult(
            reason=RetrospectiveCompletionReason.LEASE_UNAVAILABLE,
            ticket_key=ticket.key,
            reconciliation_id=fence.reconciliation_id,
            fence_reconciliation_attempted=True,
        )
    try:
        try:
            issues = linear.fetch_project_issues(project_id)
        except LinearAPIError:
            issues = []
        if not _complete_board(issues):
            coordination.defer_unresolved(
                product_id=product_id,
                reconciliation_id=fence.reconciliation_id,
                observed_at=now,
            )
            return RetrospectiveCompletionResult(
                reason=RetrospectiveCompletionReason.FENCE_STILL_UNRESOLVED,
                ticket_key=ticket.key,
                reconciliation_id=fence.reconciliation_id,
                fence_reconciliation_attempted=True,
            )
        issue = _issue_by_id(issues, fence.issue_id)
        if issue is None:
            coordination.defer_unresolved(
                product_id=product_id,
                reconciliation_id=fence.reconciliation_id,
                observed_at=now,
            )
            return RetrospectiveCompletionResult(
                reason=RetrospectiveCompletionReason.FENCE_STILL_UNRESOLVED,
                ticket_key=ticket.key,
                reconciliation_id=fence.reconciliation_id,
                fence_reconciliation_attempted=True,
            )
        if issue.state_id == fence.target_state_id:
            coordination.clear_owned_fence(
                product_id=product_id,
                owner_id=owner_id,
                reconciliation_id=fence.reconciliation_id,
                observed_at=now,
            )
            return RetrospectiveCompletionResult(
                reason=RetrospectiveCompletionReason.FENCE_RECONCILED_TARGET,
                decision=RetrospectiveCompletionDecision.DONE,
                ticket_key=ticket.key,
                reconciliation_id=fence.reconciliation_id,
                fence_reconciliation_attempted=True,
            )
        if issue.state_id != fence.source_state_id:
            coordination.defer_unresolved(
                product_id=product_id,
                reconciliation_id=fence.reconciliation_id,
                observed_at=now,
            )
            return RetrospectiveCompletionResult(
                reason=RetrospectiveCompletionReason.FENCE_RECONCILED_MOVED,
                ticket_key=ticket.key,
                reconciliation_id=fence.reconciliation_id,
                fence_reconciliation_attempted=True,
            )
        original = RetrospectiveCompletionReconciliationRepo(db).get(
            fence.reconciliation_id
        )
        publication, publication_reason = resolve_historical_publication(ticket, issues)
        if original is None or publication is None:
            coordination.defer_unresolved(
                product_id=product_id,
                reconciliation_id=fence.reconciliation_id,
                observed_at=now,
            )
            return RetrospectiveCompletionResult(
                reason=(
                    publication_reason
                    or RetrospectiveCompletionReason.FENCE_STILL_UNRESOLVED
                ),
                ticket_key=ticket.key,
                reconciliation_id=fence.reconciliation_id,
                fence_reconciliation_attempted=True,
            )
        proof, proof_reason = evaluate_retrospective_proof(
            db=db,
            tickets=tickets,
            github=github,
            ticket=ticket,
            publication=publication,
            status_map=status_map,
            project_id=project_id,
            board_issues=issues,
            now=now,
            expected_contributor_head=original.contributor_head,
        )
        if (
            proof is None
            or proof.merge_commit != original.merge_commit
            or proof.policy_id != original.policy_id
            or proof.policy_revision != original.policy_revision
            or proof.policy_fingerprint != original.policy_fingerprint
            or proof.acceptance_session_id != original.acceptance_session_id
            or proof.verification_verdict_id != original.verification_verdict_id
            or proof.criteria_fingerprint != original.criteria_fingerprint
            or proof.verification_check_ids != original.verification_check_ids
            or proof.deciding_evidence_ids != original.deciding_evidence_ids
            or proof.merged_evidence_id != original.merged_evidence_id
        ):
            coordination.defer_unresolved(
                product_id=product_id,
                reconciliation_id=fence.reconciliation_id,
                observed_at=now,
            )
            return RetrospectiveCompletionResult(
                reason=(
                    proof_reason
                    if proof is None
                    else RetrospectiveCompletionReason.PROOF_CHANGED
                ),
                ticket_key=ticket.key,
                reconciliation_id=fence.reconciliation_id,
                fence_reconciliation_attempted=True,
            )
        writer = LinearRetrospectiveCompletionWriter(linear, status_map)
        hooks.before_provider_write()
        try:
            returned = coordination.execute_owned_call(
                product_id=product_id,
                owner_id=owner_id,
                reconciliation_id=fence.reconciliation_id,
                observed_at=now,
                call=lambda: writer.transition(
                    issue.id, observed_source=TicketStatus.CI_PENDING
                ),
            )
        except (RetrospectiveProviderCallIndeterminateError, AdmissionLeaseLostError):
            return RetrospectiveCompletionResult(
                reason=RetrospectiveCompletionReason.WRITE_INDETERMINATE,
                ticket_key=ticket.key,
                reconciliation_id=fence.reconciliation_id,
                fence_reconciliation_attempted=True,
            )
        hooks.after_provider_write()
        if not _confirmed_target(returned, target_state_id=fence.target_state_id):
            coordination.defer_unresolved(
                product_id=product_id,
                reconciliation_id=fence.reconciliation_id,
                observed_at=now,
            )
            return RetrospectiveCompletionResult(
                reason=RetrospectiveCompletionReason.WRITE_INDETERMINATE,
                ticket_key=ticket.key,
                reconciliation_id=fence.reconciliation_id,
                fence_reconciliation_attempted=True,
            )
        coordination.clear_owned_fence(
            product_id=product_id,
            owner_id=owner_id,
            reconciliation_id=fence.reconciliation_id,
            observed_at=now,
        )
        return RetrospectiveCompletionResult(
            reason=RetrospectiveCompletionReason.FENCE_RECONCILED_SOURCE,
            decision=RetrospectiveCompletionDecision.DONE,
            ticket_key=ticket.key,
            reconciliation_id=fence.reconciliation_id,
            linear_mutations=1,
            fence_reconciliation_attempted=True,
        )
    finally:
        lease.release(product_id=product_id, owner_id=owner_id)
