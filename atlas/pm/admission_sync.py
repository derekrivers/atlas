"""Fail-closed single-write admission protocol for the PM sync tick."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

from atlas.core.models import AdmissionRun, Ticket
from atlas.core.models.admission_run import AdmissionHoldCode
from atlas.core.models.ticket import TicketStatus
from atlas.dependencies import ready_tickets
from atlas.dependencies.graph import build_dependency_graph
from atlas.linear.client import LinearAPIError, LinearClient, LinearIssue
from atlas.linear.ownership import LinearStatusMap
from atlas.pm.admission import evaluate_admission
from atlas.pm.delivery_snapshot import (
    DeliverySnapshot,
    LinearBoardPull,
    build_delivery_snapshot,
    delivery_graph_revision,
    delivery_policy_fingerprint,
    delivery_store_revision,
)
from atlas.pm.protected_lanes import (
    ProtectedLaneRegistryLoadResult,
    load_packaged_protected_lane_registry,
)
from atlas.storage import (
    AdmissionCoordinationRepo,
    AdmissionLeaseLostError,
    AdmissionProviderCallIndeterminateError,
    AdmissionRunRepo,
    AdmissionWriteFenceError,
    CIHandoffFencePresentError,
    Database,
    DeliveryAdmissionPolicyRepo,
    ProductRepo,
    TicketDependencyRepo,
    TicketRepo,
)

ADMISSION_LEASE_TTL = timedelta(minutes=5)


class AdmissionSyncOutcome(StrEnum):
    """Bounded operator-facing result of one admission protocol attempt."""

    ADMITTED = "admitted"
    HELD = "held"
    OVER_CAPACITY = "over_capacity"
    STALE = "stale"
    INDETERMINATE = "indeterminate"


class AdmissionSyncReason(StrEnum):
    """Safe reason codes; none contains an external response or issue body."""

    LEASE_UNAVAILABLE = "lease_unavailable"
    CI_HANDOFF_FENCE_PRESENT = "ci_handoff_fence_present"
    PRODUCT_AMBIGUOUS = "product_ambiguous"
    POLICY_UNAVAILABLE = "policy_unavailable"
    SNAPSHOT_INCOMPLETE = "snapshot_incomplete"
    NO_CANDIDATE = "no_candidate"
    POLICY_OR_CAPACITY_HOLD = "policy_or_capacity_hold"
    OVER_CAPACITY = "over_capacity"
    REVALIDATION_FAILED = "revalidation_failed"
    REVALIDATION_MISMATCH = "revalidation_mismatch"
    POLICY_CHANGED = "policy_changed"
    PROTECTED_LANE_REGISTRY_UNAVAILABLE = "protected_lane_registry_unavailable"
    PROTECTED_LANE_REGISTRY_CHANGED = "protected_lane_registry_changed"
    PROTECTED_LANE_STATE_CHANGED = "protected_lane_state_changed"
    CANDIDATE_MOVED = "candidate_moved"
    LEASE_LOST = "lease_lost"
    WRITE_CONFIRMED = "write_confirmed"
    WRITE_INDETERMINATE = "write_indeterminate"
    INDETERMINATE_STILL_UNRESOLVED = "indeterminate_still_unresolved"
    INDETERMINATE_RECONCILED_ADMITTED = "indeterminate_reconciled_admitted"
    INDETERMINATE_RECONCILED_NO_WRITE = "indeterminate_reconciled_no_write"
    INDETERMINATE_RECONCILED_MOVED = "indeterminate_reconciled_moved"


@dataclass(frozen=True)
class AdmissionSyncResult:
    """One safe admission detail projected into ``SyncResult`` and CLI output."""

    outcome: AdmissionSyncOutcome
    reason: AdmissionSyncReason
    policy_revision: int | None = None
    policy_fingerprint: str | None = None
    admission_run_id: UUID | None = None
    ticket_key: str | None = None

    @property
    def routine(self) -> bool:
        """Whether non-verbose CLI output may omit this detail line."""

        return self.reason is AdmissionSyncReason.NO_CANDIDATE

    @property
    def safe_summary(self) -> str:
        """Render only the bounded fields approved for operator output."""

        revision = "none" if self.policy_revision is None else str(self.policy_revision)
        return (
            f"admission {self.outcome.value} {self.ticket_key or 'none'}: "
            f"reason={self.reason.value} policy_revision={revision}"
        )


def _noop() -> None:
    return None


@dataclass(frozen=True)
class AdmissionSyncHooks:
    """Deterministic race seams used only by the sync integration tests."""

    after_initial_snapshot: Callable[[], None] = _noop
    after_decision: Callable[[], None] = _noop
    after_revalidation: Callable[[], None] = _noop


def _board_pull(issues: list[LinearIssue]) -> LinearBoardPull:
    return LinearBoardPull(
        issues=tuple(issues),
        complete=bool(getattr(issues, "complete", True)),
        pagination_gaps=tuple(getattr(issues, "pagination_gaps", ())),
    )


def _product_id(db: Database, tickets: tuple[Ticket, ...]) -> UUID | None:
    ticket_product_ids = {ticket.product_id for ticket in tickets}
    if len(ticket_product_ids) == 1:
        return next(iter(ticket_product_ids))
    if ticket_product_ids:
        return None
    products = ProductRepo(db).list()
    return products[0].id if len(products) == 1 else None


def _record_run_once(db: Database, run: AdmissionRun) -> None:
    existing = AdmissionRunRepo(db).get(run.id)
    if existing is None:
        AdmissionRunRepo(db).record(run)
        return
    if existing != run:
        raise RuntimeError("admission run id collided with different decision content")


def _issue_by_id(issues: tuple[LinearIssue, ...], issue_id: str) -> LinearIssue | None:
    matches = [issue for issue in issues if issue.id == issue_id]
    return matches[0] if len(matches) == 1 else None


_ACTIVE_AFTER_ADMISSION = frozenset(
    {
        TicketStatus.READY_FOR_AGENT,
        TicketStatus.IN_PROGRESS,
        TicketStatus.PR_OPEN,
        TicketStatus.CHANGES_REQUESTED,
    }
)


def _reconcile_fence(
    *,
    coordination: AdmissionCoordinationRepo,
    product_id: UUID,
    status_map: LinearStatusMap,
    board_pull: LinearBoardPull,
) -> AdmissionSyncResult | None:
    fence = coordination.get_fence(product_id)
    if fence is None:
        return None
    issue = _issue_by_id(board_pull.issues, fence.issue_id)
    if issue is None:
        return AdmissionSyncResult(
            outcome=AdmissionSyncOutcome.INDETERMINATE,
            reason=AdmissionSyncReason.INDETERMINATE_STILL_UNRESOLVED,
            policy_revision=fence.policy_revision,
            admission_run_id=fence.admission_run_id,
            ticket_key=fence.ticket_key,
        )
    mapped = status_map.status_for(issue.state_id)
    if mapped is None or not status_map.state_type_is_compatible(
        issue.state_id, issue.state_type
    ):
        return AdmissionSyncResult(
            outcome=AdmissionSyncOutcome.INDETERMINATE,
            reason=AdmissionSyncReason.INDETERMINATE_STILL_UNRESOLVED,
            policy_revision=fence.policy_revision,
            admission_run_id=fence.admission_run_id,
            ticket_key=fence.ticket_key,
        )

    coordination.clear_fence(
        product_id=product_id, admission_run_id=fence.admission_run_id
    )
    if issue.state_id == fence.target_state_id or mapped in _ACTIVE_AFTER_ADMISSION:
        return AdmissionSyncResult(
            outcome=AdmissionSyncOutcome.ADMITTED,
            reason=AdmissionSyncReason.INDETERMINATE_RECONCILED_ADMITTED,
            policy_revision=fence.policy_revision,
            admission_run_id=fence.admission_run_id,
            ticket_key=fence.ticket_key,
        )
    if issue.state_id == fence.source_state_id:
        return AdmissionSyncResult(
            outcome=AdmissionSyncOutcome.HELD,
            reason=AdmissionSyncReason.INDETERMINATE_RECONCILED_NO_WRITE,
            policy_revision=fence.policy_revision,
            admission_run_id=fence.admission_run_id,
            ticket_key=fence.ticket_key,
        )
    return AdmissionSyncResult(
        outcome=AdmissionSyncOutcome.STALE,
        reason=AdmissionSyncReason.INDETERMINATE_RECONCILED_MOVED,
        policy_revision=fence.policy_revision,
        admission_run_id=fence.admission_run_id,
        ticket_key=fence.ticket_key,
    )


_CAPACITY_HOLD_CODES = frozenset(
    {
        AdmissionHoldCode.WORKING_BUDGET,
        AdmissionHoldCode.INTEGRATION_BUDGET,
        AdmissionHoldCode.REVIEW_BUDGET,
        AdmissionHoldCode.CHANGES_REQUESTED_RESERVE,
        AdmissionHoldCode.RISK_LANE,
        AdmissionHoldCode.COMPONENT_LANE,
        AdmissionHoldCode.PROTECTED_LANE,
    }
)


def _held_result(run: AdmissionRun, snapshot: DeliverySnapshot) -> AdmissionSyncResult:
    over_capacity = bool(snapshot.over_capacity) or any(
        reason.code in _CAPACITY_HOLD_CODES
        for decision in run.decisions
        for reason in decision.reasons
    )
    return AdmissionSyncResult(
        outcome=(
            AdmissionSyncOutcome.OVER_CAPACITY
            if over_capacity
            else AdmissionSyncOutcome.HELD
        ),
        reason=(
            AdmissionSyncReason.OVER_CAPACITY
            if over_capacity
            else (
                AdmissionSyncReason.NO_CANDIDATE
                if not run.decisions
                else AdmissionSyncReason.POLICY_OR_CAPACITY_HOLD
            )
        ),
        policy_revision=run.policy_revision,
        policy_fingerprint=run.policy_fingerprint,
        admission_run_id=run.id,
    )


def admit_one_ready(
    *,
    tickets: TicketRepo,
    db: Database,
    client: LinearClient,
    status_map: LinearStatusMap,
    project_id: str,
    initial_issues: list[LinearIssue],
    now: datetime,
    hooks: AdmissionSyncHooks | None = None,
    protected_lane_registry_provider: Callable[
        [], ProtectedLaneRegistryLoadResult
    ] = load_packaged_protected_lane_registry,
) -> AdmissionSyncResult:
    """Evaluate and perform zero or one revalidated Ready-for-Agent write."""

    hooks = hooks or AdmissionSyncHooks()
    initial_tickets = tuple(tickets.list())
    product_id = _product_id(db, initial_tickets)
    if product_id is None:
        return AdmissionSyncResult(
            outcome=AdmissionSyncOutcome.HELD,
            reason=AdmissionSyncReason.PRODUCT_AMBIGUOUS,
        )

    coordination = AdmissionCoordinationRepo(db)
    owner_id = uuid4()
    if not coordination.try_acquire(
        product_id=product_id,
        owner_id=owner_id,
        acquired_at=now,
        ttl=ADMISSION_LEASE_TTL,
    ):
        policy = DeliveryAdmissionPolicyRepo(db).get_active(product_id)
        return AdmissionSyncResult(
            outcome=AdmissionSyncOutcome.HELD,
            reason=AdmissionSyncReason.LEASE_UNAVAILABLE,
            policy_revision=None if policy is None else policy.revision,
            policy_fingerprint=(
                None if policy is None else delivery_policy_fingerprint(policy)
            ),
        )

    try:
        initial_pull = _board_pull(initial_issues)
        reconciled = _reconcile_fence(
            coordination=coordination,
            product_id=product_id,
            status_map=status_map,
            board_pull=initial_pull,
        )
        if reconciled is not None:
            return reconciled

        policy_repo = DeliveryAdmissionPolicyRepo(db)
        policy = policy_repo.get_active(product_id)
        if policy is None:
            return AdmissionSyncResult(
                outcome=AdmissionSyncOutcome.HELD,
                reason=AdmissionSyncReason.POLICY_UNAVAILABLE,
            )

        registry_result = protected_lane_registry_provider()
        protected_lane_registry = registry_result.registry
        if protected_lane_registry is None:
            return AdmissionSyncResult(
                outcome=AdmissionSyncOutcome.STALE,
                reason=AdmissionSyncReason.PROTECTED_LANE_REGISTRY_UNAVAILABLE,
                policy_revision=policy.revision,
                policy_fingerprint=delivery_policy_fingerprint(policy),
            )

        dependencies = tuple(TicketDependencyRepo(db).list())
        graph = build_dependency_graph(db)
        snapshot = build_delivery_snapshot(
            product_id=product_id,
            linear_project_id=project_id,
            policy=policy,
            status_map=status_map,
            board_pull=initial_pull,
            tickets=initial_tickets,
            dependencies=dependencies,
            graph=graph,
            clock=lambda: now,
            protected_lane_registry=protected_lane_registry,
        )
        if snapshot.incompleteness_reasons:
            return AdmissionSyncResult(
                outcome=AdmissionSyncOutcome.STALE,
                reason=AdmissionSyncReason.SNAPSHOT_INCOMPLETE,
                policy_revision=policy.revision,
                policy_fingerprint=delivery_policy_fingerprint(policy),
            )
        hooks.after_initial_snapshot()

        ticket_by_key = {ticket.key: ticket for ticket in initial_tickets}
        candidates = tuple(
            ticket_by_key[result.key]
            for result in ready_tickets(graph)
            if result.key in ticket_by_key
        )
        eligibility = coordination.reconcile_eligibility(
            product_id=product_id,
            candidates=candidates,
            observed_at=now,
        )
        run = evaluate_admission(
            graph=graph,
            tickets=initial_tickets,
            policy=policy,
            snapshot=snapshot,
            continuously_eligible_since=eligibility,
            clock=lambda: now,
            protected_lane_registry=protected_lane_registry,
        )
        _record_run_once(db, run)
        hooks.after_decision()
        if run.selected_ticket_id is None or run.selected_ticket_key is None:
            return _held_result(run, snapshot)

        selected = next(
            decision
            for decision in run.decisions
            if decision.ticket_id == run.selected_ticket_id
        )
        if selected.external_linear_id is None:  # evaluator normally holds this
            return AdmissionSyncResult(
                outcome=AdmissionSyncOutcome.STALE,
                reason=AdmissionSyncReason.REVALIDATION_MISMATCH,
                policy_revision=policy.revision,
                policy_fingerprint=delivery_policy_fingerprint(policy),
                admission_run_id=run.id,
                ticket_key=selected.ticket_key,
            )
        initial_issue = _issue_by_id(initial_pull.issues, selected.external_linear_id)
        if initial_issue is None or initial_issue.state_id is None:
            return AdmissionSyncResult(
                outcome=AdmissionSyncOutcome.STALE,
                reason=AdmissionSyncReason.REVALIDATION_MISMATCH,
                policy_revision=policy.revision,
                policy_fingerprint=delivery_policy_fingerprint(policy),
                admission_run_id=run.id,
                ticket_key=selected.ticket_key,
            )

        target_state_id = status_map.state_id_for(TicketStatus.READY_FOR_AGENT)
        try:
            revalidated_issues = client.fetch_project_issues(project_id)
        except LinearAPIError:
            return AdmissionSyncResult(
                outcome=AdmissionSyncOutcome.STALE,
                reason=AdmissionSyncReason.REVALIDATION_FAILED,
                policy_revision=policy.revision,
                policy_fingerprint=delivery_policy_fingerprint(policy),
                admission_run_id=run.id,
                ticket_key=selected.ticket_key,
            )
        revalidated_pull = _board_pull(revalidated_issues)
        current_registry_result = protected_lane_registry_provider()
        current_protected_lane_registry = current_registry_result.registry
        if current_protected_lane_registry is None:
            return AdmissionSyncResult(
                outcome=AdmissionSyncOutcome.STALE,
                reason=AdmissionSyncReason.PROTECTED_LANE_REGISTRY_UNAVAILABLE,
                policy_revision=policy.revision,
                policy_fingerprint=delivery_policy_fingerprint(policy),
                admission_run_id=run.id,
                ticket_key=selected.ticket_key,
            )
        if (
            current_protected_lane_registry.version != protected_lane_registry.version
            or current_protected_lane_registry.fingerprint
            != protected_lane_registry.fingerprint
        ):
            return AdmissionSyncResult(
                outcome=AdmissionSyncOutcome.STALE,
                reason=AdmissionSyncReason.PROTECTED_LANE_REGISTRY_CHANGED,
                policy_revision=policy.revision,
                policy_fingerprint=delivery_policy_fingerprint(policy),
                admission_run_id=run.id,
                ticket_key=selected.ticket_key,
            )
        current_policy = policy_repo.get_active(product_id)
        if current_policy is None or (
            current_policy.id != policy.id
            or current_policy.revision != policy.revision
            or delivery_policy_fingerprint(current_policy)
            != delivery_policy_fingerprint(policy)
        ):
            return AdmissionSyncResult(
                outcome=AdmissionSyncOutcome.STALE,
                reason=AdmissionSyncReason.POLICY_CHANGED,
                policy_revision=(
                    policy.revision
                    if current_policy is None
                    else current_policy.revision
                ),
                policy_fingerprint=(
                    None
                    if current_policy is None
                    else delivery_policy_fingerprint(current_policy)
                ),
                admission_run_id=run.id,
                ticket_key=selected.ticket_key,
            )
        current_tickets = tuple(tickets.list())
        current_dependencies = tuple(TicketDependencyRepo(db).list())
        current_graph = build_dependency_graph(db)
        revalidated_snapshot = build_delivery_snapshot(
            product_id=product_id,
            linear_project_id=project_id,
            policy=current_policy,
            status_map=status_map,
            board_pull=revalidated_pull,
            tickets=current_tickets,
            dependencies=current_dependencies,
            graph=current_graph,
            clock=lambda: now,
            protected_lane_registry=current_protected_lane_registry,
        )
        revalidated_issue = _issue_by_id(
            revalidated_pull.issues, selected.external_linear_id
        )
        if revalidated_issue is None or (
            revalidated_issue.state_id != initial_issue.state_id
        ):
            return AdmissionSyncResult(
                outcome=AdmissionSyncOutcome.STALE,
                reason=AdmissionSyncReason.CANDIDATE_MOVED,
                policy_revision=current_policy.revision,
                policy_fingerprint=delivery_policy_fingerprint(current_policy),
                admission_run_id=run.id,
                ticket_key=selected.ticket_key,
            )
        if (
            revalidated_snapshot.protected_lane_state_fingerprint
            != snapshot.protected_lane_state_fingerprint
        ):
            return AdmissionSyncResult(
                outcome=AdmissionSyncOutcome.STALE,
                reason=AdmissionSyncReason.PROTECTED_LANE_STATE_CHANGED,
                policy_revision=current_policy.revision,
                policy_fingerprint=delivery_policy_fingerprint(current_policy),
                admission_run_id=run.id,
                ticket_key=selected.ticket_key,
            )
        if revalidated_snapshot.fingerprint != snapshot.fingerprint:
            return AdmissionSyncResult(
                outcome=AdmissionSyncOutcome.STALE,
                reason=AdmissionSyncReason.REVALIDATION_MISMATCH,
                policy_revision=current_policy.revision,
                policy_fingerprint=delivery_policy_fingerprint(current_policy),
                admission_run_id=run.id,
                ticket_key=selected.ticket_key,
            )

        hooks.after_revalidation()
        final_registry_result = protected_lane_registry_provider()
        final_protected_lane_registry = final_registry_result.registry
        if final_protected_lane_registry is None:
            return AdmissionSyncResult(
                outcome=AdmissionSyncOutcome.STALE,
                reason=AdmissionSyncReason.PROTECTED_LANE_REGISTRY_UNAVAILABLE,
                policy_revision=current_policy.revision,
                policy_fingerprint=delivery_policy_fingerprint(current_policy),
                admission_run_id=run.id,
                ticket_key=selected.ticket_key,
            )
        if (
            final_protected_lane_registry.version != protected_lane_registry.version
            or final_protected_lane_registry.fingerprint
            != protected_lane_registry.fingerprint
        ):
            return AdmissionSyncResult(
                outcome=AdmissionSyncOutcome.STALE,
                reason=AdmissionSyncReason.PROTECTED_LANE_REGISTRY_CHANGED,
                policy_revision=current_policy.revision,
                policy_fingerprint=delivery_policy_fingerprint(current_policy),
                admission_run_id=run.id,
                ticket_key=selected.ticket_key,
            )
        final_policy = policy_repo.get_active(product_id)
        if final_policy is None or (
            final_policy.id != policy.id
            or final_policy.revision != policy.revision
            or delivery_policy_fingerprint(final_policy)
            != delivery_policy_fingerprint(policy)
        ):
            return AdmissionSyncResult(
                outcome=AdmissionSyncOutcome.STALE,
                reason=AdmissionSyncReason.POLICY_CHANGED,
                policy_revision=(
                    policy.revision if final_policy is None else final_policy.revision
                ),
                policy_fingerprint=(
                    None
                    if final_policy is None
                    else delivery_policy_fingerprint(final_policy)
                ),
                admission_run_id=run.id,
                ticket_key=selected.ticket_key,
            )
        final_tickets = tuple(tickets.list())
        final_dependencies = tuple(TicketDependencyRepo(db).list())
        final_graph = build_dependency_graph(db)
        final_snapshot = build_delivery_snapshot(
            product_id=product_id,
            linear_project_id=project_id,
            policy=final_policy,
            status_map=status_map,
            board_pull=revalidated_pull,
            tickets=final_tickets,
            dependencies=final_dependencies,
            graph=final_graph,
            clock=lambda: now,
            protected_lane_registry=final_protected_lane_registry,
        )
        if (
            final_snapshot.protected_lane_state_fingerprint
            != snapshot.protected_lane_state_fingerprint
        ):
            return AdmissionSyncResult(
                outcome=AdmissionSyncOutcome.STALE,
                reason=AdmissionSyncReason.PROTECTED_LANE_STATE_CHANGED,
                policy_revision=final_policy.revision,
                policy_fingerprint=delivery_policy_fingerprint(final_policy),
                admission_run_id=run.id,
                ticket_key=selected.ticket_key,
            )
        if (
            delivery_store_revision(product_id, final_tickets)
            != snapshot.atlas_store_revision
            or delivery_graph_revision(final_graph) != snapshot.atlas_graph_revision
        ):
            return AdmissionSyncResult(
                outcome=AdmissionSyncOutcome.STALE,
                reason=AdmissionSyncReason.REVALIDATION_MISMATCH,
                policy_revision=final_policy.revision,
                policy_fingerprint=delivery_policy_fingerprint(final_policy),
                admission_run_id=run.id,
                ticket_key=selected.ticket_key,
            )
        if not coordination.is_owner(product_id=product_id, owner_id=owner_id):
            return AdmissionSyncResult(
                outcome=AdmissionSyncOutcome.STALE,
                reason=AdmissionSyncReason.LEASE_LOST,
                policy_revision=final_policy.revision,
                policy_fingerprint=delivery_policy_fingerprint(final_policy),
                admission_run_id=run.id,
                ticket_key=selected.ticket_key,
            )

        try:
            coordination.begin_write(
                product_id=product_id,
                owner_id=owner_id,
                admission_run_id=run.id,
                ticket_id=selected.ticket_id,
                ticket_key=selected.ticket_key,
                issue_id=selected.external_linear_id,
                source_state_id=initial_issue.state_id,
                target_state_id=target_state_id,
                policy_revision=final_policy.revision,
                created_at=now,
            )
        except AdmissionLeaseLostError:
            return AdmissionSyncResult(
                outcome=AdmissionSyncOutcome.STALE,
                reason=AdmissionSyncReason.LEASE_LOST,
                policy_revision=final_policy.revision,
                policy_fingerprint=delivery_policy_fingerprint(final_policy),
                admission_run_id=run.id,
                ticket_key=selected.ticket_key,
            )
        except AdmissionWriteFenceError:
            return AdmissionSyncResult(
                outcome=AdmissionSyncOutcome.STALE,
                reason=AdmissionSyncReason.LEASE_LOST,
                policy_revision=final_policy.revision,
                policy_fingerprint=delivery_policy_fingerprint(final_policy),
                admission_run_id=run.id,
                ticket_key=selected.ticket_key,
            )
        except CIHandoffFencePresentError:
            return AdmissionSyncResult(
                outcome=AdmissionSyncOutcome.HELD,
                reason=AdmissionSyncReason.CI_HANDOFF_FENCE_PRESENT,
                policy_revision=final_policy.revision,
                policy_fingerprint=delivery_policy_fingerprint(final_policy),
                admission_run_id=run.id,
                ticket_key=selected.ticket_key,
            )

        def write_and_validate() -> LinearIssue:
            written = client.set_state(
                selected.external_linear_id or "", target_state_id
            )
            if (
                written.id != selected.external_linear_id
                or written.state_id != target_state_id
            ):
                raise RuntimeError("admission provider response identity mismatch")
            return written

        try:
            coordination.execute_owned_admission_call_if_no_ci_fence(
                product_id=product_id,
                owner_id=owner_id,
                admission_run_id=run.id,
                observed_at=now,
                call=write_and_validate,
            )
        except AdmissionLeaseLostError:
            return AdmissionSyncResult(
                outcome=AdmissionSyncOutcome.STALE,
                reason=AdmissionSyncReason.LEASE_LOST,
                policy_revision=final_policy.revision,
                policy_fingerprint=delivery_policy_fingerprint(final_policy),
                admission_run_id=run.id,
                ticket_key=selected.ticket_key,
            )
        except AdmissionWriteFenceError:
            return AdmissionSyncResult(
                outcome=AdmissionSyncOutcome.STALE,
                reason=AdmissionSyncReason.LEASE_LOST,
                policy_revision=final_policy.revision,
                policy_fingerprint=delivery_policy_fingerprint(final_policy),
                admission_run_id=run.id,
                ticket_key=selected.ticket_key,
            )
        except CIHandoffFencePresentError:
            return AdmissionSyncResult(
                outcome=AdmissionSyncOutcome.HELD,
                reason=AdmissionSyncReason.CI_HANDOFF_FENCE_PRESENT,
                policy_revision=final_policy.revision,
                policy_fingerprint=delivery_policy_fingerprint(final_policy),
                admission_run_id=run.id,
                ticket_key=selected.ticket_key,
            )
        except AdmissionProviderCallIndeterminateError:
            return AdmissionSyncResult(
                outcome=AdmissionSyncOutcome.INDETERMINATE,
                reason=AdmissionSyncReason.WRITE_INDETERMINATE,
                policy_revision=final_policy.revision,
                policy_fingerprint=delivery_policy_fingerprint(final_policy),
                admission_run_id=run.id,
                ticket_key=selected.ticket_key,
            )
        except Exception:
            # Once the mutation call has begun, even a response-decoding or
            # adapter exception cannot prove that Linear did not apply it.
            # Preserve the fence and require the next complete board pull to
            # reconcile the exact issue rather than attempting another write.
            coordination.mark_indeterminate(
                product_id=product_id, admission_run_id=run.id, observed_at=now
            )
            return AdmissionSyncResult(
                outcome=AdmissionSyncOutcome.INDETERMINATE,
                reason=AdmissionSyncReason.WRITE_INDETERMINATE,
                policy_revision=final_policy.revision,
                policy_fingerprint=delivery_policy_fingerprint(final_policy),
                admission_run_id=run.id,
                ticket_key=selected.ticket_key,
            )
        coordination.clear_fence(product_id=product_id, admission_run_id=run.id)
        return AdmissionSyncResult(
            outcome=AdmissionSyncOutcome.ADMITTED,
            reason=AdmissionSyncReason.WRITE_CONFIRMED,
            policy_revision=final_policy.revision,
            policy_fingerprint=delivery_policy_fingerprint(final_policy),
            admission_run_id=run.id,
            ticket_key=selected.ticket_key,
        )
    finally:
        coordination.release(product_id=product_id, owner_id=owner_id)
