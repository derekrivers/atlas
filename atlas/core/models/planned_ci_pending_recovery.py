"""Bounded proof for governed ``planned -> ci_pending`` mirror recovery."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from atlas.core.enums import ActorType
from atlas.core.models.admission_run import (
    AdmissionDecisionType,
    AdmissionRun,
)
from atlas.core.models.pm_sync_receipt import (
    PmSyncReceipt,
    PmSyncReceiptResult,
)
from atlas.core.models.ticket import Ticket, TicketStatus
from atlas.core.models.ticket_status_transition import TicketStatusTransition

PLANNED_CI_PENDING_RECOVERY_CREATED_BY: Literal[
    "pm-engine:planned-ci-pending-recovery"
] = "pm-engine:planned-ci-pending-recovery"
ADMISSION_RUN_CREATED_BY = "atlas.pm.admission"
PM_SYNC_RECEIPT_CREATED_BY = "pm-engine"

_PRE_DISPATCH_STATUSES = frozenset(
    {TicketStatus.BACKLOG, TicketStatus.PLANNED, TicketStatus.BLOCKED}
)


def _is_sha256(value: str) -> bool:
    return bool(
        len(value) == 64 and all(character in "0123456789abcdef" for character in value)
    )


class PlannedCIPendingRecovery(BaseModel):
    """Immutable evidence for one direct, local-only mirror catch-up."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["planned-ci-pending-recovery-v1"] = (
        "planned-ci-pending-recovery-v1"
    )
    id: UUID
    product_id: UUID
    ticket_id: UUID
    ticket_key: str = Field(min_length=1, max_length=128)
    linear_issue_id: str = Field(min_length=1, max_length=128)
    linear_project_id: str = Field(min_length=1, max_length=128)
    observed_linear_state_id: str = Field(min_length=1, max_length=128)
    source_local_status: Literal["planned"] = "planned"
    recovered_local_status: Literal["ci_pending"] = "ci_pending"
    admission_run_id: UUID
    pm_sync_receipt_id: UUID
    publication_attachment_id: str = Field(min_length=1, max_length=128)
    publication_repository_owner: str = Field(
        min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$"
    )
    publication_repository_name: str = Field(
        min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$"
    )
    publication_pr_number: int = Field(ge=1, le=2147483647)
    board_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    board_issue_count: int = Field(ge=1, le=2147483647)
    observed_at: datetime
    created_by_type: Literal[ActorType.SYSTEM] = ActorType.SYSTEM
    created_by_id: Literal["pm-engine:planned-ci-pending-recovery"] = (
        PLANNED_CI_PENDING_RECOVERY_CREATED_BY
    )


def admission_run_correlates(run: AdmissionRun, ticket: Ticket) -> bool:
    """Whether any bounded selection identity relates ``run`` to ``ticket``."""

    external_linear_id = ticket.external_linear_id
    return bool(
        run.selected_ticket_id == ticket.id
        or run.selected_ticket_key == ticket.key
        or any(
            decision.ticket_id == ticket.id
            or decision.ticket_key == ticket.key
            or (
                external_linear_id is not None
                and decision.external_linear_id == external_linear_id
            )
            for decision in run.decisions
        )
    )


def admission_run_proves_selection(run: AdmissionRun, ticket: Ticket) -> bool:
    """Require the exact system-authored admission selection and identities."""

    if ticket.external_linear_id is None:
        return False
    correlated_decisions = [
        decision
        for decision in run.decisions
        if decision.ticket_id == ticket.id
        or decision.ticket_key == ticket.key
        or decision.external_linear_id == ticket.external_linear_id
    ]
    return bool(
        run.product_id == ticket.product_id
        and run.selected_ticket_id == ticket.id
        and run.selected_ticket_key == ticket.key
        and run.created_by_type is ActorType.SYSTEM
        and run.created_by_id == ADMISSION_RUN_CREATED_BY
        and _is_sha256(run.policy_fingerprint)
        and _is_sha256(run.snapshot_fingerprint)
        and run.snapshot_observed_at == run.evaluated_at
        and len(correlated_decisions) == 1
        and correlated_decisions[0].ticket_id == ticket.id
        and correlated_decisions[0].ticket_key == ticket.key
        and correlated_decisions[0].external_linear_id == ticket.external_linear_id
        and correlated_decisions[0].decision is AdmissionDecisionType.ADMIT
        and not correlated_decisions[0].reasons
    )


def pm_receipt_correlates(receipt: PmSyncReceipt, run: AdmissionRun) -> bool:
    """Use the exact tick start/evaluation instant and product as the join."""

    return bool(
        receipt.product_id == run.product_id and receipt.started_at == run.evaluated_at
    )


def pm_receipt_proves_admission(
    receipt: PmSyncReceipt,
    run: AdmissionRun,
    *,
    linear_project_id: str,
) -> bool:
    """Require one successful, determinate single-admission tick receipt."""

    definition_writes = receipt.counters.get(
        "pushed_created", 0
    ) + receipt.counters.get("pushed_updated", 0)
    result_is_consistent = bool(
        (
            receipt.result is PmSyncReceiptResult.SUCCESS_DEFINITION_CHANGED
            and definition_writes > 0
        )
        or (
            receipt.result is PmSyncReceiptResult.SUCCESS_STATUS_ONLY
            and definition_writes == 0
        )
    )
    return bool(
        pm_receipt_correlates(receipt, run)
        and receipt.linear_project_id == linear_project_id
        and result_is_consistent
        and receipt.counters.get("admitted", 0) == 1
        and receipt.counters.get("promoted", 0) == 1
        and receipt.counters.get("stale", 0) == 0
        and receipt.counters.get("indeterminate", 0) == 0
        and receipt.error_summary is None
        and receipt.finished_at >= receipt.started_at
        and receipt.fetched_board_issue_count >= 1
        and _is_sha256(receipt.status_map_fingerprint)
        and _is_sha256(receipt.fetched_board_fingerprint)
        and receipt.created_by_type is ActorType.SYSTEM
        and receipt.created_by_id == PM_SYNC_RECEIPT_CREATED_BY
    )


def planned_transition_history_is_coherent(
    ticket: Ticket,
    transitions: Sequence[TicketStatusTransition],
    *,
    admitted_at: datetime,
) -> bool:
    """Allow only a contiguous pre-dispatch history ending at ``planned``."""

    if (
        ticket.status is not TicketStatus.PLANNED
        or ticket.completed_at is not None
        or ticket.review_cycle_count != 0
        or (
            ticket.status_entered_at is not None
            and ticket.status_entered_at > admitted_at
        )
    ):
        return False
    ordered = sorted(transitions, key=lambda item: (item.occurred_at, item.id))
    if len({transition.id for transition in ordered}) != len(ordered):
        return False
    previous_target: TicketStatus | None = None
    for transition in ordered:
        try:
            source = TicketStatus(transition.from_status)
            target = TicketStatus(transition.to_status)
        except ValueError:
            return False
        if (
            source not in _PRE_DISPATCH_STATUSES
            or target not in _PRE_DISPATCH_STATUSES
            or source is target
            or transition.occurred_at > admitted_at
            or transition.created_by_type is not ActorType.SYSTEM
            or (previous_target is not None and source is not previous_target)
        ):
            return False
        previous_target = target
    if not ordered:
        return False
    return bool(
        previous_target is TicketStatus.PLANNED
        and ticket.status_entered_at == ordered[-1].occurred_at
    )
