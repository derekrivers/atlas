"""Evidence-backed predicate for local ``planned -> ci_pending`` recovery."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid5

import sqlalchemy as sa

from atlas.core.models import PlannedCIPendingRecovery, Ticket, TicketStatus
from atlas.core.models.planned_ci_pending_recovery import (
    admission_run_correlates,
    admission_run_proves_selection,
    planned_transition_history_is_coherent,
    pm_receipt_correlates,
    pm_receipt_proves_admission,
)
from atlas.linear.ownership import LinearStatusMap, status_from_issue
from atlas.pm.ci_handoff_adapter import resolve_issue_bound_publication
from atlas.pm.delivery_snapshot import LinearBoardPull, linear_board_fingerprint
from atlas.storage import (
    AdmissionCoordinationRepo,
    AdmissionRunRepo,
    CIHandoffCoordinationRepo,
    Database,
    PlannedCIPendingRecoveryRepo,
    PmSyncReceiptRepo,
    TicketRepo,
    TicketStatusTransitionRepo,
)
from atlas.storage.planned_ci_pending_recovery import (
    PlannedCIPendingRecoveryStorageError,
)

RECOVERY_NAMESPACE = UUID("d44c8b3c-aa4e-4e20-9899-4e38c8f1fc24")
_PUBLICATION_PART_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


class PlannedCIPendingRecoveryReason(StrEnum):
    """Bounded reason emitted by the dedicated fail-closed predicate."""

    RECOVERED = "recovered"
    BOARD_INCOMPLETE = "board_incomplete_or_duplicate"
    BOARD_IDENTITY = "board_ticket_identity_mismatch"
    BOARD_STATE = "board_state_mismatch"
    ADMISSION_EVIDENCE = "admission_evidence_missing_ambiguous_or_mismatched"
    PM_RECEIPT = "pm_receipt_missing_ambiguous_or_mismatched"
    PUBLICATION = "publication_missing_ambiguous_or_mismatched"
    TRANSITION_HISTORY = "conflicting_local_transition_history"
    WRITE_FENCE = "active_write_fence"
    STORAGE_REFUSED = "atomic_storage_revalidation_refused"


@dataclass(frozen=True)
class PlannedCIPendingRecoveryEvaluation:
    """The predicate's bounded outcome and optional immutable proof."""

    reason: PlannedCIPendingRecoveryReason
    recovery: PlannedCIPendingRecovery | None = None

    @property
    def eligible(self) -> bool:
        return self.recovery is not None


@dataclass(frozen=True)
class PlannedCIPendingRecoveryResult:
    """Outcome after the atomic repository seam was attempted."""

    reason: PlannedCIPendingRecoveryReason
    recovered: bool = False
    changed: bool = False
    recovery: PlannedCIPendingRecovery | None = None


def _failed(
    reason: PlannedCIPendingRecoveryReason,
) -> PlannedCIPendingRecoveryEvaluation:
    return PlannedCIPendingRecoveryEvaluation(reason=reason)


def _board_is_complete(board: LinearBoardPull) -> bool:
    if not board.complete or board.pagination_gaps or not board.issues:
        return False
    issue_ids = [issue.id for issue in board.issues]
    if any(
        not isinstance(issue_id, str) or not issue_id.strip() for issue_id in issue_ids
    ):
        return False
    if len(issue_ids) != len(set(issue_ids)):
        return False
    identifiers = [
        issue.identifier for issue in board.issues if issue.identifier is not None
    ]
    return len(identifiers) == len(set(identifiers))


def _recovery_id(payload: dict[str, object]) -> UUID:
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
    return uuid5(RECOVERY_NAMESPACE, digest)


