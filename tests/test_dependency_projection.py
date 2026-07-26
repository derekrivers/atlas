"""Dependency projection assembly for the operator API."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import networkx as nx
import pytest
from test_apply import _epic_model_kwargs, _ticket_model_kwargs
from test_models_validation import dependency_kwargs
from test_plan_pipeline import fresh_db

from atlas.core.models import Epic, Ticket, TicketDependency
from atlas.core.models.dependency import DependencyType
from atlas.dependencies import (
    BlockedResult,
    BlockedTarget,
    CriticalPath,
    CriticalPathStep,
    NotReadyCode,
    NotReadyReason,
    ReadinessResult,
    UnlocksResult,
)
from atlas.orchestration import (
    DependencyGraphEdgeState,
    DependencyGraphNodeState,
    DependencyGraphState,
    TicketDependencyState,
    dependency_critical_path,
    dependency_graph,
    dependency_projection,
    ticket_dependencies,
)
from atlas.storage import (
    Database,
    EpicRepo,
    ProductRepo,
    TicketDependencyRepo,
    TicketRepo,
)


@pytest.fixture
def store(tmp_path: Path) -> tuple[Database, UUID, UUID]:
    db = fresh_db(tmp_path)
    product = ProductRepo(db).get_by_key("ATLAS")
    assert product is not None
    epic = Epic(**_epic_model_kwargs(product.id, key="ATLAS-E1"))
    EpicRepo(db).add(epic)
    return db, product.id, epic.id


def _ticket(
    store: tuple[Database, UUID, UUID],
    key: str,
    **overrides: object,
) -> Ticket:
    _db, product_id, epic_id = store
    return Ticket(
        **(
            _ticket_model_kwargs(product_id, epic_id, key=key)
            | {"id": uuid4()}
            | overrides
        )
    )


def _depends_on(source: Ticket, target: Ticket) -> TicketDependency:
    return TicketDependency(
        **(
            dependency_kwargs()
            | {
                "id": uuid4(),
                "source_ticket_id": source.id,
                "target_entity_type": "ticket",
                "target_entity_id": target.id,
            }
        )
    )


def _seed(
    db: Database,
    tickets: list[Ticket],
    dependencies: list[TicketDependency],
) -> None:
    ticket_repo = TicketRepo(db)
    dependency_repo = TicketDependencyRepo(db)
    for ticket in tickets:
        ticket_repo.add(ticket)
    for dependency in dependencies:
        dependency_repo.add(dependency)


def test_ticket_dependencies_projects_blockers_blocked_by_and_all_reasons(
    store: tuple[Database, UUID, UUID],
) -> None:
    db, _, _ = store
    blocker = _ticket(store, "ATLAS-200", status="in_progress")
    ticket = _ticket(
        store,
        "ATLAS-199",
        status="in_progress",
        acceptance_criteria=[],
    )
    dependent = _ticket(store, "ATLAS-201")
    _seed(
        db,
        [blocker, ticket, dependent],
        [_depends_on(ticket, blocker), _depends_on(dependent, ticket)],
    )

    state = ticket_dependencies(db, "ATLAS-199")

    assert state is not None
    assert state.blockers.targets == (
        BlockedTarget("ATLAS-200", NotReadyCode.DEPENDENCY_NOT_DONE),
    )
    assert state.blocked_by.dependents == ("ATLAS-201",)
    assert state.readiness.ready is False
    assert {reason.code for reason in state.readiness.reasons} == {
        NotReadyCode.WRONG_STATUS,
        NotReadyCode.DEPENDENCY_NOT_DONE,
        NotReadyCode.NO_ACCEPTANCE_CRITERIA,
    }


def test_ticket_dependencies_returns_none_for_unknown_ticket(
    store: tuple[Database, UUID, UUID],
) -> None:
    db, _, _ = store
    _seed(db, [_ticket(store, "ATLAS-199")], [])

    assert ticket_dependencies(db, "ATLAS-404") is None


def test_dependency_critical_path_delegates_to_existing_projection(
    store: tuple[Database, UUID, UUID],
) -> None:
    db, _, _ = store
    last = _ticket(store, "ATLAS-201", estimated_effort=5)
    middle = _ticket(store, "ATLAS-202", estimated_effort=3)
    first = _ticket(store, "ATLAS-203", estimated_effort=2)
    _seed(
        db,
        [last, middle, first],
        [_depends_on(last, middle), _depends_on(middle, first)],
    )

    path = dependency_critical_path(db)

    assert path.keys == ("ATLAS-203", "ATLAS-202", "ATLAS-201")
    assert path.total_effort == 10


def test_ticket_dependencies_calls_dependency_layer_functions(
    monkeypatch: pytest.MonkeyPatch,
    store: tuple[Database, UUID, UUID],
) -> None:
    db, _, _ = store
    graph: nx.DiGraph[str] = nx.DiGraph()
    calls: list[str] = []
    blockers = BlockedResult(
        "ATLAS-199",
        (BlockedTarget("ATLAS-200", NotReadyCode.DEPENDENCY_NOT_DONE),),
    )
    blocked_by = UnlocksResult("ATLAS-199", ("ATLAS-201",))
    readiness = ReadinessResult(
        "ATLAS-199",
        (NotReadyReason(NotReadyCode.WRONG_STATUS, "bad status"),),
    )

    def fake_build(database: Database) -> nx.DiGraph[str]:
        assert database is db
        calls.append("build_dependency_graph")
        return graph

    def fake_blocked(candidate: nx.DiGraph[str], key: str) -> BlockedResult:
        assert candidate is graph
        assert key == "ATLAS-199"
        calls.append("blocked")
        return blockers

    def fake_unlocks(candidate: nx.DiGraph[str], key: str) -> UnlocksResult:
        assert candidate is graph
        assert key == "ATLAS-199"
        calls.append("unlocks")
        return blocked_by

    def fake_is_ready(candidate: nx.DiGraph[str], key: str) -> ReadinessResult:
        assert candidate is graph
        assert key == "ATLAS-199"
        calls.append("is_ready")
        return readiness

    monkeypatch.setattr(dependency_projection, "build_dependency_graph", fake_build)
    monkeypatch.setattr(dependency_projection, "blocked", fake_blocked)
    monkeypatch.setattr(dependency_projection, "unlocks", fake_unlocks)
    monkeypatch.setattr(dependency_projection, "is_ready", fake_is_ready)

    assert ticket_dependencies(db, "ATLAS-199") == TicketDependencyState(
        key="ATLAS-199",
        blockers=blockers,
        blocked_by=blocked_by,
        readiness=readiness,
    )
    assert calls == ["build_dependency_graph", "blocked", "unlocks", "is_ready"]


def test_dependency_critical_path_calls_dependency_layer_functions(
    monkeypatch: pytest.MonkeyPatch,
    store: tuple[Database, UUID, UUID],
) -> None:
    db, _, _ = store
    graph: nx.DiGraph[str] = nx.DiGraph()
    path = CriticalPath((CriticalPathStep("ATLAS-199", 3, 3),))
    calls: list[str] = []

    def fake_build(database: Database) -> nx.DiGraph[str]:
        assert database is db
        calls.append("build_dependency_graph")
        return graph

    def fake_validate(candidate: nx.DiGraph[str]) -> None:
        assert candidate is graph
        calls.append("validate_graph")

    def fake_critical_path(candidate: nx.DiGraph[str]) -> CriticalPath:
        assert candidate is graph
        calls.append("critical_path")
        return path

    monkeypatch.setattr(dependency_projection, "build_dependency_graph", fake_build)
    monkeypatch.setattr(dependency_projection, "validate_graph", fake_validate)
    monkeypatch.setattr(dependency_projection, "critical_path", fake_critical_path)

    assert dependency_critical_path(db) == path
    assert calls == ["build_dependency_graph", "validate_graph", "critical_path"]


def test_dependency_graph_calls_dependency_layer_builder_and_validator(
    monkeypatch: pytest.MonkeyPatch,
    store: tuple[Database, UUID, UUID],
) -> None:
    db, _, _ = store
    graph: nx.DiGraph[str] = nx.DiGraph()
    graph.add_node(
        "ATLAS-10",
        key="ATLAS-10",
        status="planned",
        node_type="ticket",
        present=True,
    )
    graph.add_node(
        "ATLAS-2",
        key="ATLAS-2",
        status="done",
        node_type="ticket",
        present=True,
    )
    graph.add_edge("ATLAS-10", "ATLAS-2", dependency_type="depends_on")
    graph.add_edge("ATLAS-2", "ATLAS-10", dependency_type="relates_to")
    calls: list[str] = []

    def fake_build(database: Database) -> nx.DiGraph[str]:
        assert database is db
        calls.append("build_dependency_graph")
        return graph

    def fake_validate(candidate: nx.DiGraph[str]) -> None:
        assert candidate is graph
        calls.append("validate_graph")

    monkeypatch.setattr(dependency_projection, "build_dependency_graph", fake_build)
    monkeypatch.setattr(dependency_projection, "validate_graph", fake_validate)

    assert dependency_graph(db) == DependencyGraphState(
        nodes=(
            DependencyGraphNodeState(
                key="ATLAS-2",
                status="done",
                node_type="ticket",
            ),
            DependencyGraphNodeState(
                key="ATLAS-10",
                status="planned",
                node_type="ticket",
            ),
        ),
        edges=(
            DependencyGraphEdgeState(
                source="ATLAS-10",
                target="ATLAS-2",
                dependency_type=DependencyType.DEPENDS_ON,
            ),
        ),
    )
    assert calls == ["build_dependency_graph", "validate_graph"]
