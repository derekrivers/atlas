"""Context pack generation (ATLAS-56), per docs/atlas/context-renderer.md.

The Phase 5 assembler: the first consumer of all four context retrievers
(ADR/-51, doc/-52, lesson/-53, related-ticket/-54). It folds their output and
the verbatim ticket fields (rule 5) into one :class:`ContextPack`, composes the
fixed-order ``rendered_markdown`` (the "Rendered structure" section), and
records ``input_doc_shas`` so a doc edit after rendering is detectable (the
"Staleness and provenance" section, and the Phase 5 milestone's second half).

Pure builder: ZERO storage/git/file/model-API calls and no
``collect_input_documents`` — every input is already loaded (``graph`` is an
ATLAS-31 projection, ``documents`` the caller's collection — the §2.1 corpus
plus the committed ``processed/`` stubs, so stub-minted anchors resolve
(ATLAS-162) — and ``accepted_adrs``/``lessons`` are listings). Loading them
and the CLI are ATLAS-58; this builder neither loads nor validates (ATLAS-60).
It imports only the four ``atlas.context`` retrievers, ``atlas.core.*``,
networkx, and stdlib —
nothing above ``atlas.context`` in the spine (lint-imports confirms no
``atlas.planning`` edge).

The token-budget check is fail-closed: an over-budget pack is a planning smell
(an oversized ticket), so it RAISES rather than silently truncating
(context-renderer.md "Token budget and compression ladder"). The four
compression rungs (ATLAS-55) run BEFORE that raise: when the pack is over budget
the ladder compresses in spec order, re-rendering and re-estimating after each
rung, and only raises if still over after all four. Which rungs fired is recorded
on the pack as ``compression_applied`` (D3). Compression is a RENDER concern, not
a retriever one (D4): the structured reference lists (the UUIDs) are untouched —
only ``rendered_markdown`` and ``token_estimate`` shrink. The rungs are a
render-level parameter threaded into ``_render_markdown`` and the section helpers,
never string surgery on the assembled markdown.

One field reconciliation remains a reported follow-up, not resolved here:
``constraints``/``risks``/``context`` have no clean Ticket source (constraints
is always ``[]`` in v1; risks is a derived risk-level line; context folds into
the Objective render).

The related-ticket section's compressed-vs-full contract is now owned by rung 2
(``related_objectives_to_key_title``): the full form is key+title+status and the
compressed form is key+title. The one-line objective stays unrendered as a closed
decision — the ``project_graph`` ticket node carries key/title/status but no
objective, and ATLAS-31 deliberately excludes free-text bodies, so sourcing it
would need a graph-node enrichment outside this renderer (ATLAS-56 debt retired).
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

# The four compression rung identifiers (D1/D3): stable strings, never free-form,
# the only legal ``ContextPack.compression_applied`` values. Each names the render
# concession it applies.
RUNG_LESSON_BODIES_TO_TITLES = "lesson_bodies_to_titles"
RUNG_RELATED_OBJECTIVES_TO_KEY_TITLE = "related_objectives_to_key_title"
RUNG_DOC_SECTIONS_TO_FIRST_PARA = "doc_sections_to_first_para"
RUNG_ADR_CONSEQUENCES_DROPPED = "adr_consequences_dropped"

# The ladder, in spec order (context-renderer.md "Token budget and compression
# ladder"). Rungs are cumulative: a later rung's render keeps every earlier
# rung's compression.
COMPRESSION_LADDER: tuple[str, ...] = (
    RUNG_LESSON_BODIES_TO_TITLES,
    RUNG_RELATED_OBJECTIVES_TO_KEY_TITLE,
    RUNG_DOC_SECTIONS_TO_FIRST_PARA,
    RUNG_ADR_CONSEQUENCES_DROPPED,
)


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

    When the rendered pack is over ``budget``, the four-rung compression ladder
    (``COMPRESSION_LADDER``) runs in spec order, re-rendering and re-estimating
    after each rung and stopping the moment it is under; the rungs that fired are
    recorded on ``compression_applied`` (D1/D3). Only if it is still over budget
    after all four does it raise :class:`ContextBudgetExceededError` (fail-closed
    — an over-budget pack is an oversized ticket, reported not truncated; D2).
    Retriever preconditions propagate: ``select_adrs`` /
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

    def render(applied: frozenset[str]) -> str:
        return _render_markdown(
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
            applied=applied,
        )

    # The compression ladder (D1): render uncompressed, estimate, and while over
    # budget apply the next rung in spec order, re-render, re-estimate, stopping
    # the moment it is under. Each rung is cumulative (its render keeps the
    # earlier rungs). chars/4 is monotonic (D5), so the estimate falls as rungs
    # fire. ``compression_applied`` is the ordered prefix that fired (empty when
    # under budget unchanged).
    compression_applied: list[str] = []
    rendered_markdown = render(frozenset(compression_applied))
    token_estimate = len(rendered_markdown) // 4
    for rung in COMPRESSION_LADDER:
        if token_estimate <= budget:
            break
        compression_applied.append(rung)
        rendered_markdown = render(frozenset(compression_applied))
        token_estimate = len(rendered_markdown) // 4

    # Still over after the full ladder: a planning smell, raised not truncated
    # (D2). compression_applied lists all four rungs, so the operator sees the
    # ladder genuinely ran.
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
        compression_applied=compression_applied,
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
    applied: frozenset[str] = frozenset(),
) -> str:
    """Compose ``rendered_markdown`` in the fixed section order, omitting any
    empty section header-and-all (context-renderer.md "Rendered structure").

    Order: Objective, Constraints, Acceptance Criteria, Non-goals, Relevant Docs,
    ADRs, Related Tickets, Lessons, Risks, Test Commands, Definition of Done.
    Present sections keep this relative order; an empty section is dropped whole.

    ``applied`` is the set of active compression rung ids (D1): each section
    helper that owns a rung renders compactly when its id is present, so a
    re-render after a fired rung is terser. An empty ``applied`` is the
    uncompressed render.
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
    sections.append(
        (
            "Relevant Docs",
            _render_docs(
                doc, first_para_only=RUNG_DOC_SECTIONS_TO_FIRST_PARA in applied
            ),
        )
    )
    sections.append(
        (
            "ADRs",
            _render_adrs(
                adr_matches,
                accepted_adrs,
                drop_consequences=RUNG_ADR_CONSEQUENCES_DROPPED in applied,
            ),
        )
    )
    sections.append(
        (
            "Related Tickets",
            _render_related(
                related_matches,
                graph,
                key_title_only=RUNG_RELATED_OBJECTIVES_TO_KEY_TITLE in applied,
            ),
        )
    )
    sections.append(
        (
            "Lessons",
            _render_lessons(
                lesson_matches,
                lessons,
                titles_only=RUNG_LESSON_BODIES_TO_TITLES in applied,
            ),
        )
    )
    sections.append(("Risks", _render_list(risks)))
    sections.append(("Test Commands", _render_list(ticket.test_requirements)))
    sections.append(("Definition of Done", _render_list(ticket.definition_of_done)))

    blocks = [f"## {title}\n\n{body}" for title, body in sections if body.strip()]
    return "\n\n".join(blocks)


