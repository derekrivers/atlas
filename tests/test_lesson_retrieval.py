"""Historical lesson retrieval (ATLAS-53), per docs/atlas/context-renderer.md
rule 4.

Each test is falsifiable and names the wrong answer where status or ordering
matters: the ACTIVE-only tests assert a high-confidence non-ACTIVE lesson is
ABSENT (selecting it is the wrong answer a status-ignoring retriever gives), and
the ranking test asserts the exact id-tiebroken order (a confidence-only or
input-order retriever gets it wrong).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from test_lesson_model import lesson_kwargs
from test_models_validation import ticket_kwargs

from atlas.context import (
    DEFAULT_LESSON_CAP,
    LessonMatch,
    retrieve_lessons,
    select_lessons,
)
from atlas.core.models import Lesson, Ticket
from atlas.storage import Database, LessonRepo
from atlas.storage.tables import LessonRow


def make_lesson(**overrides: Any) -> Lesson:
    """An ACTIVE lesson by default (the retriever's only eligible status), so a
    test opts INTO a non-ACTIVE status explicitly."""
    return Lesson(**lesson_kwargs() | {"status": "active"} | overrides)


def make_ticket(**overrides: Any) -> Ticket:
    return Ticket(**ticket_kwargs() | overrides)


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{tmp_path}/atlas.db")
    database.create_all()
    return database


def _ids(matches: list[LessonMatch]) -> list[UUID]:
    return [m.lesson_id for m in matches]


def _lesson_ids(lessons: list[Lesson]) -> list[UUID]:
    return [lesson.id for lesson in lessons]


def _insert_raw_lesson_row(db: Database, **overrides: Any) -> UUID:
    row_id = uuid4()
    data = (
        lesson_kwargs()
        | {
            "id": row_id,
            "status": "draft",
            "related_ticket_ids": [],
            "related_adr_ids": [],
            "tags": [],
        }
        | overrides
    )
    with db.session() as session, session.begin():
        session.add(
            LessonRow(
                id=data["id"],
                product_id=data["product_id"],
                status=data["status"],
                category=data["category"],
                title=data["title"],
                problem=data["problem"],
                solution=data["solution"],
                outcome=data["outcome"],
                confidence=data["confidence"],
                related_ticket_ids=data["related_ticket_ids"],
                related_adr_ids=data["related_adr_ids"],
                tags=data["tags"],
                created_by_type=data["created_by_type"],
                created_by_id=data["created_by_id"],
                created_at=data["created_at"],
                updated_at=data["updated_at"],
            )
        )
    return row_id


def test_tag_match_selects_intersecting_and_excludes_disjoint() -> None:
    """A lesson whose tags intersect ticket.tags is selected; a disjoint one is
    not."""
    ticket = make_ticket(tags=["renderer", "context"], component=None)
    hit = make_lesson(tags=["renderer"])
    miss = make_lesson(tags=["storage"])

    result = select_lessons([hit, miss], ticket)

    assert _ids(result) == [hit.id]


def test_active_only_excludes_high_confidence_non_active_lessons() -> None:
    """A DRAFT, ARCHIVED, or DEPRECATED lesson with intersecting tags AND high
    confidence is EXCLUDED (ADR-0009). Selecting the high-confidence non-ACTIVE
    lesson is the wrong answer."""
    ticket = make_ticket(tags=["renderer"], component=None)
    draft = make_lesson(status="draft", tags=["renderer"], confidence=1.0)
    archived = make_lesson(status="archived", tags=["renderer"], confidence=1.0)
    deprecated = make_lesson(status="deprecated", tags=["renderer"], confidence=1.0)
    active = make_lesson(status="active", tags=["renderer"], confidence=0.1)

    result = select_lessons([draft, archived, deprecated, active], ticket)

    assert _ids(result) == [active.id]
    selected = set(_ids(result))
    assert draft.id not in selected
    assert archived.id not in selected
    assert deprecated.id not in selected


def test_component_facet_alone_selects() -> None:
    """A lesson matching ONLY via ticket.component is selected."""
    ticket = make_ticket(tags=[], component="planner")
    hit = make_lesson(tags=["planner"])

    assert _ids(select_lessons([hit], ticket)) == [hit.id]


def test_ticket_type_facet_alone_selects() -> None:
    """A lesson matching ONLY via ticket.ticket_type is selected (a lesson tagged
    'tech_debt' against a TECH_DEBT ticket)."""
    ticket = make_ticket(tags=[], component=None, ticket_type="tech_debt")
    hit = make_lesson(tags=["tech_debt"])

    assert _ids(select_lessons([hit], ticket)) == [hit.id]


def test_match_is_case_and_whitespace_insensitive() -> None:
    """Normalisation: a 'Renderer' lesson tag matches a 'renderer' ticket facet,
    and surrounding whitespace is stripped on both sides."""
    ticket = make_ticket(tags=["renderer"], component=None)
    hit = make_lesson(tags=["  Renderer "])

    result = select_lessons([hit], ticket)

    assert _ids(result) == [hit.id]
    assert result[0].shared_tags == ("renderer",)


def test_ranking_confidence_then_recency_then_id() -> None:
    """Total order: descending confidence, then descending created_at, then
    ascending id. The two equal-confidence/equal-time lessons must come out in
    ascending-id order — a confidence-only or input-order retriever gets this
    wrong."""
    old = datetime(2026, 1, 1, tzinfo=UTC)
    new = datetime(2026, 6, 1, tzinfo=UTC)
    ticket = make_ticket(tags=["renderer"], component=None)

    # Top by confidence.
    top = make_lesson(tags=["renderer"], confidence=0.9, created_at=old, updated_at=old)
    # Equal confidence (0.5); 'recent' is newer so it outranks the two old ones.
    recent = make_lesson(
        tags=["renderer"], confidence=0.5, created_at=new, updated_at=new
    )
    # Equal confidence AND equal created_at: id tie-break, ascending. Force ids.
    id_lo = UUID(int=1)
    id_hi = UUID(int=2)
    tie_hi = make_lesson(
        id=id_hi, tags=["renderer"], confidence=0.5, created_at=old, updated_at=old
    )
    tie_lo = make_lesson(
        id=id_lo, tags=["renderer"], confidence=0.5, created_at=old, updated_at=old
    )

    # Pass in a deliberately scrambled input order.
    result = select_lessons([tie_hi, recent, top, tie_lo], ticket, cap=10)

    assert _ids(result) == [top.id, recent.id, tie_lo.id, tie_hi.id]


def test_ranking_sorts_null_confidence_after_numeric() -> None:
    ticket = make_ticket(tags=["renderer"], component=None)
    newer = datetime(2026, 6, 1, tzinfo=UTC)
    older = datetime(2026, 1, 1, tzinfo=UTC)
    numeric = make_lesson(
        tags=["renderer"], confidence=0.1, created_at=older, updated_at=older
    )
    null_confidence = make_lesson(
        tags=["renderer"], confidence=1.0, created_at=newer, updated_at=newer
    ).model_copy(update={"confidence": None})

    result = select_lessons([null_confidence, numeric], ticket, cap=10)

    assert _ids(result) == [numeric.id, null_confidence.id]


def test_cap_keeps_only_the_top_three() -> None:
    """More than three matches -> exactly the top three by the ranking."""
    ticket = make_ticket(tags=["renderer"], component=None)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    lessons = [
        make_lesson(
            tags=["renderer"],
            confidence=c,
            created_at=base,
            updated_at=base,
        )
        for c in (0.9, 0.8, 0.7, 0.6, 0.5)
    ]

    result = select_lessons(lessons, ticket, cap=DEFAULT_LESSON_CAP)

    assert DEFAULT_LESSON_CAP == 3
    assert _ids(result) == [lessons[0].id, lessons[1].id, lessons[2].id]


def test_no_match_and_empty_listing_yield_empty() -> None:
    """No ACTIVE/tag match -> []; an empty lessons list -> []."""
    ticket = make_ticket(tags=["renderer"], component=None)
    disjoint = make_lesson(tags=["storage"])

    assert select_lessons([disjoint], ticket) == []
    assert select_lessons([], ticket) == []


def test_shared_tags_are_the_matched_normalised_tags_sorted() -> None:
    """A returned match carries the matched normalised tags, sorted; an unmatched
    tag on either side is not included."""
    ticket = make_ticket(
        tags=["Renderer", "Context", "unmatched-ticket"], component=None
    )
    lesson = make_lesson(tags=["context", "RENDERER", "unmatched-lesson"])

    result = select_lessons([lesson], ticket)

    assert result[0].shared_tags == ("context", "renderer")


def test_retrieve_lessons_excludes_draft_at_query_level(db: Database) -> None:
    ticket = make_ticket(tags=["renderer"], component=None)
    active = make_lesson(status="active", tags=["renderer"], confidence=0.1)
    LessonRepo(db).add(active)
    draft_id = _insert_raw_lesson_row(
        db,
        status="draft",
        category="not_a_lesson_category",
        tags=["renderer"],
        confidence=1.0,
    )

    result = retrieve_lessons(ticket, db)

    assert _lesson_ids(result) == [active.id]
    assert draft_id not in _lesson_ids(result)


def test_retrieve_lessons_caps_at_three(db: Database) -> None:
    ticket = make_ticket(tags=["renderer"], component=None)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    lessons = [
        make_lesson(
            tags=["renderer"],
            confidence=confidence,
            created_at=base,
            updated_at=base,
        )
        for confidence in (0.9, 0.8, 0.7, 0.6)
    ]
    repo = LessonRepo(db)
    for lesson in lessons:
        repo.add(lesson)

    result = retrieve_lessons(ticket, db)

    assert _lesson_ids(result) == [lesson.id for lesson in lessons[:3]]


def test_retrieve_lessons_ranks_confidence_then_recency(db: Database) -> None:
    ticket = make_ticket(tags=["renderer"], component=None)
    old = datetime(2026, 1, 1, tzinfo=UTC)
    new = datetime(2026, 6, 1, tzinfo=UTC)
    top = make_lesson(tags=["renderer"], confidence=0.9, created_at=old, updated_at=old)
    recent = make_lesson(
        tags=["renderer"], confidence=0.5, created_at=new, updated_at=new
    )
    stale = make_lesson(
        tags=["renderer"], confidence=0.5, created_at=old, updated_at=old
    )
    repo = LessonRepo(db)
    for lesson in (stale, recent, top):
        repo.add(lesson)

    result = retrieve_lessons(ticket, db, cap=10)

    assert _lesson_ids(result) == [top.id, recent.id, stale.id]


def test_retrieve_lessons_uses_ticket_type_when_tags_are_insufficient(
    db: Database,
) -> None:
    ticket = make_ticket(tags=["renderer"], component=None, ticket_type="bug")
    type_match = make_lesson(tags=["bug"])
    disjoint = make_lesson(tags=["storage"])
    repo = LessonRepo(db)
    repo.add(disjoint)
    repo.add(type_match)

    result = retrieve_lessons(ticket, db)

    assert _lesson_ids(result) == [type_match.id]
