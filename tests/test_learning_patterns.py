"""ATLAS-102: deterministic pattern detection over accumulated lessons."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from test_lesson_model import lesson_kwargs

from atlas.core.enums import ActorType, EntityStatus
from atlas.core.models.debt_item import AnomalyType, DebtItem
from atlas.core.models.lesson import Lesson
from atlas.learning.patterns import (
    PATTERN_THRESHOLD,
    PatternCandidateSource,
    detect_pattern_candidates,
)

NOW = datetime(2026, 7, 14, 10, tzinfo=UTC)


@dataclass(frozen=True)
class CodeQualityDebtFixture:
    category: str
    ticket_id: UUID


def make_lesson(
    *,
    title: str,
    tags: list[str],
    category: str = "failure_pattern",
    status: str = "draft",
    created_at: datetime = NOW,
    **overrides: Any,
) -> Lesson:
    confidence = None if status == EntityStatus.DRAFT.value else 0.8
    return Lesson(
        **lesson_kwargs()
        | {
            "id": uuid4(),
            "status": status,
            "category": category,
            "title": title,
            "confidence": confidence,
            "tags": tags,
            "created_at": created_at,
            "updated_at": created_at,
        }
        | overrides
    )


def make_delivery_anomaly() -> DebtItem:
    return DebtItem(
        id=uuid4(),
        product_id=uuid4(),
        ticket_id=uuid4(),
        anomaly_type=AnomalyType.DWELL_BREACH,
        summary="Delivery anomaly, not code-quality debt.",
        observed_at=NOW,
        created_by_type=ActorType.SYSTEM,
        created_by_id="pm-sync",
        created_at=NOW,
    )


def test_three_failure_lessons_sharing_tag_produce_candidate() -> None:
    lessons = [
        make_lesson(
            title=f"Failure {index}",
            tags=["scope"],
            created_at=NOW + timedelta(minutes=index),
        )
        for index in range(PATTERN_THRESHOLD)
    ]

    candidates = detect_pattern_candidates(lessons)

    assert len(candidates) == 1
    assert candidates[0].tag == "scope"
    assert candidates[0].count == 3
    assert candidates[0].lesson_titles == ("Failure 0", "Failure 1", "Failure 2")


def test_two_failure_lessons_sharing_tag_do_not_produce_candidate() -> None:
    lessons = [
        make_lesson(title="First", tags=["scope"]),
        make_lesson(title="Second", tags=["scope"]),
    ]

    assert detect_pattern_candidates(lessons) == []


def test_success_pattern_lessons_do_not_contribute_to_failure_tag_detection() -> None:
    lessons = [
        make_lesson(title="Failure 1", tags=["handoff"]),
        make_lesson(title="Failure 2", tags=["handoff"]),
        make_lesson(title="Success 1", tags=["handoff"], category="success_pattern"),
        make_lesson(title="Success 2", tags=["handoff"], category="success_pattern"),
    ]

    assert detect_pattern_candidates(lessons) == []


def test_pattern_candidates_are_sorted_by_count_descending() -> None:
    lessons = [
        make_lesson(title="Scope 1", tags=["scope", "review"]),
        make_lesson(title="Scope 2", tags=["scope", "review"]),
        make_lesson(title="Scope 3", tags=["scope", "review"]),
        make_lesson(title="Scope 4", tags=["scope"]),
    ]

    candidates = detect_pattern_candidates(lessons)

    assert [candidate.tag for candidate in candidates] == ["scope", "review"]
    assert [candidate.count for candidate in candidates] == [4, 3]


def test_detection_returns_empty_list_when_no_patterns_meet_threshold() -> None:
    lessons = [
        make_lesson(title="Failure", tags=["scope"]),
        make_lesson(title="Architecture", tags=["scope"], category="architecture"),
        make_lesson(title="Archived", tags=["scope"], status="archived"),
    ]

    assert detect_pattern_candidates(lessons) == []


def test_code_quality_debt_branch_uses_category_not_delivery_anomaly_type() -> None:
    ticket_ids = [uuid4() for _ in range(PATTERN_THRESHOLD)]
    candidates = detect_pattern_candidates(
        [],
        code_quality_debt_items=[
            *(
                CodeQualityDebtFixture("large_file", ticket_id)
                for ticket_id in ticket_ids
            ),
            make_delivery_anomaly(),
        ],
    )

    assert len(candidates) == 1
    assert candidates[0].source is PatternCandidateSource.CODE_QUALITY_DEBT_CATEGORY
    assert candidates[0].tag == "large_file"
    assert candidates[0].count == 3
