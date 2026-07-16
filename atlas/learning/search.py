"""Deterministic keyword search over ACTIVE lessons."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from atlas.core.enums import EntityStatus
from atlas.core.models.lesson import Lesson
from atlas.core.tokens import normalise_tokens

DEFAULT_LESSON_SEARCH_LIMIT = 20


@dataclass(frozen=True)
class LessonSearchResult:
    lesson_id: UUID
    title: str
    confidence: float | None
    tags: list[str]
    match_count: int
    matched_tokens: list[str]


def _lesson_tokens(lesson: Lesson) -> frozenset[str]:
    return normalise_tokens(" ".join([lesson.title, *lesson.tags]))


def _tag_matches(lesson: Lesson, tag: str | None) -> bool:
    if tag is None:
        return True
    needle = tag.strip().casefold()
    return any(candidate.casefold() == needle for candidate in lesson.tags)


def search_lessons(
    lessons: list[Lesson],
    query: str,
    *,
    tag: str | None = None,
    limit: int = DEFAULT_LESSON_SEARCH_LIMIT,
) -> list[LessonSearchResult]:
    """Return ACTIVE lessons whose title or tags match query tokens.

    Matching is a pure scan: query, title, and tag text all use the reconciler's
    tokeniser. Results are ranked by matching-token count, then confidence.
    """

    query_tokens = normalise_tokens(query)
    if not query_tokens or limit < 1:
        return []

    results: list[LessonSearchResult] = []
    for lesson in lessons:
        if lesson.status is not EntityStatus.ACTIVE:
            continue
        if not _tag_matches(lesson, tag):
            continue

        matched_tokens = sorted(query_tokens & _lesson_tokens(lesson))
        if not matched_tokens:
            continue
        results.append(
            LessonSearchResult(
                lesson_id=lesson.id,
                title=lesson.title,
                confidence=lesson.confidence,
                tags=sorted(lesson.tags),
                match_count=len(matched_tokens),
                matched_tokens=matched_tokens,
            )
        )

    return sorted(
        results,
        key=lambda result: (
            -result.match_count,
            -(result.confidence if result.confidence is not None else -1.0),
            result.title.casefold(),
            str(result.lesson_id),
        ),
    )[:limit]


def lesson_search_results_json(
    results: list[LessonSearchResult],
) -> list[dict[str, object]]:
    return [
        {
            "lesson_id": str(result.lesson_id),
            "title": result.title,
            "confidence": result.confidence,
            "tags": result.tags,
            "match_count": result.match_count,
            "matched_tokens": result.matched_tokens,
        }
        for result in results
    ]


def render_lesson_search_results(results: list[LessonSearchResult]) -> str:
    if not results:
        return "no lessons found"

    lines = ["Lessons found:"]
    for result in results:
        confidence = "n/a" if result.confidence is None else f"{result.confidence:g}"
        tags = ", ".join(result.tags) if result.tags else "n/a"
        matches = ", ".join(result.matched_tokens)
        lines.append(
            f"{result.lesson_id}  matches={result.match_count}  "
            f"confidence={confidence}  tags={tags}  tokens={matches}  {result.title}"
        )
    return "\n".join(lines)