def _render_list(items: list[str]) -> str:
    """A markdown bullet list of ``items`` verbatim, or "" when empty (so the
    caller omits the whole section)."""
    return "\n".join(f"- {item}" for item in items)


def _render_docs(doc: DocContext, *, first_para_only: bool = False) -> str:
    """The anchored section: parent-heading breadcrumb + heading + verbatim
    body, then the recorded reference paths.

    Rung 3 (``doc_sections_to_first_para``): when ``first_para_only`` is set the
    body is trimmed to its first paragraph plus any command-bearing fenced code
    blocks (:func:`_compress_doc_body`). The references render unchanged — they
    are repo-relative paths the doc retriever matched exactly (ATLAS-52 path
    contract; the upstream planner-emit format is reconciled outside this
    renderer)."""
    section = doc.section
    parts: list[str] = []
    if section.parent_headings:
        parts.append(" > ".join(section.parent_headings))
    parts.append(f"### {section.heading}")
    body = _compress_doc_body(section.body) if first_para_only else section.body
    if body.strip():
        parts.append(body)
    if doc.references:
        ref_lines = "\n".join(f"- {ref.path}" for ref in doc.references)
        parts.append(f"References:\n{ref_lines}")
    return "\n\n".join(parts)


def _compress_doc_body(body: str) -> str:
    """Trim a doc section body to its first paragraph plus any fenced code blocks
    containing commands (rung 3, context-renderer.md).

    Deterministic single pass: keep the first run of consecutive non-blank prose
    lines (the orienting paragraph, leading blanks skipped) and drop all prose
    after it; independently, keep every fenced ```-delimited block whose content
    has at least one non-blank line (a v1 "contains commands" heuristic — a code
    block in a doc section is an example/command worth preserving; a finer
    command classifier is deferred). Lines are kept in their original order."""
    lines = body.split("\n")
    kept: list[str] = []
    in_fence = False
    fence_buf: list[str] = []
    first_para_started = False
    first_para_done = False
    for line in lines:
        if line.lstrip().startswith("```"):
            if not in_fence:
                in_fence = True
                fence_buf = [line]
            else:
                in_fence = False
                fence_buf.append(line)
                if any(content.strip() for content in fence_buf[1:-1]):
                    kept.extend(fence_buf)
                fence_buf = []
            continue
        if in_fence:
            fence_buf.append(line)
            continue
        if first_para_done:
            continue
        if line.strip():
            first_para_started = True
            kept.append(line)
        elif first_para_started:
            first_para_done = True
    # An unterminated fence (no closing ```): keep it if it has command content,
    # so a malformed block is not silently dropped.
    if in_fence and any(content.strip() for content in fence_buf[1:]):
        kept.extend(fence_buf)
    return "\n".join(kept)


