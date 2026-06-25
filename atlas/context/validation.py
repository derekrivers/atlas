"""Context pack validation (ATLAS-60), per docs/atlas/context-renderer.md
§"Validation".

A pure, reusable validator for a :class:`ContextPack`: the five spec checks —
*all anchors resolve at the recorded SHAs; objective and ≥1 acceptance criterion
present; ≥1 test command; token_estimate ≤ budget; no DRAFT lessons included.*
This module provides the callable only; wiring it into the PM Engine's
``promote_ready`` (the spec's "and the same checks ... before promotion" gate) is
a SEPARATE ticket — nothing here imports or touches PM-engine code.

It mirrors the COLLECT-ALL idiom of ``atlas/dependencies/validation.py``
(``validate_graph`` aggregates every violation rather than raising on the first),
but with one intentional divergence: that module RAISES a typed aggregate; this
validator RETURNS a :class:`ContextPackValidation` result. Invalidity is data,
not an exception — a pre-promotion gate must answer "valid or not, and why"
without throwing. Anchor *resolution* still raises internally (the ATLAS-21
typed-anchor errors); those are caught and converted to failure strings.

Inputs mirror the builder's. A ``ContextPack`` alone cannot answer two of the
five checks — it stores lesson **UUIDs** (not status) and ``relevant_docs``
**paths** (not the original ``path#slug`` anchors) — so the validator takes the
same external ``documents``/``lessons``/``ticket`` the builder consumed. The
anchor check DEGRADES by available input: path-level always, slug-level only when
a ``ticket`` is supplied (its ``source_anchor`` is the only ``path#slug`` to
resolve). ``anchor_check_depth`` records which depth ran so a reader knows whether
the spec-true slug check executed or only the path-level one.

Pure: no I/O, no clock, no storage. Validation READS; it never rebuilds or
repairs (that is the builder's and a future repair ticket's concern).
"""

from __future__ import annotations

from dataclasses import dataclass

from atlas.context.pack import DEFAULT_TOKEN_BUDGET
from atlas.core.anchors import (
    AnchorIndex,
    IngestionError,
    SourceDocument,
)
from atlas.core.enums import EntityStatus
from atlas.core.models.context_pack import ContextPack
from atlas.core.models.lesson import Lesson
from atlas.core.models.ticket import Ticket

# The two anchor-check depths recorded on the result (D5). "slug" means the
# spec-true ``source_anchor`` resolution ran (a ticket was supplied); "path" means
# only the path-level SHA check ran. When no ticket is given, "path" is honest —
# not a pass-by-omission.
ANCHOR_DEPTH_PATH = "path"
ANCHOR_DEPTH_SLUG = "slug"


@dataclass(frozen=True)
class ContextPackValidation:
    """The result of :func:`validate_context_pack` (D7).

    ``failures`` carries one human-readable message per failed check, each naming
    the check and the specific cause; ``valid`` is exactly ``len(failures) == 0``.
    ``anchor_check_depth`` is ``"slug"`` when a ticket let the slug-level check run
    and ``"path"`` otherwise. Frozen: the result is an immutable record, never
    mutated after construction.
    """

    valid: bool
    failures: tuple[str, ...]
    anchor_check_depth: str


