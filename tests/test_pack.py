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

from atlas.context import (
    ContextBudgetExceededError,
    build_context_pack,
    select_adrs,
    select_doc_sections,
    select_lessons,
    select_related_tickets,
)
from atlas.context.pack import (
    COMPRESSION_LADDER,
    RUNG_ADR_CONSEQUENCES_DROPPED,
    RUNG_DOC_SECTIONS_TO_FIRST_PARA,
    RUNG_LESSON_BODIES_TO_TITLES,
    RUNG_RELATED_OBJECTIVES_TO_KEY_TITLE,
    _render_markdown,
)
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


# --- compression ladder (ATLAS-55) ----------------------------------------

# A heavy filler paragraph: dominant in any section it lands in, so a rung that
# drops it moves the token estimate by a wide, unambiguous margin.
_HEAVY = "Filler sentence that adds budget pressure. " * 40

# The heavy anchor doc: a short orienting first paragraph (ORIENT_MARKER), a
# blank line, a heavy second paragraph (DROP_MARKER), a command code block, and
# heavy trailing prose — so rung 3 keeps the first paragraph and the command
# block while dropping the rest.
_HEAVY_ANCHOR = "\n".join(
    [
        "# Atlas",
        "## Target Section",
        "ORIENT_MARKER orienting line.",
        "",
        f"DROP_MARKER {_HEAVY}",
        "",
        "```sh",
        "atlas context render KEY",
        "```",
        "",
        f"trailing {_HEAVY}",
        "## Next Section",
        "next body",
    ]
)


def heavy_corpus() -> list[SourceDocument]:
    return [
        SourceDocument(path="main.md", sha="sha-main", content=_HEAVY_ANCHOR),
        SourceDocument(path="ref.md", sha="sha-ref", content="# Ref\nref body"),
    ]


def heavy_inputs() -> dict[str, Any]:
    """Inputs whose every compressible section carries enough content that each
    ladder rung lowers the estimate by a strictly positive margin: heavy lesson
    bodies (rung 1), two related tickets with status (rung 2), a heavy doc
    section (rung 3), and heavy ADR consequences (rung 4)."""
    ticket = make_ticket()
    target = make_ticket(id=uuid4(), key="ATLAS-99", title="Target dep", status="done")
    dependent = make_ticket(
        id=uuid4(), key="ATLAS-77", title="Dependent work", status="done"
    )
    adr = make_adr(
        5,
        title="Code calculates, agents interpret",
        decision="Code computes; agents only interpret.",
        consequences=[f"CONSEQUENCE_MARKER {_HEAVY}", _HEAVY],
        alternatives_considered=["Let agents compute."],
    )
    lesson = make_lesson(
        title="LESSON_TITLE_MARKER",
        problem=f"PROBLEM_MARKER {_HEAVY}",
        solution=_HEAVY,
        outcome=_HEAVY,
        tags=["context"],
    )
    graph = project_graph(
        [ticket, target, dependent],
        [],
        [adr],
        [
            depends_on(ticket, target.id),
            depends_on(dependent, ticket.id),
            depends_on(ticket, adr.id, "adr"),
        ],
    )
    return {
        "ticket": ticket,
        "graph": graph,
        "documents": heavy_corpus(),
        "accepted_adrs": [adr],
        "lessons": [lesson],
    }


def cumulative_estimates(inputs: dict[str, Any]) -> list[int]:
    """The token estimate at each ladder prefix (uncompressed, then after rungs
    1, 1-2, 1-3, 1-4): the thresholds the builder's loop compares against, so a
    budget placed in an open interval forces an exact rung prefix."""
    ticket = inputs["ticket"]
    graph = inputs["graph"]
    doc = select_doc_sections(inputs["documents"], ticket)
    adr_matches = select_adrs(graph, ticket, inputs["accepted_adrs"])
    related_matches = select_related_tickets(graph, ticket)
    lesson_matches = select_lessons(inputs["lessons"], ticket)
    risks = [f"Risk level: {ticket.risk_level.value}"]

    def estimate(applied: frozenset[str]) -> int:
        md = _render_markdown(
            ticket=ticket,
            doc=doc,
            adr_matches=adr_matches,
            accepted_adrs=inputs["accepted_adrs"],
            related_matches=related_matches,
            graph=graph,
            lesson_matches=lesson_matches,
            lessons=inputs["lessons"],
            constraints=[],
            risks=risks,
            applied=applied,
        )
        return len(md) // 4

    return [
        estimate(frozenset(COMPRESSION_LADDER[:i]))
        for i in range(len(COMPRESSION_LADDER) + 1)
    ]


def test_under_budget_applies_no_compression() -> None:
    # The wrong answer: firing a rung when already under budget.
    pack, *_ = build_full()
    assert pack.compression_applied == []


