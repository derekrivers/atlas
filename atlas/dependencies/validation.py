"""Graph validation (ATLAS-40), per docs/atlas/dependency-engine.md
"Validation rules".

The typed-error layer over ATLAS-31's projection. It reads the node
attributes that projection already set — ``present`` (False for the
dangling targets it left honestly visible), ``status`` (the string
``ticket.status.value``), ``node_type`` — and the ``dependency_type`` edge
attribute. It never re-queries storage: the graph IS the input.

Four rules, scoped exactly as the spec frames them:

- Acyclicity over the ``depends_on`` subgraph — only ``depends_on`` imposes
  execution ordering. Mirrors the gate-2 idiom
  (``atlas/planning/gates.py``): ``is_directed_acyclic_graph`` then
  ``find_cycle``, reporting the full cycle path.
- No self-edges; no duplicate edges (same source, target, type). Self-edges
  are an integrity rule across all edge types. "Duplicate edge" is
  structurally unrepresentable in ATLAS-31's ``DiGraph`` (a DiGraph cannot
  hold two edges between one ordered pair, and the projection further skips
  same-pair collisions) — the check is a proven-but-dormant guard for the
  ``MultiDiGraph`` future ATLAS-31 documented.
- No dangling targets — raises on the ``present=False`` nodes ATLAS-31
  represents.
- No ``depends_on`` from a ``done`` ticket to a non-terminal one
  (``rejected`` sources are exempt — see ``_terminal_dependencies``).

``validate_graph`` COLLECTS every violation and raises one
``GraphValidationFailed`` aggregate (not raise-on-first), so an operator
sees every problem at once. It is a STANDALONE function callers invoke;
``build_dependency_graph`` does not auto-validate (that would change
ATLAS-31's contract of representing dangling targets rather than raising).
The call-site contract is: validate after every build and on every
dependency mutation; ``atlas apply`` validates the projected post-apply
graph before its commit seam and refuses (no write) on any failure.
"""

from __future__ import annotations

import networkx as nx

from atlas.core.models.dependency import DependencyType
from atlas.core.models.ticket import TicketStatus
from atlas.dependencies.errors import (
    CycleError,
    DanglingTargetError,
    DuplicateEdgeError,
    GraphValidationError,
    GraphValidationFailed,
    SelfEdgeError,
    TerminalDependencyError,
)

# Terminal lifecycle states, derived from the enum (not hard-coded): a
# terminal ticket's work is closed. TicketStatus defines no separate
# cancelled state, so the set is exactly DONE and REJECTED.
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {TicketStatus.DONE.value, TicketStatus.REJECTED.value}
)

_DEPENDS_ON = DependencyType.DEPENDS_ON.value


def validate_graph(graph: nx.DiGraph[str]) -> None:
    """Validate a projected dependency graph against the four spec rules.

    Reads ATLAS-31's node/edge attributes only; never touches storage.
    Collects all violations and, if any, raises a
    :class:`GraphValidationFailed` carrying each typed violation in
    ``.violations``. Returns ``None`` on a valid graph.
    """
    violations: list[GraphValidationError] = []
    violations.extend(_self_edges(graph))
    violations.extend(_duplicate_edges(graph))
    violations.extend(_dangling_targets(graph))
    violations.extend(_cycles(graph))
    violations.extend(_terminal_dependencies(graph))
    if violations:
        raise GraphValidationFailed(violations)


def _self_edges(graph: nx.DiGraph[str]) -> list[SelfEdgeError]:
    """Any ``A -> A`` self-loop, across all edge types (integrity rule)."""
    return [SelfEdgeError(node) for node in nx.nodes_with_selfloops(graph)]


def _duplicate_edges(graph: nx.DiGraph[str]) -> list[DuplicateEdgeError]:
    """Two edges sharing (source, target, dependency_type).

    Iterating ``edges(data=...)`` yields one tuple per parallel edge on a
    ``MultiDiGraph`` and exactly one per pair on a ``DiGraph`` — so on
    ATLAS-31's current DiGraph projection this is provably a no-op, and it
    fires only if the projection becomes a MultiDiGraph.
    """
    seen: set[tuple[str, str, str]] = set()
    duplicates: list[DuplicateEdgeError] = []
    for source, target, dep_type in graph.edges(data="dependency_type"):
        triple = (source, target, str(dep_type))
        if triple in seen:
            duplicates.append(DuplicateEdgeError(source, target, str(dep_type)))
        else:
            seen.add(triple)
    return duplicates


def _dangling_targets(graph: nx.DiGraph[str]) -> list[DanglingTargetError]:
    """Edges pointing at an absent (``present=False``) node — the dangling
    targets ATLAS-31 made visible rather than dropping."""
    dangling: list[DanglingTargetError] = []
    for node, data in graph.nodes(data=True):
        if data.get("present", True):
            continue
        sources = sorted(source for source, _ in graph.in_edges(node))
        dangling.append(DanglingTargetError(node, sources))
    return dangling


def _depends_on_subgraph(graph: nx.DiGraph[str]) -> nx.DiGraph[str]:
    """A fresh DiGraph of only the ``depends_on`` edges — the sole ordering
    relation. Built rather than viewed so it collapses cleanly to a DiGraph
    even when the input is a MultiDiGraph (the duplicate-edge guard's case),
    where acyclicity ignores edge multiplicity."""
    subgraph: nx.DiGraph[str] = nx.DiGraph()
    subgraph.add_edges_from(
        (source, target)
        for source, target, dep_type in graph.edges(data="dependency_type")
        if dep_type == _DEPENDS_ON
    )
    return subgraph


def _cycles(graph: nx.DiGraph[str]) -> list[CycleError]:
    """Acyclicity over the depends_on subgraph, mirroring the gate-2 idiom
    (``is_directed_acyclic_graph`` then ``find_cycle``) with the full path."""
    subgraph = _depends_on_subgraph(graph)
    if nx.is_directed_acyclic_graph(subgraph):
        return []
    cycle = nx.find_cycle(subgraph)
    cycle_path = [edge[0] for edge in cycle] + [cycle[0][0]]
    return [CycleError(cycle_path)]


def _terminal_dependencies(
    graph: nx.DiGraph[str],
) -> list[TerminalDependencyError]:
    """A ``done`` ticket must not ``depends_on`` a non-terminal ticket:
    completed work cannot newly depend on pending work (a data error).

    ``rejected`` sources are exempt (dependency-engine.md "Validation
    rules"): rejection is a normal end for work whose prerequisites are
    unfinished, and rejected tickets are frozen to planning, so treating
    their historical edges as violations would make one rejection block
    every future apply with no sanctioned repair (observed live,
    2026-07-08). The target side still uses ``TERMINAL_STATUSES``: any
    terminal target satisfies; only which sources are policed narrowed."""
    violations: list[TerminalDependencyError] = []
    for source, target, dep_type in graph.edges(data="dependency_type"):
        if dep_type != _DEPENDS_ON:
            continue
        source_data = graph.nodes[source]
        target_data = graph.nodes[target]
        if source_data.get("node_type") != "ticket":
            continue
        if not target_data.get("present", True):
            continue  # absent target is the dangling rule's concern
        if target_data.get("node_type") != "ticket":
            continue
        source_status = source_data.get("status")
        target_status = target_data.get("status")
        if (
            source_status == TicketStatus.DONE.value
            and target_status not in TERMINAL_STATUSES
        ):
            violations.append(
                TerminalDependencyError(
                    source, str(source_status), target, str(target_status)
                )
            )
    return violations
