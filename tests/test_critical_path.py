"""ATLAS-35: the critical path over ATLAS-31's projection.

Deterministic and pure — ZERO model calls, ZERO storage queries. Every
behaviour has a falsifiable fixture:

- the edge-direction worked example pins the path as ``[C, B, A]`` (execution
  order) with cumulative effort ``[2, 5, 10]`` — NOT the reversed ``[A, B,
  C]`` a wrong orientation would produce;
- a null ``estimated_effort`` is weighted as 1 (asserted) and the node is
  never mutated;
- a terminal-status ticket on a chain is ABSENT from the path and its effort
  is NOT summed;
- each of the three tie-break levels is decided by a tie that isolates it, in
  spec order;
- effort is primary over the tie-break (the heavier branch wins);
- an empty non-terminal subgraph returns an empty path, not an error;
- TERMINAL_STATUSES is the SAME object as ATLAS-40's (reused, not redefined);
- the computation is deterministic across calls.
"""

import importlib
from uuid import uuid4

from test_models_validation import dependency_kwargs, ticket_kwargs

from atlas.core.models import Ticket, TicketDependency
from atlas.dependencies import CriticalPath, critical_path, project_graph, validation


def make_ticket(
    key: str,
    *,
    status: str = "planned",
    effort: int | None = 1,
    priority: int = 0,
    **overrides: object,
) -> Ticket:
    """A planned ticket with one acceptance criterion (criteria do not gate
    the critical path; this just keeps the model valid) and an explicit
    effort/priority so each lever is set per test."""
    base = {
        "id": uuid4(),
        "key": key,
        "status": status,
        "estimated_effort": effort,
        "priority": priority,
        "acceptance_criteria": ["criterion"],
    }
    return Ticket(**ticket_kwargs() | base | overrides)


def depends_on(source: Ticket, target: Ticket) -> TicketDependency:
    """``source depends_on target`` — projected as the edge source -> target."""
    return TicketDependency(
        **dependency_kwargs()
        | {
            "id": uuid4(),
            "source_ticket_id": source.id,
            "target_entity_type": "ticket",
            "target_entity_id": target.id,
        }
    )


def test_linear_chain_execution_order_and_cumulative() -> None:
    # Gap-2 worked example. A depends_on B depends_on C, efforts 5/3/2.
    # Execution order is C, then B, then A — the REVERSE of the depends_on
    # direction. The path must be [C, B, A], never [A, B, C].
    a = make_ticket("ATLAS-1", effort=5)
    b = make_ticket("ATLAS-2", effort=3)
    c = make_ticket("ATLAS-3", effort=2)
    graph = project_graph([a, b, c], [], [], [depends_on(a, b), depends_on(b, c)])

    path = critical_path(graph)
    assert path.keys == ("ATLAS-3", "ATLAS-2", "ATLAS-1")
    assert path.keys != ("ATLAS-1", "ATLAS-2", "ATLAS-3")  # the wrong answer
    assert [step.cumulative_effort for step in path.steps] == [2, 5, 10]
    assert [step.effort for step in path.steps] == [2, 3, 5]
    assert path.total_effort == 10


def test_null_effort_counts_as_one() -> None:
    # A null estimated_effort is weighted as 1 at compute time; the stored
    # node value stays None (never mutated).
    a = make_ticket("ATLAS-1", effort=2)
    b = make_ticket("ATLAS-2", effort=None)
    graph = project_graph([a, b], [], [], [depends_on(a, b)])

    path = critical_path(graph)
    # Execution order: B (null -> 1) then A (2). Cumulative 1, then 3.
    assert path.keys == ("ATLAS-2", "ATLAS-1")
    assert path.steps[0].effort == 1
    assert [step.cumulative_effort for step in path.steps] == [1, 3]
    assert path.total_effort == 3
    # The projection's stored value is untouched.
    assert graph.nodes["ATLAS-2"]["estimated_effort"] is None


