"""Advisory dependency-graph Mermaid view (ATLAS-37), per
docs/atlas/dependency-engine.md "CLI".

An ON-DEMAND analysis LENS rendered to stdout: the dependency graph overlaid
with readiness / blocker / critical-path state, which the static `atlas apply`
render cannot show. It is NOT the canonical docs/planning/roadmap.mmd — that
byte-stable record is `atlas apply`'s (ATLAS-27,
atlas/planning/mermaid.render_roadmap). This module writes NO file (ADR-0007:
docs/planning/ is apply's write monopoly) and never regenerates roadmap.mmd; it
returns a string the CLI prints.

It reuses the ONE implementation of the Mermaid emission primitives —
``_natural`` key ordering and ``_escape`` label quoting — by importing them from
atlas.planning.mermaid, and follows the same ``graph TD`` edge convention
(``SOURCE --> TARGET`` reads "SOURCE depends on TARGET"). Binding the lens's
ordering to render_roadmap's is deliberate: a single emission contract, no
divergent copy.

The overlay is the analysis value-add, computed from the existing Phase 3
functions and NEVER reimplemented: ``critical_path`` (ATLAS-35), ``blocked``
(ATLAS-36), ``ready_tickets`` (ATLAS-34). Each ticket gets AT MOST ONE overlay
class by a deterministic precedence: ``critical`` > ``blocked`` > ``ready`` >
none. Critical-path edges (the ``depends_on`` links between consecutive
execution steps) are emphasised as Mermaid thick links (``==>``); every other
``depends_on`` edge stays ``-->``.

Deterministic: an identical graph yields byte-identical output (fixed natural
ordering throughout). The caller validates first — :func:`render_graph` assumes
an ATLAS-40-validated graph (acyclic, no dangling targets), so it never renders
a partial view of an invalid graph.
"""

from __future__ import annotations

from dataclasses import dataclass

import networkx as nx

from atlas.core.models.dependency import DependencyType
from atlas.dependencies.blockers import blocked
from atlas.dependencies.critical_path import critical_path
from atlas.dependencies.readiness import ready_tickets
from atlas.planning.mermaid import _escape, _natural

# Only depends_on edges impose execution ordering; the lens renders them.
_DEPENDS_ON = DependencyType.DEPENDS_ON.value

# Overlay classes. Each ticket gets AT MOST ONE — the first that applies, in
# this precedence order (highest first): critical > blocked > ready > none.
OVERLAY_CRITICAL = "critical"
OVERLAY_BLOCKED = "blocked"
OVERLAY_READY = "ready"
_OVERLAY_PRECEDENCE = (OVERLAY_CRITICAL, OVERLAY_BLOCKED, OVERLAY_READY)

# Deterministic class styling, one classDef emitted per overlay present.
_CLASS_DEFS = {
    OVERLAY_CRITICAL: "classDef critical stroke:#d33,stroke-width:3px;",
    OVERLAY_BLOCKED: "classDef blocked stroke:#e69138,stroke-width:2px;",
    OVERLAY_READY: "classDef ready stroke:#38761d,stroke-width:2px;",
}

# The advisory marker (D5): deliberately UNLIKE roadmap.mmd's provenance header
# ("%% Render written only by `atlas apply` ..."), so this lens can never be
# mistaken for the canonical render or hand-saved in its place.
_ADVISORY_HEADER = (
    "%% Atlas dependency ANALYSIS view (ATLAS-37) — advisory, rendered on "
    "demand to stdout.",
    "%% NOT the canonical docs/planning/roadmap.mmd (that is `atlas apply`'s, "
    "ATLAS-27); written to no file.",
    "%% Overlays (one per ticket, by precedence): critical > blocked > ready.",
)


@dataclass(frozen=True)
class GraphNodeView:
    """One ticket node in the analysis view: its key, label title, owning
    ``epic_key`` (None when ungrouped), and the single ``overlay`` class that
    applies (None when none does)."""

    key: str
    title: str
    epic_key: str | None
    overlay: str | None


@dataclass(frozen=True)
class GraphEdgeView:
    """One ``depends_on`` edge between two present ticket nodes (``A -> B`` =
    A depends_on B), flagged ``on_critical_path`` when it is a link between
    consecutive steps of the critical path."""

    source: str
    target: str
    on_critical_path: bool


@dataclass(frozen=True)
class GraphView:
    """The overlaid analysis view: ticket nodes (with overlays) and the
    ``depends_on`` ticket->ticket edges (critical links flagged), both in
    deterministic natural-key order."""

    nodes: tuple[GraphNodeView, ...]
    edges: tuple[GraphEdgeView, ...]


def _is_present_ticket(data: dict[str, object]) -> bool:
    """A node participates iff it is a present ticket — the lens is the ticket
    execution graph (ADR/epic/absent targets are not rendered as nodes,
    mirroring render_roadmap)."""
    return data.get("node_type") == "ticket" and bool(data.get("present", True))


