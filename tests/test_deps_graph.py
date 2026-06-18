"""ATLAS-37: the advisory Mermaid analysis view (`render_graph`).

Unit tests over `atlas.dependencies.mermaid` built from `project_graph`, with
no database — the overlays, the precedence, the critical-edge emphasis, the
epic grouping, the advisory marker, and byte-determinism. The CLI wiring
(validate-first, stdout, writes-no-file) is covered in tests/test_deps_cli.py.

Falsifiable, with the wrong answer named:

- a ready ticket gets the `ready` overlay; a not-ready one does NOT;
- a ticket on the critical path gets `critical`, NOT `blocked` or `ready`
  (precedence);
- a critical-path edge renders `==>`, a non-critical one `-->`;
- the advisory `%%` marker is present and roadmap.mmd's provenance line is
  ABSENT (the lens is not mistakable for the canonical render);
- the same graph renders byte-identically twice.
"""

from __future__ import annotations

from uuid import uuid4

import networkx as nx
from test_models_validation import dependency_kwargs, epic_kwargs, ticket_kwargs

from atlas.core.models import Epic, Ticket, TicketDependency
from atlas.dependencies import project_graph, render_graph, render_graph_json


def make_ticket(key: str, **overrides: object) -> Ticket:
    """A planned ticket with one acceptance criterion — ready unless an
    override (status, missing criteria, a dependency) breaks it."""
    return Ticket(
        **ticket_kwargs()
        | {
            "id": uuid4(),
            "key": key,
            "status": "planned",
            "acceptance_criteria": ["criterion"],
        }
        | overrides
    )


def make_epic(key: str, **overrides: object) -> Epic:
    return Epic(**epic_kwargs() | {"id": uuid4(), "key": key} | overrides)


def depends_on(source: Ticket, target: Ticket) -> TicketDependency:
    """``source depends_on target`` — the edge source -> target."""
    return TicketDependency(
        **dependency_kwargs()
        | {
            "id": uuid4(),
            "source_ticket_id": source.id,
            "target_entity_type": "ticket",
            "target_entity_id": target.id,
            "dependency_type": "depends_on",
        }
    )


def build(
    tickets: list[Ticket],
    deps: list[TicketDependency] | None = None,
    epics: list[Epic] | None = None,
) -> nx.DiGraph[str]:
    return project_graph(
        tickets=tickets,
        epics=epics or [],
        adrs=[],
        dependencies=deps or [],
    )


def _overlay_by_key(graph: nx.DiGraph[str]) -> dict[str, str | None]:
    payload = render_graph_json(graph)
    nodes = payload["nodes"]
    assert isinstance(nodes, list)
    return {node["key"]: node["overlay"] for node in nodes}


# --- overlays --------------------------------------------------------------


def test_ready_ticket_gets_ready_not_ready_does_not() -> None:
    # A 3-chain owns the (longest) critical path; an isolated free ticket is
    # ready; an in_progress ticket is neither ready nor critical.
    chain = [make_ticket(k) for k in ("ATLAS-1", "ATLAS-2", "ATLAS-3")]
    free = make_ticket("ATLAS-5")
    not_ready = make_ticket("ATLAS-6", status="in_progress")
    graph = build(
        [*chain, free, not_ready],
        [depends_on(chain[0], chain[1]), depends_on(chain[1], chain[2])],
    )

    overlay = _overlay_by_key(graph)

    assert overlay["ATLAS-5"] == "ready"
    # The wrong answer: a not-ready (in_progress) ticket tagged ready.
    assert overlay["ATLAS-6"] != "ready"
    assert overlay["ATLAS-6"] is None


def test_critical_path_ticket_beats_blocked_and_ready() -> None:
    # ATLAS-1 depends_on ATLAS-2 depends_on ATLAS-3, all on the critical path.
    # ATLAS-1 is also blocked (ATLAS-2 not done); ATLAS-3 is also ready (free,
    # planned, has AC). Precedence must report both as `critical`.
    chain = [make_ticket(k) for k in ("ATLAS-1", "ATLAS-2", "ATLAS-3")]
    graph = build(
        chain,
        [depends_on(chain[0], chain[1]), depends_on(chain[1], chain[2])],
    )

    overlay = _overlay_by_key(graph)

    # The wrong answer: the blocked top of the chain tagged `blocked`.
    assert overlay["ATLAS-1"] == "critical"
    assert overlay["ATLAS-1"] != "blocked"
    # The wrong answer: the ready bottom of the chain tagged `ready`.
    assert overlay["ATLAS-3"] == "critical"
    assert overlay["ATLAS-3"] != "ready"


