"""ATLAS-105: organisational memory search over ACTIVE lessons."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from test_lesson_model import lesson_kwargs

from atlas.cli import EXIT_OK, build_parser, main
from atlas.core.models import Lesson
from atlas.learning import search_lessons
from atlas.storage import Database, LessonRepo

NOW = datetime(2026, 7, 15, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{tmp_path}/atlas.db")
    database.create_all()
    return database


def make_lesson(**overrides: object) -> Lesson:
    return Lesson(
        **lesson_kwargs()
        | {
            "id": uuid4(),
            "status": "active",
            "confidence": 0.7,
            "tags": ["learning-system"],
            "created_at": NOW,
            "updated_at": NOW,
        }
        | overrides
    )


def seed_lessons(db: Database, lessons: list[Lesson]) -> None:
    repo = LessonRepo(db)
    for lesson in lessons:
        repo.add(lesson)


def test_search_returns_only_active_lessons_matching_query_tokens() -> None:
    title_match = make_lesson(
        title="Review cycle mitigation",
        tags=["delivery"],
    )
    tag_match = make_lesson(
        title="Plain operational lesson",
        tags=["handoff-loop"],
    )
    unmatched = make_lesson(
        title="Planning gate discipline",
        tags=["operator"],
    )

    results = search_lessons(
        [title_match, tag_match, unmatched],
        "review handoff",
    )

    assert {result.lesson_id for result in results} == {title_match.id, tag_match.id}


def test_search_excludes_draft_and_archived_lessons() -> None:
    active = make_lesson(title="Memory lookup should appear")
    draft = make_lesson(
        status="draft",
        confidence=None,
        title="Memory lookup draft",
    )
    archived = make_lesson(
        status="archived",
        title="Memory lookup archived",
    )

    results = search_lessons([draft, active, archived], "memory lookup")

    assert [result.lesson_id for result in results] == [active.id]


def test_search_tag_filter_narrows_before_keyword_matching() -> None:
    handoff_match = make_lesson(
        title="Review the handoff path",
        tags=["handoff"],
    )
    wrong_tag = make_lesson(
        title="Review the planning path",
        tags=["planning"],
    )
    tag_only = make_lesson(
        title="Unrelated active lesson",
        tags=["handoff"],
    )

    results = search_lessons(
        [wrong_tag, handoff_match, tag_only],
        "review",
        tag="HANDOFF",
    )

    assert [result.lesson_id for result in results] == [handoff_match.id]


def test_search_ranks_by_match_count_then_confidence() -> None:
    two_matches_lower_confidence = make_lesson(
        title="Review handoff gate",
        confidence=0.2,
    )
    two_matches_higher_confidence = make_lesson(
        title="Review gate",
        tags=["handoff"],
        confidence=0.8,
    )
    one_match_high_confidence = make_lesson(
        title="Review only",
        confidence=1.0,
    )

    results = search_lessons(
        [
            one_match_high_confidence,
            two_matches_lower_confidence,
            two_matches_higher_confidence,
        ],
        "review handoff",
    )

    assert [result.lesson_id for result in results] == [
        two_matches_higher_confidence.id,
        two_matches_lower_confidence.id,
        one_match_high_confidence.id,
    ]
    assert [result.match_count for result in results] == [2, 2, 1]


def test_cli_lessons_search_runs_against_fixture_database(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    expected = make_lesson(
        title="Review handoff retry path",
        tags=["handoff"],
        confidence=0.8,
    )
    wrong_tag = make_lesson(
        title="Review planning retry path",
        tags=["planning"],
    )
    draft = make_lesson(
        status="draft",
        confidence=None,
        title="Review handoff draft",
        tags=["handoff"],
    )
    seed_lessons(db, [wrong_tag, expected, draft])

    code = main(["lessons", "search", "review", "--tag", "handoff"], database=db)
    output = capsys.readouterr().out

    assert code == EXIT_OK
    assert "Lessons found:" in output
    assert str(expected.id) in output
    assert "Review handoff retry path" in output
    assert "confidence=0.8" in output
    assert "Review planning retry path" not in output
    assert "Review handoff draft" not in output


def test_cli_lessons_search_json_emits_result_array(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    lesson = make_lesson(
        title="Searchable memory lesson",
        tags=["learning-system", "search"],
    )
    seed_lessons(db, [lesson])

    code = main(["lessons", "search", "memory search", "--json"], database=db)
    decoded = json.loads(capsys.readouterr().out)

    assert code == EXIT_OK
    assert decoded == [
        {
            "lesson_id": str(lesson.id),
            "title": "Searchable memory lesson",
            "confidence": 0.7,
            "tags": ["learning-system", "search"],
            "match_count": 2,
            "matched_tokens": ["memory", "search"],
        }
    ]


def test_cli_lessons_search_empty_result_exits_zero_with_clear_message(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_lessons(db, [make_lesson(title="Unrelated active lesson")])

    code = main(["lessons", "search", "missing"], database=db)
    output = capsys.readouterr().out

    assert code == EXIT_OK
    assert output.strip() == "no lessons found"


def test_lessons_search_parser_has_helped_flags() -> None:
    args = build_parser().parse_args(
        ["lessons", "search", "review", "--tag", "handoff", "--json"]
    )

    assert args.command == "lessons"
    assert args.lessons_command == "search"
    assert args.query == ["review"]
    assert args.tag == "handoff"
    assert args.json is True