def _publication_is_bounded(publication: object) -> bool:
    attachment_id = getattr(publication, "attachment_id", None)
    owner = getattr(publication, "repository_owner", None)
    name = getattr(publication, "repository_name", None)
    pr_number = getattr(publication, "pr_number", None)
    return bool(
        isinstance(attachment_id, str)
        and 1 <= len(attachment_id) <= 128
        and isinstance(owner, str)
        and 1 <= len(owner) <= 128
        and _PUBLICATION_PART_RE.fullmatch(owner)
        and isinstance(name, str)
        and 1 <= len(name) <= 128
        and _PUBLICATION_PART_RE.fullmatch(name)
        and not isinstance(pr_number, bool)
        and isinstance(pr_number, int)
        and 1 <= pr_number <= 2147483647
    )


def evaluate_planned_ci_pending_recovery(
    *,
    db: Database,
    ticket: Ticket,
    status_map: LinearStatusMap,
    board: LinearBoardPull,
    project_id: str,
    now: datetime,
) -> PlannedCIPendingRecoveryEvaluation:
    """Accept only one exact governed admission-to-publication chain."""

    if now.utcoffset() is None:
        raise ValueError("recovery observation time must be timezone-aware")
    if not _board_is_complete(board):
        return _failed(PlannedCIPendingRecoveryReason.BOARD_INCOMPLETE)
    if ticket.status is not TicketStatus.PLANNED or ticket.external_linear_id is None:
        return _failed(PlannedCIPendingRecoveryReason.BOARD_IDENTITY)
    correlated_tickets = [
        stored
        for stored in TicketRepo(db).list()
        if stored.id == ticket.id
        or stored.key == ticket.key
        or stored.external_linear_id == ticket.external_linear_id
    ]
    if (
        len(correlated_tickets) != 1
        or correlated_tickets[0].id != ticket.id
        or correlated_tickets[0].key != ticket.key
        or correlated_tickets[0].product_id != ticket.product_id
        or correlated_tickets[0].external_linear_id != ticket.external_linear_id
    ):
        return _failed(PlannedCIPendingRecoveryReason.BOARD_IDENTITY)
    issues = [issue for issue in board.issues if issue.id == ticket.external_linear_id]
    if len(issues) != 1:
        return _failed(PlannedCIPendingRecoveryReason.BOARD_IDENTITY)
    issue = issues[0]
    if (
        issue.state_id is None
        or not 1 <= len(issue.state_id) <= 128
        or status_from_issue(issue, status_map) is not TicketStatus.CI_PENDING
    ):
        return _failed(PlannedCIPendingRecoveryReason.BOARD_STATE)

    if (
        AdmissionCoordinationRepo(db).get_fence(ticket.product_id) is not None
        or CIHandoffCoordinationRepo(db).get_fence(ticket.product_id) is not None
    ):
        return _failed(PlannedCIPendingRecoveryReason.WRITE_FENCE)

    correlated_runs = [
        run
        for run in AdmissionRunRepo(db).list()
        if admission_run_correlates(run, ticket)
    ]
    if len(correlated_runs) != 1 or not admission_run_proves_selection(
        correlated_runs[0], ticket
    ):
        return _failed(PlannedCIPendingRecoveryReason.ADMISSION_EVIDENCE)
    run = correlated_runs[0]

    correlated_receipts = [
        receipt
        for receipt in PmSyncReceiptRepo(db).list()
        if pm_receipt_correlates(receipt, run)
    ]
    if (
        len(correlated_receipts) != 1
        or not pm_receipt_proves_admission(
            correlated_receipts[0], run, linear_project_id=project_id
        )
        or correlated_receipts[0].finished_at > now
    ):
        return _failed(PlannedCIPendingRecoveryReason.PM_RECEIPT)
    pm_receipt = correlated_receipts[0]

    transitions = TicketStatusTransitionRepo(db).list_for_ticket(ticket.id)
    if not planned_transition_history_is_coherent(
        ticket, transitions, admitted_at=run.evaluated_at
    ):
        return _failed(PlannedCIPendingRecoveryReason.TRANSITION_HISTORY)

    publication, _publication_reason = resolve_issue_bound_publication(
        ticket, list(board.issues)
    )
    if (
        not issue.github_publications_complete
        or len(issue.github_publications) != 1
        or publication is None
        or not _publication_is_bounded(publication)
    ):
        return _failed(PlannedCIPendingRecoveryReason.PUBLICATION)

    board_fingerprint = linear_board_fingerprint(board.issues)
    identity_payload: dict[str, object] = {
        "schema_version": "planned-ci-pending-recovery-v1",
        "product_id": str(ticket.product_id),
        "ticket_id": str(ticket.id),
        "ticket_key": ticket.key,
        "linear_issue_id": ticket.external_linear_id,
        "linear_project_id": project_id,
        "observed_linear_state_id": issue.state_id,
        "admission_run_id": str(run.id),
        "pm_sync_receipt_id": str(pm_receipt.id),
        "publication_attachment_id": publication.attachment_id,
        "publication_repository_owner": publication.repository_owner.casefold(),
        "publication_repository_name": publication.repository_name.casefold(),
        "publication_pr_number": publication.pr_number,
        "board_fingerprint": board_fingerprint,
        "board_issue_count": len(board.issues),
    }
    recovery = PlannedCIPendingRecovery(
        id=_recovery_id(identity_payload),
        product_id=ticket.product_id,
        ticket_id=ticket.id,
        ticket_key=ticket.key,
        linear_issue_id=ticket.external_linear_id,
        linear_project_id=project_id,
        observed_linear_state_id=issue.state_id,
        admission_run_id=run.id,
        pm_sync_receipt_id=pm_receipt.id,
        publication_attachment_id=publication.attachment_id,
        publication_repository_owner=publication.repository_owner.casefold(),
        publication_repository_name=publication.repository_name.casefold(),
        publication_pr_number=publication.pr_number,
        board_fingerprint=board_fingerprint,
        board_issue_count=len(board.issues),
        observed_at=now,
    )
    return PlannedCIPendingRecoveryEvaluation(
        reason=PlannedCIPendingRecoveryReason.RECOVERED,
        recovery=recovery,
    )


