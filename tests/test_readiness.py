"""ATLAS-34: the readiness predicate over ATLAS-31's projection.

Deterministic and pure — ZERO model calls, ZERO storage queries. Every one
of the five conditions has a falsifiable fixture that proves it fires in the
right place: a fully-satisfied ticket is READY; a not-done depends_on
ticket, a not-accepted ADR, zero acceptance criteria, a dangling target, and
a wrong status each make a ticket not-ready with a NAMED reason. The
dangling case asserts BOTH behaviours of the gap-2 seam — readiness reports
not-ready AND ATLAS-40's validate_graph raises on the same graph — so it is
never silently ready and never a crash. ready_tickets returns the correct
set over a mixed graph.
"""

from uuid import uuid4

import pytest
from test_models_validation import adr_kwargs, dependency_kwargs, ticket_kwargs

from atlas.core.models import ArchitectureDecisionRecord, Ticket, TicketDependency
from atlas.dependencies import (
    GraphValidationFailed,
    NotReadyCode,
    NotReadyReason,
    ReadinessResult,
    adr_key,
    is_ready,
    project_graph,
    ready_tickets,
    validate_graph,
)
from atlas.dependencies.readiness import READY_STATUSES


def make_ticket(key: str, *, criteria: int = 1, **overrides: object) -> Ticket:
    """A ticket with ``criteria`` acceptance criteria (default 1, so the
    only-condition-4 lever is explicit) and a planned status unless
    overridden."""
    base = {
        "id": uuid4(),
        "key": key,
        "status": "planned",
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
    return TicketDependency(
        **dependency_kwargs()
        | {
            "id": uuid4(),
            "source_ticket_id": source.id,
            "target_entity_type": target_type,
            "target_entity_id": target_id,
        }
    )


def _codes(result: ReadinessResult) -> set[NotReadyCode]:
    return {reason.code for reason in result.reasons}


def test_all_conditions_met_is_ready() -> None:
    # Deps done + an accepted ADR + >=1 criterion + planned status.
    dep = make_ticket("ATLAS-2", status="done")
    adr = make_adr(6, status="accepted")
    ticket = make_ticket("ATLAS-1", status="planned", criteria=2)
    graph = project_graph(
        [ticket, dep],
        [],
        [adr],
        [depends_on(ticket, dep.id), depends_on(ticket, adr.id, "adr")],
    )

    result = is_ready(graph, "ATLAS-1")
    assert result.ready is True
    assert result.reasons == ()


def test_backlog_status_is_eligible() -> None:
    # Condition 1's other accepted value: backlog, not just planned.
    assert "backlog" in READY_STATUSES
    ticket = make_ticket("ATLAS-1", status="backlog")
    graph = project_graph([ticket], [], [], [])
    assert is_ready(graph, "ATLAS-1").ready is True


def test_wrong_status_is_not_ready() -> None:
    ticket = make_ticket("ATLAS-1", status="in_progress")
    graph = project_graph([ticket], [], [], [])

    result = is_ready(graph, "ATLAS-1")
    assert result.ready is False
    assert _codes(result) == {NotReadyCode.WRONG_STATUS}
    assert result.reasons[0].status == "in_progress"


def test_depends_on_ticket_not_done_is_not_ready() -> None:
    dep = make_ticket("ATLAS-2", status="planned")  # not done
    ticket = make_ticket("ATLAS-1", status="planned")
    graph = project_graph([ticket, dep], [], [], [depends_on(ticket, dep.id)])

    result = is_ready(graph, "ATLAS-1")
    assert result.ready is False
    assert _codes(result) == {NotReadyCode.DEPENDENCY_NOT_DONE}
    # The reason NAMES the offending dependency, not just "not ready".
    assert result.reasons[0].target == "ATLAS-2"
    assert result.reasons[0].status == "planned"


def test_depends_on_adr_not_accepted_is_not_ready() -> None:
    adr = make_adr(6, status="proposed")  # not accepted
    ticket = make_ticket("ATLAS-1", status="planned")
    graph = project_graph([ticket], [], [adr], [depends_on(ticket, adr.id, "adr")])

    result = is_ready(graph, "ATLAS-1")
    assert result.ready is False
    assert _codes(result) == {NotReadyCode.ADR_NOT_ACCEPTED}
    assert result.reasons[0].target == adr_key(6)
    assert result.reasons[0].status == "proposed"


def test_node_type_selects_done_vs_accepted() -> None:
    # A done ticket target and an accepted ADR target both satisfy: the rule
    # applied is chosen by node_type, not a single status check. Swapping the
    # statuses (ticket accepted-shaped / adr done-shaped) would not exist as
    # valid enum values, so the asymmetry is what proves node_type drives it:
    # the same "done" ticket passes while a "done"-status ADR would not.
    done_ticket = make_ticket("ATLAS-2", status="done")
    accepted_adr = make_adr(6, status="accepted")
    ticket = make_ticket("ATLAS-1", status="planned")
    graph = project_graph(
        [ticket, done_ticket],
        [],
        [accepted_adr],
        [
            depends_on(ticket, done_ticket.id),
            depends_on(ticket, accepted_adr.id, "adr"),
        ],
    )
    assert is_ready(graph, "ATLAS-1").ready is True

    # An ADR in "rejected" (a real ADRStatus) is not "accepted" -> not ready,
    # via the adr branch — distinct from the ticket-done branch.
    rejected_adr = make_adr(7, status="rejected")
    ticket2 = make_ticket("ATLAS-3", status="planned")
    graph2 = project_graph(
        [ticket2], [], [rejected_adr], [depends_on(ticket2, rejected_adr.id, "adr")]
    )
    result = is_ready(graph2, "ATLAS-3")
    assert _codes(result) == {NotReadyCode.ADR_NOT_ACCEPTED}


def test_zero_acceptance_criteria_is_not_ready() -> None:
    # Condition 4 is read from acceptance_criteria_count (ATLAS-31), never a
    # storage re-query.
    ticket = make_ticket("ATLAS-1", status="planned", criteria=0)
    graph = project_graph([ticket], [], [], [])
    assert graph.nodes["ATLAS-1"]["acceptance_criteria_count"] == 0

    result = is_ready(graph, "ATLAS-1")
    assert result.ready is False
    assert _codes(result) == {NotReadyCode.NO_ACCEPTANCE_CRITERIA}


def test_dangling_target_is_not_ready_and_validate_raises() -> None:
    # Gap 2, both sides of the seam: ATLAS-31 represents the absent target as
    # present=False; readiness REPORTS not-ready-with-reason (never silently
    # ready, never a crash) AND ATLAS-40's validate_graph RAISES on it.
    missing = uuid4()
    ticket = make_ticket("ATLAS-1", status="planned")
    graph = project_graph([ticket], [], [], [depends_on(ticket, missing)])

    assert graph.nodes[str(missing)]["present"] is False

    result = is_ready(graph, "ATLAS-1")
    assert result.ready is False
    assert _codes(result) == {NotReadyCode.DANGLING_TARGET}
    assert result.reasons[0].target == str(missing)

    # The same graph fails ATLAS-40 validation — the dangling target is a
    # hard error, not a silent skip.
    with pytest.raises(GraphValidationFailed):
        validate_graph(graph)


def test_ready_property_cannot_contradict_reasons() -> None:
    # The invariant: ready is DERIVED from reasons (not an independent
    # field), so a result with any reason is never ready and a result with
    # none always is — the booleans can't disagree with the conditions.
    with_reason = ReadinessResult(
        "ATLAS-1", (NotReadyReason(NotReadyCode.WRONG_STATUS, "bad status"),)
    )
    assert with_reason.ready is False
    assert ReadinessResult("ATLAS-2").ready is True


def test_multiple_failures_collected() -> None:
    # Collect-all (mirrors ATLAS-40): a single ticket failing several
    # conditions surfaces every reason, not the first.
    dep = make_ticket("ATLAS-2", status="planned")  # not done
    # bad status + no criteria
    ticket = make_ticket("ATLAS-1", status="in_progress", criteria=0)
    graph = project_graph([ticket, dep], [], [], [depends_on(ticket, dep.id)])

    result = is_ready(graph, "ATLAS-1")
    assert result.ready is False
    assert _codes(result) == {
        NotReadyCode.WRONG_STATUS,
        NotReadyCode.DEPENDENCY_NOT_DONE,
        NotReadyCode.NO_ACCEPTANCE_CRITERIA,
    }


def test_ready_tickets_returns_correct_set() -> None:
    # A mixed graph: ready, blocked-by-dep, wrong-status, no-criteria.
    done = make_ticket("ATLAS-1", status="done")  # terminal, not eligible
    ready = make_ticket("ATLAS-2", status="planned")  # all met
    blocked = make_ticket("ATLAS-3", status="planned")  # dep not done
    # A planned leaf with a criterion: ready in its own right (which is
    # exactly why ATLAS-3, which depends on it, is blocked).
    pending_dep = make_ticket("ATLAS-4", status="planned")
    wrong_status = make_ticket("ATLAS-5", status="in_progress")
    no_criteria = make_ticket("ATLAS-6", status="backlog", criteria=0)
    also_ready = make_ticket("ATLAS-7", status="backlog")

    graph = project_graph(
        [done, ready, blocked, pending_dep, wrong_status, no_criteria, also_ready],
        [],
        [],
        [depends_on(blocked, pending_dep.id)],
    )

    results = ready_tickets(graph)
    # done (terminal), ATLAS-3 (blocked dep), ATLAS-5 (status), ATLAS-6 (no
    # criteria) excluded; the three eligible ones returned, key-ordered.
    assert [r.key for r in results] == ["ATLAS-2", "ATLAS-4", "ATLAS-7"]
    assert all(r.ready for r in results)


def test_is_ready_rejects_unknown_or_non_ticket_node() -> None:
    adr = make_adr(6)
    ticket = make_ticket("ATLAS-1", status="planned")
    graph = project_graph([ticket], [], [adr], [])

    with pytest.raises(ValueError):
        is_ready(graph, "ATLAS-404")  # absent
    with pytest.raises(ValueError):
        is_ready(graph, adr_key(6))  # present, but an ADR not a ticket
