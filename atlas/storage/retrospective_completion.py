"""Durable coordination for the retrospective ci_pending -> done owner."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TypeVar
from uuid import UUID

import sqlalchemy as sa

from atlas.core.models import RetrospectiveCompletionReconciliation, TicketStatus
from atlas.storage.admission_coordination import AdmissionLeaseLostError
from atlas.storage.db import Database
from atlas.storage.repositories import _apply_linear_status_in_session
from atlas.storage.tables import (
    AdmissionLeaseRow,
    AdmissionWriteFenceRow,
    CIHandoffWriteFenceRow,
    RetrospectiveCompletionReconciliationRow,
    RetrospectiveCompletionWriteFenceRow,
    TicketRow,
)


class RetrospectiveCompletionWriteFenceError(RuntimeError):
    """The distinct retrospective fence collided, moved, or became unsafe."""


class CompetingWorkflowFencePresentError(RetrospectiveCompletionWriteFenceError):
    """Another state-edge owner already owns this product's recovery lane."""


class RetrospectiveProviderCallIndeterminateError(
    RetrospectiveCompletionWriteFenceError
):
    """The fenced provider call failed after its outcome became unknowable."""


_T = TypeVar("_T")


@dataclass(frozen=True)
class RetrospectiveCompletionWriteFence:
    """Safe projection of one unresolved historical completion write."""

    product_id: UUID
    reconciliation_id: UUID
    ticket_id: UUID
    ticket_key: str
    issue_id: str
    source_state_id: str
    target_state_id: str
    target_status: TicketStatus
    state: str
    created_at: datetime
    updated_at: datetime


