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

from dataclasses import dataclass
from datetime import datetime
from typing import Generic, TypeVar
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel
from sqlalchemy.orm import Session

from atlas.core.enums import EvidenceStatus
from atlas.core.models import (
    AgentRun,
    AnomalyType,
    ArchitectureDecisionRecord,
    ContextPack,
    DebtItem,
    Epic,
    Evidence,
    Lesson,
    PlanRun,
    PlanRunStatus,
    Product,
    Ticket,
    TicketDependency,
    TicketStatus,
)
from atlas.core.trust import evidence_tier
from atlas.storage.db import Database
from atlas.storage.tables import (
    AgentRunRow,
    ArchitectureDecisionRecordRow,
    Base,
    ContextPackRow,
    DebtItemRow,
    EpicRow,
    EvidenceRow,
    KeyCounterRow,
    LessonRow,
    PlanRunRow,
    ProductRow,
    TicketDependencyRow,
    TicketRow,
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


@dataclass(frozen=True)
class Reservation:
    """The keys an apply reserved from one prefix's counter: the assigned
    range [first, last] and the resulting high-water mark."""

    prefix: str
    first: int
    last: int
    high_water: int


def _reject_naive(model: BaseModel) -> None:
    for name in type(model).model_fields:
        value = getattr(model, name)
        if isinstance(value, datetime) and value.utcoffset() is None:
            raise NaiveDatetimeError(type(model).__name__, name)


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


class TicketRepo(_KeyedRepo[Ticket]):
    def __init__(self, db: Database) -> None:
        super().__init__(db, Ticket, TicketRow)

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

    def apply_linear_status(self, key: str, status: TicketStatus) -> Ticket:
        """Set ``status`` from a Linear pull (Linear -> Atlas; ATLAS-42).

        The status direction's sole Atlas writer. ``updated_at`` is
        DELIBERATELY left untouched: status is Linear-owned, and the sync
        cursor compares ``updated_at > linear_synced_at`` — bumping
        ``updated_at`` on an inbound status change would spuriously re-push the
        definition Atlas -> Linear (a directionality leak). Disjoint-column
        discipline, exactly like ``set_estimated_effort`` (ADR-0006/0007).
        """
        with self._db.session() as session, session.begin():
            row = session.scalars(
                sa.select(TicketRow).where(TicketRow.key == key)
            ).first()
            if row is None:
                raise TicketNotFoundError(f"no ticket with key {key!r}")
            row.status = status.value
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


class TicketDependencyRepo(_Repo[TicketDependency]):
    def __init__(self, db: Database) -> None:
        super().__init__(db, TicketDependency, TicketDependencyRow)


class LessonRepo(_Repo[Lesson]):
    def __init__(self, db: Database) -> None:
        super().__init__(db, Lesson, LessonRow)


class AgentRunRepo(_Repo[AgentRun]):
    def __init__(self, db: Database) -> None:
        super().__init__(db, AgentRun, AgentRunRow)


class ContextPackRepo(_Repo[ContextPack]):
    def __init__(self, db: Database) -> None:
        super().__init__(db, ContextPack, ContextPackRow)


class EvidenceRepo(_Repo[Evidence]):
    """Append-only: add and queries only (ADR-0008)."""

    def __init__(self, db: Database) -> None:
        super().__init__(db, Evidence, EvidenceRow)

    def add(self, model: Evidence) -> Evidence:
        if (
            evidence_tier(model.created_by_type) == "agent"
            and model.status is not EvidenceStatus.PENDING
        ):
            raise TrustTierError(
                f"agent-tier evidence is capped at PENDING (ADR-0008); "
                f"got status {model.status.value!r}. Corroboration comes "
                "from a system-tier record or human approval, not a bypass."
            )
        return super().add(model)


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

    Surface is exactly read + reserve. No setter and no decrement exist,
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
