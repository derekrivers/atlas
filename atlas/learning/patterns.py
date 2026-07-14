"""Deterministic pattern-candidate detection for the learning system.

Pattern candidates are computed at report time from current Lessons and, once
ATLAS-117 introduces it, the code-quality debt register. This module performs
only auditable grouping and counting: it makes no model calls, reads no network
state, and persists nothing.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import UUID

from atlas.core.enums import EntityStatus
from atlas.core.models.lesson import Lesson, LessonCategory

PATTERN_THRESHOLD = 3

_PATTERN_STATUSES = {EntityStatus.DRAFT, EntityStatus.ACTIVE}


class PatternCandidateSource(StrEnum):
    """The deterministic input stream that produced a pattern candidate."""

    FAILURE_TAG = "failure_tag"
    CODE_QUALITY_DEBT_CATEGORY = "code_quality_debt_category"


@dataclass(frozen=True)
class PatternCandidate:
    """A recurring lesson tag or code-quality debt category.

    ``tag`` is the report label for the recurring value. For failure lessons it
    is the lesson tag; for future code-quality debt rows it is the debt category.
    ``count`` is the number of contributing lessons or distinct tickets.
    """

    tag: str
    count: int
    lesson_titles: tuple[str, ...]
    source: PatternCandidateSource = PatternCandidateSource.FAILURE_TAG
    ticket_ids: tuple[UUID, ...] = ()


def detect_pattern_candidates(
    lessons: Sequence[Lesson],
    *,
    threshold: int = PATTERN_THRESHOLD,
    code_quality_debt_items: Sequence[Any] | None = None,
) -> list[PatternCandidate]:
    """Return deterministic pattern candidates meeting ``threshold``.

    Failure-tag candidates count unique lesson contributions: a duplicated tag
    inside one lesson still contributes one lesson. Only DRAFT or ACTIVE
    ``failure_pattern`` lessons participate. Success-pattern lessons, archived
    lessons, and delivery-anomaly ``DebtItem`` rows do not contribute.

    The optional ``code_quality_debt_items`` hook is intentionally structural:
    ATLAS-117 has not introduced a concrete entity yet, so rows without a
    ``category`` attribute are skipped rather than interpreting delivery-anomaly
    ``anomaly_type`` as code-quality debt.
    """

    candidates = [
        *_detect_failure_tag_candidates(lessons, threshold=threshold),
        *_detect_code_quality_debt_candidates(
            code_quality_debt_items or (),
            threshold=threshold,
        ),
    ]
    return sorted(
        candidates,
        key=lambda candidate: (
            -candidate.count,
            candidate.source.value,
            candidate.tag,
        ),
    )


def _detect_failure_tag_candidates(
    lessons: Sequence[Lesson],
    *,
    threshold: int,
) -> list[PatternCandidate]:
    lessons_by_tag: dict[str, list[Lesson]] = defaultdict(list)
    for lesson in lessons:
        if (
            lesson.category is not LessonCategory.FAILURE_PATTERN
            or lesson.status not in _PATTERN_STATUSES
        ):
            continue
        for tag in sorted(set(lesson.tags)):
            lessons_by_tag[tag].append(lesson)

    candidates: list[PatternCandidate] = []
    for tag, contributing_lessons in lessons_by_tag.items():
        if len(contributing_lessons) < threshold:
            continue
        ordered_lessons = sorted(
            contributing_lessons,
            key=lambda lesson: (lesson.created_at, str(lesson.id)),
        )
        candidates.append(
            PatternCandidate(
                tag=tag,
                count=len(ordered_lessons),
                lesson_titles=tuple(lesson.title for lesson in ordered_lessons),
            )
        )
    return candidates


def _detect_code_quality_debt_candidates(
    debt_items: Sequence[Any],
    *,
    threshold: int,
) -> list[PatternCandidate]:
    ticket_ids_by_category: dict[str, set[UUID]] = defaultdict(set)
    for item in debt_items:
        category = getattr(item, "category", None)
        ticket_id = getattr(item, "ticket_id", None)
        if category is None or not isinstance(ticket_id, UUID):
            continue
        ticket_ids_by_category[str(category)].add(ticket_id)

    candidates: list[PatternCandidate] = []
    for category, ticket_ids in ticket_ids_by_category.items():
        if len(ticket_ids) < threshold:
            continue
        candidates.append(
            PatternCandidate(
                tag=category,
                count=len(ticket_ids),
                lesson_titles=(),
                source=PatternCandidateSource.CODE_QUALITY_DEBT_CATEGORY,
                ticket_ids=tuple(sorted(ticket_ids, key=str)),
            )
        )
    return candidates
