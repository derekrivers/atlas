"""Context pack generation (ATLAS-56), per docs/atlas/context-renderer.md.

The Phase 5 assembler: the first consumer of all four context retrievers
(ADR/-51, doc/-52, lesson/-53, related-ticket/-54). It folds their output and
the verbatim ticket fields (rule 5) into one :class:`ContextPack`, composes the
fixed-order ``rendered_markdown`` (the "Rendered structure" section), and
records ``input_doc_shas`` so a doc edit after rendering is detectable (the
"Staleness and provenance" section, and the Phase 5 milestone's second half).

Pure builder: ZERO storage/git/file/model-API calls and no
``collect_input_documents`` — every input is already loaded (``graph`` is an
ATLAS-31 projection, ``documents`` the ``collect_input_documents`` output,
``accepted_adrs``/``lessons`` are listings). Loading them and the CLI are
ATLAS-58; this builder neither loads nor validates (ATLAS-60). It imports only
the four ``atlas.context`` retrievers, ``atlas.core.*``, networkx, and stdlib —
nothing above ``atlas.context`` in the spine (lint-imports confirms no
``atlas.planning`` edge).

The token-budget check is fail-closed: an over-budget pack is a planning smell
(an oversized ticket), so it RAISES rather than silently truncating
(context-renderer.md "Token budget and compression ladder"). The four
compression rungs are ATLAS-55, inserted BEFORE this raise; only the terminal
raise lives here.

Two field reconciliations are reported as follow-ups, not resolved here:
``constraints``/``risks``/``context`` have no clean Ticket source (constraints
is always ``[]`` in v1; risks is a derived risk-level line; context folds into
the Objective render), and the related-ticket one-line objective is not rendered
because the graph node carries key/title/status but not objective.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import networkx as nx

from atlas.context.adr_retrieval import ADRMatch, select_adrs
from atlas.context.doc_retrieval import DocContext, select_doc_sections
from atlas.context.lesson_retrieval import LessonMatch, select_lessons
from atlas.context.related_tickets import RelatedTicket, select_related_tickets
from atlas.core.anchors import SourceDocument
from atlas.core.models.adr import ArchitectureDecisionRecord
from atlas.core.models.context_pack import ContextPack
from atlas.core.models.lesson import Lesson
from atlas.core.models.ticket import Ticket

# Default token budget (context-renderer.md: "Default budget: 12,000 tokens").
DEFAULT_TOKEN_BUDGET = 12000


class ContextBudgetExceededError(Exception):
    """Raised when a pack's ``token_estimate`` exceeds the budget.

    An over-budget pack is a planning smell (an oversized ticket), reported
    rather than silently truncated (context-renderer.md). Carries the ticket
    key, the estimate, and the budget so the caller can surface all three.
    """

    def __init__(self, ticket_key: str, estimate: int, budget: int) -> None:
        self.ticket_key = ticket_key
        self.estimate = estimate
        self.budget = budget
        super().__init__(
            f"context pack for {ticket_key!r} is over budget: "
            f"token_estimate {estimate} > budget {budget}"
        )


def build_context_pack(
    ticket: Ticket,
    *,
    graph: nx.DiGraph[str],
    documents: list[SourceDocument],
    accepted_adrs: list[ArchitectureDecisionRecord],
    lessons: list[Lesson],
    budget: int = DEFAULT_TOKEN_BUDGET,
) -> ContextPack:
    """Assemble the :class:`ContextPack` for ``ticket`` from the four retrievers
    and the verbatim ticket fields.

    Pure: it calls the retrievers over the already-loaded inputs, maps their
    output onto the pack's structured reference lists, composes the fixed-order
    ``rendered_markdown``, and records ``input_doc_shas``. It performs no I/O.

    Raises :class:`ContextBudgetExceededError` if ``token_estimate`` exceeds
    ``budget`` (fail-closed; ATLAS-55's compression rungs would run before this
    raise). Retriever preconditions propagate: ``select_adrs`` /
    ``select_related_tickets`` raise on a missing/non-ticket graph node, and
    ``select_doc_sections`` raises on an unresolvable ``source_anchor``.
    """
    adr_matches = select_adrs(graph, ticket, accepted_adrs)
    related_matches = select_related_tickets(graph, ticket)
    lesson_matches = select_lessons(lessons, ticket)
    doc = select_doc_sections(documents, ticket)

    # Structured reference lists carry the retrievers' selected UUIDs (D2);
    # rendered_markdown carries the human-readable form.
    relevant_adrs = [m.adr_id for m in adr_matches]
    related_tickets = [m.ticket_id for m in related_matches]
    historical_lessons = [m.lesson_id for m in lesson_matches]
    relevant_docs = [doc.section.path, *(r.path for r in doc.references)]
    input_doc_shas = {doc.section.path: doc.section.sha} | {
        r.path: r.sha for r in doc.references
    }

    # Gap fields (D4): no clean Ticket source. constraints is always [] in v1
    # (section omitted when empty); risks is a derived risk-level line; context
    # folds into the rendered Objective, not a structured field.
    constraints: list[str] = []
    risks = [f"Risk level: {ticket.risk_level.value}"]

    rendered_markdown = _render_markdown(
        ticket=ticket,
        doc=doc,
        adr_matches=adr_matches,
        accepted_adrs=accepted_adrs,
        related_matches=related_matches,
        graph=graph,
        lesson_matches=lesson_matches,
        lessons=lessons,
        constraints=constraints,
        risks=risks,
    )

    # chars/4: monotonicity, not precision (context-renderer.md).
    token_estimate = len(rendered_markdown) // 4
    if token_estimate > budget:
        raise ContextBudgetExceededError(ticket.key, token_estimate, budget)

    return ContextPack(
        id=uuid4(),
        product_id=ticket.product_id,
        ticket_id=ticket.id,
        title=ticket.title,
        objective=ticket.objective,
        constraints=constraints,
        relevant_docs=relevant_docs,
        relevant_adrs=relevant_adrs,
        related_tickets=related_tickets,
        historical_lessons=historical_lessons,
        acceptance_criteria=ticket.acceptance_criteria,
        risks=risks,
        # Rule 5 names "test requirements"; the pack field is test_commands.
        test_commands=ticket.test_requirements,
        definition_of_done=ticket.definition_of_done,
        rendered_markdown=rendered_markdown,
        input_doc_shas=input_doc_shas,
        token_estimate=token_estimate,
        created_at=datetime.now(UTC),
    )


def _render_markdown(
    *,
    ticket: Ticket,
    doc: DocContext,
    adr_matches: list[ADRMatch],
    accepted_adrs: list[ArchitectureDecisionRecord],
    related_matches: list[RelatedTicket],
    graph: nx.DiGraph[str],
    lesson_matches: list[LessonMatch],
    lessons: list[Lesson],
    constraints: list[str],
    risks: list[str],
) -> str:
    """Compose ``rendered_markdown`` in the fixed section order, omitting any
    empty section header-and-all (context-renderer.md "Rendered structure").

    Order: Objective, Constraints, Acceptance Criteria, Non-goals, Relevant Docs,
    ADRs, Related Tickets, Lessons, Risks, Test Commands, Definition of Done.
    Present sections keep this relative order; an empty section is dropped whole.
    """
    sections: list[tuple[str, str]] = []

    # Objective: ticket.objective, then ticket.context folded in (D4 — context
    # has no structured field; it is render-only here).
    objective_parts = [ticket.objective.strip()]
    if ticket.context.strip():
        objective_parts.append(ticket.context.strip())
    sections.append(("Objective", "\n\n".join(p for p in objective_parts if p)))

    sections.append(("Constraints", _render_list(constraints)))
    sections.append(("Acceptance Criteria", _render_list(ticket.acceptance_criteria)))
    sections.append(("Non-goals", _render_list(ticket.non_goals)))
    sections.append(("Relevant Docs", _render_docs(doc)))
    sections.append(("ADRs", _render_adrs(adr_matches, accepted_adrs)))
    sections.append(("Related Tickets", _render_related(related_matches, graph)))
    sections.append(("Lessons", _render_lessons(lesson_matches, lessons)))
    sections.append(("Risks", _render_list(risks)))
    sections.append(("Test Commands", _render_list(ticket.test_requirements)))
    sections.append(("Definition of Done", _render_list(ticket.definition_of_done)))

    blocks = [f"## {title}\n\n{body}" for title, body in sections if body.strip()]
    return "\n\n".join(blocks)


def _render_list(items: list[str]) -> str:
    """A markdown bullet list of ``items`` verbatim, or "" when empty (so the
    caller omits the whole section)."""
    return "\n".join(f"- {item}" for item in items)


def _render_docs(doc: DocContext) -> str:
    """The anchored section: parent-heading breadcrumb + heading + verbatim
    body, then the recorded reference paths."""
    section = doc.section
    parts: list[str] = []
    if section.parent_headings:
        parts.append(" > ".join(section.parent_headings))
    parts.append(f"### {section.heading}")
    if section.body.strip():
        parts.append(section.body)
    if doc.references:
        ref_lines = "\n".join(f"- {ref.path}" for ref in doc.references)
        parts.append(f"References:\n{ref_lines}")
    return "\n\n".join(parts)


def _render_adrs(
    adr_matches: list[ADRMatch], accepted_adrs: list[ArchitectureDecisionRecord]
) -> str:
    """Per ADRMatch, look up the ADR by ``adr_id`` and render decision +
    consequences only (rule 2 — context and alternatives omitted)."""
    by_id: dict[UUID, ArchitectureDecisionRecord] = {a.id: a for a in accepted_adrs}
    blocks: list[str] = []
    for match in adr_matches:
        adr = by_id.get(match.adr_id)
        if adr is None:
            continue
        block = [f"### {match.key}: {adr.title}", adr.decision]
        if adr.consequences:
            consequences = "\n".join(f"- {c}" for c in adr.consequences)
            block.append(f"Consequences:\n{consequences}")
        blocks.append("\n\n".join(block))
    return "\n\n".join(blocks)


def _render_related(
    related_matches: list[RelatedTicket], graph: nx.DiGraph[str]
) -> str:
    """Per RelatedTicket, render key + title + status off the graph node.

    The one-line objective is NOT rendered — the graph node carries
    key/title/status but not objective (reported as a follow-up).
    """
    lines: list[str] = []
    for match in related_matches:
        node = graph.nodes[match.key]
        lines.append(f"- {match.key}: {node['title']} ({node['status']})")
    return "\n".join(lines)


def _render_lessons(lesson_matches: list[LessonMatch], lessons: list[Lesson]) -> str:
    """Per LessonMatch, look up the lesson by ``lesson_id`` and render its body
    (title, problem, solution, outcome) per rule 4."""
    by_id: dict[UUID, Lesson] = {lesson.id: lesson for lesson in lessons}
    blocks: list[str] = []
    for match in lesson_matches:
        lesson = by_id.get(match.lesson_id)
        if lesson is None:
            continue
        blocks.append(
            f"### {lesson.title}\n\n"
            f"Problem: {lesson.problem}\n\n"
            f"Solution: {lesson.solution}\n\n"
            f"Outcome: {lesson.outcome}"
        )
    return "\n\n".join(blocks)