def test_blocked_ticket_off_critical_path_gets_blocked() -> None:
    # The 3-chain owns the critical path. A separate dependent (ATLAS-8) is
    # blocked by an in_progress ticket and is NOT on the path -> `blocked`.
    chain = [make_ticket(k) for k in ("ATLAS-1", "ATLAS-2", "ATLAS-3")]
    blocker = make_ticket("ATLAS-7", status="in_progress")
    dependent = make_ticket("ATLAS-8")
    graph = build(
        [*chain, blocker, dependent],
        [
            depends_on(chain[0], chain[1]),
            depends_on(chain[1], chain[2]),
            depends_on(dependent, blocker),
        ],
    )

    overlay = _overlay_by_key(graph)

    # The wrong answer: a blocked off-path ticket tagged ready or left blank.
    assert overlay["ATLAS-8"] == "blocked"


# --- edges -----------------------------------------------------------------


def test_critical_edges_are_thick_others_thin() -> None:
    # Chain A (3 nodes) is the critical path; pair B (2 nodes) is not.
    chain = [make_ticket(k) for k in ("ATLAS-1", "ATLAS-2", "ATLAS-3")]
    pair = [make_ticket(k) for k in ("ATLAS-7", "ATLAS-8")]
    graph = build(
        [*chain, *pair],
        [
            depends_on(chain[0], chain[1]),
            depends_on(chain[1], chain[2]),
            depends_on(pair[0], pair[1]),
        ],
    )

    text = render_graph(graph)

    # Critical-path links emphasised; the wrong answer renders them `-->`.
    assert "ATLAS-1 ==> ATLAS-2" in text
    assert "ATLAS-2 ==> ATLAS-3" in text
    assert "ATLAS-1 --> ATLAS-2" not in text
    # The off-path edge stays thin.
    assert "ATLAS-7 --> ATLAS-8" in text
    assert "ATLAS-7 ==> ATLAS-8" not in text


def test_json_edges_flag_critical_path() -> None:
    chain = [make_ticket(k) for k in ("ATLAS-1", "ATLAS-2")]
    graph = build(chain, [depends_on(chain[0], chain[1])])

    payload = render_graph_json(graph)
    edges = payload["edges"]
    assert isinstance(edges, list)

    assert edges == [
        {"source": "ATLAS-1", "target": "ATLAS-2", "on_critical_path": True}
    ]


# --- structure / determinism ----------------------------------------------


def test_tickets_grouped_into_epic_subgraph() -> None:
    epic = make_epic("ATLAS-E1", title="Dependency Graph")
    ticket = make_ticket("ATLAS-1", epic_id=epic.id)
    graph = build([ticket], epics=[epic])

    text = render_graph(graph)

    assert "graph TD" in text
    assert 'subgraph ATLAS-E1["Dependency Graph"]' in text
    assert '    ATLAS-1["ATLAS-1 Core models"]' in text


def test_advisory_marker_present_and_not_roadmap_provenance() -> None:
    graph = build([make_ticket("ATLAS-1")])

    text = render_graph(graph)

    # The advisory marker leads the output...
    assert text.startswith("%% Atlas dependency ANALYSIS view")
    assert "advisory" in text
    # ...and the canonical roadmap.mmd provenance header is ABSENT: the lens
    # must not be mistakable for the apply-written record.
    assert "Render written only by" not in text


def test_render_is_byte_deterministic() -> None:
    epic = make_epic("ATLAS-E1")
    chain = [make_ticket(k, epic_id=epic.id) for k in ("ATLAS-1", "ATLAS-2", "ATLAS-3")]
    deps = [depends_on(chain[0], chain[1]), depends_on(chain[1], chain[2])]

    first = render_graph(build(chain, deps, [epic]))
    second = render_graph(build(chain, deps, [epic]))

    assert first == second
