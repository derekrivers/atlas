"""ATLAS-36: advisory blocker analysis over ATLAS-31's projection.

Deterministic and pure — ZERO model calls, ZERO storage queries, and the
input graph is unchanged after every call. Every behaviour names the WRONG
answer, not merely the right one:

- blocked(t): a DONE depends_on target must NOT appear; an in_progress one
  must, tagged DEPENDENCY_NOT_DONE; a dangling target must count, tagged
  DANGLING_TARGET; an unaccepted ADR counts, tagged ADR_NOT_ACCEPTED. The
  ticket's own wrong-status / no-criteria reasons must NOT leak in.
- unlocks(t): a dependent blocked by TWO deps must NOT flip when only one is
  t; the singly-blocked one must; an already-done t unlocks exactly 0; the
  input graph is asserted unchanged.
- high_risk_blockers: a medium-risk blocker must NOT appear, a high one
  blocking the same ticket must; a done blocker is absent; a dangling target
  carries no risk and is absent; the report is ordered by blocked-count desc
  then key; and — the D4 seeded defect — a DONE (terminal) dependent must NOT
  count toward a rejected high-risk target's blocked_count.
- Preconditions raise ValueError, not a result.
"""

from uuid import uuid4

import pytest
from test_models_validation import adr_kwargs, dependency_kwargs, ticket_kwargs

from atlas.core.models import ArchitectureDecisionRecord, Ticket, TicketDependency
from atlas.dependencies import (
    NotReadyCode,
    adr_key,
    blocked,
    high_risk_blockers,
    project_graph,
    unlocks,
)


def make_ticket(
    key: str,
    *,
    status: str = "planned",
    risk_level: str = "low",
    criteria: int = 1,
    **overrides: object,
) -> Ticket:
    """A planned, low-risk ticket with one acceptance criterion by default, so
    each lever (status / risk / criteria) is set explicitly per test."""
    base = {
        "id": uuid4(),
        "key": key,
        "status": status,
        "risk_level": risk_level,
        "acceptance_criteria": [f"criterion {i}" for i in range(criteria)],
    }
    return Ticket(**ticket_kwargs() | base | overrides)


def make_adr(number: int, **overrides: object) -> ArchitectureDecisionRecord:
    base = {"id": uuid4(), "number": number, "status": "accepted"}
    return ArchitectureDecisionRecord(**adr_kwargs() | base | overrides)


def depends_on(
    source: Ticket,
    target_id: object,
    target_type: str = "ticket",
) -> TicketDependency:
    """``source depends_on target`` — projected as the edge source -> target."""
    return TicketDependency(
        **dependency_kwargs()
        | {
            "id": uuid4(),
            "source_ticket_id": source.id,
            "target_entity_type": target_type,
            "target_entity_id": target_id,
        }
    )


def _targets(result: object) -> dict[str, NotReadyCode]:
    return {target.key: target.code for target in result.targets}  # type: ignore[attr-defined]


# --------------------------------------------------------------------------
# blocked(t)
# --------------------------------------------------------------------------


def test_done_dependency_not_blocking_in_progress_is() -> None:
    # The headline falsifiable pair: a DONE depends_on target must NOT appear;
    # an in_progress one MUST, tagged DEPENDENCY_NOT_DONE.
    ticket = make_ticket("ATLAS-1")
    done = make_ticket("ATLAS-2", status="done")
    pending = make_ticket("ATLAS-3", status="in_progress")
    graph = project_graph(
        [ticket, done, pending],
        [],
        [],
        [depends_on(ticket, done.id), depends_on(ticket, pending.id)],
    )

    result = blocked(graph, "ATLAS-1")
    assert _targets(result) == {"ATLAS-3": NotReadyCode.DEPENDENCY_NOT_DONE}
    assert "ATLAS-2" not in _targets(result)  # the wrong answer
    assert result.is_blocked


def test_dangling_target_counts_as_blocking() -> None:
    missing = uuid4()
    ticket = make_ticket("ATLAS-1")
    graph = project_graph([ticket], [], [], [depends_on(ticket, missing)])

    result = blocked(graph, "ATLAS-1")
    assert _targets(result) == {str(missing): NotReadyCode.DANGLING_TARGET}
    assert result.is_blocked


