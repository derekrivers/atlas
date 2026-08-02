"""ATLAS-233: governed lesson disposition service acceptance tests."""

from __future__ import annotations

import ast
import math
from dataclasses import fields
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
import sqlalchemy as sa
from test_lesson_model import lesson_kwargs
from test_models_validation import ticket_kwargs

from atlas.cli import EXIT_OK, main
from atlas.context import retrieve_lessons
from atlas.core.enums import ActorType, EntityStatus
from atlas.core.models import Lesson, OperatorActionOutcome, Ticket
from atlas.learning import (
    PromoteLesson,
    RejectLesson,
    detect_pattern_candidates,
)
from atlas.orchestration import (
    LessonDispositionCommandContext,
    LessonDispositionService,
    LessonDispositionStatus,
    OperatorActionCommandResult,
    OperatorActionConflictCode,
    OperatorActionEnvelope,
    OperatorActionGateway,
    OperatorActionGatewayStatus,
    OperatorActionMutation,
    OperatorActionResultCode,
    canonical_request_fingerprint,
)
from atlas.storage import Database, LessonRepo, OperatorActionReceiptRepo, TicketRepo
from atlas.storage.tables import LessonRow, OperatorActionKeyRow

NOW = datetime(2026, 8, 2, 15, tzinfo=UTC)
CLI_PATH = Path(__file__).resolve().parents[1] / "atlas" / "cli.py"


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{tmp_path}/atlas.db")
    database.create_all()
    return database


class FrozenClock:
    def __call__(self) -> datetime:
        return NOW


def make_lesson(**overrides: Any) -> Lesson:
    return Lesson(
        **lesson_kwargs()
        | {
            "id": uuid4(),
            "status": EntityStatus.DRAFT,
            "confidence": None,
            "tags": ["governance"],
            "created_at": NOW - timedelta(days=2),
            "updated_at": NOW - timedelta(days=2),
        }
        | overrides
    )


def seed_lesson(db: Database, **overrides: Any) -> Lesson:
    return LessonRepo(db).add(make_lesson(**overrides))


def command_context(key: str) -> LessonDispositionCommandContext:
    return LessonDispositionCommandContext.operator(key)


def service(db: Database) -> LessonDispositionService:
    return LessonDispositionService(db, clock=FrozenClock())


def receipt_count(db: Database) -> int:
    return len(OperatorActionReceiptRepo(db).list())


def test_ac1_shared_service_returns_typed_updated_lesson_and_outcome(
    db: Database,
) -> None:
    lesson = seed_lesson(db)

    result = service(db).execute(
        PromoteLesson(lesson.id, 0.8), command_context("ac1-promote")
    )

    assert result.status is LessonDispositionStatus.SUCCEEDED
    assert result.lesson is not None
    assert result.lesson.status is EntityStatus.ACTIVE
    assert result.receipt is not None
    assert result.receipt.created_by_type is ActorType.HUMAN
    assert result.receipt.created_by_id == "operator"


@pytest.mark.parametrize("confidence", [0.0, 1.0])
def test_ac2_promote_accepts_inclusive_confidence_boundaries_and_preserves_record(
    db: Database,
    confidence: float,
) -> None:
    lesson = seed_lesson(db)
    preserved = lesson.model_dump(
        exclude={"status", "confidence", "updated_at"}, mode="json"
    )

    result = service(db).execute(
        PromoteLesson(lesson.id, confidence),
        command_context(f"ac2-{confidence}"),
    )

    assert result.status is LessonDispositionStatus.SUCCEEDED
    assert result.lesson is not None
    assert result.lesson.confidence == confidence
    assert (
        result.lesson.model_dump(
            exclude={"status", "confidence", "updated_at"}, mode="json"
        )
        == preserved
    )


@pytest.mark.parametrize(
    "confidence",
    [-0.01, 1.01, math.nan, math.inf, -math.inf],
    ids=["below", "above", "nan", "positive-infinity", "negative-infinity"],
)
def test_ac2_invalid_confidence_is_typed_and_performs_no_write(
    db: Database,
    confidence: float,
) -> None:
    lesson = seed_lesson(db)

    result = service(db).execute(
        PromoteLesson(lesson.id, confidence), command_context("ac2-invalid")
    )

    assert result.status is LessonDispositionStatus.INVALID
    assert LessonRepo(db).get(lesson.id) == lesson
    assert receipt_count(db) == 0


