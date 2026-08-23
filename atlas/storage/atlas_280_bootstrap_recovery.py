"""Atomic storage seam for the one-time ATLAS-280 mirror repair."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import uuid5

import sqlalchemy as sa

from atlas.core.models import Atlas280BootstrapRecoveryReceipt
from atlas.core.models.admission_run import AdmissionDecisionType, AdmissionRun
from atlas.core.models.atlas_280_bootstrap_recovery import (
    ATLAS_280_ADMISSION_POLICY_REVISION,
    ATLAS_280_ADMISSION_RUN_ID,
    ATLAS_280_DEBT_ITEM_ID,
    ATLAS_280_LINEAR_ID,
    ATLAS_280_PM_RECEIPT_ID,
    ATLAS_280_TICKET_ID,
    ATLAS_281_LINEAR_ID,
    ATLAS_281_TICKET_ID,
)
from atlas.core.models.debt_item import AnomalyType
from atlas.core.models.pm_sync_receipt import (
    SUCCESSFUL_PM_SYNC_RESULTS,
    PmSyncReceipt,
)
from atlas.core.models.ticket import TicketStatus
from atlas.storage.db import Database
from atlas.storage.repositories import _reject_naive, _status_transition_row
from atlas.storage.tables import (
    AdmissionRunRow,
    AdmissionWriteFenceRow,
    Atlas280BootstrapRecoveryReceiptRow,
    CIHandoffWriteFenceRow,
    DebtItemRow,
    DeliveryAdmissionPolicyActiveRow,
    DeliveryAdmissionPolicyRevisionRow,
    PmSyncReceiptRow,
    TicketDependencyRow,
    TicketRow,
    TicketStatusTransitionRow,
)

CREATED_BY = "bootstrap:atlas-280-mirror-recovery"
EXPECTED_DEPENDENCIES = frozenset({"ATLAS-249", "ATLAS-255", "ATLAS-256"})


class Atlas280BootstrapStorageCode(StrEnum):
    """Bounded reason for an atomic local-state refusal."""

    EXISTING_RECEIPT_CONFLICT = "existing_recovery_receipt_conflict"
    BLOCKER_MOVED = "blocker_local_state_moved"
    REPAIR_TICKET_MOVED = "repair_ticket_local_state_moved"
    POLICY_MOVED = "policy_moved"
    ADMISSION_EVIDENCE_MOVED = "admission_evidence_moved"
    PM_RECEIPT_MOVED = "pm_receipt_moved"
    DEBT_ITEM_MOVED = "historical_debt_item_moved"
    DEPENDENCIES_MOVED = "repair_dependencies_moved"
    TRANSITION_HISTORY_MOVED = "transition_history_moved"
    WRITE_FENCE_APPEARED = "write_fence_appeared"
    COMPARE_AND_SET_FAILED = "local_compare_and_set_failed"


class Atlas280BootstrapStorageError(RuntimeError):
    def __init__(self, code: Atlas280BootstrapStorageCode) -> None:
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True)
class Atlas280BootstrapApplyRecord:
    receipt: Atlas280BootstrapRecoveryReceipt
    changed: bool


def _receipt_model(
    row: Atlas280BootstrapRecoveryReceiptRow,
) -> Atlas280BootstrapRecoveryReceipt:
    return Atlas280BootstrapRecoveryReceipt.model_validate(row, from_attributes=True)


def _same_receipt(
    left: Atlas280BootstrapRecoveryReceipt,
    right: Atlas280BootstrapRecoveryReceipt,
) -> bool:
    return left.model_dump(mode="json") == right.model_dump(mode="json")


def _valid_admission(run: AdmissionRun) -> bool:
    admitted = [
        decision
        for decision in run.decisions
        if decision.decision is AdmissionDecisionType.ADMIT
    ]
    return (
        run.id == ATLAS_280_ADMISSION_RUN_ID
        and run.policy_revision == ATLAS_280_ADMISSION_POLICY_REVISION
        and run.selected_ticket_id == ATLAS_280_TICKET_ID
        and run.selected_ticket_key == "ATLAS-280"
        and len(admitted) == 1
        and admitted[0].ticket_id == ATLAS_280_TICKET_ID
        and admitted[0].ticket_key == "ATLAS-280"
        and admitted[0].external_linear_id == ATLAS_280_LINEAR_ID
        and not admitted[0].reasons
    )


def _corresponding_pm_receipt(receipt: PmSyncReceipt, run: AdmissionRun) -> bool:
    return (
        receipt.product_id == run.product_id
        and receipt.started_at == run.evaluated_at
        and receipt.result in SUCCESSFUL_PM_SYNC_RESULTS
        and receipt.counters.get("admitted", 0) == 1
        and receipt.counters.get("promoted", 0) == 1
        and receipt.counters.get("stale", 0) == 0
        and receipt.counters.get("indeterminate", 0) == 0
    )


def _valid_pm_receipt(receipt: PmSyncReceipt, run: AdmissionRun) -> bool:
    return receipt.id == ATLAS_280_PM_RECEIPT_ID and _corresponding_pm_receipt(
        receipt, run
    )


class Atlas280BootstrapRecoveryRepo:
    """Read and atomically apply the permanently fixed recovery receipt."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def get(self) -> Atlas280BootstrapRecoveryReceipt | None:
        with self._db.session() as session:
            row = session.scalars(
                sa.select(Atlas280BootstrapRecoveryReceiptRow).where(
                    Atlas280BootstrapRecoveryReceiptRow.blocker_ticket_id
                    == ATLAS_280_TICKET_ID
                )
            ).one_or_none()
            return None if row is None else _receipt_model(row)

    def list(self) -> list[Atlas280BootstrapRecoveryReceipt]:
        with self._db.session() as session:
            rows = session.scalars(
                sa.select(Atlas280BootstrapRecoveryReceiptRow).order_by(
                    Atlas280BootstrapRecoveryReceiptRow.created_at,
                    Atlas280BootstrapRecoveryReceiptRow.id,
                )
            )
            return [_receipt_model(row) for row in rows]

    def apply(
        self, receipt: Atlas280BootstrapRecoveryReceipt
    ) -> Atlas280BootstrapApplyRecord:
        """CAS the local edge and append its receipt in one transaction."""

        receipt = Atlas280BootstrapRecoveryReceipt.model_validate(
            receipt.model_dump(mode="python")
        )
        _reject_naive(receipt)
        with self._db.session() as session, session.begin():
            existing_row = session.scalars(
                sa.select(Atlas280BootstrapRecoveryReceiptRow).where(
                    Atlas280BootstrapRecoveryReceiptRow.blocker_ticket_id
                    == ATLAS_280_TICKET_ID
                )
            ).one_or_none()
            if existing_row is not None:
                existing = _receipt_model(existing_row)
                if _same_receipt(existing, receipt):
                    return Atlas280BootstrapApplyRecord(existing, False)
                raise Atlas280BootstrapStorageError(
                    Atlas280BootstrapStorageCode.EXISTING_RECEIPT_CONFLICT
                )

            blocker = session.get(TicketRow, ATLAS_280_TICKET_ID)
            if (
                blocker is None
                or blocker.key != "ATLAS-280"
                or blocker.external_linear_id != ATLAS_280_LINEAR_ID
                or blocker.product_id != receipt.product_id
                or blocker.status != TicketStatus.PLANNED.value
            ):
                raise Atlas280BootstrapStorageError(
                    Atlas280BootstrapStorageCode.BLOCKER_MOVED
                )
            repair = session.get(TicketRow, ATLAS_281_TICKET_ID)
            if (
                repair is None
                or repair.key != "ATLAS-281"
                or repair.external_linear_id != ATLAS_281_LINEAR_ID
                or repair.product_id != receipt.product_id
                or repair.status != TicketStatus.PLANNED.value
            ):
                raise Atlas280BootstrapStorageError(
                    Atlas280BootstrapStorageCode.REPAIR_TICKET_MOVED
                )

            active = session.get(DeliveryAdmissionPolicyActiveRow, receipt.product_id)
            policy = (
                None
                if active is None
                else session.scalars(
                    sa.select(DeliveryAdmissionPolicyRevisionRow).where(
                        DeliveryAdmissionPolicyRevisionRow.product_id
                        == receipt.product_id,
                        DeliveryAdmissionPolicyRevisionRow.revision == active.revision,
                    )
                ).one_or_none()
            )
            if (
                policy is None
                or policy.id != receipt.policy_id
                or policy.revision != receipt.policy_revision
                or policy.mode != "paused"
            ):
                raise Atlas280BootstrapStorageError(
                    Atlas280BootstrapStorageCode.POLICY_MOVED
                )

            run_row = session.get(AdmissionRunRow, ATLAS_280_ADMISSION_RUN_ID)
            run = (
                None
                if run_row is None
                else AdmissionRun.model_validate(run_row, from_attributes=True)
            )
            competing_runs = session.scalar(
                sa.select(sa.func.count())
                .select_from(AdmissionRunRow)
                .where(
                    AdmissionRunRow.selected_ticket_id == ATLAS_280_TICKET_ID,
                    AdmissionRunRow.selected_ticket_key == "ATLAS-280",
                )
            )
            if run is None or not _valid_admission(run) or competing_runs != 1:
                raise Atlas280BootstrapStorageError(
                    Atlas280BootstrapStorageCode.ADMISSION_EVIDENCE_MOVED
                )

            pm_row = session.get(PmSyncReceiptRow, ATLAS_280_PM_RECEIPT_ID)
            pm_receipt = (
                None
                if pm_row is None
                else PmSyncReceipt.model_validate(pm_row, from_attributes=True)
            )
            matching_receipts = []
            for row in session.scalars(
                sa.select(PmSyncReceiptRow).where(
                    PmSyncReceiptRow.product_id == receipt.product_id,
                    PmSyncReceiptRow.started_at == run.evaluated_at,
                )
            ):
                candidate = PmSyncReceipt.model_validate(row, from_attributes=True)
                if _corresponding_pm_receipt(candidate, run):
                    matching_receipts.append(candidate)
            if (
                pm_receipt is None
                or not _valid_pm_receipt(pm_receipt, run)
                or len(matching_receipts) != 1
            ):
                raise Atlas280BootstrapStorageError(
                    Atlas280BootstrapStorageCode.PM_RECEIPT_MOVED
                )

            debt = session.get(DebtItemRow, ATLAS_280_DEBT_ITEM_ID)
            if (
                debt is None
                or debt.ticket_id != ATLAS_280_TICKET_ID
                or debt.product_id != receipt.product_id
                or debt.anomaly_type != AnomalyType.OUT_OF_OWNERSHIP_TRANSITION.value
            ):
                raise Atlas280BootstrapStorageError(
                    Atlas280BootstrapStorageCode.DEBT_ITEM_MOVED
                )

            dependency_rows = list(
                session.scalars(
                    sa.select(TicketDependencyRow).where(
                        TicketDependencyRow.source_ticket_id == ATLAS_281_TICKET_ID,
                        TicketDependencyRow.target_entity_type == "ticket",
                        TicketDependencyRow.dependency_type == "depends_on",
                    )
                )
            )
            dependency_targets = list(
                session.scalars(
                    sa.select(TicketRow).where(
                        TicketRow.id.in_(
                            [row.target_entity_id for row in dependency_rows]
                        )
                    )
                )
            )
            if (
                {row.key for row in dependency_targets} != EXPECTED_DEPENDENCIES
                or any(
                    row.status != TicketStatus.DONE.value for row in dependency_targets
                )
                or len(dependency_rows) != len(EXPECTED_DEPENDENCIES)
            ):
                raise Atlas280BootstrapStorageError(
                    Atlas280BootstrapStorageCode.DEPENDENCIES_MOVED
                )

            transition_count = session.scalar(
                sa.select(sa.func.count())
                .select_from(TicketStatusTransitionRow)
                .where(TicketStatusTransitionRow.ticket_id == ATLAS_280_TICKET_ID)
            )
            if transition_count != 0:
                raise Atlas280BootstrapStorageError(
                    Atlas280BootstrapStorageCode.TRANSITION_HISTORY_MOVED
                )
            if (
                session.get(AdmissionWriteFenceRow, receipt.product_id) is not None
                or session.get(CIHandoffWriteFenceRow, receipt.product_id) is not None
            ):
                raise Atlas280BootstrapStorageError(
                    Atlas280BootstrapStorageCode.WRITE_FENCE_APPEARED
                )

            update = session.execute(
                sa.update(TicketRow)
                .where(
                    TicketRow.id == ATLAS_280_TICKET_ID,
                    TicketRow.key == "ATLAS-280",
                    TicketRow.product_id == receipt.product_id,
                    TicketRow.external_linear_id == ATLAS_280_LINEAR_ID,
                    TicketRow.status == TicketStatus.PLANNED.value,
                )
                .values(
                    status=TicketStatus.CI_PENDING.value,
                    status_entered_at=receipt.created_at,
                )
                .execution_options(synchronize_session=False)
            )
            if getattr(update, "rowcount", 0) != 1:
                raise Atlas280BootstrapStorageError(
                    Atlas280BootstrapStorageCode.COMPARE_AND_SET_FAILED
                )

            session.add(
                _status_transition_row(
                    transition_id=uuid5(receipt.id, "ticket-status-transition"),
                    ticket_id=ATLAS_280_TICKET_ID,
                    from_status=TicketStatus.PLANNED.value,
                    to_status=TicketStatus.CI_PENDING.value,
                    occurred_at=receipt.created_at,
                    created_by_id=CREATED_BY,
                )
            )
            session.add(
                Atlas280BootstrapRecoveryReceiptRow(**receipt.model_dump(mode="python"))
            )
            session.flush()
            return Atlas280BootstrapApplyRecord(receipt, True)
