"""Field-ownership allow-list for the Linear boundary (ATLAS-41).

This module is the mechanical expression of the ADR-0006 ownership rule
(``pm-engine-and-linear-sync.md`` "Field ownership"): definitions flow
Atlas -> Linear, status flows Linear -> Atlas, and nothing else crosses.
It is a hard allow-list -- a field absent from the tables below is
*mechanically incapable* of crossing, not merely "not currently mapped".

It imports only ``atlas.core`` (the provider-agnostic domain) and the
boundary DTOs (under ``TYPE_CHECKING`` only, so there is no runtime import
cycle with ``client.py``). It performs no I/O: the two translation helpers
are pure functions over a Ticket / a fetched issue, and the status map is
injected, never read from the environment inside a translation call.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterable, Mapping
from typing import TYPE_CHECKING

from atlas.core.models.ticket import Ticket, TicketStatus

if TYPE_CHECKING:  # back-reference only; avoids a runtime client <-> ownership cycle
    from atlas.linear.client import LinearIssue, WorkflowState

STATE_MAP_ENV = "LINEAR_STATE_MAP"


class LinearStatusMapError(ValueError):
    """The configured Linear status map is missing, malformed, or
    contradicts the workspace's workflow states (ATLAS-41 D7/D8/D9)."""


# --- Definition direction: Atlas -> Linear ---------------------------------
#
# An explicit (linear_input_key, getter) table. The outbound payload is
# built ONLY by iterating this table, so a Ticket attribute that is not
# listed here cannot appear in a Linear payload, and `stateId` -- absent
# here -- can never be pushed Atlas -> Linear.
#
# The ADR-0006 ownership table also owns `labels` and `priority`
# (Atlas -> Linear), but neither is syncable in v1:
#   - `labels` has no `Ticket.labels` field yet.
#   - `priority` is owned but *not yet mappable*. Atlas `priority` is an
#     unconstrained signed integer (the data-model example uses 10), whereas
#     Linear `priority` is an inverted 4-value category enum (0 = None,
#     1 = Urgent ... 4 = Low). A naive clamp would both lose information and
#     invert meaning, so priority is deferred -- like labels -- until a real
#     mapping (Atlas's convention pinned, Linear's inverted 0-4 respected)
#     exists. Tracked in docs/tech-debt/debt-register.md.
# Both are *owned but not yet syncable*: present in the ADR-0006 ownership
# table, absent from the payload, never silently guessed. `description` is a
# full-spec markdown render of the ticket (objective + every populated rich
# field), composed by `render_definition_description` below; context-pack
# embedding still arrives in Phase 8 per the design doc. This stays a single
# owned field -- the render widens the *content* of `description`, not the set
# of keys over the wire.
OWNED_DEFINITION_FIELDS: tuple[tuple[str, Callable[[Ticket], object]], ...] = (
    ("title", lambda ticket: ticket.title),
    ("description", lambda ticket: render_definition_description(ticket)),
)

OWNED_LINEAR_INPUT_KEYS: frozenset[str] = frozenset(
    key for key, _ in OWNED_DEFINITION_FIELDS
)


def _bullets(items: Iterable[str]) -> str:
    """One ``- `` bullet per item, in stored order (never sorted)."""

    return "\n".join(f"- {item}" for item in items)


