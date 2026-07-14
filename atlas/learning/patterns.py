"""Deterministic learning pattern detection.

The learning-system design keeps pattern detection out of LLM judgement:
recurrence is counted from stored lessons and delivery-anomaly rows. The
``atlas lessons report`` reader consumes this module and only reports the
candidate flags; it never promotes or recommends lessons automatically.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from atlas.core.models.debt_item import DebtItem
from atlas.core.models.lesson import Lesson, LessonCategory

PATTERN_THRESHOLD = 3


@dataclass(frozen=True)
class PatternCandidate:
    """A recurring learning signal, keyed by tag/category and count."""

    tag: str
    count: int


def detect_pattern_candidates(
    lessons: list[Lesson],
    debt_items: list[DebtItem],
    *,
    threshold: int = PATTERN_THRESHOLD,
) -> list[PatternCandidate]:
    """Return deterministic pattern candidates from lessons and DebtItems.

    Two heuristics are defined in ``learning-system.md``:

    - the same tag appears on at least ``threshold`` failure-pattern lessons;
    - the same DebtItem category appears across at least ``threshold`` tickets.

    Lesson tags count once per lesson, even if a malformed row repeats a tag.
    DebtItem category counts are distinct ticket counts, not row counts, because
    the documented signal is recurrence across tickets.
    """

    counts: dict[str, int] = {}
    failure_tag_counts: dict[str, int] = defaultdict(int)
    for lesson in lessons:
        if lesson.category is not LessonCategory.FAILURE_PATTERN:
            continue
        for tag in set(lesson.tags):
            if tag:
                failure_tag_counts[tag] += 1
    counts.update(failure_tag_counts)

    debt_tickets_by_category: dict[str, set[str]] = defaultdict(set)
    for item in debt_items:
        debt_tickets_by_category[item.anomaly_type.value].add(str(item.ticket_id))
    counts.update(
        {
            f"debt:{category}": len(ticket_ids)
            for category, ticket_ids in debt_tickets_by_category.items()
        }
    )

    return [
        PatternCandidate(tag=tag, count=count)
        for tag, count in sorted(counts.items())
        if count >= threshold
    ]
