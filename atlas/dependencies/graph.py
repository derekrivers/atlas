"""On-demand dependency-graph projection (ATLAS-31), per
docs/atlas/dependency-engine.md "Graph semantics" / "Graph projection".

The graph is a NetworkX ``DiGraph`` PROJECTED on demand from the
relational tables — never persisted; the database is authoritative
(ADR-0006). The build reads; it never writes.

Edge direction: ``A -> B`` means **A depends_on B** (B must complete
first), so execution order is the reverse topological order of the
ticket subgraph. ``blocks`` is the reverse view a later query derives —
never stored.

This module builds the projection and its node/edge model only. The
analyses that consume it — readiness (ATLAS-34), critical path
(ATLAS-35), blockers (ATLAS-36), validation (ATLAS-40), Mermaid viz
(ATLAS-37), and the ``atlas deps`` CLI (ATLAS-39) — are later Phase 3
tickets.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

import networkx as nx

from atlas.core.models import (
    ArchitectureDecisionRecord,
    Epic,
    Ticket,
    TicketDependency,
)
from atlas.storage import (
    ADRRepo,
    Database,
    EpicRepo,
    TicketDependencyRepo,
    TicketRepo,
)


def adr_key(number: int) -> str:
    """The repo-wide ADR key form (e.g. ``ADR-0006``). ADRs have no ``key``
    column — only ``number`` — so the node key is synthesised. Single
    product today (ADR-0009); a multi-product future would product-prefix."""
    return f"ADR-{number:04d}"


def project_graph(
    tickets: Sequence[Ticket],
    epics: Sequence[Epic],
    adrs: Sequence[ArchitectureDecisionRecord],
    dependencies: Sequence[TicketDependency],
) -> nx.DiGraph[str]:
    """Project applied backlog state into a dependency ``DiGraph``.

    Pure: models in, graph out, no I/O — the same inputs yield an
    identical graph. Nodes are keyed by entity key and typed; edges
    ``A -> B`` mean A depends_on B. A dependency target that resolves to
    no stored entity is represented as an absent node (``present=False``),
    never dropped and never raised on — ATLAS-40 detects those.
    """
    graph: nx.DiGraph[str] = nx.DiGraph()

    # UUID -> node key, resolving the UUID-based dependency endpoints to
    # entity keys once at build time.
    key_by_id: dict[UUID, str] = {}
    for ticket in tickets:
        key_by_id[ticket.id] = ticket.key
    for epic in epics:
        key_by_id[epic.id] = epic.key
    for adr in adrs:
        key_by_id[adr.id] = adr_key(adr.number)

    # Present nodes carry enough for the later analyses to read without
    # re-querying storage, but no more (no free-text bodies). Attributes
    # mirror data-model §4.1 GraphNode, carried as native graph attributes.
    for epic in epics:
        graph.add_node(
            epic.key,
            node_type="epic",
            key=epic.key,
            entity_id=epic.id,
            present=True,
            title=epic.title,
            status=epic.status.value,
            priority=epic.priority,
            risk_level=epic.risk_level.value,
        )
    for adr in adrs:
        graph.add_node(
            adr_key(adr.number),
            node_type="adr",
            key=adr_key(adr.number),
            entity_id=adr.id,
            present=True,
            title=adr.title,
            status=adr.status.value,
            number=adr.number,
        )
    for ticket in tickets:
        graph.add_node(
            ticket.key,
            node_type="ticket",
            key=ticket.key,
            entity_id=ticket.id,
            present=True,
            title=ticket.title,
            status=ticket.status.value,
            priority=ticket.priority,
            risk_level=ticket.risk_level.value,
            # As stored (D1): null until ATLAS-32 populates it; null-effort
            # weighting is ATLAS-35's concern, not this projection's.
            estimated_effort=ticket.estimated_effort,
            ticket_type=ticket.ticket_type.value,
            # Readiness (ATLAS-34) needs "≥1 acceptance criterion" without
            # re-reading storage; the count is enough, the bodies are not.
            acceptance_criteria_count=len(ticket.acceptance_criteria),
            epic_key=key_by_id.get(ticket.epic_id) if ticket.epic_id else None,
        )

    def resolve_endpoint(entity_type: str, entity_id: UUID) -> str:
        """The node key for a dependency endpoint. A target that resolves
        to no stored entity becomes an absent node (``present=False``)
        keyed by its UUID — the honest representation ATLAS-40 detects,
        never a silent drop."""
        existing = key_by_id.get(entity_id)
        if existing is not None:
            return existing
        node_key = str(entity_id)
        if node_key not in graph:
            graph.add_node(
                node_key,
                node_type=entity_type,
                key=None,
                entity_id=entity_id,
                present=False,
            )
        return node_key

    # All stored dependency rows are projected, each tagged with its
    # dependency_type; execution-order analyses traverse the depends_on
    # edges. Only depends_on is stored (blocks is the derived reverse view),
    # so a plain DiGraph is sufficient. Deterministic tie-break for a
    # same-pair collision: the lowest-id row wins.
    for dependency in sorted(dependencies, key=lambda dep: dep.id):
        source = resolve_endpoint("ticket", dependency.source_ticket_id)
        target = resolve_endpoint(
            dependency.target_entity_type, dependency.target_entity_id
        )
        if graph.has_edge(source, target):
            continue
        graph.add_edge(
            source,
            target,
            dependency_type=dependency.dependency_type.value,
            reason=dependency.reason,
            dependency_id=dependency.id,
        )

    return graph


def build_dependency_graph(db: Database) -> nx.DiGraph[str]:
    """Read applied state from storage and project it (ATLAS-31).

    Reads only — never writes, never persists the graph. The state read is
    the same APPLIED backlog ``atlas apply`` wrote (ADR-0006)."""
    return project_graph(
        tickets=TicketRepo(db).list(),
        epics=EpicRepo(db).list(),
        adrs=ADRRepo(db).list(),
        dependencies=TicketDependencyRepo(db).list(),
    )