def _render_adrs(
    adr_matches: list[ADRMatch],
    accepted_adrs: list[ArchitectureDecisionRecord],
    *,
    drop_consequences: bool = False,
) -> str:
    """Per ADRMatch, look up the ADR by ``adr_id`` and render decision +
    consequences only (rule 2 — context and alternatives omitted).

    Rung 4 (``adr_consequences_dropped``): when ``drop_consequences`` is set the
    consequences are omitted, leaving the decision alone."""
    by_id: dict[UUID, ArchitectureDecisionRecord] = {a.id: a for a in accepted_adrs}
    blocks: list[str] = []
    for match in adr_matches:
        adr = by_id.get(match.adr_id)
        if adr is None:
            continue
        block = [f"### {match.key}: {adr.title}", adr.decision]
        if adr.consequences and not drop_consequences:
            consequences = "\n".join(f"- {c}" for c in adr.consequences)
            block.append(f"Consequences:\n{consequences}")
        blocks.append("\n\n".join(block))
    return "\n\n".join(blocks)


def _render_related(
    related_matches: list[RelatedTicket],
    graph: nx.DiGraph[str],
    *,
    key_title_only: bool = False,
) -> str:
    """Per RelatedTicket, render the related-ticket line off the graph node.

    Full form is key + title + status; rung 2 (``related_objectives_to_key_title``)
    sets ``key_title_only`` to drop the status, leaving key + title. The one-line
    objective is intentionally never rendered (a closed decision, not a follow-up):
    the graph node carries key/title/status but no objective, and ATLAS-31
    excludes free-text bodies, so sourcing it would need a graph-node enrichment
    outside this renderer."""
    lines: list[str] = []
    for match in related_matches:
        node = graph.nodes[match.key]
        if key_title_only:
            lines.append(f"- {match.key}: {node['title']}")
        else:
            lines.append(f"- {match.key}: {node['title']} ({node['status']})")
    return "\n".join(lines)


def _render_lessons(
    lesson_matches: list[LessonMatch],
    lessons: list[Lesson],
    *,
    titles_only: bool = False,
) -> str:
    """Per LessonMatch, look up the lesson by ``lesson_id`` and render its body
    (title, problem, solution, outcome) per rule 4.

    Rung 1 (``lesson_bodies_to_titles``): when ``titles_only`` is set only the
    ``### {title}`` line is rendered — problem/solution/outcome dropped."""
    by_id: dict[UUID, Lesson] = {lesson.id: lesson for lesson in lessons}
    blocks: list[str] = []
    for match in lesson_matches:
        lesson = by_id.get(match.lesson_id)
        if lesson is None:
            continue
        if titles_only:
            blocks.append(f"### {lesson.title}")
        else:
            blocks.append(
                f"### {lesson.title}\n\n"
                f"Problem: {lesson.problem}\n\n"
                f"Solution: {lesson.solution}\n\n"
                f"Outcome: {lesson.outcome}"
            )
    return "\n\n".join(blocks)
