"""Domain decisions for governed lesson promotion and rejection."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from atlas.core.enums import ActorType, EntityStatus
from atlas.core.models import Lesson


@dataclass(frozen=True, slots=True)
class PromoteLesson:
    """Promote one DRAFT lesson with operator-assigned confidence."""

    lesson_id: UUID
    confidence: float


@dataclass(frozen=True, slots=True)
class RejectLesson:
    """Reject one DRAFT lesson without accepting editable lesson fields."""

    lesson_id: UUID


LessonDispositionCommand = PromoteLesson | RejectLesson


class LessonDispositionDecisionStatus(StrEnum):
    """Domain-only result before transaction and transport mapping."""

    READY = "ready"
    NOT_FOUND = "not_found"
    NOT_DRAFT = "not_draft"
    INVALID = "invalid"


@dataclass(frozen=True)
class LessonDispositionDecision:
    """A domain ruling plan containing no persistence or API concerns."""

    status: LessonDispositionDecisionStatus
    actor_type: ActorType
    actor_id: str
    current_lesson: Lesson | None = None
    updated_lesson: Lesson | None = None
    message: str | None = None


def validate_lesson_disposition_command(
    command: LessonDispositionCommand,
) -> str | None:
    """Return a safe validation message, or ``None`` for a valid command."""

    if not isinstance(command, PromoteLesson | RejectLesson):
        return "unsupported lesson disposition command"
    if not isinstance(command.lesson_id, UUID):
        return "lesson_id must be a UUID"
    if isinstance(command, RejectLesson):
        return None
    confidence = command.confidence
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, int | float)
        or not math.isfinite(confidence)
        or confidence < 0.0
        or confidence > 1.0
    ):
        return (
            "confidence must be between 0.0 and 1.0 inclusive and finite; "
            f"got {confidence!r}"
        )
    return None


def decide_lesson_disposition(
    command: LessonDispositionCommand,
    lesson: Lesson | None,
    *,
    now: datetime,
    actor_type: ActorType,
    actor_id: str,
) -> LessonDispositionDecision:
    """Apply the only Phase 13 lesson lifecycle decisions to a detached value."""

    validation_error = validate_lesson_disposition_command(command)
    if validation_error is not None:
        return LessonDispositionDecision(
            status=LessonDispositionDecisionStatus.INVALID,
            actor_type=actor_type,
            actor_id=actor_id,
            current_lesson=lesson,
            message=validation_error,
        )
    if now.utcoffset() is None:
        return LessonDispositionDecision(
            status=LessonDispositionDecisionStatus.INVALID,
            actor_type=actor_type,
            actor_id=actor_id,
            current_lesson=lesson,
            message="lesson disposition timestamp must be timezone-aware",
        )
    if not actor_id:
        return LessonDispositionDecision(
            status=LessonDispositionDecisionStatus.INVALID,
            actor_type=actor_type,
            actor_id=actor_id,
            current_lesson=lesson,
            message="lesson disposition actor id must be non-empty",
        )
    if lesson is None:
        return LessonDispositionDecision(
            status=LessonDispositionDecisionStatus.NOT_FOUND,
            actor_type=actor_type,
            actor_id=actor_id,
            message=f"no lesson with id {command.lesson_id}",
        )
    if lesson.status is not EntityStatus.DRAFT:
        verb = "promote" if isinstance(command, PromoteLesson) else "reject"
        return LessonDispositionDecision(
            status=LessonDispositionDecisionStatus.NOT_DRAFT,
            actor_type=actor_type,
            actor_id=actor_id,
            current_lesson=lesson,
            message=(
                f"can only {verb} DRAFT lessons; lesson {lesson.id} "
                f"is {lesson.status.value!r}"
            ),
        )

    if isinstance(command, PromoteLesson):
        updated = lesson.model_copy(
            deep=True,
            update={
                "status": EntityStatus.ACTIVE,
                "confidence": float(command.confidence),
                "updated_at": now,
            },
        )
    else:
        updated = lesson.model_copy(
            deep=True,
            update={
                "status": EntityStatus.ARCHIVED,
                "updated_at": now,
            },
        )
    return LessonDispositionDecision(
        status=LessonDispositionDecisionStatus.READY,
        actor_type=actor_type,
        actor_id=actor_id,
        current_lesson=lesson,
        updated_lesson=updated,
    )
