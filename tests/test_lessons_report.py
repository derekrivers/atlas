"""ATLAS-104: the `atlas lessons report` learning analytics surface."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from test_debt_item_model import debt_item_kwargs
from test_lesson_model import lesson_kwargs
from test_models_validation import ticket_kwargs

from atlas.cli import EXIT_OK, build_parser, main
from atlas.core.models import AnomalyType, DebtItem, Lesson, Ticket
from atlas.learning import (
    build_lessons_report,
    render_lessons_report_markdown,
)
from atlas.learning.patterns import detect_pattern_candidates
from atlas.storage import Database, DebtItemRepo, LessonRepo, TicketRepo

NOW = datetime(2026, 7, 14, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{tmp_path}/atlas.db")
    database.create_all()
    return database


def make_lesson(**overrides: object) -> Lesson:
    return Lesson(**lesson_kwargs() | {"id": uuid4()} | overrides)


def seed_lessons(db: Database, lessons: list[Lesson]) -> None:
    repo = LessonRepo(db)
    for lesson in lessons:
        repo.add(lesson)


def make_ticket(key: str, **overrides: object) -> Ticket:
    return Ticket(**ticket_kwargs() | {"id": uuid4(), "key": key} | overrides)


def seed_tickets(db: Database, tickets: list[Ticket]) -> None:
    repo = TicketRepo(db)
    for ticket in tickets:
        repo.add(ticket)


def make_debt(ticket_id: UUID, kind: AnomalyType, **overrides: object) -> DebtItem:
    return DebtItem(
        **debt_item_kwargs()
        | {"id": uuid4(), "ticket_id": ticket_id, "anomaly_type": kind}
        | overrides
    )


def seed_debt(db: Database, items: list[DebtItem]) -> None:
    repo = DebtItemRepo(db)
    for item in items:
        repo.record(item)


def test_report_groups_lessons_by_status_category_and_tag(db: Database) -> None:
    draft = make_lesson(
        status="draft",
        category="failure_pattern",
        title="Draft dwell lesson",
        tags=["dwell", "handoff"],
    )
    active = make_lesson(
        status="active",
        category="testing",
        title="Active testing lesson",
        tags=["handoff"],
    )
    archived = make_lesson(
        status="archived",
        category="delivery",
        title="Archived delivery lesson",
        tags=["legacy"],
    )
    seed_lessons(db, [draft, active, archived])

    report = build_lessons_report(LessonRepo(db), now=NOW)

    status_counts = {group.status: group.count for group in report.lessons_by_status}
    assert status_counts == {"draft": 1, "active": 1, "archived": 1}
    category_counts = {
        count.category: (count.draft, count.active, count.archived)
        for count in report.category_status_counts
    }
    assert category_counts == {
        "delivery": (0, 0, 1),
        "failure_pattern": (1, 0, 0),
        "testing": (0, 1, 0),
    }
    tag_counts = {group.tag: group.count for group in report.lessons_by_tag}
    assert tag_counts == {"dwell": 1, "handoff": 2, "legacy": 1}


def test_active_citation_counts_use_related_ticket_id_length(db: Database) -> None:
    ticket_ids = [uuid4(), uuid4()]
    seed_lessons(
        db,
        [
            make_lesson(
                status="active",
                title="Cited active lesson",
                related_ticket_ids=ticket_ids,
            ),
            make_lesson(
                status="draft",
                title="Draft citations are not reported here",
                related_ticket_ids=[uuid4(), uuid4(), uuid4()],
            ),
        ],
    )

    report = build_lessons_report(LessonRepo(db), now=NOW)

    assert [
        (count.title, count.citation_count, count.ticket_ids)
        for count in report.active_citation_counts
    ] == [("Cited active lesson", 2, ticket_ids)]


def test_promotion_backlog_age_uses_oldest_draft(db: Database) -> None:
    seed_lessons(
        db,
        [
            make_lesson(
                status="draft",
                title="Newest draft",
                created_at=NOW - timedelta(hours=5),
            ),
            make_lesson(
                status="draft",
                title="Oldest draft",
                created_at=NOW - timedelta(days=3),
            ),
            make_lesson(
                status="active",
                title="Old active lesson",
                created_at=NOW - timedelta(days=20),
            ),
        ],
    )

    report = build_lessons_report(LessonRepo(db), now=NOW)

    assert report.promotion_backlog_age_hours == 72.0
    assert report.promotion_backlog_oldest_created_at == NOW - timedelta(days=3)


def test_pattern_candidates_count_failure_tags_and_debt_categories() -> None:
    lessons = [
        make_lesson(status="draft", category="failure_pattern", tags=["rebase"]),
        make_lesson(status="active", category="failure_pattern", tags=["rebase"]),
        make_lesson(status="archived", category="failure_pattern", tags=["rebase"]),
        make_lesson(status="active", category="testing", tags=["rebase"]),
    ]
    tickets = [make_ticket(f"ATLAS-{index}") for index in range(1, 4)]
    debt = [make_debt(ticket.id, AnomalyType.DWELL_BREACH) for ticket in tickets]

    candidates = detect_pattern_candidates(lessons, debt)

    assert [(candidate.tag, candidate.count) for candidate in candidates] == [
        ("debt:dwell_breach", 3),
        ("rebase", 3),
    ]


def test_report_json_is_valid_and_matches_schema(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    ticket_ids = [uuid4()]
    lesson = make_lesson(
        status="active",
        title="Active report lesson",
        tags=["report"],
        related_ticket_ids=ticket_ids,
    )
    seed_lessons(db, [lesson])

    code = main(["lessons", "report", "--json"], database=db)
    decoded = json.loads(capsys.readouterr().out)

    assert code == EXIT_OK
    assert set(decoded) == {
        "generated_at",
        "category_status_counts",
        "lessons_by_status",
        "lessons_by_tag",
        "active_citation_counts",
        "pattern_candidates",
        "promotion_backlog_age_hours",
        "promotion_backlog_oldest_created_at",
        "dwell_breaches",
    }
    assert decoded["active_citation_counts"] == [
        {
            "lesson_id": str(lesson.id),
            "title": "Active report lesson",
            "citation_count": 1,
            "ticket_ids": [str(ticket_ids[0])],
        }
    ]


def test_cli_lessons_report_runs_against_fixture_database(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    ticket = make_ticket("ATLAS-9")
    seed_tickets(db, [ticket])
    seed_lessons(
        db,
        [
            make_lesson(
                product_id=ticket.product_id,
                status="active",
                title="Fixture lesson",
                tags=["fixture"],
                related_ticket_ids=[ticket.id],
            )
        ],
    )
    seed_debt(db, [make_debt(ticket.id, AnomalyType.DWELL_BREACH)])

    code = main(["lessons", "report"], database=db)
    output = capsys.readouterr().out

    assert code == EXIT_OK
    assert output.strip()
    assert "# Lessons report" in output
    assert "Fixture lesson" in output
    assert "## Dwell breaches" in output
    assert "ATLAS-9" in output


def test_report_markdown_contains_required_sections(db: Database) -> None:
    report = build_lessons_report(LessonRepo(db), now=NOW)
    markdown = render_lessons_report_markdown(report)

    assert "## Category/status counts" in markdown
    assert "## Lessons by status" in markdown
    assert "### DRAFT" in markdown
    assert "### ACTIVE" in markdown
    assert "### ARCHIVED" in markdown
    assert "## Lessons by tag" in markdown
    assert "## Active lesson citations" in markdown
    assert "## Pattern candidates" in markdown
    assert "## Promotion backlog age" in markdown


def test_lessons_report_parser_has_helped_json_flag() -> None:
    args = build_parser().parse_args(["lessons", "report", "--json"])

    assert args.command == "lessons"
    assert args.lessons_command == "report"
    assert args.json is True
