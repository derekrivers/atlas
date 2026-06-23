"""TicketStatusTransition model (ATLAS-121), per data-model-and-schemas.md
§6.5 and pm-engine-and-linear-sync.md (the status writer ``apply_linear_status``
records one row per real transition).

A durable, append-only record of ONE real status transition: from/to status and
the instant it happened. A sibling of ``DebtItem`` and ``TickFailure`` — an
operational record (ADR-0006 §2), NOT evidence, so it carries no EvidenceStatus,
no trust tier, and no PENDING cap. It must not import or reuse the Evidence
status machinery. Unlike ``TickFailure`` (tick-level, no FK target) and like
``DebtItem``, it IS ticket-scoped and FK-backed: a transition always belongs to
an existing synced ticket, so ``ticket_id`` is required and FK-backed at the
table layer.

It coexists with ``Ticket.status_entered_at`` (ATLAS-119) — the fast,
overwritten dwell clock for the *current* episode — and replaces neither it nor
``review_cycle_count`` (ATLAS-120). The single dwell value cannot reconstruct
history; this append-only log can, so historical per-state cycle time becomes
computable (the consumer is the seeded follow-on, ATLAS-126).

Contract only: append-only enforcement lives in the repository layer
(``TicketStatusTransitionRepo``), mirroring ``DebtItem``/``TickFailure`` — the
model does not police mutation. The sole writer is ``apply_linear_status``,
which appends one row inline on its own transaction so the transition commits
atomically with the status change (a transition exists iff the status actually
changed). No other production path records a row.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from atlas.core.enums import ActorType


class TicketStatusTransition(BaseModel):
    """One recorded real status transition for a ticket (data-model §6.5).

    Append-only. Like ``DebtItem.ticket_id`` (and unlike ``TickFailure``, which
    has no FK target), ``ticket_id`` is required and FK-backed: a transition is
    always observed against an existing synced ticket.

    ``from_status`` is the status left and ``to_status`` the status entered;
    both are non-null because the sole writer records only inside the
    real-change branch, where the prior status always exists and differs from
    the new one (a set-to-same status records nothing). ``occurred_at`` is the
    transition instant (timezone-aware; naive values are rejected at the
    storage boundary). There is no ``updated_at``, no ``created_at``, and no
    ``status``: the row is never mutated.
    """

    id: UUID
    ticket_id: UUID
    from_status: str
    to_status: str
    occurred_at: datetime
    created_by_type: ActorType
    created_by_id: str