def test_terminal_ticket_excluded_from_chain() -> None:
    # A done ticket on the chain (huge effort) is excluded entirely: it must
    # be ABSENT from the path, and its effort must NOT be summed. With B
    # removed, A -> B -> C falls apart into isolated A and C.
    a = make_ticket("ATLAS-1", effort=1)
    b = make_ticket("ATLAS-2", status="done", effort=100)
    c = make_ticket("ATLAS-3", effort=1)
    graph = project_graph([a, b, c], [], [], [depends_on(a, b), depends_on(b, c)])

    path = critical_path(graph)
    assert "ATLAS-2" not in path.keys
    # Single-node path (A and C are isolated once B is gone); effort is 1,
    # proving the done node's 100 was excluded, not summed.
    assert path.total_effort == 1
    assert len(path) == 1


def test_empty_non_terminal_subgraph_returns_empty_path() -> None:
    # All tickets terminal -> empty path, total 0, no exception.
    done = make_ticket("ATLAS-1", status="done")
    rejected = make_ticket("ATLAS-2", status="rejected")
    graph = project_graph([done, rejected], [], [], [])

    path = critical_path(graph)
    assert path == CriticalPath()
    assert path.keys == ()
    assert path.total_effort == 0
    assert len(path) == 0


def test_no_tickets_returns_empty_path() -> None:
    assert critical_path(project_graph([], [], [], [])) == CriticalPath()


def test_tiebreak_level1_dependent_count() -> None:
    # Two isolated, equal-effort, equal-priority tickets -> the path is a
    # single node, and the tie is decided by downstream dependent count. A
    # done dependent gives ATLAS-1 a direct dependent (counted over the full
    # graph) without extending any non-terminal path.
    a = make_ticket("ATLAS-1", effort=5, priority=0)
    b = make_ticket("ATLAS-2", effort=5, priority=0)
    dependent = make_ticket("ATLAS-3", status="done", effort=1)
    graph = project_graph([a, b, dependent], [], [], [depends_on(dependent, a)])

    # ATLAS-1 has one dependent (ATLAS-3), ATLAS-2 has none -> ATLAS-1 wins.
    assert critical_path(graph).keys == ("ATLAS-1",)


def test_tiebreak_level2_priority() -> None:
    # Equal effort, equal dependent count (both zero) -> higher priority
    # integer wins; the lower integer sorts last.
    low = make_ticket("ATLAS-1", effort=5, priority=1)
    high = make_ticket("ATLAS-2", effort=5, priority=9)
    graph = project_graph([low, high], [], [], [])

    assert critical_path(graph).keys == ("ATLAS-2",)


def test_tiebreak_level3_key_order() -> None:
    # Equal effort, dependent count, and priority -> ascending key decides.
    first = make_ticket("ATLAS-1", effort=5, priority=3)
    second = make_ticket("ATLAS-2", effort=5, priority=3)
    graph = project_graph([second, first], [], [], [])  # insertion order swapped

    assert critical_path(graph).keys == ("ATLAS-1",)


def test_effort_is_primary_over_tiebreak() -> None:
    # A diamond: ATLAS-1 depends_on both a light-but-high-count branch and a
    # heavy branch. Effort is primary, so the heavier branch is chosen even
    # though the light branch's node has more dependents.
    head = make_ticket("ATLAS-1", effort=1)
    light = make_ticket("ATLAS-2", effort=1)  # but more dependents
    heavy = make_ticket("ATLAS-3", effort=50)
    extra = make_ticket("ATLAS-4", status="done")  # gives `light` a dependent
    graph = project_graph(
        [head, light, heavy, extra],
        [],
        [],
        [depends_on(head, light), depends_on(head, heavy), depends_on(extra, light)],
    )

    path = critical_path(graph)
    # Execution: heavy (50) then head (1); the heavy branch wins on effort.
    assert path.keys == ("ATLAS-3", "ATLAS-1")
    assert path.total_effort == 51


def test_terminal_statuses_reused_not_redefined() -> None:
    # ATLAS-40's terminal set is REUSED (same object), not a private copy.
    module = importlib.import_module("atlas.dependencies.critical_path")
    assert module.TERMINAL_STATUSES is validation.TERMINAL_STATUSES


def test_deterministic_across_calls() -> None:
    a = make_ticket("ATLAS-1", effort=5)
    b = make_ticket("ATLAS-2", effort=3)
    c = make_ticket("ATLAS-3", effort=2)
    graph = project_graph([a, b, c], [], [], [depends_on(a, b), depends_on(b, c)])
    assert critical_path(graph) == critical_path(graph)
