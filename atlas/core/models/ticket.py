"""Ticket model and its enums (ATLAS-12), per data-model-and-schemas.md §3.4."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field

from atlas.core.enums import ActorType, RiskLevel


class TicketStatus(StrEnum):
    """Lifecycle of a ticket (data-model §3.4)."""

    BACKLOG = "backlog"
    PLANNED = "planned"
    BLOCKED = "blocked"
    READY_FOR_AGENT = "ready_for_agent"
    IN_PROGRESS = "in_progress"
    PR_OPEN = "pr_open"
    REVIEW_REQUIRED = "review_required"
    CHANGES_REQUESTED = "changes_requested"
    DONE = "done"
    REJECTED = "rejected"
    NEEDS_HUMAN_DECISION = "needs_human_decision"


class TicketType(StrEnum):
    """Kind of work a ticket represents (data-model §3.4)."""

    FEATURE = "feature"
    BUG = "bug"
    TECH_DEBT = "tech_debt"
    SPIKE = "spike"
    DOCUMENTATION = "documentation"
    INFRASTRUCTURE = "infrastructure"
    RESEARCH = "research"


class Ticket(BaseModel):
    """The atomic unit of agent-executable work: small, scoped,
    dependency-aware, and verifiable.

    ``completed_at`` is written by ``TicketRepo.apply_linear_status`` only on a
    real transition into ``done``. That inbound observation follows the sibling
    cursor fields' discipline and never bumps ``updated_at``.
    """

    id: UUID
    product_id: UUID
    epic_id: UUID | None = None
    key: str
    title: str
    objective: str
    context: str
    status: TicketStatus
    ticket_type: TicketType
    risk_level: RiskLevel
    priority: int = Field(ge=-2147483648, le=2147483647)  # SQL INTEGER range
    relevant_docs: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    non_goals: list[str] = Field(default_factory=list)
    implementation_notes: list[str] = Field(default_factory=list)
    test_requirements: list[str] = Field(default_factory=list)
    documentation_requirements: list[str] = Field(default_factory=list)
    definition_of_done: list[str] = Field(default_factory=list)
    # Populated from Phase 3 (critical path); SQL INTEGER range.
    estimated_effort: int | None = Field(default=None, ge=-2147483648, le=2147483647)
    external_linear_id: str | None = None
    external_github_issue_id: str | None = None
    # Free-form retrieval facets (ATLAS-127): planner-populated later
    # (ATLAS-128). The Phase 5 context renderer matches ADRs and lessons to a
    # ticket partly by these; no controlled vocabulary in v1.
    tags: list[str] = Field(default_factory=list)
    component: str | None = None
    # PM-Engine sync cursor (ATLAS-42): the value of ``updated_at`` at the
    # last confirmed definition push to Linear. A definition is re-pushed
    # only while ``updated_at > linear_synced_at`` (or this is null and the
    # ticket has never synced). Written by the sync loop, never by an Atlas
    # definition edit, so stamping it cannot itself trigger a re-push.
    linear_synced_at: datetime | None = None
    # PM-Engine transition signal (ATLAS-118): the Linear state id observed on
    # the last pull. The out-of-ownership anomaly detector compares the freshly
    # fetched id against it so an unmapped state that persists across ticks logs
    # one DebtItem (a transition), not one per tick, while a genuine
    # re-occurrence (unmapped -> mapped -> unmapped) is a new transition.
    # Written only by the sync loop and, like linear_synced_at, never bumps
    # updated_at — an inbound observation must not re-push the definition.
    last_observed_linear_state_id: str | None = None
    # PM-Engine dwell clock (ATLAS-119): when the ticket entered its current
    # status. Stamped by ``apply_linear_status`` — the sole post-creation status
    # writer — only on a real status change, so it marks the start of the current
    # dwell episode. Dwell-breach detection measures ``now - status_entered_at``
    # against the per-status horizon, and it is also the per-episode dedup
    # boundary (one DWELL_BREACH per episode = none logged since this time).
    # NULL means "unknown entry time" (e.g. a ticket whose status predates this
    # field) and dwell SKIPS it rather than guessing a false breach. Written
    # only by the sync loop and, like the cursor fields above, never bumps
    # updated_at — an inbound observation must not re-push the definition.
    status_entered_at: datetime | None = None
    # PM-Engine review-cycling counter (ATLAS-120): the number of
    # ``changes_requested -> pr_open`` round trips. Incremented by
    # ``apply_linear_status`` (the sole post-creation status writer) only on that
    # specific transition; every other transition leaves it untouched. At more
    # than three round trips the step-5 review-cycling pass routes the ticket to
    # ``needs_human_decision`` via the sanctioned ``set_state``. Monotonic in v1
    # (no reset on human intervention — deferred). Like the cursor fields above
    # it is status-coupled and NEVER bumps ``updated_at``.
    review_cycle_count: int = 0
    # Learning-system extraction cursor (ATLAS-106): the last time the
    # learning extractor attempted extraction for this ticket. Recording an
    # attempt never bumps updated_at, so it cannot look like a definition edit
    # or cause a Linear re-push.
    lesson_extraction_attempted_at: datetime | None = None
    # Reconciler anchor-match pass; AT-1 traceability — every item
    # traceable to a document anchor.
    source_anchor: str
    created_by_type: ActorType
    created_by_id: str
    created_at: datetime
    updated_at: datetime
    # Delivery completion clock (ATLAS-206): stamped by
    # ``TicketRepo.apply_linear_status`` only on a real transition into ``done``.
    # Repeated ``done`` observations and ``rejected`` closures leave it
    # untouched. Like the sync/dwell cursor fields, it never bumps
    # ``updated_at``.
    completed_at: datetime | None = None
