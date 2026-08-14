"""Repositories (ATLAS-18): one per aggregate, Pydantic in and out.

Enforcement per knowledge-core "Append-only and finalise-once
enforcement" and "Trust-tier enforcement":

- EvidenceRepo exposes add and query methods only — no update, no
  delete. add rejects agent-tier records whose status is not PENDING
  (via atlas.core.trust.evidence_tier, the single home of tier logic)
  with a typed error and no bypass parameter.
- PlanRunRepo exposes add, finalize, and queries. finalize rejects any
  row not in `proposed` with a typed error and writes only approved_by,
  applied_at, and failure_reason (plus the finalising status itself).

Datetime contract (knowledge-core "Storage architecture"): naive
datetimes are rejected at this boundary with NaiveDatetimeError;
storage normalises to UTC and returns UTC-aware values.
"""

from __future__ import annotations

import builtins
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Generic, TypeVar, cast
from uuid import UUID, uuid4

import sqlalchemy as sa
from pydantic import BaseModel
from sqlalchemy.orm import Session

from atlas.core.enums import ActorType, EntityStatus, EvidenceStatus
from atlas.core.models import (
    SUCCESSFUL_PM_SYNC_RESULTS,
    AdmissionRun,
    AgentRun,
    AnomalyType,
    ArchitectureDecisionRecord,
    CIHandoffReconciliation,
    ContextPack,
    DebtItem,
    DeliveryAdmissionPolicyRevision,
    Epic,
    EpicStatus,
    Evidence,
    Lesson,
    OperatorActionReceipt,
    PlanRun,
    PlanRunStatus,
    PmSyncReceipt,
    Product,
    Ticket,
    TicketDependency,
    TicketStatus,
    TicketStatusTransition,
    TickFailure,
    VerificationCheck,
)
from atlas.core.models import AcceptanceSession as _AcceptanceModel
from atlas.core.models import (
    AcceptanceSessionBlockingReason as _AcceptanceReason,
)
from atlas.core.models import AcceptanceSessionLifecycle as _AcceptanceLifecycle
from atlas.core.trust import evidence_tier
from atlas.storage.db import Database
from atlas.storage.tables import (
    AcceptanceSessionRow,
    AdmissionRunRow,
    AgentRunRow,
    ArchitectureDecisionRecordRow,
    Base,
    CIHandoffReconciliationRow,
    ContextPackRow,
    DebtItemRow,
    DeliveryAdmissionPolicyActiveRow,
    DeliveryAdmissionPolicyRevisionRow,
    EpicRow,
    EvidenceRow,
    KeyCounterRow,
    LessonRow,
    OperatorActionKeyRow,
    OperatorActionReceiptRow,
    PlanRunRow,
    PmSyncReceiptRow,
    ProductRow,
    TicketDependencyRow,
    TicketRow,
    TicketStatusTransitionRow,
    TickFailureRow,
    VerificationCheckRow,
)

M = TypeVar("M", bound=BaseModel)

_FINAL_STATUSES = frozenset(
    {PlanRunStatus.APPLIED, PlanRunStatus.REJECTED, PlanRunStatus.FAILED}
)


class NaiveDatetimeError(ValueError):
    """A datetime without a timezone reached the storage boundary."""

    def __init__(self, model_name: str, field: str) -> None:
        super().__init__(
            f"{model_name}.{field} is a naive datetime; storage accepts "
            "timezone-aware values only and normalises them to UTC"
        )


class TrustTierError(ValueError):
    """Agent-tier evidence above PENDING (ADR-0008); no bypass exists."""


class PlanRunStateError(ValueError):
    """finalize applies only to rows in `proposed`, exactly once."""


class KeyCounterError(ValueError):
    """A reservation requested a non-positive count."""


class EffortValidationError(ValueError):
    """estimated_effort must be a positive integer (>= 1) or null (ATLAS-32
    G1). Effort is operator-supplied and NEVER inferred; <= 0 is rejected
    rather than silently persisted."""


class TicketNotFoundError(ValueError):
    """set_estimated_effort named a key with no stored ticket."""


class LessonNotFoundError(ValueError):
    """A lesson lifecycle command named no stored lesson."""


class LessonStateError(ValueError):
    """A lesson lifecycle transition was requested from the wrong state."""


class LessonValidationError(ValueError):
    """Operator-supplied lesson lifecycle input failed validation."""


class AcceptanceSessionStateError(ValueError):
    """An acceptance-session create or transition violated stored state."""

    def __init__(self, reason: _AcceptanceReason) -> None:
        self.reason = reason
        super().__init__(reason.value)


@dataclass(frozen=True)
class Reservation:
    """The keys an apply reserved from one prefix's counter: the assigned
    range [first, last] and the resulting high-water mark."""

    prefix: str
    first: int
    last: int
    high_water: int


@dataclass(frozen=True)
class StaleLessonReview:
    """An ACTIVE lesson that should be surfaced for operator review."""

    lesson: Lesson
    context_pack_count: int


@dataclass(frozen=True)
class AcceptanceSessionCreateRecord:
    """Repository result distinguishing one insert from a durable replay."""

    session: _AcceptanceModel
    created: bool


_AcceptanceCreateResult = AcceptanceSessionCreateRecord


@dataclass(frozen=True)
class _OperatorActionReservation:
    """Internal storage view of one idempotency-key reservation."""

    idempotency_key_identity: str
    request_fingerprint: str
    receipt_id: UUID
    correlation_id: UUID
    action: str
    target_type: str
    target_id: str
    created_by_type: str
    created_by_id: str
    created_at: datetime


def _reject_naive(model: BaseModel) -> None:
    for name in type(model).model_fields:
        value = getattr(model, name)
        if isinstance(value, datetime) and value.utcoffset() is None:
            raise NaiveDatetimeError(type(model).__name__, name)


def compare_and_set_entity(
    session: Session,
    entity: object,
    *,
    expected_values: Mapping[str, object],
    updated_fields: Sequence[str],
) -> bool:
    """Atomically update selected mapped fields when observations still match.

    This is the storage-boundary primitive used by governed commands.  The
    caller supplies a detached mapped entity containing the proposed values,
    an exact set of observed predicates, and the only fields that may change.
    A false return means another transaction changed the observed state; no
    fallback merge or unconditional save is attempted.
    """

    state = sa.inspect(entity, raiseerr=False)
    if state is None or state.mapper is None:
        raise ValueError("compare-and-set requires a mapped entity")
    mapper = state.mapper
    if len(mapper.primary_key) != 1:
        raise ValueError("compare-and-set requires one primary-key column")
    if not expected_values:
        raise ValueError("compare-and-set requires at least one observed predicate")
    if not updated_fields:
        raise ValueError("compare-and-set requires at least one updated field")

    mapped_fields = {column.key for column in mapper.columns}
    primary_key = mapper.primary_key[0]
    primary_key_value = getattr(entity, primary_key.key)
    if any(field not in mapped_fields for field in expected_values):
        raise ValueError("compare-and-set predicate names must be mapped fields")
    if any(field not in mapped_fields for field in updated_fields):
        raise ValueError("compare-and-set update names must be mapped fields")
    if primary_key.key in updated_fields:
        raise ValueError("compare-and-set cannot update the primary key")

    statement = sa.update(type(entity)).where(primary_key == primary_key_value)
    for field, expected in expected_values.items():
        column = getattr(type(entity), field)
        statement = statement.where(
            column.is_(None) if expected is None else column == expected
        )
    statement = statement.values(
        {field: getattr(entity, field) for field in updated_fields}
    )
    result = session.execute(statement.execution_options(synchronize_session=False))
    rowcount = cast(int, getattr(result, "rowcount", 0))
    return rowcount == 1


def _status_transition_row(
    *,
    transition_id: UUID,
    ticket_id: UUID,
    from_status: str,
    to_status: str,
    occurred_at: datetime,
    created_by_id: str,
) -> TicketStatusTransitionRow:
    """The single definition of a status-transition row (ATLAS-121 D3).

    Both write paths build their row here so the two cannot drift: the inline
    writer in ``apply_linear_status`` (which appends atomically with the status
    change) and ``TicketStatusTransitionRepo.record`` (the completeness/tests
    verb). ``created_by_type`` is always ``system`` — a transition is observed
    by deterministic system logic, never an agent or human — and the caller
    supplies ``created_by_id``: storage never presumes its caller's identity,
    so the PM sync loop threads its own ``CREATED_BY`` through.
    """
    return TicketStatusTransitionRow(
        id=transition_id,
        ticket_id=ticket_id,
        from_status=from_status,
        to_status=to_status,
        occurred_at=occurred_at,
        created_by_type=ActorType.SYSTEM.value,
        created_by_id=created_by_id,
    )


