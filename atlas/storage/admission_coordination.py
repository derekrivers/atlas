"""Transactional storage primitives for the PM admission write protocol."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TypeVar
from uuid import UUID

import sqlalchemy as sa

from atlas.core.models import Ticket
from atlas.storage.db import Database
from atlas.storage.tables import (
    AdmissionEligibilityRow,
    AdmissionLeaseRow,
    AdmissionWriteFenceRow,
    CIHandoffWriteFenceRow,
)


class AdmissionLeaseLostError(RuntimeError):
    """The caller no longer owns the product's single admission lane."""


class AdmissionWriteFenceError(RuntimeError):
    """The durable single-write fence could not make the requested transition."""


class CIHandoffFencePresentError(RuntimeError):
    """An unresolved CI-handoff write closes the product's workflow window."""


class AdmissionProviderCallIndeterminateError(RuntimeError):
    """The admission provider call failed after its durable fence was locked."""


_T = TypeVar("_T")


@dataclass(frozen=True)
class AdmissionWriteFence:
    """Safe, bounded projection of an unresolved external state write."""

    product_id: UUID
    admission_run_id: UUID
    ticket_id: UUID
    ticket_key: str
    issue_id: str
    source_state_id: str
    target_state_id: str
    policy_revision: int
    state: str
    created_at: datetime
    updated_at: datetime


