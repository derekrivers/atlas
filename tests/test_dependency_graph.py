"""ATLAS-31: the on-demand dependency-graph projection.

Deterministic and pure — ZERO model calls. A fixture backlog projects to
the expected DiGraph (nodes keyed by entity key, typed; edges A -> B =
A depends_on B); dangling and component targets are represented honestly
(present=False) for ATLAS-40 to later detect; the projection is pure and
the storage-reading build never writes.
"""

from pathlib import Path
from uuid import UUID, uuid4

import pytest
from test_models_validation import (
    adr_kwargs,
    dependency_kwargs,
    epic_kwargs,
    ticket_kwargs,
)

from atlas.core.models import ArchitectureDecisionRecord, Epic, Ticket, TicketDependency
from atlas.dependencies import build_dependency_graph, project_graph
from atlas.storage import (
    Database,
    EpicRepo,
    TicketDependencyRepo,
    TicketRepo,
)


def make_ticket(key: str, **overrides: object) -> Ticket:
    return Ticket(**ticket_kwargs() | {"id": uuid4(), "key": key} | overrides)


def make_epic(key: str, **overrides: object) -> Epic:
    return Epic(**epic_kwargs() | {"id": uuid4(), "key": key} | overrides)


def make_adr(number: int, **overrides: object) -> ArchitectureDecisionRecord:
    return ArchitectureDecisionRecord(
        **adr_kwargs() | {"id": uuid4(), "number": number} | overrides
    )


def depends_on(
    source: Ticket,
    target_id: UUID,
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


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{tmp_path}/atlas.db")
    database.create_all()
    return database


def test_nodes_keyed_by_entity_key_and_typed() -> None:
    epic = make_epic("ATLAS-E1")
    ticket = make_ticket(
        "ATLAS-1", epic_id=epic.id, status="planned", acceptance_criteria=["one"]
    )
    adr = make_adr(8, status="accepted")
    graph = project_graph([ticket], [epic], [adr], [])

    assert set(graph.nodes) == {"ATLAS-1", "ATLAS-E1", "ADR-0008"}
    assert all(graph.nodes[node]["present"] for node in graph.nodes)
    assert graph.nodes["ATLAS-1"]["node_type"] == "ticket"
    assert graph.nodes["ATLAS-1"]["status"] == "planned"
    assert graph.nodes["ATLAS-1"]["epic_key"] == "ATLAS-E1"
    assert graph.nodes["ATLAS-1"]["acceptance_criteria_count"] == 1
    assert graph.nodes["ATLAS-E1"]["node_type"] == "epic"
    assert graph.nodes["ADR-0008"]["node_type"] == "adr"


def test_edge_direction_is_depends_on() -> None:
    a = make_ticket("ATLAS-1")
    b = make_ticket("ATLAS-2")
    # ATLAS-2 depends_on ATLAS-1: the edge points from dependant to target.
    graph = project_graph([a, b], [], [], [depends_on(b, a.id)])

    assert graph.has_edge("ATLAS-2", "ATLAS-1")
    assert not graph.has_edge("ATLAS-1", "ATLAS-2")
    # Execution order is the reverse topological order of this subgraph.
    assert list(graph.successors("ATLAS-2")) == ["ATLAS-1"]


def test_edge_attributes_carry_type_reason_and_row_id() -> None:
    a = make_ticket("ATLAS-1")
    b = make_ticket("ATLAS-2")
    dep = depends_on(b, a.id, reason="A must land before B")
    graph = project_graph([a, b], [], [], [dep])

    edge = graph.edges["ATLAS-2", "ATLAS-1"]
    assert edge["dependency_type"] == "depends_on"
    assert edge["reason"] == "A must land before B"
    assert edge["dependency_id"] == dep.id


def test_dangling_ticket_target_is_represented_not_raised() -> None:
    a = make_ticket("ATLAS-1")
    missing = uuid4()
    # Target ticket is absent from the backlog; the build must not raise.
    graph = project_graph([a], [], [], [depends_on(a, missing)])

    assert graph.has_edge("ATLAS-1", str(missing))
    absent = graph.nodes[str(missing)]
    assert absent["present"] is False
    assert absent["node_type"] == "ticket"
    assert absent["entity_id"] == missing


def test_component_target_representable_without_component() -> None:
    a = make_ticket("ATLAS-1")
    component = uuid4()
    # No component table exists (D2); the edge is representable, not an error.
    graph = project_graph([a], [], [], [depends_on(a, component, "component")])

    node = graph.nodes[str(component)]
    assert node["node_type"] == "component"
    assert node["present"] is False
    assert graph.has_edge("ATLAS-1", str(component))


def test_estimated_effort_carried_as_is_including_null() -> None:
    null_effort = make_ticket("ATLAS-1")  # defaults to None (D1)
    valued = make_ticket("ATLAS-2", estimated_effort=5)
    graph = project_graph([null_effort, valued], [], [], [])

    assert graph.nodes["ATLAS-1"]["estimated_effort"] is None
    assert graph.nodes["ATLAS-2"]["estimated_effort"] == 5


def test_adr_node_key_and_status() -> None:
    graph = project_graph([], [], [make_adr(6, status="accepted")], [])

    assert "ADR-0006" in graph.nodes
    assert graph.nodes["ADR-0006"]["status"] == "accepted"
    assert graph.nodes["ADR-0006"]["number"] == 6


def test_non_depends_on_edge_carried_with_type() -> None:
    a = make_ticket("ATLAS-1")
    b = make_ticket("ATLAS-2")
    dep = depends_on(b, a.id, dependency_type="relates_to")
    graph = project_graph([a, b], [], [], [dep])

    assert graph.edges["ATLAS-2", "ATLAS-1"]["dependency_type"] == "relates_to"


def test_projection_is_pure() -> None:
    epic = make_epic("ATLAS-E1")
    a = make_ticket("ATLAS-1", epic_id=epic.id)
    b = make_ticket("ATLAS-2")
    dep = depends_on(b, a.id)

    first = project_graph([a, b], [epic], [], [dep])
    second = project_graph([a, b], [epic], [], [dep])

    assert set(first.nodes) == set(second.nodes)
    assert set(first.edges) == set(second.edges)
    assert {node: first.nodes[node] for node in first.nodes} == {
        node: second.nodes[node] for node in second.nodes
    }
    assert {edge: first.edges[edge] for edge in first.edges} == {
        edge: second.edges[edge] for edge in second.edges
    }


def test_build_reads_from_storage_without_writing(db: Database) -> None:
    epic = make_epic("ATLAS-E1")
    a = make_ticket("ATLAS-1", epic_id=epic.id, status="done")
    b = make_ticket("ATLAS-2", status="planned")
    dep = depends_on(b, a.id)
    EpicRepo(db).add(epic)
    TicketRepo(db).add(a)
    TicketRepo(db).add(b)
    TicketDependencyRepo(db).add(dep)

    def counts() -> tuple[int, int, int]:
        return (
            len(TicketRepo(db).list()),
            len(EpicRepo(db).list()),
            len(TicketDependencyRepo(db).list()),
        )

    before = counts()
    graph = build_dependency_graph(db)
    assert counts() == before  # the build never writes

    expected = project_graph([a, b], [epic], [], [dep])
    assert set(graph.nodes) == set(expected.nodes)
    assert set(graph.edges) == set(expected.edges)
    assert graph.has_edge("ATLAS-2", "ATLAS-1")