def test_ac3_reject_has_no_editable_fields_and_archives_for_audit(
    db: Database,
) -> None:
    lesson = seed_lesson(db)
    assert [field.name for field in fields(RejectLesson)] == ["lesson_id"]

    result = service(db).execute(RejectLesson(lesson.id), command_context("ac3-reject"))

    assert result.status is LessonDispositionStatus.SUCCEEDED
    assert result.lesson is not None
    assert result.lesson.status is EntityStatus.ARCHIVED
    assert LessonRepo(db).get(lesson.id) == result.lesson


def _detached_lesson_row(db: Database, lesson: Lesson) -> LessonRow:
    with db.session() as session:
        row = session.get(LessonRow, lesson.id)
        assert row is not None
        session.expunge(row)
        return row


def _race_envelope(lesson: Lesson, key: str, action: str) -> OperatorActionEnvelope:
    return OperatorActionEnvelope(
        action=action,
        target_type="lesson",
        target_id=str(lesson.id),
        created_by_type=ActorType.HUMAN,
        created_by_id="operator",
        idempotency_key=key,
        request_fingerprint=canonical_request_fingerprint(
            action=action,
            target_type="lesson",
            target_id=str(lesson.id),
            payload={},
        ),
    )


def _cas_disposition(row: LessonRow, target: EntityStatus) -> Any:
    def command(_: object) -> OperatorActionCommandResult:
        row.status = target.value
        row.updated_at = NOW
        return OperatorActionCommandResult(
            outcome=OperatorActionOutcome.SUCCEEDED,
            result_code=OperatorActionResultCode.ACTION_SUCCEEDED,
            before_status=EntityStatus.DRAFT,
            after_status=target,
            mutations=(
                OperatorActionMutation(
                    row,
                    expected_values={"status": EntityStatus.DRAFT.value},
                    updated_fields=("status", "confidence", "updated_at"),
                ),
            ),
        )

    return command


def test_ac4_two_observed_draft_dispositions_have_one_winner_and_one_receipt(
    db: Database,
) -> None:
    lesson = seed_lesson(db)
    promote_observation = _detached_lesson_row(db, lesson)
    reject_observation = _detached_lesson_row(db, lesson)
    gateway = OperatorActionGateway(db, clock=FrozenClock())

    winner = gateway.execute(
        _race_envelope(lesson, "race-promote", "lesson.promote"),
        _cas_disposition(promote_observation, EntityStatus.ACTIVE),
    )
    loser = gateway.execute(
        _race_envelope(lesson, "race-reject", "lesson.reject"),
        _cas_disposition(reject_observation, EntityStatus.ARCHIVED),
    )

    assert winner.status is OperatorActionGatewayStatus.EXECUTED
    assert loser.status is OperatorActionGatewayStatus.CONFLICT
    assert loser.conflict is not None
    assert loser.conflict.code is OperatorActionConflictCode.STALE_STATE
    assert isinstance(loser.conflict.current_entity, LessonRow)
    assert loser.conflict.current_entity.status == EntityStatus.ACTIVE.value
    assert receipt_count(db) == 1
    stored = LessonRepo(db).get(lesson.id)
    assert stored is not None
    assert stored.status is EntityStatus.ACTIVE

    class ReturningStaleGateway:
        def execute(self, *_: object, **__: object) -> object:
            return loser

    stale_result = LessonDispositionService(
        db, gateway=cast(OperatorActionGateway, ReturningStaleGateway())
    ).execute(RejectLesson(lesson.id), command_context("race-service-result"))
    assert stale_result.status is LessonDispositionStatus.STALE_STATE
    assert stale_result.lesson == stored
    assert stale_result.receipt is None


