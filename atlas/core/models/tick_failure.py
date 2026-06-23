"""TickFailure model (ATLAS-125), per data-model-and-schemas.md §6.3 and
pm-engine-and-linear-sync.md (the create-on-crash record ATLAS-50 writes).

A durable record of one PM-scheduler tick that crashed. A tick crash is
tick-level, not ticket-scoped — it has no ticket and no FK target — so it
CANNOT be a ``DebtItem`` (whose ``ticket_id`` is required and FK-backed);
hence a dedicated model. Append-only and system-written
(``created_by_type = system``); like ``DebtItem`` it is an operational
record (ADR-0006 §2), NOT evidence — so it carries no EvidenceStatus, no
trust tier, and no PENDING cap. It must not import or reuse the Evidence
status machinery.

Contract only: append-only enforcement lives in the repository layer
(``TickFailureRepo``), mirroring ``DebtItem`` — the model does not police
mutation. Query-time dedup is a pure predicate over these rows
(``TickFailureRepo.recorded_since``), never stored on the row and never a
creation gate.

This is the record half of create-on-crash; the writer — the PM scheduler
that catches a crashing ``sync_tick``, records a ``TickFailure``, and
continues — is ATLAS-50, the sole writer. No production code writes a row
here.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from atlas.core.enums import ActorType


class TickFailure(BaseModel):
    """One recorded PM-scheduler tick crash (data-model §6.3).

    Append-only. Unlike ``DebtItem``, there is no ``ticket_id`` and no
    ``product_id``: a tick crash is observed at the tick level, not against
    any one ticket, so it carries no FK target — the very reason it is a
    separate model rather than a ``DebtItem``.

    ``occurred_at`` is when the tick crashed (create-on-crash records at the
    crash instant), and is the field the query-time dedup predicate
    (``TickFailureRepo.recorded_since``) compares against. ``failure_signature``
    is the free-form grouping key that predicate dedups on (no enum — a crash
    has no fixed taxonomy); ``detail`` is the error text. There is no
    ``updated_at`` and no ``status``: the row is never mutated.
    """

    id: UUID
    occurred_at: datetime
    failure_signature: str
    detail: str
    created_by_type: ActorType
    created_by_id: str