def render_definition_description(ticket: Ticket) -> str:
    """Compose the owned ``description`` field: a full-spec markdown render of
    the ticket's definition (objective + every populated rich field).

    Pure: same ticket -> byte-identical string, no clock, no I/O. Each section
    is omitted entirely when its source field is empty, so a ticket carrying
    only an objective renders to exactly that objective string (the render is a
    superset that degrades to the v1 summary-only behaviour). Sections follow a
    fixed order and list items render in stored order, so the output is stable
    across re-renders -- a clean-Linear-diff (cosmetic) property; correctness of
    re-push is the timestamp cursor's job, not the renderer's.

    Deliberately excludes everything Linear owns or derives and every PM-engine
    internal (status, priority, the sync cursors, external ids): spec content
    only crosses Atlas -> Linear here.
    """

    sections: list[str] = [ticket.objective]

    if ticket.context:
        sections.append(f"## Context\n\n{ticket.context}")

    list_sections: tuple[tuple[str, list[str]], ...] = (
        ("Acceptance Criteria", ticket.acceptance_criteria),
        ("Non-Goals", ticket.non_goals),
        ("Implementation Notes", ticket.implementation_notes),
        ("Test Requirements", ticket.test_requirements),
        ("Documentation Requirements", ticket.documentation_requirements),
        ("Definition of Done", ticket.definition_of_done),
    )
    for header, items in list_sections:
        if items:
            sections.append(f"## {header}\n\n{_bullets(items)}")

    meta: list[str] = []
    if ticket.relevant_docs:
        meta.append(f"- Relevant Docs: {', '.join(ticket.relevant_docs)}")
    if ticket.estimated_effort is not None:
        meta.append(f"- Estimated Effort: {ticket.estimated_effort}")
    if ticket.component:
        meta.append(f"- Component: {ticket.component}")
    if ticket.tags:
        meta.append(f"- Tags: {', '.join(ticket.tags)}")
    if meta:
        sections.append("## Meta\n\n" + "\n".join(meta))

    return "\n\n".join(sections)


def definition_payload(ticket: Ticket) -> dict[str, object]:
    """The Linear ``input`` for a definition push, built only from the owned
    table. By construction it contains no state key, so ticket *status* can
    never cross Atlas -> Linear through this boundary."""

    return {key: getter(ticket) for key, getter in OWNED_DEFINITION_FIELDS}


# --- Status direction: Linear -> Atlas -------------------------------------
#
# Permissive contradiction filter (D7). Each Atlas status accepts one or
# more Linear state *types*; validation rejects only a clear contradiction
# (e.g. a `completed` state mapped to `in_progress`, or a `started` state
# mapped to `done`). Many active statuses deliberately share `started`, so
# the filter is a contradiction check, not a rigid 1:1 oracle.
_ACCEPTED_TYPES: dict[TicketStatus, frozenset[str]] = {
    TicketStatus.BACKLOG: frozenset({"backlog", "triage"}),
    TicketStatus.PLANNED: frozenset({"backlog", "unstarted", "triage"}),
    TicketStatus.BLOCKED: frozenset({"backlog", "unstarted", "triage"}),
    TicketStatus.READY_FOR_AGENT: frozenset({"unstarted", "backlog"}),
    TicketStatus.IN_PROGRESS: frozenset({"started"}),
    TicketStatus.PR_OPEN: frozenset({"started"}),
    TicketStatus.REVIEW_REQUIRED: frozenset({"started"}),
    TicketStatus.CHANGES_REQUESTED: frozenset({"started"}),
    TicketStatus.DONE: frozenset({"completed"}),
    TicketStatus.REJECTED: frozenset({"cancelled"}),
    TicketStatus.NEEDS_HUMAN_DECISION: frozenset(
        {"started", "unstarted", "backlog", "triage"}
    ),
}


