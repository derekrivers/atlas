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

from datetime import datetime
from typing import Generic, TypeVar
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel

from atlas.core.enums import EvidenceStatus
from atlas.core.models import (
    AgentRun,
    ArchitectureDecisionRecord,
    ContextPack,
    Epic,
    Evidence,
    Lesson,
    PlanRun,
    PlanRunStatus,
    Product,
    Ticket,
    TicketDependency,
)
from atlas.core.trust import evidence_tier
from atlas.storage.db import Database
from atlas.storage.tables import (
    AgentRunRow,
    ArchitectureDecisionRecordRow,
    Base,
    ContextPackRow,
    EpicRow,
    EvidenceRow,
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

    def _to_model(self, row: Base) -> M:
        return self._model_cls.model_validate(row, from_attributes=True)

    def add(self, model: M) -> M:
        _reject_naive(model)
        with self._db.session() as session, session.begin():
            session.add(self._row_cls(**model.model_dump()))
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
