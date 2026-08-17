"""One transactionally coherent read model for delivery-control status.

The HTTP resource must not assemble independently committed repository reads:
doing so could pair a new policy with an old board or a new CI decision with
the evidence set that preceded it.  This storage operation therefore freezes
all delivery-control inputs in one read-only database transaction and returns
only bounded domain models and selected evidence identity fields.  It never
loads retained provider payloads.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.orm import Session

from atlas.core.enums import EvidenceStatus
from atlas.core.models import (
    SUCCESSFUL_PM_SYNC_RESULTS,
    AcceptanceSession,
    AdmissionRun,
    CIHandoffReconciliation,
    DeliveryAdmissionPolicyRevision,
    PmSyncReceipt,
    Product,
    Ticket,
)
from atlas.storage.admission_coordination import AdmissionWriteFence, _fence
from atlas.storage.ci_handoff_coordination import (
    CIHandoffWriteFence,
)
from atlas.storage.ci_handoff_coordination import (
    _model as _ci_handoff_fence,
)
from atlas.storage.db import Database
from atlas.storage.tables import (
    AcceptanceSessionRow,
    AdmissionRunRow,
    AdmissionWriteFenceRow,
    CIHandoffReconciliationRow,
    CIHandoffWriteFenceRow,
    DeliveryAdmissionPolicyActiveRow,
    DeliveryAdmissionPolicyRevisionRow,
    EvidenceRow,
    PmSyncReceiptRow,
    ProductRow,
    TicketRow,
)


@dataclass(frozen=True)
class DeliveryControlEvidenceIdentity:
    """Selected immutable evidence identity; raw provider data is excluded."""

    id: UUID
    commit_sha: str | None
    external_run_id: str | None
    job_name: str | None
    payload_hash: str | None
    status: EvidenceStatus
    source_event_at: datetime | None
    created_at: datetime


@dataclass(frozen=True)
class DeliveryControlSourceSnapshot:
    """All source values observed under one database snapshot."""

    products: tuple[Product, ...]
    policy: DeliveryAdmissionPolicyRevision | None = None
    tickets: tuple[Ticket, ...] = ()
    latest_sync: PmSyncReceipt | None = None
    latest_successful_sync: PmSyncReceipt | None = None
    latest_admission: AdmissionRun | None = None
    admission_fence: AdmissionWriteFence | None = None
    ci_handoff_fence: CIHandoffWriteFence | None = None
    ci_reconciliations: tuple[CIHandoffReconciliation, ...] = ()
    evidence_identities: tuple[DeliveryControlEvidenceIdentity, ...] = ()
    acceptance_sessions: tuple[AcceptanceSession, ...] = ()


def _policy(
    session: Session, product_id: UUID
) -> DeliveryAdmissionPolicyRevision | None:
    row = session.scalars(
        sa.select(DeliveryAdmissionPolicyRevisionRow)
        .join(
            DeliveryAdmissionPolicyActiveRow,
            (
                DeliveryAdmissionPolicyActiveRow.product_id
                == DeliveryAdmissionPolicyRevisionRow.product_id
            )
            & (
                DeliveryAdmissionPolicyActiveRow.revision
                == DeliveryAdmissionPolicyRevisionRow.revision
            ),
        )
        .where(DeliveryAdmissionPolicyActiveRow.product_id == product_id)
    ).one_or_none()
    return (
        None
        if row is None
        else DeliveryAdmissionPolicyRevision.model_validate(row, from_attributes=True)
    )


def _sync_receipt(
    session: Session,
    product_id: UUID,
    *,
    successful_only: bool,
) -> PmSyncReceipt | None:
    statement = sa.select(PmSyncReceiptRow).where(
        PmSyncReceiptRow.product_id == product_id
    )
    if successful_only:
        statement = statement.where(
            PmSyncReceiptRow.result.in_(
                tuple(result.value for result in SUCCESSFUL_PM_SYNC_RESULTS)
            )
        )
    row = session.scalars(
        statement.order_by(
            PmSyncReceiptRow.finished_at.desc(), PmSyncReceiptRow.id.desc()
        ).limit(1)
    ).one_or_none()
    return (
        None if row is None else PmSyncReceipt.model_validate(row, from_attributes=True)
    )


def _latest_admission(session: Session, product_id: UUID) -> AdmissionRun | None:
    row = session.scalars(
        sa.select(AdmissionRunRow)
        .where(AdmissionRunRow.product_id == product_id)
        .order_by(AdmissionRunRow.evaluated_at.desc(), AdmissionRunRow.id.desc())
        .limit(1)
    ).one_or_none()
    return (
        None if row is None else AdmissionRun.model_validate(row, from_attributes=True)
    )


def _latest_ci_reconciliations(
    session: Session, product_id: UUID
) -> tuple[CIHandoffReconciliation, ...]:
    ranked = (
        sa.select(
            CIHandoffReconciliationRow.id.label("id"),
            sa.func.row_number()
            .over(
                partition_by=CIHandoffReconciliationRow.ticket_id,
                order_by=(
                    CIHandoffReconciliationRow.observed_at.desc(),
                    CIHandoffReconciliationRow.id.desc(),
                ),
            )
            .label("position"),
        )
        .join(TicketRow, TicketRow.id == CIHandoffReconciliationRow.ticket_id)
        .where(
            TicketRow.product_id == product_id,
            TicketRow.status == "ci_pending",
            TicketRow.status_entered_at.is_not(None),
            CIHandoffReconciliationRow.observed_at >= TicketRow.status_entered_at,
        )
        .subquery()
    )
    rows = session.scalars(
        sa.select(CIHandoffReconciliationRow)
        .join(ranked, ranked.c.id == CIHandoffReconciliationRow.id)
        .where(ranked.c.position == 1)
        .order_by(
            CIHandoffReconciliationRow.ticket_key,
            CIHandoffReconciliationRow.id,
        )
    )
    return tuple(
        CIHandoffReconciliation.model_validate(row, from_attributes=True)
        for row in rows
    )


def _evidence_identities(
    session: Session, reconciliations: tuple[CIHandoffReconciliation, ...]
) -> tuple[DeliveryControlEvidenceIdentity, ...]:
    evidence_ids = tuple(
        sorted(
            {
                evidence_id
                for reconciliation in reconciliations
                for result in reconciliation.check_results
                for evidence_id in result.evidence_ids
            },
            key=str,
        )
    )
    if not evidence_ids:
        return ()
    rows = session.execute(
        sa.select(
            EvidenceRow.id,
            EvidenceRow.commit_sha,
            EvidenceRow.external_run_id,
            EvidenceRow.job_name,
            EvidenceRow.payload_hash,
            EvidenceRow.status,
            EvidenceRow.source_event_at,
            EvidenceRow.created_at,
        )
        .where(EvidenceRow.id.in_(evidence_ids))
        .order_by(EvidenceRow.id)
    )
    return tuple(
        DeliveryControlEvidenceIdentity(
            id=row.id,
            commit_sha=row.commit_sha,
            external_run_id=row.external_run_id,
            job_name=row.job_name,
            payload_hash=row.payload_hash,
            status=EvidenceStatus(row.status),
            source_event_at=row.source_event_at,
            created_at=row.created_at,
        )
        for row in rows
    )


def _latest_acceptance_sessions(
    session: Session, reconciliations: tuple[CIHandoffReconciliation, ...]
) -> tuple[AcceptanceSession, ...]:
    identities = {
        (
            reconciliation.repository_owner,
            reconciliation.repository_name,
            reconciliation.pr_number,
        )
        for reconciliation in reconciliations
    }
    if not identities:
        return ()
    ranked = (
        sa.select(
            AcceptanceSessionRow.id.label("id"),
            sa.func.row_number()
            .over(
                partition_by=(
                    AcceptanceSessionRow.repository_owner,
                    AcceptanceSessionRow.repository_name,
                    AcceptanceSessionRow.pr_number,
                ),
                order_by=(
                    AcceptanceSessionRow.updated_at.desc(),
                    AcceptanceSessionRow.id.desc(),
                ),
            )
            .label("position"),
        )
        .where(
            sa.tuple_(
                AcceptanceSessionRow.repository_owner,
                AcceptanceSessionRow.repository_name,
                AcceptanceSessionRow.pr_number,
            ).in_(tuple(sorted(identities)))
        )
        .subquery()
    )
    rows = session.scalars(
        sa.select(AcceptanceSessionRow)
        .join(ranked, ranked.c.id == AcceptanceSessionRow.id)
        .where(ranked.c.position == 1)
        .order_by(
            AcceptanceSessionRow.repository_owner,
            AcceptanceSessionRow.repository_name,
            AcceptanceSessionRow.pr_number,
        )
    )
    return tuple(
        AcceptanceSession.model_validate(row, from_attributes=True) for row in rows
    )


class DeliveryControlSnapshotRepo:
    """Freeze all delivery-control inputs without performing a write."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def read(self) -> DeliveryControlSourceSnapshot:
        """Return one repeatable-read source snapshot.

        SQLite holds a transaction snapshot after its first read. PostgreSQL
        is explicitly upgraded from READ COMMITTED to REPEATABLE READ so every
        selected identity belongs to the same MVCC observation.
        """

        with self._database.engine.connect() as connection:
            if connection.dialect.name == "postgresql":
                connection = connection.execution_options(
                    isolation_level="REPEATABLE READ"
                )
            with Session(bind=connection) as session, session.begin():
                products = tuple(
                    Product.model_validate(row, from_attributes=True)
                    for row in session.scalars(
                        sa.select(ProductRow).order_by(ProductRow.key, ProductRow.id)
                    )
                )
                if len(products) != 1:
                    return DeliveryControlSourceSnapshot(products=products)

                product_id = products[0].id
                tickets = tuple(
                    Ticket.model_validate(row, from_attributes=True)
                    for row in session.scalars(
                        sa.select(TicketRow)
                        .where(TicketRow.product_id == product_id)
                        .order_by(TicketRow.key, TicketRow.id)
                    )
                )
                reconciliations = _latest_ci_reconciliations(session, product_id)
                return DeliveryControlSourceSnapshot(
                    products=products,
                    policy=_policy(session, product_id),
                    tickets=tickets,
                    latest_sync=_sync_receipt(
                        session, product_id, successful_only=False
                    ),
                    latest_successful_sync=_sync_receipt(
                        session, product_id, successful_only=True
                    ),
                    latest_admission=_latest_admission(session, product_id),
                    admission_fence=_fence(
                        session.get(AdmissionWriteFenceRow, product_id)
                    ),
                    ci_handoff_fence=_ci_handoff_fence(
                        session.get(CIHandoffWriteFenceRow, product_id)
                    ),
                    ci_reconciliations=reconciliations,
                    evidence_identities=_evidence_identities(session, reconciliations),
                    acceptance_sessions=_latest_acceptance_sessions(
                        session, reconciliations
                    ),
                )


__all__ = [
    "DeliveryControlEvidenceIdentity",
    "DeliveryControlSnapshotRepo",
    "DeliveryControlSourceSnapshot",
]
