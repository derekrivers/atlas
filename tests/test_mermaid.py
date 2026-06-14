"""ATLAS-27 gap 2: the roadmap.mmd renderer is deterministic and correct.

Byte-determinism across two renders of the same backlog; edge direction
(SOURCE --> TARGET = depends_on) and node identity; the %% header.
"""

from __future__ import annotations

from uuid import uuid4

from test_models_validation import dependency_kwargs, epic_kwargs, ticket_kwargs

from atlas.core.models import Epic, Ticket, TicketDependency
from atlas.core.yaml_io import RenderHeader
from atlas.planning.mermaid import render_roadmap

HEADER = RenderHeader(
    plan_run_id="b3a9f1e2-7c4d-4a8b-9e6f-1d2c3b4a5e60",
    prompt_version="planner-v1.1.0",
    ticket_key_high_water=2,
    epic_key_high_water=1,
)


def _backlog() -> tuple[list[Epic], list[Ticket], list[TicketDependency]]:
    epic = Epic(**epic_kwargs() | {"key": "ATLAS-E1", "id": uuid4()})
    t1 = Ticket(
        **ticket_kwargs()
        | {"key": "ATLAS-1", "id": uuid4(), "epic_id": epic.id, "status": "planned"}
    )
    t2 = Ticket(
        **ticket_kwargs()
        | {"key": "ATLAS-2", "id": uuid4(), "epic_id": epic.id, "status": "planned"}
    )
    # ATLAS-2 depends_on ATLAS-1.
    dependency = TicketDependency(
        **dependency_kwargs()
        | {
            "source_ticket_id": t2.id,
            "target_entity_id": t1.id,
            "reason": "ordering",
        }
    )
    return [epic], [t1, t2], [dependency]


def test_render_is_byte_deterministic() -> None:
    epics, tickets, deps = _backlog()
    first = render_roadmap(epics, tickets, deps, HEADER)
    second = render_roadmap(epics, tickets, deps, HEADER)
    assert first == second


def test_edge_direction_is_depends_on() -> None:
    epics, tickets, deps = _backlog()
    text = render_roadmap(epics, tickets, deps, HEADER)
    # ATLAS-2 depends_on ATLAS-1 renders as ATLAS-2 --> ATLAS-1.
    assert "ATLAS-2 --> ATLAS-1" in text
    assert "ATLAS-1 --> ATLAS-2" not in text


def test_nodes_grouped_in_epic_subgraph() -> None:
    epics, tickets, deps = _backlog()
    text = render_roadmap(epics, tickets, deps, HEADER)
    assert "graph TD" in text
    assert "subgraph ATLAS-E1" in text
    assert 'ATLAS-1["ATLAS-1 Core models"]' in text


def test_mermaid_header_is_percent_prefixed() -> None:
    epics, tickets, deps = _backlog()
    lines = render_roadmap(epics, tickets, deps, HEADER).splitlines()
    assert lines[0].startswith("%% Render written only by")
    assert "%% ticket_key_high_water: 2" in lines
    assert "%% epic_key_high_water: 1" in lines


def test_empty_backlog_renders_graph_header_only() -> None:
    text = render_roadmap([], [], [], HEADER)
    assert "graph TD" in text
    assert text.endswith("\n")
