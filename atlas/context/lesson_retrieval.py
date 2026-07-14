"""Historical lesson retrieval (ATLAS-53), per docs/atlas/context-renderer.md
"Retrieval rules (v1)" rule 4 (Lessons).

Selects the lessons worth putting in front of an execution agent for one ticket.
One source: ``status: ACTIVE`` lessons (ADR-0009) whose tags intersect the
ticket's tag/ticket_type facets.

The pure :func:`select_lessons` selector takes an already-loaded lesson listing,
exactly as :func:`~atlas.context.adr_retrieval.select_adrs` takes the
accepted-ADR listing. The DB-backed :func:`retrieve_lessons` API enforces the
ADR-0009 ``ACTIVE`` filter in the SQL query before applying the same selector.
Both select *which* lessons; rendering each as problem/solution/outcome is
ATLAS-56's job. Each match carries the lesson's UUID from the start —
``ContextPack.historical_lessons`` is ``list[UUID]`` — so ATLAS-56 does
``[m.lesson_id for m in select_lessons(...)]``.

Deliberate divergences from :func:`select_adrs` (rule 2):

* **Whole-tag set intersection**, NOT ``adr_retrieval._tokenise``. That tokeniser
  splits prose ADR *titles* into stop-word-pruned alphanumeric runs; lessons match
  on their curated ``tags`` against the ticket's curated facets, so the operation
  is a normalised whole-tag set overlap — a single shared tag is a match. The two
  are different operations and are kept separate (no shared helper).
* **No ``*Source`` enum** — a lesson has a single selection source (tag overlap),
  so :class:`LessonMatch` records only the lesson id and the shared tags, with no
  provenance discriminator.
* **No precondition raise** — this retriever takes no graph, so there is no
  node-existence check (contrast :func:`select_adrs` / ``select_related_tickets``
  raising on a missing ticket node). An empty listing or no ACTIVE/tag match
  simply yields ``[]``.

Ranking is a total order so callers and tests can assert an exact list: descending
confidence, then descending ``created_at`` (recency), then ascending lesson ``id``
as the deterministic tie-break — never input or set-iteration order. The selection
is capped (default 3, per rule 4).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa

from atlas.core.enums import EntityStatus
from atlas.core.models.lesson import Lesson
from atlas.core.models.ticket import Ticket
from atlas.storage.db import Database
from atlas.storage.tables import LessonRow

# The design's cap on rendered lessons (context-renderer.md rule 4: "Cap: 3").
DEFAULT_LESSON_CAP = 3


def _normalise(tag: str) -> str:
    """A facet/tag in its comparable form: stripped and lower-cased. Empty
    results are dropped by the callers building the sets."""
    return tag.strip().lower()


@dataclass(frozen=True)
class LessonMatch:
    """One selected lesson. ``lesson_id`` is the stored lesson's UUID — the
    identity the ContextPack records in ``historical_lessons``; ``shared_tags``
    are the normalised tags the ticket facets and the lesson share, in sorted
    order so the value is deterministic."""

    lesson_id: UUID
    shared_tags: tuple[str, ...]


def _ticket_facets(ticket: Ticket) -> frozenset[str]:
    """The ticket's matchable facet set (rule 4): its tags and ticket_type,
    each normalised, with empty strings dropped."""
    facets = {_normalise(tag) for tag in ticket.tags}
    facets.add(_normalise(ticket.ticket_type.value))
    facets.discard("")
    return frozenset(facets)


def _matching_lessons(
    lessons: list[Lesson],
    ticket: Ticket,
    *,
    cap: int,
) -> list[tuple[Lesson, tuple[str, ...]]]:
    facets = _ticket_facets(ticket)
    if not facets:
        return []

    matched: list[tuple[Lesson, tuple[str, ...]]] = []
    for lesson in lessons:
        if lesson.status != EntityStatus.ACTIVE:
            continue
        shared = facets & {_normalise(tag) for tag in lesson.tags}
        if not shared:
            continue
        matched.append((lesson, tuple(sorted(shared))))

    # Total order via successive stable sorts, least-significant key first:
    # ascending id, then descending created_at, then descending confidence with
    # NULL confidence after every numeric value. The unique id makes it a true
    # total order (no input/set-iteration order leaks).
    matched.sort(key=lambda pair: pair[0].id)
    matched.sort(key=lambda pair: pair[0].created_at, reverse=True)
    matched.sort(
        key=lambda pair: (
            pair[0].confidence is not None,
            pair[0].confidence if pair[0].confidence is not None else 0.0,
        ),
        reverse=True,
    )

    return matched[:cap]


def select_lessons(
    lessons: list[Lesson],
    ticket: Ticket,
    *,
    cap: int = DEFAULT_LESSON_CAP,
) -> list[LessonMatch]:
    """Select up to ``cap`` ACTIVE lessons for ``ticket`` from the ``lessons``
    listing by tag overlap.

    Only ``status == EntityStatus.ACTIVE`` lessons are eligible (ADR-0009): a
    DRAFT, ARCHIVED, or DEPRECATED lesson is never selected, whatever its tags or
    confidence — mirroring :func:`select_adrs` admitting only ACCEPTED ADRs.
    """
    return [
        LessonMatch(lesson_id=lesson.id, shared_tags=shared)
        for lesson, shared in _matching_lessons(lessons, ticket, cap=cap)
    ]


def retrieve_lessons(
    ticket: Ticket,
    db: Database,
    *,
    cap: int = DEFAULT_LESSON_CAP,
) -> list[Lesson]:
    """Load up to ``cap`` matching ACTIVE lessons for ``ticket`` from storage.

    ADR-0009's governance boundary is enforced in the query itself: DRAFT (and
    other non-ACTIVE) rows are not loaded and then filtered in Python. Tag and
    ticket_type matching stays in Python because v1 matching is a normalised set
    intersection over the JSON tag list.
    """
    with db.session() as session:
        rows = session.scalars(
            sa.select(LessonRow).where(LessonRow.status == EntityStatus.ACTIVE.value)
        )
        active_lessons = [
            Lesson.model_validate(row, from_attributes=True) for row in rows
        ]
    return [
        lesson for lesson, _shared in _matching_lessons(active_lessons, ticket, cap=cap)
    ]