def test_unaccepted_adr_counts_as_blocking() -> None:
    ticket = make_ticket("ATLAS-1")
    adr = make_adr(6, status="proposed")
    graph = project_graph([ticket], [], [adr], [depends_on(ticket, adr.id, "adr")])

    result = blocked(graph, "ATLAS-1")
    assert _targets(result) == {adr_key(6): NotReadyCode.ADR_NOT_ACCEPTED}


def test_own_status_and_criteria_reasons_do_not_leak() -> None:
    # A ticket with a wrong status and zero criteria but a DONE dependency is
    # NOT blocked: those are reasons about the ticket itself, not a dependency.
    done = make_ticket("ATLAS-2", status="done")
    ticket = make_ticket("ATLAS-1", status="in_progress", criteria=0)
    graph = project_graph([ticket, done], [], [], [depends_on(ticket, done.id)])

    result = blocked(graph, "ATLAS-1")
    assert result.targets == ()
    assert not result.is_blocked


def test_blocked_targets_are_key_sorted() -> None:
    ticket = make_ticket("ATLAS-1")
    b = make_ticket("ATLAS-3", status="in_progress")
    a = make_ticket("ATLAS-2", status="in_progress")
    graph = project_graph(
        [ticket, a, b], [], [], [depends_on(ticket, b.id), depends_on(ticket, a.id)]
    )

    result = blocked(graph, "ATLAS-1")
    assert [target.key for target in result.targets] == ["ATLAS-2", "ATLAS-3"]


# --------------------------------------------------------------------------
# unlocks(t)
# --------------------------------------------------------------------------


def test_dependent_blocked_by_two_deps_does_not_flip() -> None:
    # d depends_on BOTH t and other (other in_progress). Completing t alone
    # leaves d blocked, so d must NOT be unlocked by t. A dependent blocked
    # only by t MUST be.
    t = make_ticket("ATLAS-1", status="in_progress")
    other = make_ticket("ATLAS-2", status="in_progress")
    two_dep = make_ticket("ATLAS-3")
    one_dep = make_ticket("ATLAS-4")
    graph = project_graph(
        [t, other, two_dep, one_dep],
        [],
        [],
        [
            depends_on(two_dep, t.id),
            depends_on(two_dep, other.id),
            depends_on(one_dep, t.id),
        ],
    )

    result = unlocks(graph, "ATLAS-1")
    assert result.dependents == ("ATLAS-4",)
    assert "ATLAS-3" not in result.dependents  # the wrong answer
    assert result.count == 1


def test_already_done_ticket_unlocks_zero() -> None:
    # t is already done, so its dependent is already ready: nothing flips.
    t = make_ticket("ATLAS-1", status="done")
    dependent = make_ticket("ATLAS-2")
    graph = project_graph([t, dependent], [], [], [depends_on(dependent, t.id)])

    result = unlocks(graph, "ATLAS-1")
    assert result.dependents == ()
    assert result.count == 0


def test_unlocks_does_not_mutate_input_graph() -> None:
    t = make_ticket("ATLAS-1", status="in_progress")
    dependent = make_ticket("ATLAS-2")
    graph = project_graph([t, dependent], [], [], [depends_on(dependent, t.id)])
    before = {node: dict(data) for node, data in graph.nodes(data=True)}

    result = unlocks(graph, "ATLAS-1")

    assert result.dependents == ("ATLAS-2",)  # it did unlock — the copy worked
    assert graph.nodes["ATLAS-1"]["status"] == "in_progress"  # input untouched
    after = {node: dict(data) for node, data in graph.nodes(data=True)}
    assert after == before


# --------------------------------------------------------------------------
# high_risk_blockers
# --------------------------------------------------------------------------