def validate_context_pack(
    pack: ContextPack,
    *,
    documents: list[SourceDocument],
    lessons: list[Lesson],
    ticket: Ticket | None = None,
    budget: int = DEFAULT_TOKEN_BUDGET,
) -> ContextPackValidation:
    """Validate ``pack`` against the five context-renderer.md spec checks.

    Returns a :class:`ContextPackValidation`; it NEVER raises (invalidity is
    data). It COLLECTS every failure — it does not short-circuit on the first —
    so an operator sees every problem at once.

    The checks:

    - **D1 objective present:** ``pack.objective`` is non-empty after strip.
    - **D2 ≥1 acceptance criterion:** ``pack.acceptance_criteria`` non-empty.
    - **D3 ≥1 test command:** ``pack.test_commands`` non-empty.
    - **D4 token_estimate ≤ budget:** ``token_estimate`` is set and within
      ``budget``. Belt-and-suspenders: post-ATLAS-55 an over-budget pack cannot be
      built (``build_context_pack`` raises), so this guards a persisted/
      round-tripped pack.
    - **D5 anchors resolve, degrading by input:** every ``relevant_docs`` path is
      recorded in ``input_doc_shas`` and is an indexed document whose recorded SHA
      matches the :class:`AnchorIndex` SHA (path-level, always). When ``ticket`` is
      supplied, ``ticket.source_anchor`` is additionally resolved against the index
      (slug-level); a typed anchor error becomes a failure carrying its reason.
      ``anchor_check_depth`` records which ran.
    - **D6 no DRAFT lessons (defensive re-verify):** each ``historical_lessons``
      UUID resolves to a known, ACTIVE :class:`Lesson`. ``select_lessons`` is
      ACTIVE-only by construction, so a normally-built pack passes; this defends a
      tampered/round-tripped/externally-built pack — a dangling UUID is reported
      distinctly from a non-ACTIVE status.

    Args:
        pack: the pack under validation.
        documents: the source documents the pack was built from — the anchor
            index is built from these (mirrors the builder's ``documents``).
        lessons: the lesson listing — ``historical_lessons`` UUIDs resolve here.
        ticket: when supplied, enables the slug-level ``source_anchor`` check;
            ``None`` runs path-level only and records depth ``"path"``.
        budget: the token budget the estimate must not exceed.
    """
    failures: list[str] = []

    # D1 — objective present.
    if not pack.objective.strip():
        failures.append("objective: missing or empty")

    # D2 — at least one acceptance criterion.
    if not pack.acceptance_criteria:
        failures.append("acceptance_criteria: at least one is required, found none")

    # D3 — at least one test command.
    if not pack.test_commands:
        failures.append("test_commands: at least one is required, found none")

    # D4 — token_estimate within budget (guards a persisted/round-tripped pack).
    if pack.token_estimate is None:
        failures.append("token_estimate: missing (cannot confirm it is within budget)")
    elif pack.token_estimate > budget:
        failures.append(
            f"token_estimate: {pack.token_estimate} exceeds budget {budget}"
        )

    # D5 — anchors resolve, degrading by available input.
    index = AnchorIndex.build(documents)
    failures.extend(_path_anchor_failures(pack, index))
    depth = ANCHOR_DEPTH_PATH
    if ticket is not None:
        depth = ANCHOR_DEPTH_SLUG
        failures.extend(_slug_anchor_failures(ticket, index))

    # D6 — no DRAFT (or otherwise non-ACTIVE / dangling) lessons.
    failures.extend(_lesson_failures(pack, lessons))

    return ContextPackValidation(
        valid=len(failures) == 0,
        failures=tuple(failures),
        anchor_check_depth=depth,
    )


def _path_anchor_failures(pack: ContextPack, index: AnchorIndex) -> list[str]:
    """Path-level anchor check (always runs).

    Every ``relevant_docs`` path must be recorded in ``input_doc_shas`` AND be an
    indexed document whose recorded SHA matches the index's SHA for that path. A
    missing path, an un-indexed path, or a SHA mismatch is a failure.
    """
    failures: list[str] = []
    index_shas = index.input_doc_shas
    for path in pack.relevant_docs:
        if path not in pack.input_doc_shas:
            failures.append(f"anchor[{path}]: path is not recorded in input_doc_shas")
            continue
        recorded_sha = pack.input_doc_shas[path]
        if path not in index_shas:
            failures.append(f"anchor[{path}]: path is not in the indexed document set")
            continue
        if index_shas[path] != recorded_sha:
            failures.append(
                f"anchor[{path}]: recorded SHA {recorded_sha!r} does not match "
                f"the document SHA {index_shas[path]!r}"
            )
    return failures


def _slug_anchor_failures(ticket: Ticket, index: AnchorIndex) -> list[str]:
    """Slug-level anchor check (runs only when a ticket is supplied).

    Resolves ``ticket.source_anchor`` (``path#slug``) against the index; a
    :class:`MalformedAnchorError` / :class:`UnknownDocumentError` /
    :class:`UnknownAnchorError` (caught via their shared :class:`IngestionError`
    base) becomes a failure carrying the typed reason.
    """
    try:
        index.resolve(ticket.source_anchor)
    except IngestionError as error:
        return [f"anchor[{ticket.source_anchor}]: {type(error).__name__}: {error}"]
    return []


def _lesson_failures(pack: ContextPack, lessons: list[Lesson]) -> list[str]:
    """Defensive lesson re-verify (D6).

    Each ``historical_lessons`` UUID must resolve to a known, ACTIVE lesson. A
    dangling UUID (no matching :class:`Lesson`) is reported distinctly from a
    found-but-non-ACTIVE lesson, whose offending status is named.
    """
    by_id = {lesson.id: lesson for lesson in lessons}
    failures: list[str] = []
    for lesson_id in pack.historical_lessons:
        lesson = by_id.get(lesson_id)
        if lesson is None:
            failures.append(
                f"lesson[{lesson_id}]: referenced lesson not found (dangling)"
            )
        elif lesson.status != EntityStatus.ACTIVE:
            failures.append(
                f"lesson[{lesson_id}]: status is {lesson.status.value!r}, "
                "expected 'active'"
            )
    return failures