def test_rungs_fire_in_spec_order_and_stop_when_under() -> None:
    # Each rung lowers the estimate, so a budget in (e[k], e[k-1]) fires exactly
    # the first k rungs and stops. The wrong answer: applying a later rung after
    # already under budget, or out of order.
    inputs = heavy_inputs()
    e = cumulative_estimates(inputs)
    assert e[0] > e[1] > e[2] > e[3] > e[4], e

    expected_prefix = [
        [RUNG_LESSON_BODIES_TO_TITLES],
        [RUNG_LESSON_BODIES_TO_TITLES, RUNG_RELATED_OBJECTIVES_TO_KEY_TITLE],
        [
            RUNG_LESSON_BODIES_TO_TITLES,
            RUNG_RELATED_OBJECTIVES_TO_KEY_TITLE,
            RUNG_DOC_SECTIONS_TO_FIRST_PARA,
        ],
        list(COMPRESSION_LADDER),
    ]
    for k, prefix in enumerate(expected_prefix, start=1):
        budget = (e[k] + e[k - 1]) // 2
        pack = build_context_pack(**inputs, budget=budget)
        assert pack.compression_applied == prefix, (k, budget)
        # The recorded estimate is the compressed one and is within budget.
        assert pack.token_estimate == e[k]
        assert pack.token_estimate <= budget


def test_rung_content_is_correct() -> None:
    inputs = heavy_inputs()
    e = cumulative_estimates(inputs)

    # Rung 1: lesson bodies gone, titles remain.
    pack1 = build_context_pack(**inputs, budget=(e[1] + e[0]) // 2)
    assert pack1.compression_applied == [RUNG_LESSON_BODIES_TO_TITLES]
    assert "LESSON_TITLE_MARKER" in pack1.rendered_markdown
    assert "PROBLEM_MARKER" not in pack1.rendered_markdown

    # Rung 2: related objectives/status gone, key+title remain.
    pack2 = build_context_pack(**inputs, budget=(e[2] + e[1]) // 2)
    assert pack2.compression_applied[-1] == RUNG_RELATED_OBJECTIVES_TO_KEY_TITLE
    assert "- ATLAS-99: Target dep" in pack2.rendered_markdown
    assert "- ATLAS-99: Target dep (done)" not in pack2.rendered_markdown

    # Rung 3: doc section keeps first paragraph + command code block; later prose
    # is dropped.
    pack3 = build_context_pack(**inputs, budget=(e[3] + e[2]) // 2)
    assert pack3.compression_applied[-1] == RUNG_DOC_SECTIONS_TO_FIRST_PARA
    assert "ORIENT_MARKER" in pack3.rendered_markdown
    assert "atlas context render KEY" in pack3.rendered_markdown
    assert "DROP_MARKER" not in pack3.rendered_markdown

    # Rung 4: ADR consequences dropped, decision kept.
    assert COMPRESSION_LADDER[-1] == RUNG_ADR_CONSEQUENCES_DROPPED
    pack4 = build_context_pack(**inputs, budget=(e[4] + e[3]) // 2)
    assert pack4.compression_applied == list(COMPRESSION_LADDER)
    assert "Code computes; agents only interpret." in pack4.rendered_markdown
    assert "CONSEQUENCE_MARKER" not in pack4.rendered_markdown


def test_still_over_after_full_ladder_raises_having_run_all_four() -> None:
    # The wrong answer: silent truncation, or returning an over-budget pack. The
    # estimate carried by the raise is the fully-compressed one, proving the full
    # ladder ran before the terminal raise.
    inputs = heavy_inputs()
    fully_compressed = cumulative_estimates(inputs)[-1]
    with pytest.raises(ContextBudgetExceededError) as excinfo:
        build_context_pack(**inputs, budget=1)
    assert excinfo.value.ticket_key == inputs["ticket"].key
    assert excinfo.value.estimate == fully_compressed


def test_compression_leaves_structured_references_unchanged() -> None:
    # The wrong answer: compression dropping a referenced entity. Only
    # rendered_markdown / token_estimate may differ between an uncompressed and a
    # compressed build of the same inputs.
    inputs = heavy_inputs()
    e = cumulative_estimates(inputs)
    uncompressed = build_context_pack(**inputs, budget=e[0] + 1000)
    compressed = build_context_pack(**inputs, budget=(e[4] + e[3]) // 2)

    assert uncompressed.compression_applied == []
    assert compressed.compression_applied == list(COMPRESSION_LADDER)

    assert compressed.relevant_adrs == uncompressed.relevant_adrs
    assert compressed.related_tickets == uncompressed.related_tickets
    assert compressed.historical_lessons == uncompressed.historical_lessons
    assert compressed.relevant_docs == uncompressed.relevant_docs
    assert compressed.input_doc_shas == uncompressed.input_doc_shas
    # The two levers that DO move.
    assert compressed.rendered_markdown != uncompressed.rendered_markdown
    assert compressed.token_estimate is not None
    assert uncompressed.token_estimate is not None
    assert compressed.token_estimate < uncompressed.token_estimate