def test_ac5_unknown_and_non_draft_are_distinct_typed_outcomes(db: Database) -> None:
    active = seed_lesson(
        db,
        status=EntityStatus.ACTIVE,
        confidence=0.7,
        updated_at=NOW - timedelta(days=1),
    )

    unknown = service(db).execute(RejectLesson(uuid4()), command_context("ac5-unknown"))
    non_draft = service(db).execute(
        RejectLesson(active.id), command_context("ac5-non-draft")
    )

    assert unknown.status is LessonDispositionStatus.NOT_FOUND
    assert unknown.lesson is None
    assert non_draft.status is LessonDispositionStatus.NOT_DRAFT
    assert non_draft.lesson == active
    assert LessonRepo(db).get(active.id) == active


def test_ac5_replay_and_altered_replay_are_distinct_and_do_not_repeat_write(
    db: Database,
) -> None:
    lesson = seed_lesson(db)
    disposition = service(db)
    context = command_context("ac5-replay")

    first = disposition.execute(PromoteLesson(lesson.id, 0.8), context)
    replay = disposition.execute(PromoteLesson(lesson.id, 0.8), context)
    altered = disposition.execute(PromoteLesson(lesson.id, 0.9), context)

    assert first.status is LessonDispositionStatus.SUCCEEDED
    assert replay.status is LessonDispositionStatus.REPLAYED
    assert replay.receipt == first.receipt
    assert altered.status is LessonDispositionStatus.IDEMPOTENCY_CONFLICT
    assert receipt_count(db) == 1
    stored = LessonRepo(db).get(lesson.id)
    assert stored is not None
    assert stored.confidence == 0.8


def test_ac5_receipt_persistence_failure_rolls_back_lesson_and_reservation(
    db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lesson = seed_lesson(db)

    def fail_receipt(*_: object, **__: object) -> None:
        raise sa.exc.IntegrityError("receipt", {}, RuntimeError("unavailable"))

    monkeypatch.setattr(
        "atlas.orchestration.operator_actions._add_operator_action_receipt",
        fail_receipt,
    )
    result = service(db).execute(
        PromoteLesson(lesson.id, 0.8), command_context("ac5-receipt-failure")
    )

    assert result.status is LessonDispositionStatus.RECEIPT_PERSISTENCE_FAILED
    assert result.lesson == lesson
    assert LessonRepo(db).get(lesson.id) == lesson
    with db.session() as session:
        assert session.scalars(sa.select(OperatorActionKeyRow)).all() == []
    assert receipt_count(db) == 0


def test_ac6_cli_delegates_and_preserves_output_with_operator_attribution(
    db: Database,
    capsys: pytest.CaptureFixture[str],
) -> None:
    lesson = seed_lesson(db)

    code = main(
        ["lessons", "promote", str(lesson.id), "--confidence", "0.75"],
        database=db,
    )

    assert code == EXIT_OK
    assert capsys.readouterr().out == (
        f"Promoted lesson {lesson.id} to ACTIVE (confidence: 0.75).\n"
    )
    [receipt] = OperatorActionReceiptRepo(db).list()
    assert (receipt.created_by_type, receipt.created_by_id) == (
        ActorType.HUMAN,
        "operator",
    )

    cli_tree = ast.parse(CLI_PATH.read_text(encoding="utf-8"))
    forbidden_calls = [
        node
        for node in ast.walk(cli_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"promote", "reject"}
    ]
    assert forbidden_calls == []


def test_ac7_active_retrieval_and_pattern_detection_semantics_after_disposition(
    db: Database,
) -> None:
    ticket = Ticket(
        **ticket_kwargs()
        | {
            "id": uuid4(),
            "tags": ["governance"],
            "created_at": NOW,
            "updated_at": NOW,
        }
    )
    TicketRepo(db).add(ticket)
    promoted = seed_lesson(db, title="Promoted")
    rejected = seed_lesson(db, title="Rejected")
    other_drafts = [seed_lesson(db, title=f"Pattern {index}") for index in range(2)]

    service(db).execute(PromoteLesson(promoted.id, 0.9), command_context("ac7-p"))
    service(db).execute(RejectLesson(rejected.id), command_context("ac7-r"))

    assert [lesson.id for lesson in retrieve_lessons(ticket, db)] == [promoted.id]
    patterns = detect_pattern_candidates(LessonRepo(db).list())
    assert [(pattern.tag, pattern.count) for pattern in patterns] == [("governance", 3)]
    assert {lesson.status for lesson in other_drafts} == {EntityStatus.DRAFT}