def _add_operator_action_reservation(
    session: Session,
    *,
    idempotency_key_identity: str,
    request_fingerprint: str,
    receipt_id: UUID,
    correlation_id: UUID,
    action: str,
    target_type: str,
    target_id: str,
    created_by_type: str,
    created_by_id: str,
    created_at: datetime,
) -> None:
    """Insert the idempotency-key owner row inside the caller transaction."""

    session.add(
        OperatorActionKeyRow(
            idempotency_key_identity=idempotency_key_identity,
            request_fingerprint=request_fingerprint,
            receipt_id=receipt_id,
            correlation_id=correlation_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            created_by_type=created_by_type,
            created_by_id=created_by_id,
            created_at=created_at,
        )
    )


def _get_operator_action_reservation(
    session: Session, idempotency_key_identity: str
) -> _OperatorActionReservation | None:
    """Read one idempotency-key owner row inside the caller transaction."""

    row = session.get(OperatorActionKeyRow, idempotency_key_identity)
    if row is None:
        return None
    return _OperatorActionReservation(
        idempotency_key_identity=row.idempotency_key_identity,
        request_fingerprint=row.request_fingerprint,
        receipt_id=row.receipt_id,
        correlation_id=row.correlation_id,
        action=row.action,
        target_type=row.target_type,
        target_id=row.target_id,
        created_by_type=row.created_by_type,
        created_by_id=row.created_by_id,
        created_at=row.created_at,
    )


def _operator_action_receipt_row(
    receipt: OperatorActionReceipt,
) -> OperatorActionReceiptRow:
    validated = OperatorActionReceipt.model_validate(
        {
            field_name: getattr(receipt, field_name)
            for field_name in OperatorActionReceipt.model_fields
        }
    )
    payload = validated.model_dump()
    payload["result_metadata"] = validated.model_dump(mode="json")["result_metadata"]
    return OperatorActionReceiptRow(**payload)


def _add_operator_action_receipt(
    session: Session, receipt: OperatorActionReceipt
) -> None:
    """Insert a terminal receipt inside the caller transaction."""

    session.add(_operator_action_receipt_row(receipt))


def _get_operator_action_receipt_by_identity(
    session: Session, idempotency_key_identity: str
) -> OperatorActionReceipt | None:
    """Read the terminal receipt for an idempotency-key identity."""

    row = session.scalars(
        sa.select(OperatorActionReceiptRow).where(
            OperatorActionReceiptRow.idempotency_key_identity
            == idempotency_key_identity
        )
    ).first()
    return (
        None
        if row is None
        else OperatorActionReceipt.model_validate(row, from_attributes=True)
    )


class _Repo(Generic[M]):
    """Shared add/get/list; conversion lives here, at the boundary."""

    def __init__(self, db: Database, model_cls: type[M], row_cls: type[Base]) -> None:
        self._db = db
        self._model_cls = model_cls
        self._row_cls = row_cls
        # JSON columns need JSON-compatible values: a list[UUID] field
        # dumped in python mode reaches json.dumps unserialised (found
        # by the ATLAS-19 property tests).
        self._json_fields = {
            column.name
            for column in row_cls.__table__.columns
            if isinstance(column.type, sa.JSON)
        }

    def _to_model(self, row: Base) -> M:
        return self._model_cls.model_validate(row, from_attributes=True)

    def _to_row(self, model: M) -> Base:
        payload = model.model_dump()
        if self._json_fields:
            json_payload = model.model_dump(mode="json")
            for name in self._json_fields:
                payload[name] = json_payload[name]
        return self._row_cls(**payload)

    def add(self, model: M) -> M:
        _reject_naive(model)
        with self._db.session() as session, session.begin():
            session.add(self._to_row(model))
        return model

    def get(self, entity_id: UUID) -> M | None:
        with self._db.session() as session:
            row = session.get(self._row_cls, entity_id)
            return None if row is None else self._to_model(row)

    def list(self) -> list[M]:
        with self._db.session() as session:
            rows = session.scalars(
                sa.select(self._row_cls).order_by(self._row_cls.__table__.c.id)
            )
            return [self._to_model(row) for row in rows]


class _KeyedRepo(_Repo[M]):
    """Aggregates with a human-readable unique key."""

    def get_by_key(self, key: str) -> M | None:
        with self._db.session() as session:
            row = session.scalars(
                sa.select(self._row_cls).where(self._row_cls.__table__.c.key == key)
            ).first()
            return None if row is None else self._to_model(row)


class ProductRepo(_KeyedRepo[Product]):
    def __init__(self, db: Database) -> None:
        super().__init__(db, Product, ProductRow)


class ADRRepo(_Repo[ArchitectureDecisionRecord]):
    def __init__(self, db: Database) -> None:
        super().__init__(db, ArchitectureDecisionRecord, ArchitectureDecisionRecordRow)


class EpicRepo(_KeyedRepo[Epic]):
    def __init__(self, db: Database) -> None:
        super().__init__(db, Epic, EpicRow)

    def set_status(self, key: str, status: EpicStatus) -> Epic:
        """Set an epic's operational lifecycle status without changing its key."""
        with self._db.session() as session, session.begin():
            row = session.scalars(sa.select(EpicRow).where(EpicRow.key == key)).first()
            if row is None:
                raise ValueError(f"no epic with key {key!r}")
            row.status = status.value
            return self._to_model(row)


