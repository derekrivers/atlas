"""Read-only delivery analytics for lessons.

``atlas lessons report`` is the Learning System's analytics surface: it groups
stored lessons by governance status, category, and tag; counts citations for
ACTIVE lessons; surfaces deterministic pattern candidates; measures the DRAFT
promotion backlog age; and shows dwell-breach anomaly rows. It writes nothing
and makes no model or network calls. ``now`` is injected at the CLI boundary so
the report remains deterministic under test.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from atlas.core.enums import EntityStatus
from atlas.core.models.debt_item import AnomalyType, DebtItem
from atlas.core.models.lesson import Lesson, LessonCategory
from atlas.core.models.ticket import Ticket
from atlas.learning.patterns import PatternCandidate, detect_pattern_candidates
from atlas.storage.repositories import DebtItemRepo, LessonRepo, TicketRepo

REPORT_STATUSES = (
    EntityStatus.DRAFT,
    EntityStatus.ACTIVE,
    EntityStatus.ARCHIVED,
)


@dataclass(frozen=True)
class LessonSummary:
    lesson_id: UUID
    title: str
    category: str
    tags: list[str]


@dataclass(frozen=True)
class CategoryStatusCount:
    category: str
    draft: int
    active: int
    archived: int


@dataclass(frozen=True)
class StatusLessonGroup:
    status: str
    count: int
    lessons: list[LessonSummary]


@dataclass(frozen=True)
class TagLessonGroup:
    tag: str
    count: int
    status_counts: dict[str, int]
    lessons: list[LessonSummary]


@dataclass(frozen=True)
class ActiveCitationCount:
    lesson_id: UUID
    title: str
    citation_count: int
    ticket_ids: list[UUID]


@dataclass(frozen=True)
class LessonDwellBreach:
    ticket_id: UUID
    ticket_key: str
    count: int


@dataclass(frozen=True)
class LessonsReport:
    generated_at: datetime
    category_status_counts: list[CategoryStatusCount]
    lessons_by_status: list[StatusLessonGroup]
    lessons_by_tag: list[TagLessonGroup]
    active_citation_counts: list[ActiveCitationCount]
    pattern_candidates: list[PatternCandidate]
    promotion_backlog_age_hours: float | None
    promotion_backlog_oldest_created_at: datetime | None
    dwell_breaches: list[LessonDwellBreach]


def _summary(lesson: Lesson) -> LessonSummary:
    return LessonSummary(
        lesson_id=lesson.id,
        title=lesson.title,
        category=lesson.category.value,
        tags=sorted(lesson.tags),
    )


def _sort_lessons(lessons: list[Lesson]) -> list[Lesson]:
    return sorted(lessons, key=lambda lesson: (lesson.title, str(lesson.id)))


def _category_status_counts(lessons: list[Lesson]) -> list[CategoryStatusCount]:
    counts: dict[LessonCategory, dict[EntityStatus, int]] = {}
    for lesson in lessons:
        if lesson.status not in REPORT_STATUSES:
            continue
        category_counts = counts.setdefault(
            lesson.category, dict.fromkeys(REPORT_STATUSES, 0)
        )
        category_counts[lesson.status] += 1

    return [
        CategoryStatusCount(
            category=category.value,
            draft=status_counts[EntityStatus.DRAFT],
            active=status_counts[EntityStatus.ACTIVE],
            archived=status_counts[EntityStatus.ARCHIVED],
        )
        for category, status_counts in sorted(
            counts.items(), key=lambda item: item[0].value
        )
    ]


def _lessons_by_status(lessons: list[Lesson]) -> list[StatusLessonGroup]:
    grouped: dict[EntityStatus, list[Lesson]] = {
        status: [] for status in REPORT_STATUSES
    }
    for lesson in lessons:
        if lesson.status in grouped:
            grouped[lesson.status].append(lesson)

    return [
        StatusLessonGroup(
            status=status.value,
            count=len(grouped[status]),
            lessons=[_summary(lesson) for lesson in _sort_lessons(grouped[status])],
        )
        for status in REPORT_STATUSES
    ]


def _lessons_by_tag(lessons: list[Lesson]) -> list[TagLessonGroup]:
    grouped: dict[str, list[Lesson]] = defaultdict(list)
    for lesson in lessons:
        for tag in sorted(set(lesson.tags)):
            if tag:
                grouped[tag].append(lesson)

    groups: list[TagLessonGroup] = []
    for tag, tag_lessons in sorted(grouped.items()):
        status_counts = {status.value: 0 for status in REPORT_STATUSES}
        for lesson in tag_lessons:
            if lesson.status in REPORT_STATUSES:
                status_counts[lesson.status.value] += 1
        groups.append(
            TagLessonGroup(
                tag=tag,
                count=len(tag_lessons),
                status_counts=status_counts,
                lessons=[_summary(lesson) for lesson in _sort_lessons(tag_lessons)],
            )
        )
    return groups


def _active_citation_counts(lessons: list[Lesson]) -> list[ActiveCitationCount]:
    active_lessons = [
        lesson for lesson in lessons if lesson.status is EntityStatus.ACTIVE
    ]
    return [
        ActiveCitationCount(
            lesson_id=lesson.id,
            title=lesson.title,
            citation_count=len(lesson.related_ticket_ids),
            ticket_ids=lesson.related_ticket_ids,
        )
        for lesson in _sort_lessons(active_lessons)
    ]


def _promotion_backlog(
    lessons: list[Lesson], now: datetime
) -> tuple[float | None, datetime | None]:
    draft_created = [
        lesson.created_at for lesson in lessons if lesson.status is EntityStatus.DRAFT
    ]
    if not draft_created:
        return None, None
    oldest = min(draft_created)
    age_hours = round((now - oldest).total_seconds() / 3600, 2)
    return age_hours, oldest


def _dwell_breaches(
    debt_items: list[DebtItem], tickets: list[Ticket]
) -> list[LessonDwellBreach]:
    key_by_id = {ticket.id: ticket.key for ticket in tickets}
    counts: dict[UUID, int] = defaultdict(int)
    for item in debt_items:
        if item.anomaly_type is AnomalyType.DWELL_BREACH:
            counts[item.ticket_id] += 1

    return sorted(
        [
            LessonDwellBreach(
                ticket_id=ticket_id,
                ticket_key=key_by_id.get(ticket_id, str(ticket_id)),
                count=count,
            )
            for ticket_id, count in counts.items()
        ],
        key=lambda breach: breach.ticket_key,
    )


def build_lessons_report(
    lesson_repo: LessonRepo,
    debt_repo: DebtItemRepo | None = None,
    ticket_repo: TicketRepo | None = None,
    *,
    now: datetime,
) -> LessonsReport:
    """Build the read-only lesson analytics report from repository reads."""

    lessons = lesson_repo.list()
    debt_items = debt_repo.list() if debt_repo is not None else []
    tickets = ticket_repo.list() if ticket_repo is not None else []
    backlog_age, backlog_oldest = _promotion_backlog(lessons, now)
    return LessonsReport(
        generated_at=now,
        category_status_counts=_category_status_counts(lessons),
        lessons_by_status=_lessons_by_status(lessons),
        lessons_by_tag=_lessons_by_tag(lessons),
        active_citation_counts=_active_citation_counts(lessons),
        pattern_candidates=detect_pattern_candidates(lessons, debt_items),
        promotion_backlog_age_hours=backlog_age,
        promotion_backlog_oldest_created_at=backlog_oldest,
        dwell_breaches=_dwell_breaches(debt_items, tickets),
    )


def _summary_json(summary: LessonSummary) -> dict[str, object]:
    return {
        "lesson_id": str(summary.lesson_id),
        "title": summary.title,
        "category": summary.category,
        "tags": summary.tags,
    }


def lessons_report_json(report: LessonsReport) -> dict[str, object]:
    """The ``--json`` form, carrying the same data as the markdown report."""

    return {
        "generated_at": report.generated_at.isoformat(),
        "category_status_counts": [
            {
                "category": count.category,
                "draft": count.draft,
                "active": count.active,
                "archived": count.archived,
            }
            for count in report.category_status_counts
        ],
        "lessons_by_status": [
            {
                "status": group.status,
                "count": group.count,
                "lessons": [_summary_json(summary) for summary in group.lessons],
            }
            for group in report.lessons_by_status
        ],
        "lessons_by_tag": [
            {
                "tag": group.tag,
                "count": group.count,
                "status_counts": group.status_counts,
                "lessons": [_summary_json(summary) for summary in group.lessons],
            }
            for group in report.lessons_by_tag
        ],
        "active_citation_counts": [
            {
                "lesson_id": str(count.lesson_id),
                "title": count.title,
                "citation_count": count.citation_count,
                "ticket_ids": [str(ticket_id) for ticket_id in count.ticket_ids],
            }
            for count in report.active_citation_counts
        ],
        "pattern_candidates": [
            {"tag": candidate.tag, "count": candidate.count}
            for candidate in report.pattern_candidates
        ],
        "promotion_backlog_age_hours": report.promotion_backlog_age_hours,
        "promotion_backlog_oldest_created_at": (
            report.promotion_backlog_oldest_created_at.isoformat()
            if report.promotion_backlog_oldest_created_at is not None
            else None
        ),
        "dwell_breaches": [
            {
                "ticket_id": str(breach.ticket_id),
                "ticket_key": breach.ticket_key,
                "count": breach.count,
            }
            for breach in report.dwell_breaches
        ],
    }


def _md(value: object) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


def _tags(tags: list[str]) -> str:
    return ", ".join(tags) if tags else "n/a"


def _hours(value: float | None) -> str:
    return "n/a" if value is None else f"{value:g}"


def render_lessons_report_markdown(report: LessonsReport) -> str:
    """Render the lesson analytics report as Markdown."""

    lines: list[str] = ["# Lessons report", ""]
    lines.append(
        f"_Generated {report.generated_at.isoformat()} - read-only; computed "
        "from stored Lessons and DebtItems (no LLM calls, no writes)._"
    )
    lines.append("")

    lines.append("## Category/status counts")
    lines.append("")
    if report.category_status_counts:
        lines.append("| Category | DRAFT | ACTIVE | ARCHIVED |")
        lines.append("| --- | --- | --- | --- |")
        for category_count in report.category_status_counts:
            lines.append(
                f"| {_md(category_count.category)} | {category_count.draft} | "
                f"{category_count.active} | {category_count.archived} |"
            )
    else:
        lines.append("No lessons recorded.")
    lines.append("")

    lines.append("## Lessons by status")
    lines.append("")
    for status_group in report.lessons_by_status:
        lines.append(f"### {status_group.status.upper()}")
        lines.append("")
        if status_group.lessons:
            lines.append("| Lesson | Category | Tags |")
            lines.append("| --- | --- | --- |")
            for lesson in status_group.lessons:
                lines.append(
                    f"| {_md(lesson.title)} | {_md(lesson.category)} | "
                    f"{_md(_tags(lesson.tags))} |"
                )
        else:
            lines.append(f"No {status_group.status.upper()} lessons recorded.")
        lines.append("")

    lines.append("## Lessons by tag")
    lines.append("")
    if report.lessons_by_tag:
        lines.append("| Tag | Count | Lesson titles | DRAFT | ACTIVE | ARCHIVED |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for tag_group in report.lessons_by_tag:
            titles = ", ".join(lesson.title for lesson in tag_group.lessons)
            lines.append(
                f"| {_md(tag_group.tag)} | {tag_group.count} | {_md(titles)} | "
                f"{tag_group.status_counts[EntityStatus.DRAFT.value]} | "
                f"{tag_group.status_counts[EntityStatus.ACTIVE.value]} | "
                f"{tag_group.status_counts[EntityStatus.ARCHIVED.value]} |"
            )
    else:
        lines.append("No lesson tags recorded.")
    lines.append("")

    lines.append("## Active lesson citations")
    lines.append("")
    if report.active_citation_counts:
        lines.append("| Lesson | Citations | Ticket ids |")
        lines.append("| --- | --- | --- |")
        for citation_count in report.active_citation_counts:
            ticket_ids = ", ".join(
                str(ticket_id) for ticket_id in citation_count.ticket_ids
            )
            lines.append(
                f"| {_md(citation_count.title)} | {citation_count.citation_count} | "
                f"{_md(ticket_ids or 'n/a')} |"
            )
    else:
        lines.append("No ACTIVE lessons recorded.")
    lines.append("")

    lines.append("## Pattern candidates")
    lines.append("")
    if report.pattern_candidates:
        lines.append("| Tag | Count |")
        lines.append("| --- | --- |")
        for candidate in report.pattern_candidates:
            lines.append(f"| {_md(candidate.tag)} | {candidate.count} |")
    else:
        lines.append("No pattern candidates detected.")
    lines.append("")

    lines.append("## Promotion backlog age")
    lines.append("")
    if report.promotion_backlog_age_hours is None:
        lines.append("No DRAFT lessons awaiting promotion.")
    else:
        oldest = report.promotion_backlog_oldest_created_at
        oldest_text = oldest.isoformat() if oldest is not None else "unknown"
        lines.append(
            f"Oldest DRAFT lesson age: {_hours(report.promotion_backlog_age_hours)} "
            f"hour(s), created at {oldest_text}."
        )
    lines.append("")

    lines.append("## Dwell breaches")
    lines.append("")
    if report.dwell_breaches:
        lines.append("| Ticket | Breaches |")
        lines.append("| --- | --- |")
        for breach in report.dwell_breaches:
            lines.append(f"| {_md(breach.ticket_key)} | {breach.count} |")
    else:
        lines.append("No dwell breaches recorded.")
    lines.append("")

    return "\n".join(lines)
