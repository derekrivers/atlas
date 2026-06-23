"""Related-ticket retrieval (ATLAS-54), per docs/atlas/context-renderer.md
"Retrieval rules (v1)" rule 3.

Selects the tickets related to one ticket for the ContextPack's
``related_tickets`` reference list. Two ``depends_on`` sources off ATLAS-31's
projected graph, direct (1 hop) only:

1. **Dependency targets** — the tickets this ticket ``depends_on`` (its
   out-edges). The ticket's own execution dependencies, so they rank first.
2. **Dependents** — the tickets that ``depends_on`` this ticket (its in-edges).
   Work waiting on this ticket; weaker execution context, so it fills the
   slots targets leave.

Deliberate contrast with ATLAS-51 (ADR rule 2): that retriever took dependency
targets of *any* edge type, but rule 3 is ``depends_on`` SPECIFICALLY —
``relates_to`` and ``implements`` ticket edges are excluded. The graph stores
all three under ``dependency_type``, so the filter is load-bearing.

Pure computation: ZERO model/API calls and ZERO storage queries. The graph is
the sole input (it already carries each ticket node's ``entity_id``, ``key``,
``node_type`` and ``present`` — no body text); building it is the caller's job
(ATLAS-56), so this stays a pure function exercised directly in tests. It
selects *which* tickets; rendering each as key/title/status/objective is
ATLAS-56's job.

Each match carries the ticket's UUID from the start (the ATLAS-51 amendment
lesson — ``ContextPack.related_tickets`` is ``list[UUID]``), so ATLAS-56 does
``[m.ticket_id for m in select_related_tickets(...)]`` with no re-amendment.

Ordering is a total order so callers and tests can assert an exact list: the
design specifies no ranking for rule 3, so an imposed deterministic v1 order —
targets first, then dependents, each by ascending node ``key`` — is documented
here. Key ordering is lexicographic on the node key: a deterministic v1 order,
not a relevance score (a numeric-suffix natural sort is a later nicety if keys
are ever guaranteed ``PREFIX-N``). The selection is capped (default 8, rule 3);
targets ahead of dependents at the cap reflects that the ticket's own
dependencies are the more direct execution context.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

import networkx as nx

from atlas.core.models.dependency import DependencyType
from atlas.core.models.ticket import Ticket

# The design's cap on related tickets (context-renderer.md rule 3: "Cap: 8").
DEFAULT_RELATED_CAP = 8

# Only this edge type counts (the rule-3 filter that distinguishes this from
# ATLAS-51's any-edge ADR selection). Enum-derived, never a bare string.
_DEPENDS_ON = DependencyType.DEPENDS_ON.value


class RelatedTicketSource(StrEnum):
    """Why a ticket was selected, so the caller can explain provenance and
    rank — a ticket this one depends on (TARGET) vs. one that depends on this
    one (DEPENDENT)."""

    TARGET = "target"
    DEPENDENT = "dependent"


@dataclass(frozen=True)
class RelatedTicket:
    """One selected related ticket. ``ticket_id`` is the stored ticket's UUID —
    the identity the ContextPack records in ``related_tickets``; ``key`` is the
    node key (``ATLAS-12``); ``source`` records which direction selected it."""

    ticket_id: UUID
    key: str
    source: RelatedTicketSource


def select_related_tickets(
    graph: nx.DiGraph[str],
    ticket: Ticket,
    *,
    cap: int = DEFAULT_RELATED_CAP,
) -> list[RelatedTicket]:
    """Select up to ``cap`` related tickets for ``ticket`` from its
    ``depends_on`` targets and dependents in ``graph``.

    ``ticket.key`` must name a present ticket node in ``graph`` — that is a
    precondition, not a retrieval condition, so a missing key or a non-ticket
    node raises :class:`ValueError` (mirroring ATLAS-34's readiness contract
    and ATLAS-51's ADR retriever).

    Reads only ATLAS-31's node/edge attributes; never touches storage.
    Neighbours that are absent (``present=False``) cannot be rendered and are
    skipped — ATLAS-40's ``validate_graph`` is the gate that raises on a
    dangling target, not this retriever. A self-``depends_on`` edge (A -> A) is
    likewise skipped, not raised on: a ticket is never its own related ticket,
    and the self-edge integrity raise is ``validate_graph``'s job (its
    ``_self_edges`` rule), not this retriever's.
    """
    if ticket.key not in graph:
        raise ValueError(f"no such node in graph: {ticket.key!r}")
    ticket_data = graph.nodes[ticket.key]
    if ticket_data.get("node_type") != "ticket" or not ticket_data.get("present", True):
        raise ValueError(f"not a present ticket node: {ticket.key!r}")

    # Dedup by ticket_id across both directions; a mutual depends_on pair (the
    # other ticket is both a target and a dependent) is emitted once, as the
    # stronger TARGET source — so the target loop runs first and claims the id.
    seen_ids: set[UUID] = set()

    # Source 1: dependency targets. Edge A -> B means A depends_on B, so the
    # ticket's targets are its out-edges with a depends_on type to a present
    # ticket node. Self-edges (target == ticket.key) are skipped.
    targets: list[RelatedTicket] = []
    for _source, target, dep_type in graph.out_edges(
        ticket.key, data="dependency_type"
    ):
        if dep_type != _DEPENDS_ON:
            continue
        if target == ticket.key:
            continue
        target_data = graph.nodes[target]
        if target_data.get("node_type") != "ticket" or not target_data.get(
            "present", True
        ):
            continue
        ticket_id = target_data["entity_id"]
        if ticket_id in seen_ids:
            continue
        seen_ids.add(ticket_id)
        targets.append(
            RelatedTicket(ticket_id, target_data["key"], RelatedTicketSource.TARGET)
        )

    # Source 2: dependents. The tickets that depends_on this one are its
    # in-edges with a depends_on type from a present ticket node. Same
    # self-edge skip; an id already claimed as a target is not re-added.
    dependents: list[RelatedTicket] = []
    for source, _target, dep_type in graph.in_edges(ticket.key, data="dependency_type"):
        if dep_type != _DEPENDS_ON:
            continue
        if source == ticket.key:
            continue
        source_data = graph.nodes[source]
        if source_data.get("node_type") != "ticket" or not source_data.get(
            "present", True
        ):
            continue
        ticket_id = source_data["entity_id"]
        if ticket_id in seen_ids:
            continue
        seen_ids.add(ticket_id)
        dependents.append(
            RelatedTicket(ticket_id, source_data["key"], RelatedTicketSource.DEPENDENT)
        )

    # Imposed deterministic v1 order (the design specifies none): each group by
    # ascending node key, targets ahead of dependents, then cap. Lexicographic
    # key order is a stable v1 order, not a relevance score.
    targets.sort(key=lambda match: match.key)
    dependents.sort(key=lambda match: match.key)
    return (targets + dependents)[:cap]
