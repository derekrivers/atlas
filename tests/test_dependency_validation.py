"""ATLAS-40: the typed-error validation layer over ATLAS-31's projection.

Deterministic and pure — ZERO model calls. Every rule has a falsifiable
seeded-defect fixture that proves the check fires in the right place: a
3-node cycle, a self-edge, a dangling (present=False) target consumed from
ATLAS-31's own projection, a done->planned terminal dependency, and the
duplicate-edge guard proven on a MultiDiGraph. A valid graph passes clean,
and a two-defect graph collects both violations into one aggregate.
"""

from typing import TypeVar
from uuid import uuid4

import networkx as nx
import pytest
from test_models_validation import dependency_kwargs, ticket_kwargs

from atlas.core.models import Ticket, TicketDependency
from atlas.dependencies import (
    CycleError,
    DanglingTargetError,
    DuplicateEdgeError,
    GraphValidationError,
    GraphValidationFailed,
    SelfEdgeError,
    TerminalDependencyError,
    project_graph,
    validate_graph,
)


def make_ticket(key: str, **overrides: object) -> Ticket:
    return Ticket(**ticket_kwargs() | {"id": uuid4(), "key": key} | overrides)


def depends_on(
    source: Ticket,
    target_id: object,
    target_type: str = "ticket",
    **overrides: object,
) -> TicketDependency:
    return TicketDependency(
        **dependency_kwargs()
        | {
            "id": uuid4(),
            "source_ticket_id": source.id,
            "target_entity_type": target_type,
            "target_entity_id": target_id,
        }
        | overrides
    )


_E = TypeVar("_E", bound=GraphValidationError)


def _only(error: GraphValidationFailed, kind: type[_E]) -> _E:
    """The single violation of ``kind`` in the aggregate (asserts exactly
    one, so a test cannot pass on an unrelated rule firing)."""
    matches = [v for v in error.violations if isinstance(v, kind)]
    assert len(matches) == 1, error.violations
    return matches[0]


def test_valid_graph_passes_clean() -> None:
    a = make_ticket("ATLAS-1", status="planned")
    b = make_ticket("ATLAS-2", status="planned")
    graph = project_graph([a, b], [], [], [depends_on(a, b.id)])
    validate_graph(graph)  # no raise


def test_cycle_raises_with_full_path() -> None:
    a = make_ticket("ATLAS-1", status="planned")
    b = make_ticket("ATLAS-2", status="planned")
    c = make_ticket("ATLAS-3", status="planned")
    deps = [depends_on(a, b.id), depends_on(b, c.id), depends_on(c, a.id)]
    graph = project_graph([a, b, c], [], [], deps)

    with pytest.raises(GraphValidationFailed) as caught:
        validate_graph(graph)
    cycle = _only(caught.value, CycleError)
    # Full path is a closed loop over the three nodes (mirrors gate-2).
    assert cycle.cycle_path[0] == cycle.cycle_path[-1]
    assert set(cycle.cycle_path) == {"ATLAS-1", "ATLAS-2", "ATLAS-3"}


def test_self_edge_raises() -> None:
    a = make_ticket("ATLAS-1", status="planned")
    graph = project_graph([a], [], [], [depends_on(a, a.id)])

    with pytest.raises(GraphValidationFailed) as caught:
        validate_graph(graph)
    assert _only(caught.value, SelfEdgeError).node == "ATLAS-1"


def test_dangling_present_false_target_raises() -> None:
    a = make_ticket("ATLAS-1", status="planned")
    missing = uuid4()
    graph = project_graph([a], [], [], [depends_on(a, missing)])

    # The seam: ATLAS-31 REPRESENTS the absent target as a present=False
    # node; ATLAS-40 RAISES on exactly that node.
    assert graph.nodes[str(missing)]["present"] is False
    with pytest.raises(GraphValidationFailed) as caught:
        validate_graph(graph)
    dangling = _only(caught.value, DanglingTargetError)
    assert dangling.target == str(missing)
    assert dangling.sources == ["ATLAS-1"]


