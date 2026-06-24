"""Staged-generation progress events (additive observability).

The staged path (ADR-0010) runs minutes-long across stage 1 (epics) → stage 2
(tickets, one call per epic) → stage 3 (dependencies) → assembly, and is
otherwise silent until the final diff. This module defines the typed event the
generator emits at each stage boundary, through an OPTIONAL injected callback,
so the CLI — which owns presentation (the spine is ``cli > planning``) — can
render progress to stderr without ``atlas.planning`` learning about IO.

Semantic, not cosmetic: the event names the stage and (for the per-epic ticket
stage) the 1-based position, total, epic title, and retry attempt. The display
text ("Stage 2/3 …") is the CLI's; the event carries no rendering. A ``None``
callback is a no-op, so every existing caller and test is unaffected.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

# Stage values — module constants, referenced by both emitter and renderer so
# the two cannot drift on a string literal.
STAGE_EPICS = "epics"
STAGE_TICKETS = "tickets"
STAGE_DEPENDENCIES = "dependencies"
STAGE_ASSEMBLY = "assembly"


@dataclass(frozen=True)
class PlanProgress:
    """One staged-generation progress event. Semantic, not cosmetic: it names
    the stage and (for the per-epic ticket stage) the 1-based position, total,
    and epic title. The CLI maps these to display text ('Stage 2/3 …')."""

    stage: str
    index: int | None = None  # 1-based position within the stage (tickets)
    total: int | None = None  # total in the stage (epic count, for tickets)
    detail: str | None = None  # human label, e.g. the epic title
    attempt: int | None = None  # retry attempt; 0/None = first try
    reason: str | None = None  # on a retry, the prior attempt's grazed field(s)


ProgressCallback = Callable[[PlanProgress], None]
