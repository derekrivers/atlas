"""ATLAS-17: YAML serialisation layer per knowledge-core "Planning render
format". Representative round-trips and format pins; exhaustive property
suites are ATLAS-19. All paths are tmp paths — docs/planning/ is never
touched."""

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import yaml
from pydantic import BaseModel
from test_agent_run_model import agent_run_kwargs
from test_context_pack_model import context_pack_kwargs
from test_evidence_model import evidence_kwargs
from test_lesson_model import lesson_kwargs
from test_models_validation import (
    adr_kwargs,
    dependency_kwargs,
    epic_kwargs,
    product_kwargs,
    ticket_kwargs,
)
from test_plan_run_model import plan_run_kwargs

from atlas.core.models import (
    AgentRun,
    ArchitectureDecisionRecord,
    ContextPack,
    Epic,
    Evidence,
    Lesson,
    PlanRun,
    Product,
    Ticket,
    TicketDependency,
)
from atlas.core.yaml_io import (
    RenderFormatError,
    RenderHeader,
    UnknownFieldError,
    dump_entity,
    load_entity,
    parse_document,
    render_document,
)

HEADER = RenderHeader(
    plan_run_id="b3a9f1e2-7c4d-4a8b-9e6f-1d2c3b4a5e60",
    prompt_version="planner-v1.0.0",
    ticket_key_high_water=42,
    epic_key_high_water=7,
)

ROUND_TRIP_CASES = [
    (Product, product_kwargs),
    (ArchitectureDecisionRecord, adr_kwargs),
    (Epic, epic_kwargs),
    (Ticket, ticket_kwargs),
    (TicketDependency, dependency_kwargs),
    (Lesson, lesson_kwargs),
    (Evidence, evidence_kwargs),
    (AgentRun, agent_run_kwargs),
    (ContextPack, context_pack_kwargs),
    (PlanRun, plan_run_kwargs),
]
CASE_IDS = [model.__name__ for model, _ in ROUND_TRIP_CASES]


@pytest.mark.parametrize(("model_cls", "kwargs"), ROUND_TRIP_CASES, ids=CASE_IDS)
def test_round_trip_is_identity(model_cls: type[BaseModel], kwargs: Any) -> None:
    original = model_cls(**kwargs())
    assert load_entity(model_cls, dump_entity(original)) == original


@pytest.mark.parametrize(("model_cls", "kwargs"), ROUND_TRIP_CASES, ids=CASE_IDS)
def test_double_dump_is_byte_identical(model_cls: type[BaseModel], kwargs: Any) -> None:
    model = model_cls(**kwargs())
    assert dump_entity(model) == dump_entity(model)


def test_output_shape_lf_utf8_trailing_newline() -> None:
    text = dump_entity(Product(**product_kwargs()))
    assert "\r" not in text
    assert text.endswith("\n")
    text.encode("utf-8")


def test_fields_emit_in_declaration_order() -> None:
    text = dump_entity(Ticket(**ticket_kwargs()))
    emitted = [
        line.split(":", 1)[0]
        for line in text.splitlines()
        if line and not line.startswith((" ", "-"))
    ]
    assert emitted == list(Ticket.model_fields)


def test_optional_fields_present_as_null() -> None:
    text = dump_entity(Product(**product_kwargs()))
    assert "archived_at: null" in text


def test_no_anchors_or_aliases_for_shared_objects() -> None:
    shared = ["no Neo4j in v1"]
    pack = ContextPack(
        **context_pack_kwargs() | {"constraints": shared, "risks": shared}
    )
    text = dump_entity(pack)
    assert "&id" not in text
    assert "*id" not in text
    assert text.count("no Neo4j in v1") == 2


def test_multiline_string_emits_literal_block() -> None:
    pack = ContextPack(
        **context_pack_kwargs() | {"rendered_markdown": "# Brief\n\n- item\n"}
    )
    text = dump_entity(pack)
    assert "rendered_markdown: |" in text
    assert load_entity(ContextPack, text) == pack


def test_mapping_fields_emit_sorted_keys() -> None:
    run = PlanRun(
        **plan_run_kwargs() | {"input_doc_shas": {"b.md": "2222222", "a.md": "1111111"}}
    )
    text = dump_entity(run)
    assert text.index("a.md") < text.index("b.md")
    assert load_entity(PlanRun, text) == run


