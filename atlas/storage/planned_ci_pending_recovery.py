"""Atomic storage seam for evidence-backed ``planned -> ci_pending`` recovery."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID, uuid5

import sqlalchemy as sa

from atlas.core.models import (
    AdmissionRun,
    PlannedCIPendingRecovery,
    PmSyncReceipt,
    Ticket,
    TicketStatus,
    TicketStatusTransition,
)
from atlas.core.models.planned_ci_pending_recovery import (
    PLANNED_CI_PENDING_RECOVERY_CREATED_BY,
    admission_run_correlates,
    admission_run_proves_selection,
    planned_transition_history_is_coherent,
    pm_receipt_correlates,
    pm_receipt_proves_admission,
)
from atlas.storage.db import Database
from atlas.storage.repositories import _reject_naive, _status_transition_row
from atlas.storage.tables import (
    AdmissionRunRow,
    AdmissionWriteFenceRow,
    CIHandoffWriteFenceRow,
    PlannedCIPendingRecoveryRow,
    PmSyncReceiptRow,
    TicketRow,
    TicketStatusTransitionRow,
)


class PlannedCIPendingRecoveryStorageCode(StrEnum):
    """Bounded reason why the atomic local recovery refused to commit."""

    EXISTING_RECOVERY_CONFLICT = "existing_recovery_conflict"
    TICKET_MOVED = "ticket_moved"
    ADMISSION_EVIDENCE_MOVED = "admission_evidence_moved"
    PM_RECEIPT_MOVED = "pm_receipt_moved"
    TRANSITION_HISTORY_MOVED = "transition_history_moved"
    WRITE_FENCE_APPEARED = "write_fence_appeared"
    COMPARE_AND_SET_FAILED = "compare_and_set_failed"


class PlannedCIPendingRecoveryStorageError(RuntimeError):
    def __init__(self, code: PlannedCIPendingRecoveryStorageCode) -> None:
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True)
class PlannedCIPendingRecoveryApplyRecord:
    recovery: PlannedCIPendingRecovery
    changed: bool


def _recovery_model(
    row: PlannedCIPendingRecoveryRow,
) -> PlannedCIPendingRecovery:
    return PlannedCIPendingRecovery.model_validate(row, from_attributes=True)


def _ticket_model(row: TicketRow) -> Ticket:
    return Ticket.model_validate(row, from_attributes=True)


def _transition_models(
    rows: list[TicketStatusTransitionRow],
) -> list[TicketStatusTransition]:
    return [
        TicketStatusTransition.model_validate(row, from_attributes=True) for row in rows
    ]


class PlannedCIPendingRecoveryRepo:
    """Read and atomically append governed recovery decisions."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def get_for_ticket(self, ticket_id: UUID) -> PlannedCIPendingRecovery | None:
        with self._db.session() as session:
            row = session.scalars(
                sa.select(PlannedCIPendingRecoveryRow).where(
                    PlannedCIPendingRecoveryRow.ticket_id == ticket_id
                )
            ).one_or_none()
            return None if row is None else _recovery_model(row)

    def list(self) -> list[PlannedCIPendingRecovery]:
        with self._db.session() as session:
            rows = session.scalars(
                sa.select(PlannedCIPendingRecoveryRow).order_by(
                    PlannedCIPendingRecoveryRow.observed_at,
                    PlannedCIPendingRecoveryRow.id,
                )
            )
            return [_recovery_model(row) for row in rows]

    def apply(
        self, recovery: PlannedCIPendingRecovery
    ) -> PlannedCIPendingRecoveryApplyRecord:
        """Revalidate durable proof, then append state and evidence atomically."""

        recovery = PlannedCIPendingRecovery.model_validate(
            recovery.model_dump(mode="python")
        )
        _reject_naive(recovery)
        transition_id = uuid5(recovery.id, "ticket-status-transition")
        with self._db.session() as session, session.begin():
            existing_row = session.scalars(
                sa.select(PlannedCIPendingRecoveryRow).where(
                    PlannedCIPendingRecoveryRow.ticket_id == recovery.ticket_id
                )
            ).one_or_none()
            if existing_row is not None:
                existing = _recovery_model(existing_row)
                existing_ticket_row = session.get(TicketRow, recovery.ticket_id)
                transition = session.get(TicketStatusTransitionRow, transition_id)
                recovery_transition_count = session.scalar(
                    sa.select(sa.func.count())
                    .select_from(TicketStatusTransitionRow)
                    .where(
                        TicketStatusTransitionRow.ticket_id == existing.ticket_id,
                        TicketStatusTransitionRow.from_status
                        == TicketStatus.PLANNED.value,
                        TicketStatusTransitionRow.to_status
                        == TicketStatus.CI_PENDING.value,
                        TicketStatusTransitionRow.created_by_id
                        == PLANNED_CI_PENDING_RECOVERY_CREATED_BY,
                    )
                )
                if (
                    existing.id == recovery.id
                    and existing_ticket_row is not None
                    and existing_ticket_row.status == TicketStatus.CI_PENDING.value
                    and existing_ticket_row.external_linear_id
                    == existing.linear_issue_id
                    and existing_ticket_row.status_entered_at == existing.observed_at
                    and existing_ticket_row.last_observed_linear_state_id
                    == existing.observed_linear_state_id
                    and transition is not None
                    and transition.ticket_id == existing.ticket_id
                    and transition.from_status == TicketStatus.PLANNED.value
                    and transition.to_status == TicketStatus.CI_PENDING.value
                    and transition.occurred_at == existing.observed_at
                    and transition.created_by_id
                    == PLANNED_CI_PENDING_RECOVERY_CREATED_BY
                    and recovery_transition_count == 1
                ):
                    return PlannedCIPendingRecoveryApplyRecord(existing, False)
                raise PlannedCIPendingRecoveryStorageError(
                    PlannedCIPendingRecoveryStorageCode.EXISTING_RECOVERY_CONFLICT
                )

            ticket_row = session.get(TicketRow, recovery.ticket_id)
            external_join_count = session.scalar(
                sa.select(sa.func.count())
                .select_from(TicketRow)
                .where(TicketRow.external_linear_id == recovery.linear_issue_id)
            )
            if (
                ticket_row is None
                or ticket_row.key != recovery.ticket_key
                or ticket_row.product_id != recovery.product_id
                or ticket_row.external_linear_id != recovery.linear_issue_id
                or ticket_row.status != TicketStatus.PLANNED.value
                or external_join_count != 1
            ):
                raise PlannedCIPendingRecoveryStorageError(
                    PlannedCIPendingRecoveryStorageCode.TICKET_MOVED
                )
            ticket_model = _ticket_model(ticket_row)

            runs = [
                AdmissionRun.model_validate(row, from_attributes=True)
                for row in session.scalars(sa.select(AdmissionRunRow))
            ]
            correlated_runs = [
                run for run in runs if admission_run_correlates(run, ticket_model)
            ]
            if (
                len(correlated_runs) != 1
                or correlated_runs[0].id != recovery.admission_run_id
                or not admission_run_proves_selection(correlated_runs[0], ticket_model)
            ):
                raise PlannedCIPendingRecoveryStorageError(
                    PlannedCIPendingRecoveryStorageCode.ADMISSION_EVIDENCE_MOVED
                )
            run = correlated_runs[0]

            receipts = [
                PmSyncReceipt.model_validate(row, from_attributes=True)
                for row in session.scalars(sa.select(PmSyncReceiptRow))
            ]
            correlated_receipts = [
                receipt for receipt in receipts if pm_receipt_correlates(receipt, run)
            ]
            if (
                len(correlated_receipts) != 1
                or correlated_receipts[0].id != recovery.pm_sync_receipt_id
                or not pm_receipt_proves_admission(
                    correlated_receipts[0],
                    run,
                    linear_project_id=recovery.linear_project_id,
                )
                or correlated_receipts[0].finished_at > recovery.observed_at
            ):
                raise PlannedCIPendingRecoveryStorageError(
                    PlannedCIPendingRecoveryStorageCode.PM_RECEIPT_MOVED
                )

            transition_rows = list(
                session.scalars(
                    sa.select(TicketStatusTransitionRow)
                    .where(TicketStatusTransitionRow.ticket_id == recovery.ticket_id)
                    .order_by(
                        TicketStatusTransitionRow.occurred_at,
                        TicketStatusTransitionRow.id,
                    )
                )
            )
            if not planned_transition_history_is_coherent(
                ticket_model,
                _transition_models(transition_rows),
                admitted_at=run.evaluated_at,
            ):
                raise PlannedCIPendingRecoveryStorageError(
                    PlannedCIPendingRecoveryStorageCode.TRANSITION_HISTORY_MOVED
                )
            if (
                session.get(AdmissionWriteFenceRow, recovery.product_id) is not None
                or session.get(CIHandoffWriteFenceRow, recovery.product_id) is not None
            ):
                raise PlannedCIPendingRecoveryStorageError(
                    PlannedCIPendingRecoveryStorageCode.WRITE_FENCE_APPEARED
                )

            update = session.execute(
                sa.update(TicketRow)
                .where(
                    TicketRow.id == recovery.ticket_id,
                    TicketRow.key == recovery.ticket_key,
                    TicketRow.product_id == recovery.product_id,
                    TicketRow.external_linear_id == recovery.linear_issue_id,
                    TicketRow.status == TicketStatus.PLANNED.value,
                )
                .values(
                    status=TicketStatus.CI_PENDING.value,
                    status_entered_at=recovery.observed_at,
                    last_observed_linear_state_id=(recovery.observed_linear_state_id),
                )
                .execution_options(synchronize_session=False)
            )
            if getattr(update, "rowcount", 0) != 1:
                raise PlannedCIPendingRecoveryStorageError(
                    PlannedCIPendingRecoveryStorageCode.COMPARE_AND_SET_FAILED
                )

            session.add(
                _status_transition_row(
                    transition_id=transition_id,
                    ticket_id=recovery.ticket_id,
                    from_status=TicketStatus.PLANNED.value,
                    to_status=TicketStatus.CI_PENDING.value,
                    occurred_at=recovery.observed_at,
                    created_by_id=PLANNED_CI_PENDING_RECOVERY_CREATED_BY,
                )
            )
            session.add(
                PlannedCIPendingRecoveryRow(**recovery.model_dump(mode="python"))
            )
            session.flush()
            return PlannedCIPendingRecoveryApplyRecord(recovery, True)
