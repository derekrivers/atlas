"""DebtItem model (ATLAS-116), per data-model-and-schemas.md §6.1 and
pm-engine-and-linear-sync.md "Anomaly and dwell detection".

A delivery-anomaly observation: the PM Engine appends ONE ROW PER
OBSERVATION (decision D1). Append-only and system-written
(``created_by_type = system``); it is an operational record (ADR-0006
§2), NOT evidence — so it carries no EvidenceStatus, no trust tier, and
no PENDING cap (decision D2). It must not import or reuse the Evidence
status machinery.

Contract only: append-only enforcement lives in the repository layer
(DebtItemRepo), mirroring Evidence — the model does not police mutation
(decision D4). Recurrence is a pure query-time predicate over these rows
(DebtItemRepo.recurring), never stored on the row and never a creation
gate (decision D3).
"""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel

from atlas.core.enums import ActorType


class AnomalyType(StrEnum):
    """Class of delivery anomaly a DebtItem records (data-model §6.1;
    pm-engine-and-linear-sync.md "Anomaly and dwell detection").

    The three observation classes the PM Engine appends one row per. A
    model-specific enum, defined with its model like EvidenceType — not a
    §2 shared type — and used only by DebtItem and DebtItemRepo.
    """

    OUT_OF_OWNERSHIP_TRANSITION = "out_of_ownership_transition"
    REVIEW_CYCLE = "review_cycle"
    DWELL_BREACH = "dwell_breach"


class DebtItem(BaseModel):
    """One observed delivery anomaly (data-model §6.1).

    Append-only. Unlike Evidence's deliberately FK-less ``ticket_id`` (an
    agent run may be reconstructed after the fact, so evidence can precede
    its run row), a delivery anomaly is always observed by the PM Engine
    against an existing synced Atlas ticket — the observation never
    precedes the ticket — so ``ticket_id`` is required and FK-backed.

    ``observed_at`` is when the anomaly occurred; ``created_at`` is when
    the row was written. There is no ``updated_at`` and no ``status``:
    severity and recurrence derive by query, never by mutation.
    """

    id: UUID
    product_id: UUID
    ticket_id: UUID
    anomaly_type: AnomalyType
    summary: str
    observed_at: datetime
    created_by_type: ActorType
    created_by_id: str
    created_at: datetime