class TicketRepo(_KeyedRepo[Ticket]):
    def __init__(self, db: Database) -> None:
        super().__init__(db, Ticket, TicketRow)

    def count(self) -> int:
        """Return the number of stored tickets without loading their records."""
        with self._db.session() as session:
            statement = sa.select(sa.func.count()).select_from(TicketRow)
            return int(session.scalar(statement))

    def latest_linear_synced_at(self) -> datetime | None:
        """Return the newest Linear definition-sync cursor across tickets."""
        with self._db.session() as session:
            statement = sa.select(sa.func.max(TicketRow.linear_synced_at))
            return cast(datetime | None, session.scalar(statement))

    def reconcile_claimed_record(self, ticket: Ticket) -> Ticket:
        """Replace one already-minted ticket with an exact incident record.

        ATLAS-029M uses this only after normal apply has atomically assigned the
        claimed key and epic relationship. Identity must already match; the
        method cannot insert a key or retarget an existing key to another row.
        Repeating the same replacement is an exact no-op at the model boundary.
        """
        _reject_naive(ticket)
        with self._db.session() as session, session.begin():
            row = session.scalars(
                sa.select(TicketRow).where(TicketRow.key == ticket.key)
            ).first()
            if row is None:
                raise TicketNotFoundError(f"no ticket with key {ticket.key!r}")
            if row.id != ticket.id:
                raise ValueError(
                    f"ticket {ticket.key!r} has id {row.id}, not {ticket.id}"
                )
            session.merge(self._to_row(ticket))
        return ticket

    def list_by_status(self, status: TicketStatus) -> list[Ticket]:
        """Return tickets in ``status``, ordered by key."""
        with self._db.session() as session:
            rows = session.scalars(
                sa.select(TicketRow)
                .where(TicketRow.status == status.value)
                .order_by(TicketRow.key, TicketRow.id)
            )
            return [self._to_model(row) for row in rows]

    def set_estimated_effort(self, key: str, effort: int | None) -> Ticket:
        """Set ``estimated_effort`` on the stored ticket ``key`` (ATLAS-32).

        The SINGLE writer of ``estimated_effort`` — an operator-supplied,
        never-computed operational input (dependency-engine.md "Effort
        population"). ``effort`` must be a positive integer (>= 1) or
        ``None``; ``None`` is the legitimate "not yet estimated" state the
        critical path weights as 1, and is also how an operator clears a
        prior estimate. A value <= 0 raises :class:`EffortValidationError`
        with no persistence (G1: never silently store a non-positive effort,
        never use 0 as a stand-in for "unknown").

        Ownership stays per-field (ADR-0006/0007): ``atlas apply`` inserts
        every ticket with this field null and owns the doc-sourced definition
        fields; this writer owns only ``estimated_effort``. The two touch
        disjoint columns, so no field has two writers. ``updated_at`` is
        DELIBERATELY left untouched — effort is a Phase 4-unsynced field, and
        bumping ``updated_at`` would trigger a spurious definition re-push for
        a field that never syncs (see the design doc).
        """
        if effort is not None and effort < 1:
            raise EffortValidationError(
                "estimated_effort must be a positive integer (>= 1) or null; "
                f"got {effort!r}. Effort is operator-supplied and never "
                "inferred (ATLAS-32 G1)."
            )
        with self._db.session() as session, session.begin():
            row = session.scalars(
                sa.select(TicketRow).where(TicketRow.key == key)
            ).first()
            if row is None:
                raise TicketNotFoundError(f"no ticket with key {key!r}")
            row.estimated_effort = effort
            return self._to_model(row)

    def apply_linear_status(
        self, key: str, status: TicketStatus, *, now: datetime, created_by_id: str
    ) -> Ticket:
        """Apply a trusted Linear status observation (Linear -> Atlas).

        This is the status direction's sole local-store writer. The ordinary
        caller is the Linear pull (ATLAS-42); an owner-specific fenced writer
        may also call it only after Linear confirms the exact transition (the
        CI handoff seam). ``updated_at`` is
        DELIBERATELY left untouched: status is Linear-owned, and the sync
        cursor compares ``updated_at > linear_synced_at`` — bumping
        ``updated_at`` on an inbound status change would spuriously re-push the
        definition Atlas -> Linear (a directionality leak). Disjoint-column
        discipline, exactly like ``set_estimated_effort`` (ADR-0006/0007).

        ``status_entered_at`` (ATLAS-119) is stamped to ``now`` ONLY when the
        status actually changes — being the sole status writer, this is the one
        place the dwell clock can advance, and it must mark a *real* transition,
        not every pull. A set-to-same status leaves it untouched (the episode
        did not restart). Like ``updated_at``, the dwell clock is disjoint from
        the definition cursor; stamping it never re-pushes. ``now`` is the
        injected tick clock and must be timezone-aware.

        ``completed_at`` is stamped from the same tick clock ONLY on a real
        transition into ``done``. Repeated observations of ``done`` leave the
        first delivery timestamp intact, and ``rejected`` is a closure rather
        than a delivery completion. Like the other inbound status-coupled
        fields, writing it never bumps ``updated_at``.

        ``review_cycle_count`` (ATLAS-120) is incremented in the same real-change
        branch, but ONLY on a ``changes_requested -> pr_open`` transition (the
        round trip the review-cycling rule counts). Every other transition —
        including any other arrival into ``pr_open`` (e.g. ``in_progress ->
        pr_open``) or the reverse ``pr_open -> changes_requested`` — leaves it
        untouched, and a set-to-same never reaches this branch. Status-coupled
        and disjoint from the definition cursor, exactly like the dwell clock, so
        it too never bumps ``updated_at``.

        ``TicketStatusTransition`` (ATLAS-121) is appended in the same real-change
        branch, on the SAME ``session`` already open here, so the transition
        commits atomically with the status change: a transition exists iff the
        status actually changed, and a crash rolls back both together. It records
        ``from_status = row.status`` (the value BEFORE reassignment), ``to_status
        = status.value``, and ``occurred_at = now``, attributed to ``system`` with
        the caller-supplied ``created_by_id`` (storage never presumes the caller's
        identity). Unlike the dwell clock it is append-only history, not an
        overwritten value — every real change leaves its own row, so historical
        per-state cycle time becomes computable (the consumer is ATLAS-126). A
        set-to-same status records NO transition (it never enters this branch).
        Like the clocks above it touches no definition column and never bumps
        ``updated_at`` — no directionality leak.
        """
        if now.utcoffset() is None:
            raise NaiveDatetimeError("Ticket", "status_entered_at")
        with self._db.session() as session, session.begin():
            row = session.scalars(
                sa.select(TicketRow).where(TicketRow.key == key)
            ).first()
            if row is None:
                raise TicketNotFoundError(f"no ticket with key {key!r}")
            if row.status != status.value:
                row.status_entered_at = now
                if status == TicketStatus.DONE:
                    row.completed_at = now
                if (
                    row.status == TicketStatus.CHANGES_REQUESTED.value
                    and status == TicketStatus.PR_OPEN
                ):
                    row.review_cycle_count = row.review_cycle_count + 1
                session.add(
                    _status_transition_row(
                        transition_id=uuid4(),
                        ticket_id=row.id,
                        from_status=row.status,
                        to_status=status.value,
                        occurred_at=now,
                        created_by_id=created_by_id,
                    )
                )
            row.status = status.value
            return self._to_model(row)

    def mark_linear_state_observed(self, key: str, state_id: str | None) -> Ticket:
        """Record the Linear state id observed on this pull (ATLAS-118).

        The out-of-ownership anomaly detector's dedup signal: the next pull
        compares the freshly fetched id against ``last_observed_linear_state_id``
        and logs one ``DebtItem`` only on a *transition* into an unmapped state,
        so a persisting unmapped state writes no new row while a genuine
        re-occurrence (unmapped -> mapped -> unmapped) is a new transition.

        ``updated_at`` is DELIBERATELY left untouched: this is an inbound
        observation, not an Atlas edit, and the sync cursor compares
        ``updated_at > linear_synced_at`` — bumping ``updated_at`` here would
        spuriously re-push the definition Atlas -> Linear. Disjoint-column
        discipline, exactly like ``apply_linear_status`` (ADR-0006/0007).
        """
        with self._db.session() as session, session.begin():
            row = session.scalars(
                sa.select(TicketRow).where(TicketRow.key == key)
            ).first()
            if row is None:
                raise TicketNotFoundError(f"no ticket with key {key!r}")
            row.last_observed_linear_state_id = state_id
            return self._to_model(row)

    def mark_external_linear_id(self, key: str, external_linear_id: str) -> Ticket:
        """Record a confirmed Linear join key without stamping the sync cursor.

        A first create whose context-pack render degraded to definition-only must
        remember the issue it just created so the next retry updates that issue
        rather than creating a duplicate. This is deliberately narrower than
        :meth:`mark_definition_pushed`: it writes only ``external_linear_id`` and
        leaves ``linear_synced_at``/``updated_at`` untouched, so the definition
        cursor still retries until a full embedded push succeeds.
        """
        with self._db.session() as session, session.begin():
            row = session.scalars(
                sa.select(TicketRow).where(TicketRow.key == key)
            ).first()
            if row is None:
                raise TicketNotFoundError(f"no ticket with key {key!r}")
            if row.external_linear_id is None:
                row.external_linear_id = external_linear_id
            return self._to_model(row)

    def mark_definition_pushed(
        self,
        key: str,
        *,
        synced_at: datetime,
        external_linear_id: str | None = None,
    ) -> Ticket:
        """Stamp the sync cursor after a confirmed definition push (ATLAS-42).

        Writes ``linear_synced_at`` (``synced_at`` is the ``updated_at`` value
        that was pushed, so a later tick sees ``updated_at == linear_synced_at``
        and does not re-push) and, only on first creation, ``external_linear_id``
        (the join key — written once and never reused). Touches no definition
        column and never ``updated_at``: stamping must not itself look like a
        new Atlas edit, or the ticket would re-push every tick. Order is
        push-then-stamp (D5): the caller confirms the Linear write before this.
        """
        if synced_at.utcoffset() is None:
            raise NaiveDatetimeError("Ticket", "linear_synced_at")
        with self._db.session() as session, session.begin():
            row = session.scalars(
                sa.select(TicketRow).where(TicketRow.key == key)
            ).first()
            if row is None:
                raise TicketNotFoundError(f"no ticket with key {key!r}")
            if external_linear_id is not None and row.external_linear_id is None:
                row.external_linear_id = external_linear_id
            row.linear_synced_at = synced_at
            return self._to_model(row)

    def mark_lesson_extraction_attempted(
        self, key: str, *, attempted_at: datetime
    ) -> Ticket:
        """Stamp the learning system's extraction-attempt cursor.

        This is the single storage writer for ``lesson_extraction_attempted_at``:
        extraction paths call it after each attempt, whether that attempt
        produced a lesson or failed. It touches no definition fields and
        deliberately leaves ``updated_at`` unchanged, so a learning cursor write
        never causes a Linear definition re-push.
        """
        if attempted_at.utcoffset() is None:
            raise NaiveDatetimeError("Ticket", "lesson_extraction_attempted_at")
        with self._db.session() as session, session.begin():
            row = session.scalars(
                sa.select(TicketRow).where(TicketRow.key == key)
            ).first()
            if row is None:
                raise TicketNotFoundError(f"no ticket with key {key!r}")
            row.lesson_extraction_attempted_at = attempted_at
            return self._to_model(row)


class TicketDependencyRepo(_Repo[TicketDependency]):
    def __init__(self, db: Database) -> None:
        super().__init__(db, TicketDependency, TicketDependencyRow)


