"""ATLAS-56: context pack generation over the four Phase 5 retrievers, per
docs/atlas/context-renderer.md.

Pure builder — ZERO storage/git/file/model calls. Each lever is falsifiable, and
the two negative tests name the wrong answer: an empty section header that must
be ABSENT from the markdown (rendering it is the wrong answer), and an
over-budget pack that must RAISE (returning a silently-oversized pack is the
wrong answer).
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from test_lesson_model import lesson_kwargs
from test_models_validation import (
    adr_kwargs,
    dependency_kwargs,
    ticket_kwargs,
)

from atlas.context import ContextBudgetExceededError, build_context_pack
from atlas.core.anchors import SourceDocument
from atlas.core.models import (
    ArchitectureDecisionRecord,
    Lesson,
    Ticket,
    TicketDependency,
)
from atlas.dependencies import adr_key, project_graph

# The anchor doc: a heading whose slug is "target-section", a parent heading for
# the breadcrumb, a deeper subheading that stays inside the section, and a
# following same-level heading that ends it.
_ANCHOR_CONTENT = "\n".join(
    [
        "# Atlas",
        "## Target Section",
        "Body line one.",
        "### Sub detail",
        "sub body",
        "## Next Section",
        "next body",
    ]
)


def make_ticket(**overrides: Any) -> Ticket:
    """A fully-populated ticket: an anchor that resolves in the corpus below,
    one reference doc, and the verbatim rule-5 lists set."""
    base = {
        "id": uuid4(),
        "key": "ATLAS-12",
        "objective": "Build the context pack assembler.",
        "context": "Phase 5 execution context.",
        "ticket_type": "feature",
        "risk_level": "high",
        "source_anchor": "main.md#target-section",
        "relevant_docs": ["ref.md"],
        "acceptance_criteria": ["A pack is produced.", "Only ACTIVE lessons appear."],
        "non_goals": ["The CLI is out of scope."],
        "test_requirements": ["uv run pytest tests/test_pack.py"],
        "definition_of_done": ["Gate suite green."],
        "tags": ["context"],
    }
    return Ticket(**ticket_kwargs() | base | overrides)


def make_adr(number: int, **overrides: Any) -> ArchitectureDecisionRecord:
    base = {"id": uuid4(), "number": number, "status": "accepted"}
    return ArchitectureDecisionRecord(**adr_kwargs() | base | overrides)


def make_lesson(**overrides: Any) -> Lesson:
    return Lesson(**lesson_kwargs() | {"status": "active"} | overrides)


def depends_on(
    source: Ticket, target_id: Any, target_type: str = "ticket"
) -> TicketDependency:
    return TicketDependency(
        **dependency_kwargs()
        | {
            "id": uuid4(),
            "source_ticket_id": source.id,
            "target_entity_type": target_type,
            "target_entity_id": target_id,
            "dependency_type": "depends_on",
        }
    )


def make_corpus() -> list[SourceDocument]:
    return [
        SourceDocument(path="main.md", sha="sha-main", content=_ANCHOR_CONTENT),
        SourceDocument(path="ref.md", sha="sha-ref", content="# Ref\nref body"),
    ]


def build_full() -> Any:
    """The fully-wired pack: a ticket with an ADR dependency target, a related
    ticket it depends on, and a tag-matching ACTIVE lesson."""
    ticket = make_ticket()
    related = make_ticket(
        id=uuid4(), key="ATLAS-99", title="Dependency retrieval", status="done"
    )
    adr = make_adr(
        5,
        title="Code calculates, agents interpret",
        decision="Code computes; agents only interpret.",
        consequences=["Determinism is testable."],
        alternatives_considered=["Let agents compute."],
    )
    lesson = make_lesson(
        title="Keep packs small",
        problem="Oversized packs blow the budget.",
        solution="Cap each source.",
        outcome="Packs stay scannable.",
        tags=["context"],
    )
    graph = project_graph(
        [ticket, related],
        [],
        [adr],
        [depends_on(ticket, related.id), depends_on(ticket, adr.id, "adr")],
    )
    pack = build_context_pack(
        ticket,
        graph=graph,
        documents=make_corpus(),
        accepted_adrs=[adr],
        lessons=[lesson],
    )
    return pack, ticket, related, adr, lesson


def build_bare() -> Any:
    """A pack with no ADR/related/lesson matches: a lone ticket node, no
    dependencies, empty ADR and lesson listings. The doc anchor still resolves."""
    ticket = make_ticket(relevant_docs=[], tags=[])
    graph = project_graph([ticket], [], [], [])
    pack = build_context_pack(
        ticket,
        graph=graph,
        documents=make_corpus(),
        accepted_adrs=[],
        lessons=[],
    )
    return pack, ticket


# --- structured field mapping (D2) ---------------------------------------


def test_structured_id_mapping_carries_retriever_uuids() -> None:
    pack, _ticket, related, adr, lesson = build_full()
    assert pack.relevant_adrs == [adr.id]
    assert pack.related_tickets == [related.id]
    assert pack.historical_lessons == [lesson.id]


def test_relevant_docs_is_section_plus_reference_paths() -> None:
    pack, _ticket, *_ = build_full()
    assert pack.relevant_docs == ["main.md", "ref.md"]


def test_input_doc_shas_is_union_of_section_and_reference_shas() -> None:
    pack, *_ = build_full()
    assert pack.input_doc_shas == {"main.md": "sha-main", "ref.md": "sha-ref"}


# --- clean ticket-field mapping (D3) -------------------------------------


def test_clean_fields_map_through() -> None:
    pack, ticket, *_ = build_full()
    # Rule 5 names "test requirements"; the pack field is test_commands.
    assert pack.test_commands == ticket.test_requirements
    assert pack.acceptance_criteria == ticket.acceptance_criteria
    assert pack.definition_of_done == ticket.definition_of_done
    assert pack.objective == ticket.objective


# --- gap fields (D4) ------------------------------------------------------


def test_constraints_empty_and_risks_is_derived_level_line() -> None:
    pack, ticket, *_ = build_full()
    assert pack.constraints == []
    assert pack.risks == [f"Risk level: {ticket.risk_level.value}"]


def test_ticket_context_appears_in_rendered_objective() -> None:
    pack, ticket, *_ = build_full()
    objective_block = pack.rendered_markdown.split("##", 2)[1]
    assert objective_block.startswith(" Objective")
    assert ticket.context in objective_block


# --- rendered order + omit-empty (D5) ------------------------------------


def test_present_sections_in_fixed_order() -> None:
    pack, *_ = build_full()
    md = pack.rendered_markdown
    order = [
        "## Objective",
        "## Acceptance Criteria",
        "## Non-goals",
        "## Relevant Docs",
        "## ADRs",
        "## Related Tickets",
        "## Lessons",
        "## Risks",
        "## Test Commands",
        "## Definition of Done",
    ]
    positions = [md.find(header) for header in order]
    assert all(p != -1 for p in positions), positions
    assert positions == sorted(positions)


def test_empty_matches_omit_their_headers() -> None:
    # The wrong answer: rendering an empty "## ADRs" / "## Related Tickets" /
    # "## Lessons" / "## Constraints" header. They must be ABSENT entirely.
    pack, _ticket = build_bare()
    md = pack.rendered_markdown
    assert "## ADRs" not in md
    assert "## Related Tickets" not in md
    assert "## Lessons" not in md
    assert "## Constraints" not in md
    # A present section is unaffected.
    assert "## Objective" in md


# --- rendering content ----------------------------------------------------


def test_adr_section_shows_decision_and_consequence_not_context_or_alternatives() -> (
    None
):
    pack, _ticket, _related, adr, _lesson = build_full()
    md = pack.rendered_markdown
    assert adr_key(adr.number) in md
    assert adr.decision in md
    assert adr.consequences[0] in md
    # Rule 2: context and alternatives are omitted.
    assert adr.context not in md
    assert adr.alternatives_considered[0] not in md


def test_related_ticket_line_shows_key_title_status() -> None:
    pack, _ticket, related, *_ = build_full()
    md = pack.rendered_markdown
    assert related.key in md
    assert related.title in md
    assert related.status.value in md


def test_lesson_shows_body() -> None:
    pack, _ticket, _related, _adr, lesson = build_full()
    md = pack.rendered_markdown
    assert lesson.title in md
    assert lesson.problem in md
    assert lesson.solution in md
    assert lesson.outcome in md


# --- token estimate (D6) --------------------------------------------------


def test_token_estimate_is_len_over_four() -> None:
    pack, *_ = build_full()
    assert pack.token_estimate == len(pack.rendered_markdown) // 4


# --- over-budget fails closed (D6) ---------------------------------------


def test_over_budget_raises() -> None:
    # The wrong answer: returning a silently-oversized pack. A tiny budget forces
    # token_estimate > budget, which must RAISE.
    ticket = make_ticket()
    related = make_ticket(id=uuid4(), key="ATLAS-99", title="Related", status="done")
    adr = make_adr(5, title="Code calculates, agents interpret")
    lesson = make_lesson(tags=["context"])
    graph = project_graph(
        [ticket, related],
        [],
        [adr],
        [depends_on(ticket, related.id), depends_on(ticket, adr.id, "adr")],
    )
    with pytest.raises(ContextBudgetExceededError) as excinfo:
        build_context_pack(
            ticket,
            graph=graph,
            documents=make_corpus(),
            accepted_adrs=[adr],
            lessons=[lesson],
            budget=1,
        )
    assert excinfo.value.ticket_key == ticket.key
    assert excinfo.value.budget == 1
    assert excinfo.value.estimate > 1