def test_medium_risk_absent_high_risk_present() -> None:
    # Two blockers of the same ticket: a medium-risk one must NOT appear, the
    # high-risk one MUST.
    dependent = make_ticket("ATLAS-1")
    medium = make_ticket("ATLAS-2", status="in_progress", risk_level="medium")
    high = make_ticket("ATLAS-3", status="in_progress", risk_level="high")
    graph = project_graph(
        [dependent, medium, high],
        [],
        [],
        [depends_on(dependent, medium.id), depends_on(dependent, high.id)],
    )

    report = high_risk_blockers(graph)
    assert [b.target for b in report] == ["ATLAS-3"]
    assert report[0].risk_level == "high"
    assert report[0].blocks == ("ATLAS-1",)
    assert report[0].blocked_count == 1


def test_done_high_risk_blocker_is_absent() -> None:
    # A DONE high-risk target is satisfied — it blocks nobody and is omitted.
    dependent = make_ticket("ATLAS-1")
    done_high = make_ticket("ATLAS-2", status="done", risk_level="high")
    graph = project_graph(
        [dependent, done_high], [], [], [depends_on(dependent, done_high.id)]
    )

    assert high_risk_blockers(graph) == ()


def test_critical_risk_blocker_is_reported() -> None:
    dependent = make_ticket("ATLAS-1")
    crit = make_ticket("ATLAS-2", status="in_progress", risk_level="critical")
    graph = project_graph([dependent, crit], [], [], [depends_on(dependent, crit.id)])

    report = high_risk_blockers(graph)
    assert [b.target for b in report] == ["ATLAS-2"]
    assert report[0].risk_level == "critical"


def test_done_dependent_does_not_count_toward_blocked_count() -> None:
    # D4 seeded defect: "non-terminal source" is load-bearing. A DONE (terminal)
    # ticket may validly depends_on a REJECTED (terminal, not-done) high-risk
    # target. That done dependent must NOT count: a non-terminal dependent must.
    rejected_high = make_ticket("ATLAS-1", status="rejected", risk_level="high")
    done_dependent = make_ticket("ATLAS-2", status="done")
    live_dependent = make_ticket("ATLAS-3", status="in_progress")
    graph = project_graph(
        [rejected_high, done_dependent, live_dependent],
        [],
        [],
        [
            depends_on(done_dependent, rejected_high.id),
            depends_on(live_dependent, rejected_high.id),
        ],
    )

    report = high_risk_blockers(graph)
    assert [b.target for b in report] == ["ATLAS-1"]
    # Only the non-terminal dependent counts; the done one is excluded.
    assert report[0].blocks == ("ATLAS-3",)
    assert "ATLAS-2" not in report[0].blocks  # the wrong answer
    assert report[0].blocked_count == 1


def test_report_ordered_by_blocked_count_desc_then_key() -> None:
    # blocker B2 blocks two tickets, B1 blocks one: B2 first by count. B3 also
    # blocks one: ties break by key (B1 before B3).
    d1 = make_ticket("ATLAS-1")
    d2 = make_ticket("ATLAS-2")
    b1 = make_ticket("ATLAS-10", status="in_progress", risk_level="high")
    b2 = make_ticket("ATLAS-11", status="in_progress", risk_level="critical")
    b3 = make_ticket("ATLAS-12", status="in_progress", risk_level="high")
    graph = project_graph(
        [d1, d2, b1, b2, b3],
        [],
        [],
        [
            depends_on(d1, b1.id),
            depends_on(d1, b2.id),
            depends_on(d2, b2.id),
            depends_on(d2, b3.id),
        ],
    )

    report = high_risk_blockers(graph)
    assert [b.target for b in report] == ["ATLAS-11", "ATLAS-10", "ATLAS-12"]
    assert [b.blocked_count for b in report] == [2, 1, 1]


# --------------------------------------------------------------------------
# Preconditions
# --------------------------------------------------------------------------


def test_blocked_missing_node_raises() -> None:
    graph = project_graph([make_ticket("ATLAS-1")], [], [], [])
    with pytest.raises(ValueError, match="no such node"):
        blocked(graph, "ATLAS-999")


def test_unlocks_non_ticket_node_raises() -> None:
    ticket = make_ticket("ATLAS-1")
    adr = make_adr(6)
    graph = project_graph([ticket], [], [adr], [depends_on(ticket, adr.id, "adr")])
    with pytest.raises(ValueError, match="not a present ticket node"):
        unlocks(graph, adr_key(6))