class LessonRepo(_Repo[Lesson]):
    def __init__(self, db: Database) -> None:
        super().__init__(db, Lesson, LessonRow)

    def list(self) -> builtins.list[Lesson]:
        """Return all lessons in deterministic creation order."""
        with self._db.session() as session:
            rows = session.scalars(
                sa.select(LessonRow).order_by(LessonRow.created_at, LessonRow.id)
            )
            return [self._to_model(row) for row in rows]

    def list_by_status(self, status: EntityStatus) -> builtins.list[Lesson]:
        """Return lessons in ``status``, ordered by creation time."""
        with self._db.session() as session:
            rows = session.scalars(
                sa.select(LessonRow)
                .where(LessonRow.status == status.value)
                .order_by(LessonRow.created_at, LessonRow.id)
            )
            return [self._to_model(row) for row in rows]

    def list_drafts(self) -> builtins.list[Lesson]:
        """Lessons waiting for the ADR-0009 operator promotion gate."""
        with self._db.session() as session:
            rows = session.scalars(
                sa.select(LessonRow)
                .where(LessonRow.status == EntityStatus.DRAFT.value)
                .order_by(LessonRow.created_at, LessonRow.id)
            )
            return [self._to_model(row) for row in rows]

    def archive(self, lesson_id: UUID, *, now: datetime) -> Lesson:
        """Archive an obsolete ACTIVE lesson without deleting it."""
        if now.utcoffset() is None:
            raise NaiveDatetimeError("Lesson", "updated_at")
        with self._db.session() as session, session.begin():
            row = self._get_lesson_row(session, lesson_id)
            self._require_status(row, EntityStatus.ACTIVE, action="archive")
            row.status = EntityStatus.ARCHIVED.value
            row.updated_at = now
            return self._to_model(row)

    def merge(
        self,
        draft_lesson_id: UUID,
        target_lesson_id: UUID,
        *,
        now: datetime,
    ) -> tuple[Lesson, Lesson]:
        """Merge a DRAFT lesson into an existing ACTIVE lesson.

        The DRAFT is archived and its citation tickets are appended to the
        target's ``related_ticket_ids`` without duplicates. The target's
        ``source_ticket_id`` remains the target provenance; the archived DRAFT
        row retains its own source for audit.
        """
        if now.utcoffset() is None:
            raise NaiveDatetimeError("Lesson", "updated_at")
        if draft_lesson_id == target_lesson_id:
            raise LessonValidationError("cannot merge a lesson into itself")
        with self._db.session() as session, session.begin():
            draft_row = self._get_lesson_row(session, draft_lesson_id)
            target_row = self._get_lesson_row(session, target_lesson_id)
            self._require_status(draft_row, EntityStatus.DRAFT, action="merge")
            self._require_status(target_row, EntityStatus.ACTIVE, action="merge into")

            merged_ticket_ids = builtins.list(target_row.related_ticket_ids or [])
            seen = {str(ticket_id) for ticket_id in merged_ticket_ids}
            for ticket_id in draft_row.related_ticket_ids or []:
                ticket_id_str = str(ticket_id)
                if ticket_id_str not in seen:
                    merged_ticket_ids.append(ticket_id_str)
                    seen.add(ticket_id_str)

            target_row.related_ticket_ids = merged_ticket_ids
            target_row.updated_at = now
            draft_row.status = EntityStatus.ARCHIVED.value
            draft_row.updated_at = now
            return self._to_model(draft_row), self._to_model(target_row)

    def record_ticket_citation(
        self, *, lesson_ids: builtins.list[UUID], ticket_id: UUID
    ) -> builtins.list[Lesson]:
        """Append ``ticket_id`` to the cited lessons' ``related_ticket_ids``.

        Citation feedback is system-observed usage, not an operator lifecycle
        action, so it deliberately leaves ``updated_at`` untouched. That keeps
        ``updated_at`` as the stale-review boundary for promotion/reject/archive/
        merge actions while still making successful reuse visible on the lesson.
        Missing lesson ids are ignored: a stale/tampered pack should not block
        ticket completion feedback for the lessons that still exist.
        """
        ordered_ids = builtins.list(dict.fromkeys(lesson_ids))
        if not ordered_ids:
            return []

        with self._db.session() as session, session.begin():
            rows = builtins.list(
                session.scalars(
                    sa.select(LessonRow).where(LessonRow.id.in_(ordered_ids))
                )
            )
            rows_by_id = {row.id: row for row in rows}
            cited: builtins.list[Lesson] = []
            ticket_id_str = str(ticket_id)
            for lesson_id in ordered_ids:
                row = rows_by_id.get(lesson_id)
                if row is None:
                    continue
                related_ticket_ids = [
                    str(existing_id) for existing_id in row.related_ticket_ids or []
                ]
                if ticket_id_str not in related_ticket_ids:
                    related_ticket_ids.append(ticket_id_str)
                    row.related_ticket_ids = related_ticket_ids
                cited.append(self._to_model(row))
            return cited

    def list_stale_active(
        self, *, threshold: int = 10
    ) -> builtins.list[StaleLessonReview]:
        """ACTIVE lessons due for stale-memory review.

        Predicate, documented for ATLAS-100: ``updated_at`` is the last operator
        action because promotion/reject/archive/merge are the only v1 operator
        writers. For each ACTIVE lesson, count context packs created after that
        timestamp that included the lesson. If at least ``threshold`` packs
        included it and none of those packs' ``ticket_id`` values appears in the
        lesson's ``related_ticket_ids``, the lesson has zero post-operator
        citation/re-confirmation signal and is returned for review.
        """
        if threshold < 1:
            raise LessonValidationError("stale review threshold must be >= 1")

        with self._db.session() as session:
            lesson_rows = builtins.list(
                session.scalars(
                    sa.select(LessonRow)
                    .where(LessonRow.status == EntityStatus.ACTIVE.value)
                    .order_by(LessonRow.created_at, LessonRow.id)
                )
            )
            pack_rows = builtins.list(
                session.scalars(
                    sa.select(ContextPackRow).order_by(
                        ContextPackRow.created_at, ContextPackRow.id
                    )
                )
            )

        reviews: builtins.list[StaleLessonReview] = []
        for lesson_row in lesson_rows:
            lesson_id = str(lesson_row.id)
            included_after_operator_action = [
                pack_row
                for pack_row in pack_rows
                if pack_row.created_at > lesson_row.updated_at
                and lesson_id in {str(value) for value in pack_row.historical_lessons}
            ]
            if len(included_after_operator_action) < threshold:
                continue

            related_ticket_ids = {
                str(ticket_id) for ticket_id in lesson_row.related_ticket_ids or []
            }
            included_ticket_ids = {
                str(pack_row.ticket_id)
                for pack_row in included_after_operator_action
                if pack_row.ticket_id is not None
            }
            if related_ticket_ids & included_ticket_ids:
                continue

            reviews.append(
                StaleLessonReview(
                    lesson=self._to_model(lesson_row),
                    context_pack_count=len(included_after_operator_action),
                )
            )
        return reviews

    def _get_lesson_row(self, session: Session, lesson_id: UUID) -> LessonRow:
        row = session.get(LessonRow, lesson_id)
        if row is None:
            raise LessonNotFoundError(f"no lesson with id {lesson_id}")
        return row

    @staticmethod
    def _require_status(row: LessonRow, required: EntityStatus, *, action: str) -> None:
        if row.status != required.value:
            raise LessonStateError(
                f"can only {action} {required.value.upper()} lessons; "
                f"lesson {row.id} is {row.status!r}"
            )


class AgentRunRepo(_Repo[AgentRun]):
    def __init__(self, db: Database) -> None:
        super().__init__(db, AgentRun, AgentRunRow)

    def list_for_ticket(self, ticket_id: UUID) -> list[AgentRun]:
        """Every run reconstructed/recorded for ``ticket_id``, oldest first.

        AgentRun is not append-only in the Evidence sense: Phase 8
        reconstruction may create a partial observed row at dispatch and fill in
        handoff/evidence fields on a later tick. The read path stays focused so
        the producer can enforce one row per dispatch transition.
        """
        with self._db.session() as session:
            rows = session.scalars(
                sa.select(AgentRunRow)
                .where(AgentRunRow.ticket_id == ticket_id)
                .order_by(
                    AgentRunRow.started_at, AgentRunRow.created_at, AgentRunRow.id
                )
            )
            return [self._to_model(row) for row in rows]

    def replace(self, model: AgentRun) -> AgentRun:
        """Replace the mutable observation fields for one existing AgentRun.

        Used by AgentRun reconstruction only: the row identity and created_at
        stay stable, while nullable observation fields can be filled when later
        ticks see handoff/evidence. A missing id is a programmer error and is
        reported as ``ValueError`` rather than silently inserting a second run.
        """
        _reject_naive(model)
        with self._db.session() as session, session.begin():
            row = session.get(AgentRunRow, model.id)
            if row is None:
                raise ValueError(f"no AgentRun with id {model.id}")
            payload = model.model_dump()
            for column in AgentRunRow.__table__.columns:
                setattr(row, column.name, payload[column.name])
        return model


class ContextPackRepo(_Repo[ContextPack]):
    def __init__(self, db: Database) -> None:
        super().__init__(db, ContextPack, ContextPackRow)

    def latest_for_ticket(self, ticket_id: UUID) -> ContextPack | None:
        """Return the newest stored context pack for ``ticket_id``, if any."""
        with self._db.session() as session:
            row = session.scalars(
                sa.select(ContextPackRow)
                .where(ContextPackRow.ticket_id == ticket_id)
                .order_by(ContextPackRow.created_at.desc(), ContextPackRow.id.desc())
            ).first()
            return None if row is None else self._to_model(row)