def recover_planned_ci_pending(
    *,
    db: Database,
    ticket: Ticket,
    status_map: LinearStatusMap,
    board: LinearBoardPull,
    project_id: str,
    now: datetime,
) -> PlannedCIPendingRecoveryResult:
    """Evaluate then commit the local transition and proof in one transaction."""

    evaluation = evaluate_planned_ci_pending_recovery(
        db=db,
        ticket=ticket,
        status_map=status_map,
        board=board,
        project_id=project_id,
        now=now,
    )
    if evaluation.recovery is None:
        return PlannedCIPendingRecoveryResult(reason=evaluation.reason)
    repository = PlannedCIPendingRecoveryRepo(db)
    try:
        applied = repository.apply(evaluation.recovery)
    except sa.exc.IntegrityError:
        existing = repository.get_for_ticket(ticket.id)
        if existing is None or existing.id != evaluation.recovery.id:
            return PlannedCIPendingRecoveryResult(
                reason=PlannedCIPendingRecoveryReason.STORAGE_REFUSED
            )
        try:
            applied = repository.apply(evaluation.recovery)
        except (PlannedCIPendingRecoveryStorageError, sa.exc.IntegrityError):
            return PlannedCIPendingRecoveryResult(
                reason=PlannedCIPendingRecoveryReason.STORAGE_REFUSED
            )
    except PlannedCIPendingRecoveryStorageError:
        return PlannedCIPendingRecoveryResult(
            reason=PlannedCIPendingRecoveryReason.STORAGE_REFUSED
        )
    return PlannedCIPendingRecoveryResult(
        reason=PlannedCIPendingRecoveryReason.RECOVERED,
        recovered=True,
        changed=applied.changed,
        recovery=applied.recovery,
    )