def analyse_graph(graph: nx.DiGraph[str]) -> GraphView:
    """Compute the overlaid view over a validated projected graph.

    Overlays come from the existing Phase 3 functions, never reimplemented;
    precedence is critical > blocked > ready > none, so each ticket carries at
    most one. Deterministic (natural key ordering). Reads node/edge attributes
    only; never touches storage and never mutates ``graph``.
    """
    critical_keys = critical_path(graph).keys
    critical_set = set(critical_keys)
    # The critical-path EDGES are the depends_on links between consecutive
    # execution steps. The path is execution order: step i+1 depends_on step i
    # (it executes after it), so the depends_on edge is (step[i+1] -> step[i]).
    critical_edges = {
        (critical_keys[i + 1], critical_keys[i]) for i in range(len(critical_keys) - 1)
    }
    ready_set = {result.key for result in ready_tickets(graph)}

    nodes: list[GraphNodeView] = []
    for key, data in graph.nodes(data=True):
        if not _is_present_ticket(data):
            continue
        # Precedence: critical first (so a critical ticket is never reported as
        # blocked or ready even though it usually is both); then blocked; then
        # ready; else no overlay.
        if key in critical_set:
            overlay: str | None = OVERLAY_CRITICAL
        elif blocked(graph, key).is_blocked:
            overlay = OVERLAY_BLOCKED
        elif key in ready_set:
            overlay = OVERLAY_READY
        else:
            overlay = None
        nodes.append(
            GraphNodeView(
                key=key,
                title=str(data.get("title", "")),
                epic_key=data.get("epic_key"),
                overlay=overlay,
            )
        )
    nodes.sort(key=lambda node: _natural(node.key))

    edges: list[GraphEdgeView] = []
    for source, target, dep_type in graph.edges(data="dependency_type"):
        if dep_type != _DEPENDS_ON:
            continue
        if not _is_present_ticket(graph.nodes[source]):
            continue
        if not _is_present_ticket(graph.nodes[target]):
            continue
        edges.append(GraphEdgeView(source, target, (source, target) in critical_edges))
    edges.sort(key=lambda edge: (_natural(edge.source), _natural(edge.target)))

    return GraphView(tuple(nodes), tuple(edges))


def render_graph(graph: nx.DiGraph[str]) -> str:
    """Render the advisory analysis view as deterministic Mermaid ``graph TD``.

    Returns the text (the caller prints it; this writes NO file). Identical
    graphs yield byte-identical output. Precondition: ``graph`` is already
    ATLAS-40 validated, so it has no cycle or dangling target to render around.
    """
    view = analyse_graph(graph)
    lines = [*_ADVISORY_HEADER, "graph TD"]

    # Ticket nodes grouped into epic subgraphs by epic_key (None -> loose),
    # epics in natural key order, members in natural key order — the same
    # ordering and `["..."]` label quoting as render_roadmap.
    grouped: dict[str | None, list[GraphNodeView]] = {}
    for node in view.nodes:
        grouped.setdefault(node.epic_key, []).append(node)

    for epic_key in sorted((k for k in grouped if k is not None), key=_natural):
        epic_title = (
            str(graph.nodes[epic_key].get("title", epic_key))
            if epic_key in graph
            else epic_key
        )
        lines.append(f'  subgraph {epic_key}["{_escape(epic_title)}"]')
        for node in grouped[epic_key]:
            lines.append(f'    {node.key}["{node.key} {_escape(node.title)}"]')
        lines.append("  end")
    for node in grouped.get(None, []):
        lines.append(f'  {node.key}["{node.key} {_escape(node.title)}"]')

    # depends_on edges: critical-path links thick (==>), others thin (-->).
    for edge in view.edges:
        connector = "==>" if edge.on_critical_path else "-->"
        lines.append(f"  {edge.source} {connector} {edge.target}")

    # Overlay classes: one classDef per overlay present (precedence order),
    # then one class assignment per overlay with naturally-ordered members.
    present = [
        overlay
        for overlay in _OVERLAY_PRECEDENCE
        if any(node.overlay == overlay for node in view.nodes)
    ]
    for overlay in present:
        lines.append(f"  {_CLASS_DEFS[overlay]}")
    for overlay in present:
        members = sorted(
            (node.key for node in view.nodes if node.overlay == overlay),
            key=_natural,
        )
        lines.append(f"  class {','.join(members)} {overlay};")

    return "\n".join(lines) + "\n"


def render_graph_json(graph: nx.DiGraph[str]) -> dict[str, object]:
    """The stable structured form of the analysis view for ``--json``: nodes
    (key, title, epic_key, overlay) and edges (source, target,
    on_critical_path), carrying the same overlay classification as the text."""
    view = analyse_graph(graph)
    return {
        "nodes": [
            {
                "key": node.key,
                "title": node.title,
                "epic_key": node.epic_key,
                "overlay": node.overlay,
            }
            for node in view.nodes
        ],
        "edges": [
            {
                "source": edge.source,
                "target": edge.target,
                "on_critical_path": edge.on_critical_path,
            }
            for edge in view.edges
        ],
    }