RAW_PAYLOAD_CAP_BYTES = 64 * 1024
"""The evidence-pipeline.md "Retention" cap on a stored ``raw_payload``.

Measured as the serialised byte length under the SAME canonicalisation the
dedup ``payload_hash`` uses (``json.dumps`` sorted-keys, tight separators,
UTF-8; see ``atlas.github.normaliser.payload_hash``), so "size" is one
deterministic notion. A payload larger than this is replaced at the write
boundary by a self-describing marker (``_raw_payload_marker``); the pin triple
and ``payload_hash`` are never touched. 64KB verbatim from the design doc.
"""


def _serialised_len(payload: dict[str, Any]) -> int:
    """Byte length of ``payload`` under the dedup-hash canonicalisation.

    The single definition of an evidence payload's "size", matching
    ``atlas.github.normaliser.payload_hash`` exactly so the retention cap and
    the hash measure the same bytes.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return len(canonical.encode("utf-8"))


def _raw_payload_marker(model: Evidence, original_bytes: int) -> dict[str, Any]:
    """The self-describing replacement stored in place of an oversized payload.

    Carries the FULL payload's ``payload_hash`` and ``source_uri`` so the row
    stays auditable and the original is re-fetchable from GitHub (a Phase 10+
    concern; not this ticket). ``payload_hash`` is the upstream full-payload
    hash, never recomputed — truncation changes only the stored bytes.
    """
    return {
        "_truncated": True,
        "_original_bytes": original_bytes,
        "_payload_hash": model.payload_hash,
        "_source_uri": model.source_uri,
    }


def _prepare_evidence_append(model: Evidence) -> Evidence:
    """Apply the canonical evidence write guards without opening a transaction."""

    _reject_naive(model)
    if (
        evidence_tier(model.created_by_type) == "agent"
        and model.status is not EvidenceStatus.PENDING
    ):
        raise TrustTierError(
            "agent-tier evidence is capped at PENDING (ADR-0008); "
            f"got status {model.status.value!r}. Corroboration comes "
            "from a system-tier record or human approval, not a bypass."
        )
    if evidence_tier(model.created_by_type) == "system":
        missing = [
            name
            for name in ("commit_sha", "external_run_id", "payload_hash")
            if getattr(model, name) is None
        ]
        if missing:
            raise TrustTierError(
                "system-tier evidence must be commit-pinned (ADR-0008); "
                f"missing {missing}. Ingestion rejects records without "
                "commit_sha, external_run_id, and payload_hash."
            )

    size = _serialised_len(model.raw_payload)
    if size > RAW_PAYLOAD_CAP_BYTES:
        return model.model_copy(
            update={"raw_payload": _raw_payload_marker(model, size)}
        )
    return model


def _evidence_row(model: Evidence) -> EvidenceRow:
    payload = model.model_dump()
    payload["raw_payload"] = model.model_dump(mode="json")["raw_payload"]
    return EvidenceRow(**payload)


def _get_evidence_by_dedup_key(
    session: Session,
    external_run_id: str,
    payload_hash: str,
    *,
    require_job_metadata: bool = False,
) -> Evidence | None:
    """Return one immutable system observation inside the caller's transaction."""

    statement = sa.select(EvidenceRow).where(
        EvidenceRow.external_run_id == external_run_id,
        EvidenceRow.payload_hash == payload_hash,
    )
    if require_job_metadata:
        statement = statement.where(EvidenceRow.job_name.is_not(None))
    row = session.scalars(
        statement.order_by(EvidenceRow.created_at, EvidenceRow.id)
    ).first()
    return None if row is None else Evidence.model_validate(row, from_attributes=True)


def _add_evidence(session: Session, model: Evidence) -> Evidence:
    """Canonically append evidence inside a caller-owned transaction.

    This is the transaction-aware form of :meth:`EvidenceRepo.add`: it applies
    every trust/retention guard and preserves the immutable system-source dedup
    identity without opening or committing a second transaction.
    """

    prepared = _prepare_evidence_append(model)
    if evidence_tier(prepared.created_by_type) == "system":
        assert prepared.external_run_id is not None
        assert prepared.payload_hash is not None
        existing = _get_evidence_by_dedup_key(
            session,
            prepared.external_run_id,
            prepared.payload_hash,
            require_job_metadata=prepared.job_name is not None,
        )
        if existing is not None:
            return existing
    session.add(_evidence_row(prepared))
    return prepared


class EvidenceRepo(_Repo[Evidence]):
    """Append-only: add and queries only (ADR-0008).

    Append-only is repository-shape only: there is no update or delete verb
    (and no DB trigger, CHECK, or revoke) — the guarantee is that this
    surface exposes no mutating verb, matching DebtItemRepo / TickFailureRepo.
    """

    def __init__(self, db: Database) -> None:
        super().__init__(db, Evidence, EvidenceRow)

    def count(self) -> int:
        """Return the number of stored evidence records without loading them."""
        with self._db.session() as session:
            statement = sa.select(sa.func.count()).select_from(EvidenceRow)
            return int(session.scalar(statement))

    def get_by_dedup_key(
        self,
        external_run_id: str,
        payload_hash: str,
        *,
        require_job_metadata: bool = False,
    ) -> Evidence | None:
        """Return an already-ingested immutable source payload, if present.

        The evidence-pipeline dedup identity is exactly
        ``(external_run_id, payload_hash)``. Enriched CI records deliberately do
        not match historical rows whose ``job_name`` is null, allowing one
        append-only re-pull to add the ordering metadata needed by verification.
        """

        with self._db.session() as session:
            return _get_evidence_by_dedup_key(
                session,
                external_run_id,
                payload_hash,
                require_job_metadata=require_job_metadata,
            )

    def add(self, model: Evidence) -> Evidence:
        with self._db.session() as session, session.begin():
            return _add_evidence(session, model)

    def list_for_ticket(self, ticket_id: UUID) -> list[Evidence]:
        """Return one ticket's evidence, oldest record first."""
        with self._db.session() as session:
            rows = session.scalars(
                sa.select(EvidenceRow)
                .where(EvidenceRow.ticket_id == ticket_id)
                .order_by(EvidenceRow.created_at, EvidenceRow.id)
            )
            return [self._to_model(row) for row in rows]

    def list_for_product_commit(
        self, product_id: UUID, commit_sha: str
    ) -> list[Evidence]:
        """Return one product's exact-commit evidence in stable history order."""

        with self._db.session() as session:
            rows = session.scalars(
                sa.select(EvidenceRow)
                .where(
                    EvidenceRow.product_id == product_id,
                    EvidenceRow.commit_sha == commit_sha,
                )
                .order_by(EvidenceRow.created_at, EvidenceRow.id)
            )
            return [self._to_model(row) for row in rows]

    def latest_system_created_at(self) -> datetime | None:
        """Return the newest system-tier evidence timestamp."""
        with self._db.session() as session:
            statement = sa.select(sa.func.max(EvidenceRow.created_at)).where(
                EvidenceRow.created_by_type == ActorType.SYSTEM.value
            )
            return cast(datetime | None, session.scalar(statement))


