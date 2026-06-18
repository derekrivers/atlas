"""Phase 3 milestone test (Dependency Engine).

One named guard for the phase claim from the roadmap: "readiness, blockers, and
critical path computed correctly on fixture graphs including cycle and
dangling-target failures." The exhaustive, per-rule falsifiable coverage lives
in test_readiness / test_blockers / test_critical_path / test_dependency_validation;
this asserts the three computations TOGETHER on one valid fixture, plus the two
failure modes the milestone names explicitly, so the phase claim is verifiable
as a unit rather than inferred from scattered tests.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from test_models_validation import dependency_kwargs, ticket_kwargs

from atlas.core.models import Ticket, TicketDependency
from atlas.dependencies import (
    CycleError,
    DanglingTargetError,
    GraphValidationFailed,
    NotReadyCode,
    blocked,
    critical_path,
    is_ready,
    project_graph,
    ready_tickets,
    validate_graph,
)


def _ticket(key: str, *, status: str = "planned", effort: int | None = None) -> Ticket:
    return Ticket(
        **ticket_kwargs()
        | {
            "id": uuid4(),
            "key": key,
            "status": status,
            "estimated_effort": effort,
            "acceptance_criteria": ["criterion"],
        }
    )


def _depends_on(source: Ticket, target_id: UUID) -> TicketDependency:
    return TicketDependency(
        **dependency_kwargs()
        | {
            "id": uuid4(),
            "source_ticket_id": source.id,
            "target_entity_type": "ticket",
            "target_entity_id": target_id,
        }
    )


def test_milestone_valid_graph_computes_readiness_blockers_critical_path() -> None:
    # ATLAS-1 done; ATLAS-2 (eff 3) depends_on ATLAS-1 -> ready;
    # ATLAS-3 (eff 5) depends_on ATLAS-2 -> blocked. One graph, all three.
    t1 = _ticket("ATLAS-1", status="done")
    t2 = _ticket("ATLAS-2", effort=3)
    t3 = _ticket("ATLAS-3", effort=5)
    graph = project_graph(
        [t1, t2, t3], [], [], [_depends_on(t2, t1.id), _depends_on(t3, t2.id)]
    )

    validate_graph(graph)  # a valid fixture: no raise

    # Readiness: only ATLAS-2 (its sole dependency, ATLAS-1, is done).
    assert [r.key for r in ready_tickets(graph)] == ["ATLAS-2"]

    # Blockers: ATLAS-2 is not blocked; ATLAS-3 is blocked by ATLAS-2 (not done).
    assert not blocked(graph, "ATLAS-2").is_blocked
    assert [t.key for t in blocked(graph, "ATLAS-3").targets] == ["ATLAS-2"]

    # Critical path: execution order [ATLAS-2, ATLAS-3], effort 3 + 5 = 8.
    cp = critical_path(graph)
    assert cp.keys == ("ATLAS-2", "ATLAS-3")
    assert cp.total_effort == 8


def test_milestone_cycle_is_detected() -> None:
    # The wrong answer: a cycle slips through and the computations run on it.
    t1 = _ticket("ATLAS-1")
    t2 = _ticket("ATLAS-2")
    graph = project_graph(
        [t1, t2], [], [], [_depends_on(t1, t2.id), _depends_on(t2, t1.id)]
    )
    with pytest.raises(GraphValidationFailed) as caught:
        validate_graph(graph)
    assert any(isinstance(v, CycleError) for v in caught.value.violations)


def test_milestone_dangling_target_detected_and_never_silently_ready() -> None:
    # A depends_on target with no stored ticket. Validation is the hard gate;
    # readiness independently defends so the ticket is never silently ready.
    t1 = _ticket("ATLAS-1")
    graph = project_graph([t1], [], [], [_depends_on(t1, uuid4())])

    with pytest.raises(GraphValidationFailed) as caught:
        validate_graph(graph)
    assert any(isinstance(v, DanglingTargetError) for v in caught.value.violations)

    result = is_ready(graph, "ATLAS-1")
    assert not result.ready  # the wrong answer: dangling treated as satisfied
    assert NotReadyCode.DANGLING_TARGET in {r.code for r in result.reasons}