def _aware(value: datetime, *, name: str) -> datetime:
    if value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _model(
    row: RetrospectiveCompletionWriteFenceRow | None,
) -> RetrospectiveCompletionWriteFence | None:
    if row is None:
        return None
    return RetrospectiveCompletionWriteFence(
        product_id=row.product_id,
        reconciliation_id=row.reconciliation_id,
        ticket_id=row.ticket_id,
        ticket_key=row.ticket_key,
        issue_id=row.issue_id,
        source_state_id=row.source_state_id,
        target_state_id=row.target_state_id,
        target_status=TicketStatus(row.target_status),
        state=row.state,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class RetrospectiveCompletionCoordinationRepo:
    """Own the distinct fence while the shared product workflow lease is held."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def get_fence(self, product_id: UUID) -> RetrospectiveCompletionWriteFence | None:
        with self._db.session() as session:
            return _model(session.get(RetrospectiveCompletionWriteFenceRow, product_id))

    def begin_write(
        self,
        *,
        product_id: UUID,
        owner_id: UUID,
        reconciliation_id: UUID,
        ticket_id: UUID,
        ticket_key: str,
        issue_id: str,
        source_state_id: str,
        target_state_id: str,
        created_at: datetime,
    ) -> RetrospectiveCompletionWriteFence:
        created = _aware(created_at, name="retrospective fence created_at")
        with self._db.session() as session, session.begin():
            lease_lock = session.execute(
                sa.update(AdmissionLeaseRow)
                .where(
                    AdmissionLeaseRow.product_id == product_id,
                    AdmissionLeaseRow.owner_id == owner_id,
                    AdmissionLeaseRow.expires_at > created,
                )
                .values(owner_id=AdmissionLeaseRow.owner_id)
            )
            if getattr(lease_lock, "rowcount", 0) != 1:
                raise AdmissionLeaseLostError(
                    "PM write lease was lost before retrospective preparation"
                )
            if (
                session.get(AdmissionWriteFenceRow, product_id) is not None
                or session.get(CIHandoffWriteFenceRow, product_id) is not None
            ):
                raise CompetingWorkflowFencePresentError(
                    "a competing workflow fence blocks retrospective completion"
                )
            if (
                session.get(RetrospectiveCompletionWriteFenceRow, product_id)
                is not None
            ):
                raise RetrospectiveCompletionWriteFenceError(
                    "a retrospective completion fence already blocks this product"
                )
            session.add(
                RetrospectiveCompletionWriteFenceRow(
                    product_id=product_id,
                    reconciliation_id=reconciliation_id,
                    ticket_id=ticket_id,
                    ticket_key=ticket_key,
                    issue_id=issue_id,
                    source_state_id=source_state_id,
                    target_state_id=target_state_id,
                    target_status=TicketStatus.DONE.value,
                    state="pending",
                    created_at=created,
                    updated_at=created,
                )
            )
        fence = self.get_fence(product_id)
        if fence is None:  # pragma: no cover - committed insert invariant
            raise RetrospectiveCompletionWriteFenceError(
                "retrospective completion fence was not persisted"
            )
        return fence

    def record_and_begin_write(
        self,
        *,
        owner_id: UUID,
        reconciliation: RetrospectiveCompletionReconciliation,
        source_state_id: str,
        target_state_id: str,
    ) -> RetrospectiveCompletionWriteFence:
        """Atomically append exact proof and prepare its external-write fence."""

        if reconciliation.linear_issue_id is None:
            raise RetrospectiveCompletionWriteFenceError(
                "retrospective prepared proof requires a Linear issue"
            )
        created = _aware(
            reconciliation.observed_at,
            name="retrospective reconciliation observed_at",
        )
        payload = reconciliation.model_dump()
        json_payload = reconciliation.model_dump(mode="json")
        payload["verification_check_ids"] = json_payload["verification_check_ids"]
        payload["deciding_evidence_ids"] = json_payload["deciding_evidence_ids"]
        with self._db.session() as session, session.begin():
            lease_lock = session.execute(
                sa.update(AdmissionLeaseRow)
                .where(
                    AdmissionLeaseRow.product_id == reconciliation.product_id,
                    AdmissionLeaseRow.owner_id == owner_id,
                    AdmissionLeaseRow.expires_at > created,
                )
                .values(owner_id=AdmissionLeaseRow.owner_id)
            )
            if getattr(lease_lock, "rowcount", 0) != 1:
                raise AdmissionLeaseLostError(
                    "PM write lease was lost before retrospective preparation"
                )
            if (
                session.get(AdmissionWriteFenceRow, reconciliation.product_id)
                is not None
                or session.get(CIHandoffWriteFenceRow, reconciliation.product_id)
                is not None
            ):
                raise CompetingWorkflowFencePresentError(
                    "a competing workflow fence blocks retrospective completion"
                )
            if (
                session.get(
                    RetrospectiveCompletionWriteFenceRow,
                    reconciliation.product_id,
                )
                is not None
            ):
                raise RetrospectiveCompletionWriteFenceError(
                    "a retrospective completion fence already blocks this product"
                )
            session.add(RetrospectiveCompletionReconciliationRow(**payload))
            session.add(
                RetrospectiveCompletionWriteFenceRow(
                    product_id=reconciliation.product_id,
                    reconciliation_id=reconciliation.id,
                    ticket_id=reconciliation.ticket_id,
                    ticket_key=reconciliation.ticket_key,
                    issue_id=reconciliation.linear_issue_id,
                    source_state_id=source_state_id,
                    target_state_id=target_state_id,
                    target_status=TicketStatus.DONE.value,
                    state="pending",
                    created_at=created,
                    updated_at=created,
                )
            )
        fence = self.get_fence(reconciliation.product_id)
        if fence is None:  # pragma: no cover
            raise RetrospectiveCompletionWriteFenceError(
                "retrospective completion fence was not persisted"
            )
        return fence

    def execute_owned_call(
        self,
        *,
        product_id: UUID,
        owner_id: UUID,
        reconciliation_id: UUID,
        observed_at: datetime,
        call: Callable[[], _T],
    ) -> _T:
        """Hold the shared lease and exact fence lock across one provider call."""

        observed = _aware(observed_at, name="retrospective call observed_at")
        returned: list[_T] = []
        failure: Exception | None = None
        with self._db.session() as session, session.begin():
            lease_lock = session.execute(
                sa.update(AdmissionLeaseRow)
                .where(
                    AdmissionLeaseRow.product_id == product_id,
                    AdmissionLeaseRow.owner_id == owner_id,
                    AdmissionLeaseRow.expires_at > observed,
                )
                .values(owner_id=AdmissionLeaseRow.owner_id)
            )
            if getattr(lease_lock, "rowcount", 0) != 1:
                raise AdmissionLeaseLostError(
                    "PM write lease expired before retrospective provider call"
                )
            fence = session.get(RetrospectiveCompletionWriteFenceRow, product_id)
            if fence is None or fence.reconciliation_id != reconciliation_id:
                raise RetrospectiveCompletionWriteFenceError(
                    "retrospective fence changed before provider call"
                )
            try:
                returned.append(call())
            except Exception as error:
                fence.state = "indeterminate"
                fence.updated_at = observed
                failure = error
        if failure is not None:
            raise RetrospectiveProviderCallIndeterminateError(
                "retrospective provider result is indeterminate"
            ) from failure
        if not returned:  # pragma: no cover
            raise RetrospectiveCompletionWriteFenceError(
                "retrospective provider call had no result"
            )
        return returned[0]

    def clear_owned_fence(
        self,
        *,
        product_id: UUID,
        owner_id: UUID,
        reconciliation_id: UUID,
        observed_at: datetime,
    ) -> None:
        observed = _aware(observed_at, name="retrospective fence clear time")
        with self._db.session() as session, session.begin():
            lease_lock = session.execute(
                sa.update(AdmissionLeaseRow)
                .where(
                    AdmissionLeaseRow.product_id == product_id,
                    AdmissionLeaseRow.owner_id == owner_id,
                    AdmissionLeaseRow.expires_at > observed,
                )
                .values(owner_id=AdmissionLeaseRow.owner_id)
            )
            if getattr(lease_lock, "rowcount", 0) != 1:
                raise AdmissionLeaseLostError(
                    "PM write lease expired during retrospective recovery"
                )
            result = session.execute(
                sa.delete(RetrospectiveCompletionWriteFenceRow).where(
                    RetrospectiveCompletionWriteFenceRow.product_id == product_id,
                    RetrospectiveCompletionWriteFenceRow.reconciliation_id
                    == reconciliation_id,
                )
            )
            if getattr(result, "rowcount", 0) != 1:
                raise RetrospectiveCompletionWriteFenceError(
                    "retrospective completion fence could not be cleared"
                )

    def finalize_owned_target(
        self,
        *,
        product_id: UUID,
        owner_id: UUID,
        reconciliation_id: UUID,
        ticket_id: UUID,
        ticket_key: str,
        observed_at: datetime,
        status_observed_at: datetime,
        created_by_id: str,
    ) -> None:
        """Atomically apply confirmed Done and retire the exact owned fence."""

        observed = _aware(observed_at, name="retrospective finalization time")
        status_observed = _aware(
            status_observed_at, name="retrospective status observation time"
        )
        with self._db.session() as session, session.begin():
            lease_lock = session.execute(
                sa.update(AdmissionLeaseRow)
                .where(
                    AdmissionLeaseRow.product_id == product_id,
                    AdmissionLeaseRow.owner_id == owner_id,
                    AdmissionLeaseRow.expires_at > observed,
                )
                .values(owner_id=AdmissionLeaseRow.owner_id)
            )
            if getattr(lease_lock, "rowcount", 0) != 1:
                raise AdmissionLeaseLostError(
                    "PM write lease expired before retrospective finalization"
                )
            fence = session.get(RetrospectiveCompletionWriteFenceRow, product_id)
            if (
                fence is None
                or fence.reconciliation_id != reconciliation_id
                or fence.ticket_id != ticket_id
                or fence.ticket_key != ticket_key
                or fence.target_status != TicketStatus.DONE.value
            ):
                raise RetrospectiveCompletionWriteFenceError(
                    "retrospective fence changed before owned finalization"
                )
            ticket_lock = session.execute(
                sa.update(TicketRow)
                .where(
                    TicketRow.id == ticket_id,
                    TicketRow.product_id == product_id,
                    TicketRow.key == ticket_key,
                    TicketRow.status.in_(
                        [TicketStatus.CI_PENDING.value, TicketStatus.DONE.value]
                    ),
                )
                .values(status=TicketRow.status)
            )
            if getattr(ticket_lock, "rowcount", 0) != 1:
                raise RetrospectiveCompletionWriteFenceError(
                    "retrospective target finalization found divergent local status"
                )
            _apply_linear_status_in_session(
                session,
                key=ticket_key,
                status=TicketStatus.DONE,
                now=status_observed,
                created_by_id=created_by_id,
                expected_ticket_id=ticket_id,
                expected_product_id=product_id,
            )
            session.delete(fence)

    def defer_unresolved(
        self,
        *,
        product_id: UUID,
        reconciliation_id: UUID,
        observed_at: datetime,
    ) -> None:
        """Move one unresolved fence behind older independent product work."""

        observed = _aware(observed_at, name="retrospective deferral time")
        with self._db.session() as session, session.begin():
            row = session.get(RetrospectiveCompletionWriteFenceRow, product_id)
            if row is None or row.reconciliation_id != reconciliation_id:
                raise RetrospectiveCompletionWriteFenceError(
                    "retrospective fence changed before recovery deferral"
                )
            row.state = "indeterminate"
            row.updated_at = max(observed, row.updated_at + timedelta(microseconds=1))
