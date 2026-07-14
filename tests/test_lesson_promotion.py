"""ATLAS-100: lesson promotion gate and lifecycle commands."""

from __future__ import annotations

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
    shared_ticket = uuid4()
    new_ticket = uuid4()
    draft = seed_lesson(
        db,
        make_lesson(
            title="Duplicate draft",
            related_ticket_ids=[shared_ticket, new_ticket],
            confidence=None,
        ),
    )
    target = seed_lesson(
        db,
        make_lesson(
            status="active",
            title="Existing active",
            related_ticket_ids=[shared_ticket],
        ),
    )

    archived_draft, updated_target = LessonRepo(db).merge(draft.id, target.id, now=NOW)

    assert archived_draft.status is EntityStatus.ARCHIVED
    assert updated_target.status is EntityStatus.ACTIVE
    assert updated_target.related_ticket_ids == [shared_ticket, new_ticket]
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
            related_ticket_ids=[source.id],
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
            related_ticket_ids=[source.id],
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
    assert "packs=10" in out
    assert "Only nine packs" not in out
    assert "Recently re-confirmed" not in out
    assert "Draft with packs" not in out