class DebtItemRepo(_Repo[DebtItem]):
    """Append-only delivery-anomaly log (ATLAS-116).

    Mirrors EvidenceRepo's append-only shape: it exposes an append verb
    (``record``) and queries only — no update, no delete, no bypass — so
    one-row-per-observation (decision D1) is structural (decision D4). It
    is NOT EvidenceRepo: a DebtItem is an operational record, not
    evidence, so there is no trust-tier cap (decision D2).

    Recurrence is computed here at query time over the rows
    (``recurring``); it is never stored on a row and never gates a
    ``record`` (decision D3).
    """

    def __init__(self, db: Database) -> None:
        super().__init__(db, DebtItem, DebtItemRow)

    def record(self, model: DebtItem) -> DebtItem:
        """Append one observation and return the persisted row.

        The PM Engine's sole append verb. Recording never reads or writes
        ticket state and never consults recurrence — logging debt and
        moving a ticket are separate concerns (pm-engine-and-linear-sync.md).
        """
        return self.add(model)

    def list_for_ticket(self, ticket_id: UUID) -> list[DebtItem]:
        """Every recorded anomaly for ``ticket_id``, oldest observation
        first (then by id for a stable order on identical timestamps)."""
        with self._db.session() as session:
            rows = session.scalars(
                sa.select(DebtItemRow)
                .where(DebtItemRow.ticket_id == ticket_id)
                .order_by(DebtItemRow.observed_at, DebtItemRow.id)
            )
            return [self._to_model(row) for row in rows]

    def logged_since(
        self, ticket_id: UUID, anomaly_type: AnomalyType, since: datetime
    ) -> bool:
        """True when ``ticket_id`` already has a row of ``anomaly_type`` whose
        ``observed_at`` is at or after ``since`` (ATLAS-119 per-episode dedup).

        The dwell-breach episode predicate: ``since`` is the ticket's
        ``status_entered_at`` (the episode boundary), so this answers "has a
        breach already been logged since the ticket entered this status?" — one
        ``DWELL_BREACH`` per episode, not per tick. The boundary is INCLUSIVE
        (``>= since``): a row written exactly at the entry instant counts as
        already-logged. When the status changes, ``status_entered_at`` advances
        past the prior episode's rows, so a fresh episode logs again. A pure
        query-time predicate: it reads the rows, stores nothing, never gates a
        ``record``."""
        if since.utcoffset() is None:
            raise NaiveDatetimeError("DebtItem", "observed_at")
        with self._db.session() as session:
            count = session.scalar(
                sa.select(sa.func.count())
                .select_from(DebtItemRow)
                .where(
                    DebtItemRow.ticket_id == ticket_id,
                    DebtItemRow.anomaly_type == anomaly_type.value,
                    DebtItemRow.observed_at >= since,
                )
            )
            return (count or 0) > 0

    def recurring(
        self, ticket_id: UUID, anomaly_type: AnomalyType, threshold: int = 3
    ) -> bool:
        """True when ``ticket_id`` has at least ``threshold`` rows of
        ``anomaly_type`` (default 3, per pm-engine-and-linear-sync.md).

        A pure query-time predicate: it reads the rows, stores nothing, and
        is not a creation gate. The boundary is inclusive — exactly
        ``threshold`` rows recurs; ``threshold - 1`` does not."""
        with self._db.session() as session:
            count = session.scalar(
                sa.select(sa.func.count())
                .select_from(DebtItemRow)
                .where(
                    DebtItemRow.ticket_id == ticket_id,
                    DebtItemRow.anomaly_type == anomaly_type.value,
                )
            )
            return (count or 0) >= threshold


class TickFailureRepo(_Repo[TickFailure]):
    """Append-only PM-scheduler tick-crash log (ATLAS-125).

    Mirrors DebtItemRepo's append-only shape: it exposes an append verb
    (``record``) and a query-time dedup predicate (``recorded_since``) only
    — no update, no delete, no bypass — so one-row-per-crash is structural.
    Like DebtItemRepo it is NOT EvidenceRepo: a TickFailure is an operational
    record, not evidence, so there is no trust-tier cap.

    The record half of create-on-crash: the PM scheduler (ATLAS-50) is the
    SOLE writer; no other production path records a row. ``recorded_since``
    is computed here at query time over the rows; it is never stored on a row
    and never gates a ``record``.
    """

    def __init__(self, db: Database) -> None:
        super().__init__(db, TickFailure, TickFailureRow)

    def record(self, model: TickFailure) -> TickFailure:
        """Append one tick-crash record and return the persisted row.

        The scheduler's sole append verb (ATLAS-50). Recording never reads or
        consults the dedup predicate — recording a crash and deciding whether
        to record it are separate concerns; the caller dedups before calling.
        """
        return self.add(model)

    def recorded_since(self, failure_signature: str, since: datetime) -> bool:
        """True when a row of ``failure_signature`` has ``occurred_at`` at or
        after ``since`` (ATLAS-125 query-time dedup predicate).

        The create-on-crash dedup predicate, mirroring
        ``DebtItemRepo.logged_since``: the caller (ATLAS-50) supplies ``since``
        — the dedup window boundary — so this answers "has a crash of this
        signature already been recorded since the window opened?" The window
        policy lives with the caller; no default window is set here. The
        boundary is INCLUSIVE (``>= since``): a row written exactly at the
        boundary instant counts as already-recorded. Scoped per signature: a
        different signature never satisfies. A pure query-time predicate — it
        reads the rows, stores nothing, and never gates a ``record``.
        """
        if since.utcoffset() is None:
            raise NaiveDatetimeError("TickFailure", "occurred_at")
        with self._db.session() as session:
            count = session.scalar(
                sa.select(sa.func.count())
                .select_from(TickFailureRow)
                .where(
                    TickFailureRow.failure_signature == failure_signature,
                    TickFailureRow.occurred_at >= since,
                )
            )
            return (count or 0) > 0


class PmSyncReceiptRepo(_Repo[PmSyncReceipt]):
    """Append-only PM sync receipt log (ATLAS-245).

    The PM sync loop's local completion boundary records one receipt per tick,
    successful or not. The repository exposes append and read-only projections
    only — no update, no delete, no bypass — so a recorded tick outcome is never
    rewritten. ``latest_successful_finished_at`` is a pure query over the
    bounded success classifications and deliberately ignores partial, failed,
    cancelled and malformed-pull receipts.
    """

    def __init__(self, db: Database) -> None:
        super().__init__(db, PmSyncReceipt, PmSyncReceiptRow)

    def record(self, model: PmSyncReceipt) -> PmSyncReceipt:
        """Append one sync receipt and return the persisted row."""
        return self.add(model)

    def list(self) -> list[PmSyncReceipt]:
        """Return receipts in tick order, with id as the stable tie-breaker."""
        with self._db.session() as session:
            rows = session.scalars(
                sa.select(PmSyncReceiptRow).order_by(
                    PmSyncReceiptRow.started_at,
                    PmSyncReceiptRow.finished_at,
                    PmSyncReceiptRow.id,
                )
            )
            return [self._to_model(row) for row in rows]

    def latest_successful_finished_at(
        self, product_id: UUID | None = None
    ) -> datetime | None:
        """Return the newest genuinely successful finish, optionally by product."""
        successful = [result.value for result in SUCCESSFUL_PM_SYNC_RESULTS]
        with self._db.session() as session:
            statement = sa.select(sa.func.max(PmSyncReceiptRow.finished_at)).where(
                PmSyncReceiptRow.result.in_(successful)
            )
            if product_id is not None:
                statement = statement.where(PmSyncReceiptRow.product_id == product_id)
            return cast(datetime | None, session.scalar(statement))


class DeliveryAdmissionPolicyRepo:
    """Read-only access to immutable policy history and its active pointer.

    Policy revision writes are intentionally absent from this public surface.
    They are composed only by the governed operator-action command service so
    a caller cannot bypass actor attribution, idempotency, compare-and-set or
    the atomic receipt boundary.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    def get_active(self, product_id: UUID) -> DeliveryAdmissionPolicyRevision | None:
        """Return the active immutable revision for ``product_id``."""

        with self._db.session() as session:
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
            return _delivery_admission_policy_model(row)

    def get_revision(
        self, product_id: UUID, revision: int
    ) -> DeliveryAdmissionPolicyRevision | None:
        """Return one historical product revision by monotonic number."""

        with self._db.session() as session:
            row = session.scalars(
                sa.select(DeliveryAdmissionPolicyRevisionRow).where(
                    DeliveryAdmissionPolicyRevisionRow.product_id == product_id,
                    DeliveryAdmissionPolicyRevisionRow.revision == revision,
                )
            ).one_or_none()
            return _delivery_admission_policy_model(row)

    def list_revisions(self, product_id: UUID) -> list[DeliveryAdmissionPolicyRevision]:
        """Return all immutable revisions oldest first."""

        with self._db.session() as session:
            rows = session.scalars(
                sa.select(DeliveryAdmissionPolicyRevisionRow)
                .where(DeliveryAdmissionPolicyRevisionRow.product_id == product_id)
                .order_by(DeliveryAdmissionPolicyRevisionRow.revision)
            )
            return [
                DeliveryAdmissionPolicyRevision.model_validate(
                    row, from_attributes=True
                )
                for row in rows
            ]


def _delivery_admission_policy_model(
    row: DeliveryAdmissionPolicyRevisionRow | None,
) -> DeliveryAdmissionPolicyRevision | None:
    if row is None:
        return None
    return DeliveryAdmissionPolicyRevision.model_validate(row, from_attributes=True)


class AdmissionRunRepo(_Repo[AdmissionRun]):
    """Append-only admission evaluation history."""

    def __init__(self, db: Database) -> None:
        super().__init__(db, AdmissionRun, AdmissionRunRow)

    def record(self, model: AdmissionRun) -> AdmissionRun:
        """Append one already-calculated run without changing its decision."""

        return self.add(model)

    def list_for_product(self, product_id: UUID) -> list[AdmissionRun]:
        """Return a product's runs in evaluation order."""

        with self._db.session() as session:
            rows = session.scalars(
                sa.select(AdmissionRunRow)
                .where(AdmissionRunRow.product_id == product_id)
                .order_by(AdmissionRunRow.evaluated_at, AdmissionRunRow.id)
            )
            return [self._to_model(row) for row in rows]

    def latest_for_product(self, product_id: UUID) -> AdmissionRun | None:
        """Return the newest product run without loading its full history."""

        with self._db.session() as session:
            row = session.scalars(
                sa.select(AdmissionRunRow)
                .where(AdmissionRunRow.product_id == product_id)
                .order_by(
                    AdmissionRunRow.evaluated_at.desc(),
                    AdmissionRunRow.id.desc(),
                )
                .limit(1)
            ).one_or_none()
            return None if row is None else self._to_model(row)


