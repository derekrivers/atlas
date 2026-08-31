"""Fail-closed, one-candidate system-tier CI handoff reconciliation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from time import monotonic
from uuid import UUID, uuid4

from atlas.core.enums import ActorType
from atlas.core.models import (
    CIHandoffClassification,
    CIHandoffDecision,
    CIHandoffReason,
    CIHandoffReconciliation,
    DeliveryAdmissionPolicyRevision,
    Evidence,
    Ticket,
    TicketStatus,
)
from atlas.dependencies.graph import build_dependency_graph
from atlas.github import (
    GitHubAPIError,
    GitHubClient,
    GitHubMalformedResponseError,
)
from atlas.linear import LinearCIHandoffWriter
from atlas.linear.client import LinearAPIError, LinearClient, LinearIssue
from atlas.linear.ownership import LinearStatusMap
from atlas.pm.delivery_snapshot import (
    DeliverySnapshot,
    LinearBoardPull,
    build_delivery_snapshot,
    delivery_policy_fingerprint,
)
from atlas.storage import (
    AdmissionCoordinationRepo,
    AdmissionLeaseLostError,
    CIHandoffCoordinationRepo,
    CIHandoffReconciliationRepo,
    CIHandoffWriteFenceError,
    Database,
    DeliveryAdmissionPolicyRepo,
    EvidenceRepo,
    TicketDependencyRepo,
    TicketRepo,
)
from atlas.verification import CIHandoffAssessment, evaluate_ci_handoff

CI_HANDOFF_LEASE_TTL = timedelta(minutes=5)
CREATED_BY = "ci-handoff-reconciler"


def _noop() -> None:
    return None


@dataclass(frozen=True)
class CIHandoffHooks:
    """Deterministic race seams for the critical pre-write identity checks."""

    after_initial_snapshot: Callable[[], None] = _noop
    after_classification: Callable[[], None] = _noop
    after_revalidation: Callable[[], None] = _noop
    after_fence_persisted: Callable[[], None] = _noop
    monotonic_clock: Callable[[], float] = monotonic


@dataclass(frozen=True)
class CIHandoffResult:
    """Safe bounded result returned to a tick/API adapter."""

    classification: CIHandoffClassification
    reason: CIHandoffReason
    decision: CIHandoffDecision
    ticket_key: str
    reconciliation_id: UUID | None = None
    linear_mutations: int = 0

    @property
    def safe_summary(self) -> str:
        return (
            f"CI handoff {self.ticket_key}: classification="
            f"{self.classification.value} decision={self.decision.value} "
            f"reason={self.reason.value} mutations={self.linear_mutations}"
        )


def _board_pull(issues: list[LinearIssue]) -> LinearBoardPull:
    return LinearBoardPull(
        issues=tuple(issues),
        complete=bool(getattr(issues, "complete", True)),
        pagination_gaps=tuple(getattr(issues, "pagination_gaps", ())),
    )


def _issue_by_id(issues: tuple[LinearIssue, ...], issue_id: str) -> LinearIssue | None:
    matches = [issue for issue in issues if issue.id == issue_id]
    return matches[0] if len(matches) == 1 else None


def _decision(classification: CIHandoffClassification) -> CIHandoffDecision:
    return {
        CIHandoffClassification.PASSED: CIHandoffDecision.REVIEW_REQUIRED,
        CIHandoffClassification.IMPLEMENTATION_FAILURE: (
            CIHandoffDecision.CHANGES_REQUESTED
        ),
    }.get(classification, CIHandoffDecision.HOLD)


def _result(
    ticket: Ticket,
    *,
    classification: CIHandoffClassification,
    reason: CIHandoffReason,
    reconciliation_id: UUID | None = None,
    linear_mutations: int = 0,
) -> CIHandoffResult:
    return CIHandoffResult(
        classification=classification,
        reason=reason,
        decision=_decision(classification),
        ticket_key=ticket.key,
        reconciliation_id=reconciliation_id,
        linear_mutations=linear_mutations,
    )


def _live_pr_head(
    github: GitHubClient,
    *,
    owner: str,
    repo: str,
    pr_number: int,
) -> tuple[str | None, CIHandoffReason | None]:
    try:
        payload = github.fetch_pull_request(owner, repo, pr_number)
    except GitHubMalformedResponseError:
        return None, CIHandoffReason.PR_IDENTITY_MALFORMED
    except GitHubAPIError:
        return None, CIHandoffReason.GITHUB_INFRASTRUCTURE
    try:
        number = payload["number"]
        head = payload["head"]
        base = payload["base"]
        sha = head["sha"]
        full_name = base["repo"]["full_name"]
    except (KeyError, TypeError):
        return None, CIHandoffReason.PR_IDENTITY_MALFORMED
    expected_repo = f"{owner}/{repo}"
    if (
        isinstance(number, bool)
        or number != pr_number
        or not isinstance(sha, str)
        or len(sha) != 40
        or any(char not in "0123456789abcdefABCDEF" for char in sha)
        or not isinstance(full_name, str)
        or full_name.casefold() != expected_repo.casefold()
    ):
        return None, CIHandoffReason.PR_IDENTITY_MALFORMED
    return sha.lower(), None


def _snapshot(
    *,
    db: Database,
    ticket: Ticket,
    project_id: str,
    status_map: LinearStatusMap,
    board_pull: LinearBoardPull,
    policy: DeliveryAdmissionPolicyRevision,
    now: datetime,
) -> DeliverySnapshot:
    return build_delivery_snapshot(
        product_id=ticket.product_id,
        linear_project_id=project_id,
        policy=policy,
        status_map=status_map,
        board_pull=board_pull,
        tickets=tuple(TicketRepo(db).list()),
        dependencies=tuple(TicketDependencyRepo(db).list()),
        graph=build_dependency_graph(db),
        clock=lambda: now,
    )


def _record(
    *,
    db: Database,
    ticket: Ticket,
    owner: str,
    repo: str,
    pr_number: int,
    expected_head: str,
    classification: CIHandoffClassification,
    reason: CIHandoffReason,
    assessment: CIHandoffAssessment | None,
    snapshot: DeliverySnapshot | None,
    policy: DeliveryAdmissionPolicyRevision | None,
    now: datetime,
    uuid_factory: Callable[[], UUID],
) -> CIHandoffReconciliation:
    policy_id = None if policy is None else policy.id
    policy_revision = None if policy is None else policy.revision
    policy_fingerprint = None if policy is None else delivery_policy_fingerprint(policy)
    model = CIHandoffReconciliation(
        id=uuid_factory(),
        product_id=ticket.product_id,
        ticket_id=ticket.id,
        ticket_key=ticket.key,
        linear_issue_id=ticket.external_linear_id,
        repository_owner=owner,
        repository_name=repo,
        pr_number=pr_number,
        head_commit=expected_head,
        policy_id=policy_id,
        policy_revision=policy_revision,
        policy_fingerprint=policy_fingerprint,
        snapshot_fingerprint=None if snapshot is None else snapshot.fingerprint,
        classification=classification,
        reason=reason,
        decision=_decision(classification),
        check_results=() if assessment is None else assessment.check_results,
        observed_at=now,
        created_by_type=ActorType.SYSTEM,
        created_by_id=CREATED_BY,
    )
    return CIHandoffReconciliationRepo(db).record(model)


def _reconcile_fence(
    *,
    db: Database,
    ticket: Ticket,
    status_map: LinearStatusMap,
    board_pull: LinearBoardPull,
    now: datetime,
) -> CIHandoffResult | None:
    coordination = CIHandoffCoordinationRepo(db)
    fence = coordination.get_fence(ticket.product_id)
    if fence is None:
        return None
    if not board_pull.complete or board_pull.pagination_gaps:
        return _result(
            ticket,
            classification=CIHandoffClassification.INDETERMINATE,
            reason=CIHandoffReason.FENCE_STILL_UNRESOLVED,
            reconciliation_id=fence.reconciliation_id,
        )
    issue = _issue_by_id(board_pull.issues, fence.issue_id)
    if issue is None or not status_map.state_type_is_compatible(
        issue.state_id, issue.state_type
    ):
        return _result(
            ticket,
            classification=CIHandoffClassification.INDETERMINATE,
            reason=CIHandoffReason.FENCE_STILL_UNRESOLVED,
            reconciliation_id=fence.reconciliation_id,
        )
    if issue.state_id == fence.target_state_id:
        current = TicketRepo(db).get_by_key(fence.ticket_key)
        if current is not None and current.status is TicketStatus.CI_PENDING:
            TicketRepo(db).apply_linear_status(
                current.key,
                fence.target_status,
                now=now,
                created_by_id=CREATED_BY,
            )
        coordination.clear_fence(
            product_id=fence.product_id,
            reconciliation_id=fence.reconciliation_id,
        )
        original = CIHandoffReconciliationRepo(db).get(fence.reconciliation_id)
        classification = (
            CIHandoffClassification.INDETERMINATE
            if original is None
            else original.classification
        )
        return _result(
            ticket,
            classification=classification,
            reason=CIHandoffReason.FENCE_RECONCILED_TARGET,
            reconciliation_id=fence.reconciliation_id,
        )
    coordination.clear_fence(
        product_id=fence.product_id,
        reconciliation_id=fence.reconciliation_id,
    )
    if issue.state_id == fence.source_state_id:
        return _result(
            ticket,
            classification=CIHandoffClassification.INDETERMINATE,
            reason=CIHandoffReason.FENCE_RECONCILED_SOURCE,
            reconciliation_id=fence.reconciliation_id,
        )
    return _result(
        ticket,
        classification=CIHandoffClassification.STALE,
        reason=CIHandoffReason.FENCE_RECONCILED_MOVED,
        reconciliation_id=fence.reconciliation_id,
    )


def reconcile_ci_handoff_fence(
    *,
    db: Database,
    tickets: TicketRepo,
    status_map: LinearStatusMap,
    initial_issues: list[LinearIssue],
    product_id: UUID,
    now: datetime,
    uuid_factory: Callable[[], UUID] = uuid4,
) -> CIHandoffResult | None:
    """Reconcile one existing product fence before ordinary candidate work.

    The fence already names the exact ticket and prior reconciliation.  Its
    next-process owner therefore needs only a complete board observation and
    the shared writer lease; publication discovery and CI evidence evaluation
    must not gate recovery of a possibly completed external write.
    """

    if now.utcoffset() is None:
        raise ValueError("CI handoff clock must be timezone-aware")
    fence = CIHandoffCoordinationRepo(db).get_fence(product_id)
    if fence is None:
        return None
    ticket = tickets.get_by_key(fence.ticket_key)
    if ticket is None or ticket.id != fence.ticket_id:
        raise CIHandoffWriteFenceError(
            "CI handoff fence no longer resolves to its exact ticket"
        )

    lease = AdmissionCoordinationRepo(db)
    owner_id = uuid_factory()
    if not lease.try_acquire(
        product_id=product_id,
        owner_id=owner_id,
        acquired_at=now,
        ttl=CI_HANDOFF_LEASE_TTL,
    ):
        return _result(
            ticket,
            classification=CIHandoffClassification.INDETERMINATE,
            reason=CIHandoffReason.LEASE_UNAVAILABLE,
        )
    try:
        return _reconcile_fence(
            db=db,
            ticket=ticket,
            status_map=status_map,
            board_pull=_board_pull(initial_issues),
            now=now,
        )
    finally:
        lease.release(product_id=product_id, owner_id=owner_id)


def reconcile_ci_handoff(
    *,
    db: Database,
    tickets: TicketRepo,
    github: GitHubClient,
    linear: LinearClient,
    status_map: LinearStatusMap,
    project_id: str,
    initial_issues: list[LinearIssue],
    ticket_key: str,
    repository_owner: str,
    repository_name: str,
    pr_number: int,
    expected_head: str,
    now: datetime,
    evidence_ids: tuple[UUID, ...] | None = None,
    hooks: CIHandoffHooks | None = None,
    uuid_factory: Callable[[], UUID] = uuid4,
) -> CIHandoffResult:
    """Evaluate and perform zero or one revalidated CI-pending Linear write."""

    if (
        len(expected_head) != 40
        or expected_head != expected_head.lower()
        or any(char not in "0123456789abcdef" for char in expected_head)
    ):
        raise ValueError("expected_head must be a full lowercase 40-character SHA")
    if now.utcoffset() is None:
        raise ValueError("CI handoff clock must be timezone-aware")
    ticket = tickets.get_by_key(ticket_key)
    if ticket is None:
        raise ValueError(f"unknown ticket {ticket_key!r}")
    hooks = hooks or CIHandoffHooks()
    lease_started_at = hooks.monotonic_clock()
    selected_evidence_ids = None if evidence_ids is None else frozenset(evidence_ids)
    selected_source_ids: frozenset[str] | None = None
    if selected_evidence_ids is not None:
        selected_source_ids = frozenset(
            record.external_run_id
            for record in EvidenceRepo(db).list_for_product(ticket.product_id)
            if record.id in selected_evidence_ids and record.external_run_id is not None
        )

    def scoped_evidence(product_id: UUID) -> list[Evidence]:
        records = EvidenceRepo(db).list_for_product(product_id)
        if selected_source_ids is None:
            return records
        return [
            record
            for record in records
            if record.external_run_id in selected_source_ids
        ]

    lease = AdmissionCoordinationRepo(db)
    owner_id = uuid_factory()
    if not lease.try_acquire(
        product_id=ticket.product_id,
        owner_id=owner_id,
        acquired_at=now,
        ttl=CI_HANDOFF_LEASE_TTL,
    ):
        return _result(
            ticket,
            classification=CIHandoffClassification.INDETERMINATE,
            reason=CIHandoffReason.LEASE_UNAVAILABLE,
        )

    try:
        initial_pull = _board_pull(initial_issues)
        fenced = _reconcile_fence(
            db=db,
            ticket=ticket,
            status_map=status_map,
            board_pull=initial_pull,
            now=now,
        )
        if fenced is not None:
            return fenced
        if AdmissionCoordinationRepo(db).get_fence(ticket.product_id) is not None:
            return _result(
                ticket,
                classification=CIHandoffClassification.INDETERMINATE,
                reason=CIHandoffReason.CONCURRENT_WRITE_FENCE,
            )

        policy_repo = DeliveryAdmissionPolicyRepo(db)
        policy = policy_repo.get_active(ticket.product_id)
        if policy is None:
            recorded = _record(
                db=db,
                ticket=ticket,
                owner=repository_owner,
                repo=repository_name,
                pr_number=pr_number,
                expected_head=expected_head,
                classification=CIHandoffClassification.INDETERMINATE,
                reason=CIHandoffReason.POLICY_UNAVAILABLE,
                assessment=None,
                snapshot=None,
                policy=None,
                now=now,
                uuid_factory=uuid_factory,
            )
            return _result(
                ticket,
                classification=recorded.classification,
                reason=recorded.reason,
                reconciliation_id=recorded.id,
            )

        initial_snapshot = _snapshot(
            db=db,
            ticket=ticket,
            project_id=project_id,
            status_map=status_map,
            board_pull=initial_pull,
            policy=policy,
            now=now,
        )
        if initial_snapshot.incompleteness_reasons:
            recorded = _record(
                db=db,
                ticket=ticket,
                owner=repository_owner,
                repo=repository_name,
                pr_number=pr_number,
                expected_head=expected_head,
                classification=CIHandoffClassification.INDETERMINATE,
                reason=CIHandoffReason.SNAPSHOT_INCOMPLETE,
                assessment=None,
                snapshot=initial_snapshot,
                policy=policy,
                now=now,
                uuid_factory=uuid_factory,
            )
            return _result(
                ticket,
                classification=recorded.classification,
                reason=recorded.reason,
                reconciliation_id=recorded.id,
            )
        hooks.after_initial_snapshot()

        current_ticket = tickets.get_by_key(ticket_key)
        evidence_eligible = False
        if current_ticket is None or current_ticket.id != ticket.id:
            classification = CIHandoffClassification.STALE
            reason = CIHandoffReason.TICKET_IDENTITY_MISMATCH
        elif current_ticket.status is not TicketStatus.CI_PENDING:
            classification = CIHandoffClassification.STALE
            reason = CIHandoffReason.TICKET_NOT_CI_PENDING
        elif current_ticket.external_linear_id is None:
            classification = CIHandoffClassification.INDETERMINATE
            reason = CIHandoffReason.LINEAR_ISSUE_MISSING
        else:
            initial_issue = _issue_by_id(
                initial_pull.issues, current_ticket.external_linear_id
            )
            if initial_issue is None:
                classification = CIHandoffClassification.INDETERMINATE
                reason = CIHandoffReason.LINEAR_ISSUE_MISSING
            elif status_map.status_for(
                initial_issue.state_id
            ) is not TicketStatus.CI_PENDING or not status_map.state_type_is_compatible(
                initial_issue.state_id, initial_issue.state_type
            ):
                classification = CIHandoffClassification.STALE
                reason = CIHandoffReason.LINEAR_STATE_MISMATCH
            else:
                evidence_eligible = True
                classification = CIHandoffClassification.INDETERMINATE
                reason = CIHandoffReason.INDETERMINATE_EVIDENCE

        live_head, pr_reason = _live_pr_head(
            github,
            owner=repository_owner,
            repo=repository_name,
            pr_number=pr_number,
        )
        if pr_reason is not None:
            classification = (
                CIHandoffClassification.MALFORMED
                if pr_reason is CIHandoffReason.PR_IDENTITY_MALFORMED
                else CIHandoffClassification.INFRASTRUCTURE
            )
            reason = pr_reason
        elif live_head != expected_head:
            classification = CIHandoffClassification.STALE
            reason = CIHandoffReason.PR_HEAD_MOVED

        assessment: CIHandoffAssessment | None = None
        if (
            current_ticket is not None
            and current_ticket.id == ticket.id
            and current_ticket.status is TicketStatus.CI_PENDING
            and current_ticket.external_linear_id is not None
            and live_head == expected_head
            and pr_reason is None
            and evidence_eligible
        ):
            assessment = evaluate_ci_handoff(
                current_ticket,
                head_commit=expected_head,
                evidence=scoped_evidence(current_ticket.product_id),
            )
            classification = assessment.classification
            reason = assessment.reason

        if _decision(classification) is CIHandoffDecision.HOLD:
            recorded = _record(
                db=db,
                ticket=ticket,
                owner=repository_owner,
                repo=repository_name,
                pr_number=pr_number,
                expected_head=expected_head,
                classification=classification,
                reason=reason,
                assessment=assessment,
                snapshot=initial_snapshot,
                policy=policy,
                now=now,
                uuid_factory=uuid_factory,
            )
            return _result(
                ticket,
                classification=recorded.classification,
                reason=recorded.reason,
                reconciliation_id=recorded.id,
            )

        assert current_ticket is not None
        assert current_ticket.external_linear_id is not None
        assert assessment is not None
        issue_id = current_ticket.external_linear_id
        hooks.after_classification()

        def revalidate() -> tuple[CIHandoffClassification, CIHandoffReason] | None:
            re_head, re_pr_reason = _live_pr_head(
                github,
                owner=repository_owner,
                repo=repository_name,
                pr_number=pr_number,
            )
            if re_pr_reason is not None:
                return (
                    CIHandoffClassification.MALFORMED
                    if re_pr_reason is CIHandoffReason.PR_IDENTITY_MALFORMED
                    else CIHandoffClassification.INFRASTRUCTURE,
                    re_pr_reason,
                )
            if re_head != expected_head:
                return CIHandoffClassification.STALE, CIHandoffReason.PR_HEAD_MOVED
            try:
                board = _board_pull(linear.fetch_project_issues(project_id))
            except LinearAPIError:
                return (
                    CIHandoffClassification.INFRASTRUCTURE,
                    CIHandoffReason.BOARD_REVALIDATION_FAILED,
                )
            current_policy = policy_repo.get_active(ticket.product_id)
            if current_policy is None or (
                current_policy.id != policy.id
                or current_policy.revision != policy.revision
                or delivery_policy_fingerprint(current_policy)
                != delivery_policy_fingerprint(policy)
            ):
                return CIHandoffClassification.STALE, CIHandoffReason.POLICY_CHANGED
            re_ticket = tickets.get_by_key(ticket.key)
            if re_ticket is None or re_ticket.id != ticket.id:
                return (
                    CIHandoffClassification.STALE,
                    CIHandoffReason.TICKET_IDENTITY_MISMATCH,
                )
            if (
                re_ticket.status is not TicketStatus.CI_PENDING
                or re_ticket.external_linear_id != issue_id
            ):
                return (
                    CIHandoffClassification.STALE,
                    CIHandoffReason.BOARD_STATE_MOVED,
                )
            issue = _issue_by_id(board.issues, issue_id)
            if issue is None or (
                status_map.status_for(issue.state_id) is not TicketStatus.CI_PENDING
                or not status_map.state_type_is_compatible(
                    issue.state_id, issue.state_type
                )
            ):
                return (
                    CIHandoffClassification.STALE,
                    CIHandoffReason.BOARD_STATE_MOVED,
                )
            current_snapshot = _snapshot(
                db=db,
                ticket=re_ticket,
                project_id=project_id,
                status_map=status_map,
                board_pull=board,
                policy=current_policy,
                now=now,
            )
            if current_snapshot.incompleteness_reasons:
                return (
                    CIHandoffClassification.STALE,
                    CIHandoffReason.SNAPSHOT_INCOMPLETE,
                )
            if current_snapshot.fingerprint != initial_snapshot.fingerprint:
                return (
                    CIHandoffClassification.STALE,
                    CIHandoffReason.SNAPSHOT_CHANGED,
                )
            return None

        race = revalidate()
        if race is None:
            hooks.after_revalidation()
            race = revalidate()
        if race is not None:
            race_classification, race_reason = race
            recorded = _record(
                db=db,
                ticket=ticket,
                owner=repository_owner,
                repo=repository_name,
                pr_number=pr_number,
                expected_head=expected_head,
                classification=race_classification,
                reason=race_reason,
                assessment=assessment,
                snapshot=initial_snapshot,
                policy=policy,
                now=now,
                uuid_factory=uuid_factory,
            )
            return _result(
                ticket,
                classification=recorded.classification,
                reason=recorded.reason,
                reconciliation_id=recorded.id,
            )
        if not lease.is_owner(product_id=ticket.product_id, owner_id=owner_id):
            recorded = _record(
                db=db,
                ticket=ticket,
                owner=repository_owner,
                repo=repository_name,
                pr_number=pr_number,
                expected_head=expected_head,
                classification=CIHandoffClassification.STALE,
                reason=CIHandoffReason.LEASE_LOST,
                assessment=assessment,
                snapshot=initial_snapshot,
                policy=policy,
                now=now,
                uuid_factory=uuid_factory,
            )
            return _result(
                ticket,
                classification=recorded.classification,
                reason=recorded.reason,
                reconciliation_id=recorded.id,
            )

        refreshed_assessment = evaluate_ci_handoff(
            current_ticket,
            head_commit=expected_head,
            evidence=scoped_evidence(current_ticket.product_id),
        )
        if refreshed_assessment != assessment:
            recorded = _record(
                db=db,
                ticket=ticket,
                owner=repository_owner,
                repo=repository_name,
                pr_number=pr_number,
                expected_head=expected_head,
                classification=CIHandoffClassification.STALE,
                reason=CIHandoffReason.EVIDENCE_CHANGED,
                assessment=refreshed_assessment,
                snapshot=initial_snapshot,
                policy=policy,
                now=now,
                uuid_factory=uuid_factory,
            )
            return _result(
                ticket,
                classification=recorded.classification,
                reason=recorded.reason,
                reconciliation_id=recorded.id,
            )

        recorded = _record(
            db=db,
            ticket=ticket,
            owner=repository_owner,
            repo=repository_name,
            pr_number=pr_number,
            expected_head=expected_head,
            classification=assessment.classification,
            reason=assessment.reason,
            assessment=assessment,
            snapshot=initial_snapshot,
            policy=policy,
            now=now,
            uuid_factory=uuid_factory,
        )
        target = recorded.target_status
        assert target is not None
        writer = LinearCIHandoffWriter(linear, status_map)
        target_state_id = writer.target_state_id(target)
        source_state_id = status_map.state_id_for(TicketStatus.CI_PENDING)
        coordination = CIHandoffCoordinationRepo(db)
        try:
            coordination.begin_write(
                product_id=ticket.product_id,
                owner_id=owner_id,
                reconciliation_id=recorded.id,
                ticket_id=ticket.id,
                ticket_key=ticket.key,
                issue_id=issue_id,
                source_state_id=source_state_id,
                target_state_id=target_state_id,
                target_status=target,
                created_at=now,
            )
        except AdmissionLeaseLostError:
            return _result(
                ticket,
                classification=CIHandoffClassification.STALE,
                reason=CIHandoffReason.LEASE_LOST,
                reconciliation_id=recorded.id,
            )

        hooks.after_fence_persisted()
        lease_age = hooks.monotonic_clock() - lease_started_at
        if (
            lease_age < 0
            or lease_age >= CI_HANDOFF_LEASE_TTL.total_seconds()
            or not (lease.is_owner(product_id=ticket.product_id, owner_id=owner_id))
        ):
            # The durable fence remains for the replacement owner.  An expired
            # process must never resume its external call after that owner can
            # have observed source and begun recovery.
            return _result(
                ticket,
                classification=CIHandoffClassification.STALE,
                reason=CIHandoffReason.LEASE_LOST,
                reconciliation_id=recorded.id,
            )

        try:
            written = writer.transition(
                issue_id,
                observed_source=TicketStatus.CI_PENDING,
                target=target,
            )
        except Exception:
            coordination.mark_indeterminate(
                product_id=ticket.product_id,
                reconciliation_id=recorded.id,
                observed_at=now,
            )
            return _result(
                ticket,
                classification=CIHandoffClassification.INDETERMINATE,
                reason=CIHandoffReason.WRITE_INDETERMINATE,
                reconciliation_id=recorded.id,
                linear_mutations=1,
            )
        if written.id != issue_id or written.state_id != target_state_id:
            coordination.mark_indeterminate(
                product_id=ticket.product_id,
                reconciliation_id=recorded.id,
                observed_at=now,
            )
            return _result(
                ticket,
                classification=CIHandoffClassification.INDETERMINATE,
                reason=CIHandoffReason.WRITE_INDETERMINATE,
                reconciliation_id=recorded.id,
                linear_mutations=1,
            )
        tickets.apply_linear_status(
            ticket.key,
            target,
            now=now,
            created_by_id=CREATED_BY,
        )
        coordination.clear_fence(
            product_id=ticket.product_id,
            reconciliation_id=recorded.id,
        )
        return _result(
            ticket,
            classification=recorded.classification,
            reason=CIHandoffReason.WRITE_CONFIRMED,
            reconciliation_id=recorded.id,
            linear_mutations=1,
        )
    finally:
        lease.release(product_id=ticket.product_id, owner_id=owner_id)
