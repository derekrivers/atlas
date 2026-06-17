"""Critical path analysis (ATLAS-35), per docs/atlas/dependency-engine.md
"Critical path".

The longest effort-weighted dependency chain through the non-terminal ticket
subgraph, computed over ATLAS-31's projected, ATLAS-40-validated DiGraph. The
PM Engine consumes it for sequencing hints; it is ADVISORY and NEVER gates
dispatch (this module wires into no gate). Pure, deterministic computation:
ZERO model/API calls and ZERO storage queries — the graph IS the input.

It reads only the node attributes ATLAS-31 already set — ``node_type``,
``present``, ``status`` (the string ``ticket.status.value``), ``priority``,
and ``estimated_effort`` (as stored — nullable until ATLAS-32 populates it) —
and the ``dependency_type`` edge attribute. A null ``estimated_effort`` is
weighted as ``1`` at COMPUTE time only; the node's stored value is never
mutated (D1 — effort population is ATLAS-32's, not this ticket's).

Scope of the subgraph (the spec's "tickets not in a terminal status"):

- Only present ticket nodes whose ``status`` is NOT in ATLAS-40's
  :data:`~atlas.dependencies.validation.TERMINAL_STATUSES` participate — the
  terminal set is REUSED, never redefined (single source). A done/rejected
  ticket on a chain therefore never appears in the path, and its effort is
  never summed.
- Only ``depends_on`` edges between two such tickets form the chain; edges to
  ADRs, epics, components, or terminal tickets are not execution links.

Edge direction (the load-bearing decision). ``A -> B`` means **A depends_on
B**, so B executes before A; execution order is the reverse-topological order
of the ``depends_on`` subgraph. The critical path is the longest chain in
EXECUTION order, so the longest-path DP runs over the EXECUTION DAG ``H``,
which has an edge ``B -> A`` for every projected ``depends_on`` edge
``A -> B`` between two included tickets. A linear chain ``A depends_on B
depends_on C`` (efforts 5, 3, 2) yields the path ``[C, B, A]`` with cumulative
effort ``[2, 5, 10]`` — C first because nothing precedes it — not the reversed
``[A, B, C]``.

``nx.dag_longest_path`` is deliberately NOT used: it weights edges, but the
weight here is per-node (``estimated_effort``), and it cannot express the
three-level tie-break.

Tie-break (exact spec order). Among paths of equal total effort the winner is
decided, at the first node where two paths diverge (lexicographic from the
execution root), by: greater downstream dependent count, then higher
priority, then key order. This is composed into a single strict total-ordering
key (:func:`_path_sort_key`, smaller = better), so no tie is ever left
unbroken and the result is fully deterministic:

- "downstream dependent count" is the number of tickets that DIRECTLY
  ``depends_on`` the ticket (its ``depends_on`` in-edges from present ticket
  nodes), counted over the FULL projected graph regardless of status — a
  stable structural property.
- "higher priority" is the greater ``priority`` integer; a lower priority
  integer sorts last.
- "key order" is ascending key, the final deterministic discriminator.

Results are typed (:class:`CriticalPath`), mirroring ATLAS-34's
:class:`~atlas.dependencies.readiness.ReadinessResult` idiom: an ordered list
of :class:`CriticalPathStep` carrying each ticket key, the effort weighted for
it (null -> 1), and the cumulative effort at that step. An empty non-terminal
subgraph returns an empty :class:`CriticalPath` (``total_effort == 0``), not
an error.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import networkx as nx

from atlas.core.models.dependency import DependencyType
from atlas.dependencies.validation import TERMINAL_STATUSES

# Only depends_on edges impose execution ordering; the chain traverses them.
_DEPENDS_ON = DependencyType.DEPENDS_ON.value
# A null estimated_effort counts as 1 at compute time (the node is untouched).
_NULL_EFFORT_WEIGHT = 1


@dataclass(frozen=True)
class CriticalPathStep:
    """One ticket on the critical path. ``effort`` is the weight used for this
    node — its stored ``estimated_effort``, or :data:`_NULL_EFFORT_WEIGHT`
    when that is null — and ``cumulative_effort`` is the running sum in
    execution order up to and including this step."""

    key: str
    effort: int
    cumulative_effort: int


@dataclass(frozen=True)
class CriticalPath:
    """The longest effort-weighted execution chain through the non-terminal
    ticket subgraph: ordered :class:`CriticalPathStep` s with cumulative
    effort. Empty when the subgraph has no non-terminal tickets."""

    steps: tuple[CriticalPathStep, ...] = field(default_factory=tuple)

    @property
    def keys(self) -> tuple[str, ...]:
        """The ordered ticket keys (execution order)."""
        return tuple(step.key for step in self.steps)

    @property
    def total_effort(self) -> int:
        """Cumulative effort of the whole path (0 for an empty path)."""
        return self.steps[-1].cumulative_effort if self.steps else 0

    def __len__(self) -> int:
        return len(self.steps)


def _effort(graph: nx.DiGraph[str], key: str) -> int:
    """The weight for a node: its stored ``estimated_effort``, or
    :data:`_NULL_EFFORT_WEIGHT` when null. Reads the node; never mutates it."""
    effort = graph.nodes[key].get("estimated_effort")
    return _NULL_EFFORT_WEIGHT if effort is None else effort


def _is_included(graph: nx.DiGraph[str], key: str) -> bool:
    """A node is in the critical-path subgraph iff it is a present ticket in a
    non-terminal status (terminal set REUSED from ATLAS-40)."""
    data = graph.nodes[key]
    return (
        data.get("node_type") == "ticket"
        and data.get("present", True)
        and data.get("status") not in TERMINAL_STATUSES
    )


def _dependent_counts(graph: nx.DiGraph[str]) -> dict[str, int]:
    """Direct downstream-dependent count per present ticket node: how many
    tickets DIRECTLY ``depends_on`` it, over the FULL graph regardless of
    status. A stable structural property used only by the tie-break."""
    counts: dict[str, int] = {}
    for source, target, dep_type in graph.edges(data="dependency_type"):
        if dep_type != _DEPENDS_ON:
            continue
        source_data = graph.nodes[source]
        target_data = graph.nodes[target]
        if source_data.get("node_type") != "ticket" or not source_data.get(
            "present", True
        ):
            continue
        if target_data.get("node_type") != "ticket" or not target_data.get(
            "present", True
        ):
            continue
        counts[target] = counts.get(target, 0) + 1
    return counts


def critical_path(graph: nx.DiGraph[str]) -> CriticalPath:
    """The critical path over ``graph``'s non-terminal ticket subgraph.

    Longest path weighted by ``estimated_effort`` (null -> 1), in EXECUTION
    order, with the full three-level tie-break in spec order. Reads ATLAS-31's
    node/edge attributes only; never touches storage. Returns a typed
    :class:`CriticalPath` — empty when no non-terminal ticket exists.

    Precondition (mirroring readiness): the graph is already ATLAS-40
    validated, so the ``depends_on`` subgraph is acyclic and the execution DAG
    is topologically sortable.
    """
    included = [key for key in graph.nodes if _is_included(graph, key)]
    if not included:
        return CriticalPath()

    dependent_counts = _dependent_counts(graph)

    # Execution DAG H: edge B -> A for every projected depends_on edge A -> B
    # between two included tickets, so a path through H follows execution
    # order. Acyclic because ATLAS-40 validated the depends_on subgraph.
    execution: nx.DiGraph[str] = nx.DiGraph()
    execution.add_nodes_from(included)
    for source, target, dep_type in graph.edges(data="dependency_type"):
        if dep_type != _DEPENDS_ON:
            continue
        if source in execution and target in execution:
            execution.add_edge(target, source)

    def node_sort_key(key: str) -> tuple[int, int, str]:
        # Smaller = better: greater dependent count, then higher priority,
        # then ascending key — the spec tie-break, negated where "greater"
        # should win so Python's min() selects it.
        data = graph.nodes[key]
        return (-dependent_counts.get(key, 0), -data["priority"], key)

    def path_sort_key(path: list[str]) -> tuple[int, tuple[tuple[int, int, str], ...]]:
        # Primary: longest total effort (negated so min picks the longest).
        # Then the per-node tie-break keys in execution order, compared
        # lexicographically from the root. Node keys are unique, so distinct
        # paths never share a sort key — the order is strict and total.
        total = sum(_effort(graph, node) for node in path)
        return (-total, tuple(node_sort_key(node) for node in path))

    # Longest-path DP over the execution (topological) order: the best path
    # ENDING at each node is itself, or the best path ending at a predecessor
    # extended by this node. Optimal for additive positive weights.
    best: dict[str, list[str]] = {}
    for node in nx.topological_sort(execution):
        candidates = [[node]]
        for predecessor in execution.predecessors(node):
            candidates.append(best[predecessor] + [node])
        best[node] = min(candidates, key=path_sort_key)

    winner = min(best.values(), key=path_sort_key)

    steps: list[CriticalPathStep] = []
    cumulative = 0
    for node in winner:
        effort = _effort(graph, node)
        cumulative += effort
        steps.append(CriticalPathStep(node, effort, cumulative))
    return CriticalPath(tuple(steps))