class CIHandoffReconciliationRepo(_Repo[CIHandoffReconciliation]):
    """Append-only, bounded CI handoff decision history."""

    def __init__(self, db: Database) -> None:
        super().__init__(db, CIHandoffReconciliation, CIHandoffReconciliationRow)

    def record(self, model: CIHandoffReconciliation) -> CIHandoffReconciliation:
        """Append one already-classified observation without changing it."""

        return self.add(model)

    def list_for_ticket(self, ticket_id: UUID) -> list[CIHandoffReconciliation]:
        """Return one ticket's reconciliation outcomes in observation order."""

        with self._db.session() as session:
            rows = session.scalars(
                sa.select(CIHandoffReconciliationRow)
                .where(CIHandoffReconciliationRow.ticket_id == ticket_id)
                .order_by(
                    CIHandoffReconciliationRow.observed_at,
                    CIHandoffReconciliationRow.id,
                )
            )
            return [self._to_model(row) for row in rows]


class TicketStatusTransitionRepo(_Repo[TicketStatusTransition]):
    """Append-only status-transition history (ATLAS-121).

    Mirrors DebtItemRepo/TickFailureRepo's append-only shape: it exposes an
    append verb (``record``) and a read-only query (``list_for_ticket``) only —
    no update, no delete, no bypass — so one-row-per-real-transition is
    structural. Like them it is NOT EvidenceRepo: a TicketStatusTransition is an
    operational record, not evidence, so there is no trust-tier cap.

    The PRODUCTION writer is ``TicketRepo.apply_linear_status``, which appends
    inline on its own transaction so the transition commits atomically with the
    status change (atomicity is why that path does not route through ``record``).
    ``record`` exists for completeness and tests; it builds its row through the
    SAME ``_status_transition_row`` factory the inline writer uses, so the two
    cannot drift. The log is append-only history — reads never mutate.
    """

    def __init__(self, db: Database) -> None:
        super().__init__(db, TicketStatusTransition, TicketStatusTransitionRow)

    def record(self, model: TicketStatusTransition) -> TicketStatusTransition:
        """Append one transition record and return the persisted row.

        Builds the row via the shared ``_status_transition_row`` factory — the
        single row definition, so this path cannot drift from the inline writer
        in ``apply_linear_status`` — preserving the model's own id. Recording
        never reads or mutates ticket state.
        """
        _reject_naive(model)
        with self._db.session() as session, session.begin():
            session.add(
                _status_transition_row(
                    transition_id=model.id,
                    ticket_id=model.ticket_id,
                    from_status=model.from_status,
                    to_status=model.to_status,
                    occurred_at=model.occurred_at,
                    created_by_id=model.created_by_id,
                )
            )
        return model

    def list_for_ticket(self, ticket_id: UUID) -> list[TicketStatusTransition]:
        """Every recorded transition for ``ticket_id``, oldest first (by
        ``occurred_at`` ascending, then by id for a stable order on identical
        instants). Append-only: it reads the rows and mutates nothing."""
        with self._db.session() as session:
            rows = session.scalars(
                sa.select(TicketStatusTransitionRow)
                .where(TicketStatusTransitionRow.ticket_id == ticket_id)
                .order_by(
                    TicketStatusTransitionRow.occurred_at,
                    TicketStatusTransitionRow.id,
                )
            )
            return [self._to_model(row) for row in rows]

    def list_all(self) -> list[TicketStatusTransition]:
        """Every recorded transition, ordered by ``ticket_id``, then
        ``occurred_at`` ascending, then id (stable on identical instants).

        The read surface for historical cycle time (ATLAS-126): one batch query
        the report groups by ticket in memory, rather than N
        ``list_for_ticket`` calls. The per-ticket order matches
        ``list_for_ticket`` so an episode walk over either sees the same
        sequence. Append-only: it reads the rows and mutates nothing.
        """
        with self._db.session() as session:
            rows = session.scalars(
                sa.select(TicketStatusTransitionRow).order_by(
                    TicketStatusTransitionRow.ticket_id,
                    TicketStatusTransitionRow.occurred_at,
                    TicketStatusTransitionRow.id,
                )
            )
            return [self._to_model(row) for row in rows]


class VerificationCheckRepo(_Repo[VerificationCheck]):
    """Append-only Verification Engine record (ATLAS-71).

    Mirrors EvidenceRepo's append-only shape: it exposes ``add`` and queries
    (``get``, ``list``) only — no update, no delete, no bypass — so the
    record of an evaluation is immutable once written. Unlike EvidenceRepo
    it applies NO trust-tier cap and NO commit-pin guard: a VerificationCheck
    is NOT evidence (ADR-0008), so ``add`` is the inherited insert with no
    status policing. The repo lands unused, exactly as Evidence/EvidenceRepo
    (ATLAS-14) preceded its writers (ATLAS-63); the per-check evaluators and
    validators that write rows are later Phase 7 tickets.
    """

    def __init__(self, db: Database) -> None:
        super().__init__(db, VerificationCheck, VerificationCheckRow)

    def list_for_ticket(self, ticket_id: UUID) -> list[VerificationCheck]:
        """Every recorded check for ``ticket_id``, oldest evaluation first (by
        ``created_at`` ascending, then by id for a stable order on identical
        instants) — a focused query, never a load-and-scan of the whole table.

        The PM Engine's completion consumer (ATLAS-131) reads these rows and
        composes the per-ticket verdict via
        :func:`atlas.verification.completion.ticket_verdict_from_checks`. Mirrors
        ``DebtItemRepo.list_for_ticket``: append-only, it reads and mutates nothing.
        """
        with self._db.session() as session:
            rows = session.scalars(
                sa.select(VerificationCheckRow)
                .where(VerificationCheckRow.ticket_id == ticket_id)
                .order_by(VerificationCheckRow.created_at, VerificationCheckRow.id)
            )
            return [self._to_model(row) for row in rows]