class LinearStatusMap:
    """An operator-configured ``dict[linear_state_id -> TicketStatus]``.

    The lookup key is the stable Linear state id (UUID) -- never the
    customizable name, never the coarse type. Built from the
    ``LINEAR_STATE_MAP`` JSON env var at the client boundary and injected
    into ``status_from_issue`` (dependency injection; the translation never
    reads the environment).
    """

    def __init__(self, mapping: Mapping[str, TicketStatus]) -> None:
        self._mapping: dict[str, TicketStatus] = dict(mapping)

    def __len__(self) -> int:
        return len(self._mapping)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> LinearStatusMap:
        """Parse ``LINEAR_STATE_MAP`` (JSON object of state-id -> status).

        Raises ``LinearStatusMapError`` on a missing/empty value (D8: a
        live path with no map is a loud failure, never a silent
        drop-everything), on malformed JSON, or on an unknown status value.
        """

        source = os.environ if env is None else env
        raw = source.get(STATE_MAP_ENV, "").strip()
        if not raw:
            raise LinearStatusMapError(
                f"{STATE_MAP_ENV} is not set; configure the Linear state-id -> "
                "Atlas status map (a missing map would silently disable status "
                "sync and flood anomalies)"
            )
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as error:
            raise LinearStatusMapError(
                f"{STATE_MAP_ENV} is not valid JSON: {error}"
            ) from error
        if not isinstance(parsed, dict) or not parsed:
            raise LinearStatusMapError(
                f"{STATE_MAP_ENV} must be a non-empty JSON object of state-id -> status"
            )
        mapping: dict[str, TicketStatus] = {}
        for state_id, status_value in parsed.items():
            try:
                mapping[state_id] = TicketStatus(status_value)
            except ValueError as error:
                raise LinearStatusMapError(
                    f"{STATE_MAP_ENV}[{state_id!r}] = {status_value!r} is not a "
                    "known TicketStatus"
                ) from error
        return cls(mapping)

    def validate_against_states(self, states: Iterable[WorkflowState]) -> None:
        """Load-time validation against the workspace's workflow states (D7).

        Confirms each configured id still exists (stale-map guard: rotated
        UUIDs fail loudly instead of silently dropping) and that its type is
        not contradictory with the mapped status. Permissive: several Atlas
        statuses may share one Linear type.
        """

        by_id = {state.id: state for state in states}
        for state_id, status in self._mapping.items():
            state = by_id.get(state_id)
            if state is None:
                raise LinearStatusMapError(
                    f"{STATE_MAP_ENV} maps state id {state_id!r} which does not "
                    "exist in the workspace's workflow states (stale map?)"
                )
            accepted = _ACCEPTED_TYPES[status]
            if state.type not in accepted:
                raise LinearStatusMapError(
                    f"{STATE_MAP_ENV} maps state {state_id!r} (Linear type "
                    f"{state.type!r}) to {status.value!r}, which only accepts "
                    f"Linear type(s) {sorted(accepted)}"
                )

    def status_for(self, state_id: str | None) -> TicketStatus | None:
        if state_id is None:
            return None
        return self._mapping.get(state_id)

    def state_id_for(self, status: TicketStatus) -> str:
        """The unique Linear state id mapped to ``status`` -- the inverse of the
        forward map, for the sanctioned PM-Engine promotion write (ATLAS-43).

        The forward map permits several Linear states under one Atlas status
        (many ``started`` ids -> ``in_progress``), but a promotion TARGET must
        be unambiguous: exactly one configured state must map to ``status``.
        Zero (the operator configured no such state) or more than one
        (ambiguous target) raises :class:`LinearStatusMapError`. Resolving this
        up front -- before any write -- is the load-time guard that the
        Ready-for-Agent state is configured and unique. Round-trip (D4): the id
        returned is exactly the one :meth:`status_for` maps back to ``status``,
        so ATLAS-42's next pull reads the promotion as ``status`` and no-ops."""

        matches = sorted(
            state_id for state_id, mapped in self._mapping.items() if mapped == status
        )
        if not matches:
            raise LinearStatusMapError(
                f"{STATE_MAP_ENV} configures no Linear state for {status.value!r}; "
                "the PM Engine needs exactly one state to promote into "
                "(configure a unique Ready-for-Agent state)"
            )
        if len(matches) > 1:
            raise LinearStatusMapError(
                f"{STATE_MAP_ENV} maps {len(matches)} Linear states to "
                f"{status.value!r} ({matches}); exactly one is required so the "
                "promotion target is unambiguous"
            )
        return matches[0]


def status_from_issue(
    issue: LinearIssue, status_map: LinearStatusMap
) -> TicketStatus | None:
    """Translate a fetched Linear issue into an Atlas status, or ``None``.

    Reads ONLY the issue's state id, so no Linear definition field (title,
    priority, description, ...) can cross Linear -> Atlas. An id absent from
    the configured map yields ``None`` -- dropped, not guessed (ATLAS-42
    counts and logs it; ATLAS-118 surfaces it as a reconciliation anomaly).
    """

    return status_map.status_for(issue.state_id)
