"""Durable write-fence primitives for the CI-pending Linear transition."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TypeVar
from uuid import UUID

import sqlalchemy as sa

from atlas.core.models.ticket import TicketStatus
from atlas.storage.admission_coordination import AdmissionLeaseLostError
from atlas.storage.db import Database
from atlas.storage.repositories import _apply_linear_status_in_session
from atlas.storage.tables import (
    AdmissionLeaseRow,
    AdmissionWriteFenceRow,
    CIHandoffWriteFenceRow,
    RetrospectiveCompletionWriteFenceRow,
    TicketRow,
)


class CIHandoffWriteFenceError(RuntimeError):
    """A CI handoff fence disappeared, collided or carried an unsafe target."""


class AdmissionFencePresentError(CIHandoffWriteFenceError):
    """A prior ambiguous admission write owns the product recovery lane."""


class RetrospectiveFencePresentError(CIHandoffWriteFenceError):
    """A prior retrospective write owns the product recovery lane."""


_T = TypeVar("_T")


@dataclass(frozen=True)
class CIHandoffWriteFence:
    """Safe projection of one unresolved CI-pending state mutation."""

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


def _model(row: CIHandoffWriteFenceRow | None) -> CIHandoffWriteFence | None:
    if row is None:
        return None
    return CIHandoffWriteFence(
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


class CIHandoffCoordinationRepo:
    """Own the crash-safe fence while the shared PM write lease is held."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def get_fence(self, product_id: UUID) -> CIHandoffWriteFence | None:
        with self._db.session() as session:
            return _model(session.get(CIHandoffWriteFenceRow, product_id))

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
        target_status: TicketStatus,
        created_at: datetime,
    ) -> CIHandoffWriteFence:
        """Commit a fence only for an owned CI-pending exit."""

        if target_status not in {
            TicketStatus.REVIEW_REQUIRED,
            TicketStatus.CHANGES_REQUESTED,
        }:
            raise CIHandoffWriteFenceError("CI handoff target is not permitted")
        created = _aware(created_at, name="CI handoff fence created_at")
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
                raise AdmissionLeaseLostError("PM write lease was lost before CI write")
            if session.get(AdmissionWriteFenceRow, product_id) is not None:
                raise AdmissionFencePresentError(
                    "an unresolved admission write blocks CI handoff"
                )
            if (
                session.get(RetrospectiveCompletionWriteFenceRow, product_id)
                is not None
            ):
                raise RetrospectiveFencePresentError(
                    "an unresolved retrospective write blocks CI handoff"
                )
            if session.get(CIHandoffWriteFenceRow, product_id) is not None:
                raise CIHandoffWriteFenceError(
                    "an unresolved CI handoff write already blocks this product"
                )
            session.add(
                CIHandoffWriteFenceRow(
                    product_id=product_id,
                    reconciliation_id=reconciliation_id,
                    ticket_id=ticket_id,
                    ticket_key=ticket_key,
                    issue_id=issue_id,
                    source_state_id=source_state_id,
                    target_state_id=target_state_id,
                    target_status=target_status.value,
                    state="pending",
                    created_at=created,
                    updated_at=created,
                )
            )
        fence = self.get_fence(product_id)
        if fence is None:  # pragma: no cover - committed insert invariant
            raise CIHandoffWriteFenceError("CI handoff fence was not persisted")
        return fence

    def mark_indeterminate(
        self, *, product_id: UUID, reconciliation_id: UUID, observed_at: datetime
    ) -> None:
        observed = _aware(observed_at, name="CI handoff indeterminate time")
        with self._db.session() as session, session.begin():
            row = session.get(CIHandoffWriteFenceRow, product_id)
            if row is None or row.reconciliation_id != reconciliation_id:
                raise CIHandoffWriteFenceError(
                    "CI handoff fence disappeared before indeterminate mark"
                )
            row.state = "indeterminate"
            row.updated_at = observed

    def execute_owned_call(
        self,
        *,
        product_id: UUID,
        owner_id: UUID,
        reconciliation_id: UUID,
        observed_at: datetime,
        call: Callable[[], _T],
    ) -> _T:
        """Hold the lease and fence locks across one bounded provider call."""

        observed = _aware(observed_at, name="CI handoff call observation time")
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
                    "PM write lease expired or was replaced before CI write"
                )
            fence_lock = session.execute(
                sa.update(CIHandoffWriteFenceRow)
                .where(
                    CIHandoffWriteFenceRow.product_id == product_id,
                    CIHandoffWriteFenceRow.reconciliation_id == reconciliation_id,
                )
                .values(state=CIHandoffWriteFenceRow.state)
            )
            if getattr(fence_lock, "rowcount", 0) != 1:
                raise CIHandoffWriteFenceError(
                    "CI handoff fence disappeared before the owned call"
                )
            try:
                returned.append(call())
            except Exception as exc:
                row = session.get(CIHandoffWriteFenceRow, product_id)
                if row is None or row.reconciliation_id != reconciliation_id:
                    raise CIHandoffWriteFenceError(
                        "CI handoff fence disappeared during the owned call"
                    ) from exc
                row.state = "indeterminate"
                row.updated_at = observed
                failure = exc
        if failure is not None:
            raise failure
        if not returned:  # pragma: no cover - call either returns or raises
            raise CIHandoffWriteFenceError("owned CI handoff call had no outcome")
        return returned[0]

    def clear_owned_fence(
        self,
        *,
        product_id: UUID,
        owner_id: UUID,
        reconciliation_id: UUID,
        observed_at: datetime,
    ) -> None:
        """Clear recovery state only while the exact lease is still live."""

        observed = _aware(observed_at, name="CI handoff recovery observation time")
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
                    "PM write lease expired or was replaced during fence recovery"
                )
            result = session.execute(
                sa.delete(CIHandoffWriteFenceRow).where(
                    CIHandoffWriteFenceRow.product_id == product_id,
                    CIHandoffWriteFenceRow.reconciliation_id == reconciliation_id,
                )
            )
            if getattr(result, "rowcount", 0) != 1:
                raise CIHandoffWriteFenceError("CI handoff fence could not be cleared")

    def finalize_owned_target(
        self,
        *,
        product_id: UUID,
        owner_id: UUID,
        reconciliation_id: UUID,
        ticket_id: UUID,
        ticket_key: str,
        target_status: TicketStatus,
        observed_at: datetime,
        status_observed_at: datetime,
        created_by_id: str,
    ) -> None:
        """Atomically apply the confirmed target and retire the exact owned fence."""

        observed = _aware(observed_at, name="CI handoff finalization time")
        status_observed = _aware(
            status_observed_at, name="CI handoff status observation time"
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
                    "PM write lease expired or was replaced before CI finalization"
                )
            fence = session.get(CIHandoffWriteFenceRow, product_id)
            if (
                fence is None
                or fence.reconciliation_id != reconciliation_id
                or fence.ticket_id != ticket_id
                or fence.ticket_key != ticket_key
                or fence.target_status != target_status.value
            ):
                raise CIHandoffWriteFenceError(
                    "CI handoff fence changed before owned finalization"
                )
            ticket_lock = session.execute(
                sa.update(TicketRow)
                .where(
                    TicketRow.id == ticket_id,
                    TicketRow.product_id == product_id,
                    TicketRow.key == ticket_key,
                    TicketRow.status.in_(
                        [TicketStatus.CI_PENDING.value, target_status.value]
                    ),
                )
                .values(status=TicketRow.status)
            )
            if getattr(ticket_lock, "rowcount", 0) != 1:
                raise CIHandoffWriteFenceError(
                    "CI handoff target finalization found a divergent local status"
                )
            _apply_linear_status_in_session(
                session,
                key=ticket_key,
                status=target_status,
                now=status_observed,
                created_by_id=created_by_id,
                expected_ticket_id=ticket_id,
                expected_product_id=product_id,
            )
            session.delete(fence)