def _aware_utc(value: datetime, *, name: str) -> datetime:
    if value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _fence(row: AdmissionWriteFenceRow | None) -> AdmissionWriteFence | None:
    if row is None:
        return None
    return AdmissionWriteFence(
        product_id=row.product_id,
        admission_run_id=row.admission_run_id,
        ticket_id=row.ticket_id,
        ticket_key=row.ticket_key,
        issue_id=row.issue_id,
        source_state_id=row.source_state_id,
        target_state_id=row.target_state_id,
        policy_revision=row.policy_revision,
        state=row.state,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class AdmissionCoordinationRepo:
    """Own the lease, eligibility episodes and crash-safe write fence.

    These rows are operational coordination state, not evidence or admission
    decisions.  The immutable decision remains in ``admission_runs`` and the
    bounded tick outcome remains in ``PmSyncReceipt`` counters.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    def try_acquire(
        self,
        *,
        product_id: UUID,
        owner_id: UUID,
        acquired_at: datetime,
        ttl: timedelta,
    ) -> bool:
        """Atomically replace an expired lease or refuse a live owner."""

        acquired = _aware_utc(acquired_at, name="admission lease acquired_at")
        if ttl <= timedelta(0):
            raise ValueError("admission lease ttl must be positive")
        try:
            with self._db.session() as session, session.begin():
                session.execute(
                    sa.delete(AdmissionLeaseRow).where(
                        AdmissionLeaseRow.product_id == product_id,
                        AdmissionLeaseRow.expires_at <= acquired,
                    )
                )
                session.add(
                    AdmissionLeaseRow(
                        product_id=product_id,
                        owner_id=owner_id,
                        acquired_at=acquired,
                        expires_at=acquired + ttl,
                    )
                )
        except sa.exc.IntegrityError:
            return False
        return True

    def is_owner(self, *, product_id: UUID, owner_id: UUID) -> bool:
        """Return whether the exact owner still holds the admission lane."""

        with self._db.session() as session:
            return (
                session.scalar(
                    sa.select(AdmissionLeaseRow.owner_id).where(
                        AdmissionLeaseRow.product_id == product_id,
                        AdmissionLeaseRow.owner_id == owner_id,
                    )
                )
                == owner_id
            )

    def release(self, *, product_id: UUID, owner_id: UUID) -> None:
        """Release only the caller's lease; never delete a replacement owner."""

        with self._db.session() as session, session.begin():
            session.execute(
                sa.delete(AdmissionLeaseRow).where(
                    AdmissionLeaseRow.product_id == product_id,
                    AdmissionLeaseRow.owner_id == owner_id,
                )
            )

    def reconcile_eligibility(
        self,
        *,
        product_id: UUID,
        candidates: tuple[Ticket, ...],
        observed_at: datetime,
    ) -> dict[str, datetime]:
        """Persist exact starts for the current uninterrupted ready episodes."""

        observed = _aware_utc(
            observed_at, name="admission eligibility observation time"
        )
        candidate_by_id = {candidate.id: candidate for candidate in candidates}
        candidate_ids = set(candidate_by_id)
        with self._db.session() as session, session.begin():
            rows = list(
                session.scalars(
                    sa.select(AdmissionEligibilityRow).where(
                        AdmissionEligibilityRow.product_id == product_id
                    )
                )
            )
            for row in rows:
                if row.ticket_id not in candidate_ids:
                    session.delete(row)
            existing = {row.ticket_id: row for row in rows}
            for candidate in candidates:
                if candidate.id not in existing:
                    session.add(
                        AdmissionEligibilityRow(
                            ticket_id=candidate.id,
                            product_id=product_id,
                            continuously_eligible_since=observed,
                        )
                    )

        with self._db.session() as session:
            current = list(
                session.scalars(
                    sa.select(AdmissionEligibilityRow).where(
                        AdmissionEligibilityRow.product_id == product_id,
                        AdmissionEligibilityRow.ticket_id.in_(candidate_ids),
                    )
                )
            )
        return {
            candidate_by_id[row.ticket_id].key: row.continuously_eligible_since
            for row in current
        }

    def get_fence(self, product_id: UUID) -> AdmissionWriteFence | None:
        """Return the unresolved write for one product, if present."""

        with self._db.session() as session:
            return _fence(session.get(AdmissionWriteFenceRow, product_id))

    def begin_write(
        self,
        *,
        product_id: UUID,
        owner_id: UUID,
        admission_run_id: UUID,
        ticket_id: UUID,
        ticket_key: str,
        issue_id: str,
        source_state_id: str,
        target_state_id: str,
        policy_revision: int,
        created_at: datetime,
    ) -> AdmissionWriteFence:
        """Commit a durable fence before the one external state mutation."""

        created = _aware_utc(created_at, name="admission write fence created_at")
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
                raise AdmissionLeaseLostError("admission lease was lost before write")
            if session.get(CIHandoffWriteFenceRow, product_id) is not None:
                raise CIHandoffFencePresentError(
                    "an unresolved CI handoff write blocks admission"
                )
            if session.get(AdmissionWriteFenceRow, product_id) is not None:
                raise AdmissionWriteFenceError(
                    "an unresolved admission write already blocks this product"
                )
            session.add(
                AdmissionWriteFenceRow(
                    product_id=product_id,
                    admission_run_id=admission_run_id,
                    ticket_id=ticket_id,
                    ticket_key=ticket_key,
                    issue_id=issue_id,
                    source_state_id=source_state_id,
                    target_state_id=target_state_id,
                    policy_revision=policy_revision,
                    state="pending",
                    created_at=created,
                    updated_at=created,
                )
            )
        fence = self.get_fence(product_id)
        if fence is None:  # pragma: no cover - committed insert invariant
            raise AdmissionWriteFenceError("admission write fence was not persisted")
        return fence

    def execute_owned_call_if_no_ci_fence(
        self,
        *,
        product_id: UUID,
        owner_id: UUID,
        observed_at: datetime,
        call: Callable[[], _T],
    ) -> _T:
        """Run one provider call while owned and atomically CI-unfenced.

        The lease-row write lock is held across the bounded call. CI-handoff
        fence creation takes the same lock, so the absence check cannot race a
        new ambiguous workflow mutation.
        """

        observed = _aware_utc(
            observed_at, name="guarded workflow call observation time"
        )
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
                    "PM write lease expired or was replaced before workflow call"
                )
            if session.get(CIHandoffWriteFenceRow, product_id) is not None:
                raise CIHandoffFencePresentError(
                    "an unresolved CI handoff write blocks the workflow call"
                )
            try:
                returned.append(call())
            except Exception as exc:
                failure = exc
        if failure is not None:
            raise failure
        if not returned:  # pragma: no cover - call either returns or raises
            raise RuntimeError("guarded workflow call had no outcome")
        return returned[0]

    def execute_owned_admission_call_if_no_ci_fence(
        self,
        *,
        product_id: UUID,
        owner_id: UUID,
        admission_run_id: UUID,
        observed_at: datetime,
        call: Callable[[], _T],
    ) -> _T:
        """Run the exact fenced admission call and retain ambiguity atomically."""

        observed = _aware_utc(
            observed_at, name="guarded admission call observation time"
        )
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
                    "PM write lease expired or was replaced before admission call"
                )
            if session.get(CIHandoffWriteFenceRow, product_id) is not None:
                raise CIHandoffFencePresentError(
                    "an unresolved CI handoff write blocks the admission call"
                )
            fence = session.get(AdmissionWriteFenceRow, product_id)
            if fence is None or fence.admission_run_id != admission_run_id:
                raise AdmissionWriteFenceError(
                    "the admission write fence changed before the owned call"
                )
            try:
                returned.append(call())
            except Exception as exc:
                fence.state = "indeterminate"
                fence.updated_at = observed
                failure = exc
        if failure is not None:
            raise AdmissionProviderCallIndeterminateError(
                "the fenced admission provider call had an indeterminate outcome"
            ) from failure
        if not returned:  # pragma: no cover - call either returns or raises
            raise AdmissionWriteFenceError("owned admission call had no outcome")
        return returned[0]

    def mark_indeterminate(
        self, *, product_id: UUID, admission_run_id: UUID, observed_at: datetime
    ) -> None:
        """Retain the fence and name the transport-ambiguous state."""

        observed = _aware_utc(
            observed_at, name="indeterminate admission observation time"
        )
        with self._db.session() as session, session.begin():
            row = session.get(AdmissionWriteFenceRow, product_id)
            if row is None or row.admission_run_id != admission_run_id:
                raise AdmissionWriteFenceError(
                    "the admission write fence disappeared before indeterminate mark"
                )
            row.state = "indeterminate"
            row.updated_at = observed

    def clear_fence(self, *, product_id: UUID, admission_run_id: UUID) -> None:
        """Clear one exact fence after confirmed write or fresh reconciliation."""

        with self._db.session() as session, session.begin():
            row = session.get(AdmissionWriteFenceRow, product_id)
            if row is None or row.admission_run_id != admission_run_id:
                raise AdmissionWriteFenceError(
                    "the admission write fence disappeared before confirmation"
                )
            session.delete(row)
