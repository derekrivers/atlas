"""Apply unit-of-work (ATLAS-27): all of apply's DB writes in one
transaction.

The per-aggregate repos self-commit, so they cannot share a transaction;
gap 1's single-commit-point needs the counter increment, the backlog row
inserts, and the PlanRun finalise to live in one session. This is exactly
what KeyCounterRepo.reserve(session, …) was built for (ATLAS-25 gap 3).

Sessions and row classes stay inside the storage package; the caller
(atlas.planning.apply) passes Pydantic models and gets back the resulting
high-water marks. The DB commit here is the single linearisation point of
an apply (gap 1): the caller writes renders to temp files before calling
this, and atomically moves them into place only after it returns.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa

from atlas.core.models import Epic, PlanRunStatus, Ticket, TicketDependency
from atlas.storage.db import Database
from atlas.storage.repositories import KeyCounterRepo, PlanRunStateError
from atlas.storage.tables import (
    Base,
    EpicRow,
    PlanRunRow,
    TicketDependencyRow,
    TicketRow,
)

TICKET_PREFIX = "ATLAS"
EPIC_PREFIX = "ATLAS-E"


def _to_row(model: object, row_cls: type[Base]) -> Base:
    """Convert a Pydantic model to its row, JSON columns in JSON mode (the
    same boundary conversion the repositories use)."""
    json_fields = {
        column.name
        for column in row_cls.__table__.columns
        if isinstance(column.type, sa.JSON)
    }
    payload = model.model_dump()  # type: ignore[attr-defined]
    if json_fields:
        json_payload = model.model_dump(mode="json")  # type: ignore[attr-defined]
        for name in json_fields:
            payload[name] = json_payload[name]
    return row_cls(**payload)


def apply_backlog(
    database: Database,
    *,
    plan_run_id: UUID,
    new_epics: list[Epic],
    new_tickets: list[Ticket],
    new_dependencies: list[TicketDependency],
    approved_by: str,
    applied_at: datetime,
) -> dict[str, int]:
    """Commit one apply atomically: reserve key ranges for the added
    epics/tickets, insert the new backlog rows, and finalise the PlanRun
    proposed -> applied. Returns the resulting high-water marks.

    Single commit point: everything runs in one transaction that commits
    on clean exit and rolls back on any error (gap 1). Counts come from the
    entity lists — the keys were already assigned from these same counts."""
    counter = KeyCounterRepo(database)
    marks: dict[str, int] = {}
    session = database.session()
    with session.begin():
        if new_epics:
            marks[EPIC_PREFIX] = counter.reserve(
                session, EPIC_PREFIX, len(new_epics)
            ).high_water
        if new_tickets:
            marks[TICKET_PREFIX] = counter.reserve(
                session, TICKET_PREFIX, len(new_tickets)
            ).high_water
        for epic in new_epics:
            session.add(_to_row(epic, EpicRow))
        for ticket in new_tickets:
            session.add(_to_row(ticket, TicketRow))
        for dependency in new_dependencies:
            session.add(_to_row(dependency, TicketDependencyRow))

        # The §6 finalise guard, inline so it shares this transaction.
        row = session.get(PlanRunRow, plan_run_id)
        if row is None:
            raise PlanRunStateError(f"no PlanRun with id {plan_run_id}")
        if row.status != PlanRunStatus.PROPOSED.value:
            raise PlanRunStateError(
                f"PlanRun {plan_run_id} is {row.status!r}, not 'proposed'; "
                "rows are finalised exactly once"
            )
        row.status = PlanRunStatus.APPLIED.value
        row.approved_by = approved_by
        row.applied_at = applied_at
    return marks