def test_aware_datetime_round_trips_with_offset() -> None:
    # UTC serialises as the Z suffix (Pydantic JSON mode); the stored
    # timezone is preserved exactly through the round trip.
    lesson = Lesson(**lesson_kwargs())
    text = dump_entity(lesson)
    assert "2026-06-12T00:00:00Z" in text
    assert load_entity(Lesson, text).created_at == lesson.created_at


def test_unknown_key_fails_closed_on_load() -> None:
    text = dump_entity(Lesson(**lesson_kwargs())) + "surprise: 1\n"
    with pytest.raises(UnknownFieldError, match="surprise"):
        load_entity(Lesson, text)


def test_render_document_format(tmp_path: Path) -> None:
    tickets = [
        Ticket(**ticket_kwargs() | {"key": "ATLAS-10", "id": uuid4()}),
        Ticket(**ticket_kwargs() | {"key": "ATLAS-2", "id": uuid4()}),
    ]
    text = render_document("tickets", tickets, HEADER)

    lines = text.splitlines()
    assert "atlas apply" in lines[0]
    assert lines[1] == f"# plan_run_id: {HEADER.plan_run_id}"
    assert lines[2] == "# prompt_version: planner-v1.0.0"
    assert lines[3] == "# ticket_key_high_water: 42"
    assert lines[4] == "# epic_key_high_water: 7"
    assert lines[5] == "tickets:"
    # Numeric collation within the shared prefix: ATLAS-2 before ATLAS-10.
    assert text.index("key: ATLAS-2\n") < text.index("key: ATLAS-10\n")
    # Each entry carries both key and id.
    for entry in yaml.safe_load(text)["tickets"]:
        assert "key" in entry
        assert "id" in entry
    # Render is written to a caller-supplied path only.
    (tmp_path / "tickets.yaml").write_text(text, encoding="utf-8")


def test_render_document_orders_epic_keys_naturally() -> None:
    # ATLAS-113 behaviour fix: epic keys (ATLAS-E<n>) the old strict grammar
    # could not parse used to sort lexically (E1, E10, E2). The shared
    # natural_key now orders them E1, E2, …, E10. The E10 span is load-bearing:
    # with only E1-E9 lexical and natural orderings agree and this would not
    # bite the regression it exists to pin.
    epics = [
        Epic(**epic_kwargs() | {"key": f"ATLAS-E{n}", "id": uuid4()})
        for n in (10, 2, 1)
    ]
    text = render_document("epics", epics, HEADER)
    positions = [text.index(f"key: ATLAS-E{n}\n") for n in (1, 2, 10)]
    assert positions == sorted(positions)


def test_render_document_double_render_byte_identical() -> None:
    tickets = [Ticket(**ticket_kwargs())]
    assert render_document("tickets", tickets, HEADER) == render_document(
        "tickets", tickets, HEADER
    )


def test_render_parse_round_trip() -> None:
    dependencies = [
        TicketDependency(**dependency_kwargs()),
        TicketDependency(**dependency_kwargs()),
    ]
    text = render_document("dependencies", dependencies, HEADER)
    parsed = parse_document(TicketDependency, text, "dependencies")
    assert sorted(parsed, key=lambda d: str(d.id)) == sorted(
        dependencies, key=lambda d: str(d.id)
    )


def test_empty_render_round_trips() -> None:
    text = render_document("epics", [], HEADER)
    assert parse_document(Epic, text, "epics") == []


def test_parse_document_unknown_entry_key_fails_closed() -> None:
    text = render_document("epics", [Epic(**epic_kwargs())], HEADER)
    text = text.replace("priority: 1", "priority: 1\n  sprint: 9")
    with pytest.raises(UnknownFieldError, match="sprint"):
        parse_document(Epic, text, "epics")


def test_parse_document_wrong_top_key_fails() -> None:
    text = render_document("epics", [Epic(**epic_kwargs())], HEADER)
    with pytest.raises(RenderFormatError, match="tickets"):
        parse_document(Epic, text, "tickets")


def test_parse_document_non_list_fails() -> None:
    with pytest.raises(RenderFormatError, match="list"):
        parse_document(Epic, "epics: not-a-list\n", "epics")


def test_header_satisfies_planning_render_check() -> None:
    # The v1 linter's PLAN001 check requires plan_run_id and atlas apply
    # within the first ten lines of a real render; the generated header
    # guarantees it by construction.
    head = "\n".join(render_document("epics", [], HEADER).splitlines()[:10]).lower()
    assert "plan_run_id" in head
    assert "atlas apply" in head