class AcceptanceSessionRepo(_Repo[_AcceptanceModel]):
    """Pinned-identity acceptance sessions with narrow lifecycle mutation."""

    def __init__(self, db: Database) -> None:
        super().__init__(db, _AcceptanceModel, AcceptanceSessionRow)

    def create(self, model: _AcceptanceModel) -> _AcceptanceCreateResult:
        """Insert one session or replay the same creation command.

        The partial unique index serialises all non-terminal attempts for one
        repository/PR.  The all-time unique creation-key identity makes an
        exact idempotent command replay its original immutable outcome.  A
        different command never receives another command's active row.
        """

        _reject_naive(model)
        try:
            with self._db.session() as session, session.begin():
                session.add(self._to_row(model))
                session.flush()
            return AcceptanceSessionCreateRecord(session=model, created=True)
        except sa.exc.IntegrityError as error:
            by_command = self.get_by_creation_idempotency_key_identity(
                model.creation_idempotency_key_identity
            )
            if by_command is not None:
                if (
                    by_command.repository_owner == model.repository_owner
                    and by_command.repository_name == model.repository_name
                    and by_command.pr_number == model.pr_number
                    and by_command.created_by_type == model.created_by_type
                    and by_command.created_by_id == model.created_by_id
                ):
                    return AcceptanceSessionCreateRecord(
                        session=by_command, created=False
                    )
                raise AcceptanceSessionStateError(
                    _AcceptanceReason.IDEMPOTENCY_KEY_REUSED
                ) from error

            active = self.get_non_terminal_for_pr(
                model.repository_owner,
                model.repository_name,
                model.pr_number,
            )
            if active is not None:
                raise AcceptanceSessionStateError(
                    _AcceptanceReason.ACTIVE_SESSION_EXISTS
                ) from error
            raise

    def get_by_creation_idempotency_key_identity(
        self, identity: str
    ) -> _AcceptanceModel | None:
        """Return the immutable outcome owned by one creation command."""

        with self._db.session() as session:
            row = session.scalars(
                sa.select(AcceptanceSessionRow).where(
                    AcceptanceSessionRow.creation_idempotency_key_identity == identity
                )
            ).first()
            return None if row is None else self._to_model(row)

    def get_non_terminal_for_pr(
        self, repository_owner: str, repository_name: str, pr_number: int
    ) -> _AcceptanceModel | None:
        """Return the sole non-terminal attempt for a repository/PR."""

        terminal = tuple(
            status.value for status in _AcceptanceLifecycle if status.is_terminal
        )
        with self._db.session() as session:
            row = session.scalars(
                sa.select(AcceptanceSessionRow).where(
                    AcceptanceSessionRow.repository_owner == repository_owner,
                    AcceptanceSessionRow.repository_name == repository_name,
                    AcceptanceSessionRow.pr_number == pr_number,
                    AcceptanceSessionRow.lifecycle.not_in(terminal),
                )
            ).first()
            return None if row is None else self._to_model(row)

    def list_for_pr(
        self, repository_owner: str, repository_name: str, pr_number: int
    ) -> list[_AcceptanceModel]:
        """Return every immutable-head attempt in historical order."""

        with self._db.session() as session:
            rows = session.scalars(
                sa.select(AcceptanceSessionRow)
                .where(
                    AcceptanceSessionRow.repository_owner == repository_owner,
                    AcceptanceSessionRow.repository_name == repository_name,
                    AcceptanceSessionRow.pr_number == pr_number,
                )
                .order_by(
                    AcceptanceSessionRow.created_at,
                    AcceptanceSessionRow.id,
                )
            )
            return [self._to_model(row) for row in rows]

    def mark_stale(
        self,
        session_id: UUID,
        reasons: tuple[_AcceptanceReason, ...],
        *,
        staled_at: datetime,
    ) -> _AcceptanceModel:
        """Atomically terminalise a moved session while preserving history."""

        if staled_at.utcoffset() is None:
            raise NaiveDatetimeError("AcceptanceSession", "staled_at")
        if not reasons:
            raise ValueError("mark_stale requires at least one typed reason")
        with self._db.session() as session, session.begin():
            row = session.get(AcceptanceSessionRow, session_id)
            if row is None:
                raise AcceptanceSessionStateError(_AcceptanceReason.SESSION_STALE)
            current = _AcceptanceLifecycle(row.lifecycle)
            if current.is_terminal:
                return self._to_model(row)

            existing_reasons = [
                _AcceptanceReason(reason) for reason in row.blocking_reasons
            ]
            row.blocking_reasons = [
                reason.value for reason in dict.fromkeys([*existing_reasons, *reasons])
            ]
            historical = [
                _AcceptanceReason(reason) for reason in row.historical_readiness_reasons
            ]
            row.historical_readiness_reasons = [
                reason.value
                for reason in dict.fromkeys(
                    [
                        *historical,
                        _AcceptanceReason.SESSION_STALE,
                        *reasons,
                    ]
                )
            ]
            row.lifecycle = _AcceptanceLifecycle.STALE.value
            row.updated_at = staled_at
            row.staled_at = staled_at
            session.flush()
            return self._to_model(row)


class OperatorActionReceiptRepo(_Repo[OperatorActionReceipt]):
    """Append-only governed-operator action receipts.

    The companion ``operator_action_keys`` table owns idempotency reservation.
    This repository exposes only terminal receipt append and read paths — no
    update, no delete, no bypass — and the database trigger rejects direct row
    mutation for the same append-only guarantee.
    """

    def __init__(self, db: Database) -> None:
        super().__init__(db, OperatorActionReceipt, OperatorActionReceiptRow)

    def _to_row(self, model: OperatorActionReceipt) -> Base:
        return _operator_action_receipt_row(model)

    def record(self, model: OperatorActionReceipt) -> OperatorActionReceipt:
        """Append one terminal receipt and return the persisted row."""
        _reject_naive(model)
        with self._db.session() as session, session.begin():
            _add_operator_action_receipt(session, model)
        return model

    def get_by_idempotency_key_identity(
        self, idempotency_key_identity: str
    ) -> OperatorActionReceipt | None:
        """Return the terminal receipt for an idempotency key identity."""
        with self._db.session() as session:
            return _get_operator_action_receipt_by_identity(
                session, idempotency_key_identity
            )


class PlanRunRepo(_Repo[PlanRun]):
    """Insert-plus-single-finalisation (ADR-0007, knowledge-core)."""

    def __init__(self, db: Database) -> None:
        super().__init__(db, PlanRun, PlanRunRow)

    def latest_proposed(self) -> PlanRun | None:
        """The PlanRun `atlas apply` loads (spec §2.2 step 1)."""
        with self._db.session() as session:
            row = session.scalars(
                sa.select(PlanRunRow)
                .where(PlanRunRow.status == PlanRunStatus.PROPOSED.value)
                .order_by(PlanRunRow.created_at.desc())
            ).first()
            return None if row is None else self._to_model(row)

    def latest_applied(self) -> PlanRun | None:
        """The most recent applied PlanRun — the provenance-retrieval
        counterpart of `latest_proposed` (ATLAS-28). Lets a caller read an
        applied backlog's plan back by recency without scanning `list()`;
        ordered by `applied_at` (set on the finalising transition)."""
        with self._db.session() as session:
            row = session.scalars(
                sa.select(PlanRunRow)
                .where(PlanRunRow.status == PlanRunStatus.APPLIED.value)
                .order_by(PlanRunRow.applied_at.desc())
            ).first()
            return None if row is None else self._to_model(row)

    def finalize(
        self,
        plan_run_id: UUID,
        status: PlanRunStatus,
        *,
        approved_by: str | None = None,
        applied_at: datetime | None = None,
        failure_reason: str | None = None,
    ) -> PlanRun:
        """The single permitted transition out of `proposed`. Writes only
        approved_by, applied_at, and failure_reason."""
        if status not in _FINAL_STATUSES:
            raise PlanRunStateError(
                f"finalize accepts applied, rejected, or failed; got {status.value!r}"
            )
        if applied_at is not None and applied_at.utcoffset() is None:
            raise NaiveDatetimeError("PlanRun", "applied_at")
        with self._db.session() as session, session.begin():
            row = session.get(PlanRunRow, plan_run_id)
            if row is None:
                raise PlanRunStateError(f"no PlanRun with id {plan_run_id}")
            if row.status != PlanRunStatus.PROPOSED.value:
                raise PlanRunStateError(
                    f"PlanRun {plan_run_id} is {row.status!r}, not 'proposed'; "
                    "rows are finalised exactly once"
                )
            row.status = status.value
            row.approved_by = approved_by
            row.applied_at = applied_at
            row.failure_reason = failure_reason
            return self._to_model(row)


class KeyCounterRepo:
    """Monotonic per-prefix key counter (ATLAS-25, knowledge-core "Key
    counter"; contract data-model §3.12).

    Surface is read + reserve + incident-only monotonic advance. No setter and
    no decrement exist,
    so no-reuse — including across archived keys — is structural: the
    value only advances and is decoupled from backlog membership (AT-6).

    reserve participates in a CALLER-SUPPLIED session (gap 3): it neither
    opens nor commits a transaction, so ATLAS-27 composes the increment
    with the render writes and the PlanRun finalise atomically. Sessions
    and row classes stay inside this package; the public currency here is
    plain ints and a Reservation, never an ORM row.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    def high_water_marks(self) -> dict[str, int]:
        """The current high-water mark per seen prefix. An unseen prefix
        is absent; callers read it as 0 (its first key is `<prefix>-1`)."""
        with self._db.session() as session:
            rows = session.scalars(sa.select(KeyCounterRow))
            return {row.prefix: row.high_water for row in rows}

    def advance_to(self, prefix: str, high_water: int) -> int:
        """Raise ``prefix`` to at least ``high_water`` in one transaction.

        This is the namespace-incident repair seam (ATLAS-029M), not ordinary
        allocation: it never returns assigned keys and never lowers a counter.
        Repeating the same repair is therefore an exact no-op.
        """
        if high_water < 0:
            raise KeyCounterError(
                f"advance_to requires a non-negative high-water mark; got {high_water}"
            )
        with self._db.session() as session, session.begin():
            row = session.get(KeyCounterRow, prefix)
            if row is None:
                session.add(KeyCounterRow(prefix=prefix, high_water=high_water))
                return high_water
            if row.high_water < high_water:
                row.high_water = high_water
            return row.high_water

    def reserve(self, session: Session, prefix: str, count: int) -> Reservation:
        """Advance `prefix` by `count` inside the caller's transaction and
        return the assigned range [first, last].

        Monotonic by construction — the value only ever increases — so a
        number once assigned, including one whose ticket was later
        archived, is never reissued. The read-increment-persist happens on
        the supplied session; the caller commits."""
        if count <= 0:
            raise KeyCounterError(f"reserve requires a positive count; got {count}")
        row = session.get(KeyCounterRow, prefix)
        if row is None:
            first = 1
            row = KeyCounterRow(prefix=prefix, high_water=count)
            session.add(row)
        else:
            first = row.high_water + 1
            row.high_water = row.high_water + count
        return Reservation(
            prefix=prefix, first=first, last=row.high_water, high_water=row.high_water
        )
