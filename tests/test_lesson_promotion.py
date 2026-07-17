"""ATLAS-100: lesson promotion gate and lifecycle commands."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from test_context_pack_model import context_pack_kwargs
from test_lesson_model import lesson_kwargs
from test_models_validation import ticket_kwargs

from atlas.cli import EXIT_OK, EXIT_PRECONDITION, main
from atlas.context import select_lessons
from atlas.core.enums import EntityStatus
from atlas.core.models import ContextPack, Lesson, Ticket
from atlas.storage import (
    ContextPackRepo,
    Database,
    LessonRepo,
    LessonValidationError,
    TicketRepo,
)

NOW = datetime(2026, 7, 14, 10, tzinfo=UTC)


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{tmp_path}/atlas.db")
    database.create_all()
    return database


def make_ticket(key: str = "ATLAS-100", **overrides: Any) -> Ticket:
    return Ticket(
        **ticket_kwargs()
        | {
            "id": uuid4(),
            "key": key,
            "title": f"{key} ticket",
            "tags": ["learning-system"],
            "created_at": NOW,
            "updated_at": NOW,
        }
        | overrides
    )


def make_lesson(status: str = "draft", **overrides: Any) -> Lesson:
    confidence = None if status == EntityStatus.DRAFT.value else 0.7
    return Lesson(
        **lesson_kwargs()
        | {
            "id": uuid4(),
            "status": status,
            "title": f"{status.upper()} lesson",
            "confidence": confidence,
            "tags": ["learning-system"],
            "created_at": NOW - timedelta(hours=1),
            "updated_at": NOW - timedelta(hours=1),
        }
        | overrides
    )


def seed_lesson(db: Database, lesson: Lesson) -> Lesson:
    return LessonRepo(db).add(lesson)


def seed_ticket(db: Database, ticket: Ticket) -> Ticket:
    return TicketRepo(db).add(ticket)


def sqlite_store_snapshot(db_path: Path) -> dict[str, bytes]:
    return {
        path.name: path.read_bytes()
        for path in sorted(db_path.parent.glob(f"{db_path.name}*"))
    }


def make_pack(
    lesson_id: UUID,
    *,
    ticket_id: UUID | None = None,
    created_at: datetime = NOW + timedelta(minutes=1),
) -> ContextPack:
    return ContextPack(
        **context_pack_kwargs()
        | {
            "id": uuid4(),
            "ticket_id": ticket_id,
            "historical_lessons": [lesson_id],
            "created_at": created_at,
        }
    )


def test_promote_draft_sets_active_confidence_and_timestamp(db: Database) -> None:
    lesson = seed_lesson(db, make_lesson(confidence=None))

    promoted = LessonRepo(db).promote(lesson.id, confidence=0.8, now=NOW)

    assert promoted.status is EntityStatus.ACTIVE
    assert promoted.confidence == 0.8
    assert promoted.updated_at == NOW
    assert LessonRepo(db).get(lesson.id) == promoted


def test_reject_draft_sets_archived(db: Database) -> None:
    lesson = seed_lesson(db, make_lesson(confidence=None))

    rejected = LessonRepo(db).reject(lesson.id, now=NOW)

    assert rejected.status is EntityStatus.ARCHIVED
    assert rejected.confidence is None
    assert rejected.updated_at == NOW


def test_archive_active_sets_archived(db: Database) -> None:
    lesson = seed_lesson(db, make_lesson(status="active"))

    archived = LessonRepo(db).archive(lesson.id, now=NOW)

    assert archived.status is EntityStatus.ARCHIVED
    assert archived.confidence == 0.7


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_promote_out_of_range_confidence_raises_typed_validation_error(
    db: Database, confidence: float
) -> None:
    lesson = seed_lesson(db, make_lesson(confidence=None))

    with pytest.raises(LessonValidationError, match=r"between 0\.0 and 1\.0"):
        LessonRepo(db).promote(lesson.id, confidence=confidence, now=NOW)

    stored = LessonRepo(db).get(lesson.id)
    assert stored is not None
    assert stored.status is EntityStatus.DRAFT
    assert stored.confidence is None


def test_merge_archives_draft_and_updates_target_related_tickets(
    db: Database,
) -> None:
    target_source = uuid4()
    draft_source = uuid4()
    shared_ticket = uuid4()
    new_ticket = uuid4()
    draft = seed_lesson(
        db,
        make_lesson(
            title="Duplicate draft",
            source_ticket_id=draft_source,
            related_ticket_ids=[shared_ticket, new_ticket],
            confidence=None,
        ),
    )
    target = seed_lesson(
        db,
        make_lesson(
            status="active",
            title="Existing active",
            source_ticket_id=target_source,
            related_ticket_ids=[shared_ticket],
        ),
    )

    archived_draft, updated_target = LessonRepo(db).merge(draft.id, target.id, now=NOW)

    assert archived_draft.status is EntityStatus.ARCHIVED
    assert updated_target.status is EntityStatus.ACTIVE
    assert archived_draft.source_ticket_id == draft_source
    assert updated_target.source_ticket_id == target_source
    assert updated_target.related_ticket_ids == [shared_ticket, new_ticket]
    # Seeded red first with assert 1 == 2 (B011): the merged-away source is
    # audit provenance on the archived draft, not a target citation.
    if draft_source in updated_target.related_ticket_ids:
        assert 1 == 2  # type: ignore[comparison-overlap]
    assert LessonRepo(db).get(draft.id) == archived_draft
    assert LessonRepo(db).get(target.id) == updated_target


def test_cli_review_lists_only_draft_lessons_with_source_ticket(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    source = seed_ticket(db, make_ticket("ATLAS-270"))
    draft = seed_lesson(
        db,
        make_lesson(
            title="Review me",
            source_ticket_id=source.id,
            related_ticket_ids=[],
            created_at=NOW - timedelta(days=1),
            confidence=None,
        ),
    )
    seed_lesson(db, make_lesson(status="active", title="Already promoted"))

    code = main(["lessons", "review"], database=db)
    out = capsys.readouterr().out

    assert code == EXIT_OK
    assert "Review me" in out
    assert str(draft.id) in out
    assert "ATLAS-270" in out
    assert draft.created_at.isoformat() in out
    assert "Already promoted" not in out


def test_cli_show_prints_full_lesson_record_with_resolved_ticket_keys(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    source = seed_ticket(db, make_ticket("ATLAS-270"))
    citation = seed_ticket(db, make_ticket("ATLAS-271"))
    unresolved_ticket_id = uuid4()
    adr_id = uuid4()
    lesson = seed_lesson(
        db,
        make_lesson(
            title="Show me before promotion",
            category="delivery",
            problem="The gate could only see a title.",
            solution="Expose the full stored lesson record.",
            outcome="The operator can rule on the body.",
            source_ticket_id=source.id,
            related_ticket_ids=[citation.id, unresolved_ticket_id],
            related_adr_ids=[adr_id],
            tags=["learning-system", "promotion-gate"],
            confidence=0.625,
            created_by_id="codex",
        ),
    )

    assert main(["lessons", "review"], database=db) == EXIT_OK
    review_out = capsys.readouterr().out
    assert str(lesson.id) in review_out

    code = main(["lessons", "show", str(lesson.id)], database=db)
    out = capsys.readouterr().out

    assert code == EXIT_OK
    for expected in [
        "id:",
        str(lesson.id),
        "title:",
        "Show me before promotion",
        "category:",
        "delivery",
        "status:",
        "draft",
        "confidence:",
        "0.625",
        "tags:",
        "learning-system, promotion-gate",
        "problem:",
        "The gate could only see a title.",
        "solution:",
        "Expose the full stored lesson record.",
        "outcome:",
        "The operator can rule on the body.",
        "source_ticket:",
        "ATLAS-270",
        "related_tickets:",
        "ATLAS-271",
        str(unresolved_ticket_id),
        "related_adr_ids:",
        str(adr_id),
        "created_by:",
        "agent:codex",
        "created_at:",
        lesson.created_at.isoformat(),
        "updated_at:",
        lesson.updated_at.isoformat(),
    ]:
        assert expected in out
    assert str(source.id) not in out
    assert str(citation.id) not in out


def test_cli_show_json_emits_same_record_field_coverage(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    source = seed_ticket(db, make_ticket("ATLAS-280"))
    citation = seed_ticket(db, make_ticket("ATLAS-281"))
    adr_id = uuid4()
    lesson = seed_lesson(
        db,
        make_lesson(
            title="Structured lesson detail",
            source_ticket_id=source.id,
            related_ticket_ids=[citation.id],
            related_adr_ids=[adr_id],
            tags=["json", "gate"],
            confidence=0.75,
        ),
    )

    code = main(["lessons", "show", str(lesson.id), "--json"], database=db)
    payload = json.loads(capsys.readouterr().out)

    assert code == EXIT_OK
    assert payload == {
        "id": str(lesson.id),
        "title": "Structured lesson detail",
        "category": "failure_pattern",
        "status": "draft",
        "confidence": 0.75,
        "tags": ["json", "gate"],
        "problem": "Large tickets caused broad, hard-to-review changes.",
        "solution": "Split into narrow, dependency-aware units.",
        "outcome": "Agent PRs became easier to review.",
        "source_ticket": "ATLAS-280",
        "related_tickets": ["ATLAS-281"],
        "related_adr_ids": [str(adr_id)],
        "created_by": "agent:claude",
        "created_at": lesson.created_at.isoformat(),
        "updated_at": lesson.updated_at.isoformat(),
    }


def test_cli_show_non_uuid_id_is_clean_precondition(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["lessons", "show", "not-a-uuid"], database=db)
    captured = capsys.readouterr()

    assert code == EXIT_PRECONDITION
    assert captured.out == ""
    assert captured.err.strip().splitlines() == ["not a valid lesson id: 'not-a-uuid'"]
    assert "Traceback" not in captured.err


def test_cli_show_unknown_uuid_is_clean_precondition(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    unknown_id = uuid4()

    code = main(["lessons", "show", str(unknown_id)], database=db)
    captured = capsys.readouterr()

    assert code == EXIT_PRECONDITION
    assert captured.out == ""
    assert captured.err.strip().splitlines() == [f"no lesson with id {unknown_id}"]
    assert "Traceback" not in captured.err


def test_cli_show_cold_database_is_clean_precondition(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cold_db = Database(f"sqlite:///{tmp_path}/cold.db")

    code = main(["lessons", "show", str(uuid4())], database=cold_db)
    captured = capsys.readouterr()

    assert code == EXIT_PRECONDITION
    assert captured.out == ""
    assert captured.err.strip().splitlines() == [
        "database is not initialised (no such table); run the database "
        "migrations before using `atlas lessons show`."
    ]
    assert "Traceback" not in captured.err


def test_cli_show_null_confidence_and_empty_lists_render_placeholders(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    lesson = seed_lesson(
        db,
        make_lesson(
            confidence=None,
            tags=[],
            related_ticket_ids=[],
            related_adr_ids=[],
        ),
    )

    code = main(["lessons", "show", str(lesson.id)], database=db)
    out = capsys.readouterr().out

    assert code == EXIT_OK
    assert "confidence:" in out
    assert "tags:" in out
    assert "related_tickets:" in out
    assert "related_adr_ids:" in out
    assert "confidence:       -" in out
    assert "tags:             -" in out
    assert "related_tickets:  -" in out
    assert "related_adr_ids:  -" in out
    if "confidence:       None" in out or "tags:             None" in out:
        assert 1 == 2  # type: ignore[comparison-overlap]


def test_cli_show_performs_no_writes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = tmp_path / "atlas.db"
    database = Database(f"sqlite:///{db_path}")
    database.create_all()
    source = seed_ticket(database, make_ticket("ATLAS-290"))
    lesson = seed_lesson(database, make_lesson(source_ticket_id=source.id))
    database.engine.dispose()
    before = sqlite_store_snapshot(db_path)

    code = main(["lessons", "show", str(lesson.id)], database=database)
    capsys.readouterr()
    database.engine.dispose()
    after = sqlite_store_snapshot(db_path)

    assert code == EXIT_OK
    assert after == before


def test_cli_promotion_makes_lesson_retrievable(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    ticket = seed_ticket(db, make_ticket("ATLAS-271"))
    lesson = seed_lesson(
        db,
        make_lesson(
            title="Promote for retrieval",
            related_ticket_ids=[ticket.id],
            confidence=None,
        ),
    )
    repo = LessonRepo(db)

    assert select_lessons(repo.list(), ticket) == []

    code = main(
        ["lessons", "promote", str(lesson.id), "--confidence", "0.9"],
        database=db,
    )
    capsys.readouterr()

    assert code == EXIT_OK
    matches = select_lessons(repo.list(), ticket)
    assert [match.lesson_id for match in matches] == [lesson.id]


def test_cli_promote_rejects_out_of_range_confidence(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    lesson = seed_lesson(db, make_lesson(confidence=None))

    code = main(
        ["lessons", "promote", str(lesson.id), "--confidence", "1.5"],
        database=db,
    )
    captured = capsys.readouterr()

    assert code == EXIT_PRECONDITION
    assert captured.out == ""
    assert "confidence must be between 0.0 and 1.0" in captured.err
    stored = LessonRepo(db).get(lesson.id)
    assert stored is not None
    assert stored.status is EntityStatus.DRAFT


def test_cli_review_stale_lists_active_lessons_meeting_pack_threshold(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    source = seed_ticket(db, make_ticket("ATLAS-272"))
    stale = seed_lesson(
        db,
        make_lesson(
            status="active",
            title="Needs stale review",
            source_ticket_id=source.id,
            related_ticket_ids=[],
            updated_at=NOW,
        ),
    )
    below_threshold = seed_lesson(
        db,
        make_lesson(status="active", title="Only nine packs", updated_at=NOW),
    )
    reconfirmed_ticket_id = uuid4()
    reconfirmed = seed_lesson(
        db,
        make_lesson(
            status="active",
            title="Recently re-confirmed",
            related_ticket_ids=[reconfirmed_ticket_id],
            updated_at=NOW,
        ),
    )
    draft = seed_lesson(
        db,
        make_lesson(title="Draft with packs", confidence=None, updated_at=NOW),
    )
    packs = ContextPackRepo(db)
    for index in range(10):
        packs.add(
            make_pack(
                stale.id,
                ticket_id=uuid4(),
                created_at=NOW + timedelta(minutes=index + 1),
            )
        )
    for index in range(9):
        packs.add(
            make_pack(
                below_threshold.id,
                ticket_id=uuid4(),
                created_at=NOW + timedelta(minutes=index + 1),
            )
        )
    for index in range(10):
        packs.add(
            make_pack(
                reconfirmed.id,
                ticket_id=reconfirmed_ticket_id if index == 0 else uuid4(),
                created_at=NOW + timedelta(minutes=index + 1),
            )
        )
    for index in range(10):
        packs.add(
            make_pack(
                draft.id,
                ticket_id=uuid4(),
                created_at=NOW + timedelta(minutes=index + 1),
            )
        )

    code = main(["lessons", "review", "--stale"], database=db)
    out = capsys.readouterr().out

    assert code == EXIT_OK
    assert "Needs stale review" in out
    assert "ATLAS-272" in out
    assert "packs=10" in out
    assert "Only nine packs" not in out
    assert "Recently re-confirmed" not in out
    assert "Draft with packs" not in out


def test_cli_lessons_report_includes_pattern_candidates(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    for index in range(3):
        seed_lesson(
            db,
            make_lesson(
                title=f"Recurring failure {index + 1}",
                tags=["review-loop"],
                created_at=NOW + timedelta(minutes=index),
                updated_at=NOW + timedelta(minutes=index),
                confidence=None,
            ),
        )
    seed_lesson(
        db,
        make_lesson(
            title="Below threshold",
            tags=["scope-creep"],
            confidence=None,
        ),
    )

    code = main(["lessons", "report"], database=db)
    out = capsys.readouterr().out

    assert code == EXIT_OK
    assert "## Pattern candidates" in out
    assert "| failure tag | review-loop | 3 |" in out
    assert "Recurring failure 1" in out
    assert "Recurring failure 2" in out
    assert "Recurring failure 3" in out
    assert "| failure tag | scope-creep |" not in out