def test_terminal_to_non_terminal_raises() -> None:
    done = make_ticket("ATLAS-1", status="done")
    planned = make_ticket("ATLAS-2", status="planned")
    graph = project_graph([done, planned], [], [], [depends_on(done, planned.id)])

    with pytest.raises(GraphValidationFailed) as caught:
        validate_graph(graph)
    terminal = _only(caught.value, TerminalDependencyError)
    assert terminal.source == "ATLAS-1"
    assert terminal.source_status == "done"
    assert terminal.target == "ATLAS-2"
    assert terminal.target_status == "planned"


def test_rejected_counts_as_terminal() -> None:
    # The terminal set comes from the enum (DONE and REJECTED), not a
    # hard-coded "done" — rejected->planned must also raise.
    rejected = make_ticket("ATLAS-1", status="rejected")
    planned = make_ticket("ATLAS-2", status="planned")
    graph = project_graph(
        [rejected, planned], [], [], [depends_on(rejected, planned.id)]
    )

    with pytest.raises(GraphValidationFailed) as caught:
        validate_graph(graph)
    assert _only(caught.value, TerminalDependencyError).source_status == "rejected"


def test_terminal_to_terminal_is_allowed() -> None:
    # done -> done is fine: completed work depending on completed work.
    a = make_ticket("ATLAS-1", status="done")
    b = make_ticket("ATLAS-2", status="done")
    graph = project_graph([a, b], [], [], [depends_on(a, b.id)])
    validate_graph(graph)  # no raise


def test_duplicate_edge_fires_on_multidigraph() -> None:
    # Gap 2: a DiGraph cannot hold a duplicate, so the guard is dormant on
    # the real projection but PROVEN here on a MultiDiGraph that holds two
    # depends_on edges over the same pair.
    multi: nx.MultiDiGraph[str] = nx.MultiDiGraph()
    multi.add_node("ATLAS-1", node_type="ticket", present=True, status="planned")
    multi.add_node("ATLAS-2", node_type="ticket", present=True, status="planned")
    multi.add_edge("ATLAS-1", "ATLAS-2", dependency_type="depends_on")
    multi.add_edge("ATLAS-1", "ATLAS-2", dependency_type="depends_on")

    with pytest.raises(GraphValidationFailed) as caught:
        validate_graph(multi)
    dup = _only(caught.value, DuplicateEdgeError)
    assert (dup.source, dup.target, dup.dependency_type) == (
        "ATLAS-1",
        "ATLAS-2",
        "depends_on",
    )


def test_duplicate_edge_dormant_on_digraph() -> None:
    # The same logic is a no-op on ATLAS-31's DiGraph: two dependency rows
    # over one pair collapse to a single edge, so no DuplicateEdgeError.
    a = make_ticket("ATLAS-1", status="planned")
    b = make_ticket("ATLAS-2", status="planned")
    graph = project_graph([a, b], [], [], [depends_on(a, b.id), depends_on(a, b.id)])
    assert graph.number_of_edges() == 1
    validate_graph(graph)  # no raise


def test_collect_all_aggregates_every_violation() -> None:
    # A self-edge AND a dangling target: one aggregate carries both typed
    # violations (collect-all, not raise-on-first).
    a = make_ticket("ATLAS-1", status="planned")
    missing = uuid4()
    deps = [depends_on(a, a.id), depends_on(a, missing)]
    graph = project_graph([a], [], [], deps)

    with pytest.raises(GraphValidationFailed) as caught:
        validate_graph(graph)
    kinds = {type(v) for v in caught.value.violations}
    assert SelfEdgeError in kinds
    assert DanglingTargetError in kinds


def test_non_depends_on_cycle_is_allowed() -> None:
    # Acyclicity is scoped to the depends_on subgraph: a relates_to cycle
    # imposes no ordering and must not raise.
    a = make_ticket("ATLAS-1", status="planned")
    b = make_ticket("ATLAS-2", status="planned")
    deps = [
        depends_on(a, b.id, dependency_type="relates_to"),
        depends_on(b, a.id, dependency_type="relates_to"),
    ]
    graph = project_graph([a, b], [], [], deps)
    validate_graph(graph)  # no raise
