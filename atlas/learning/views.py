"""JSON-shaped view records over learning-domain objects, shared by any front-end."""

from __future__ import annotations

from uuid import UUID

from atlas.core.models.lesson import Lesson


def source_ticket_label(lesson: Lesson, ticket_keys_by_id: dict[UUID, str]) -> str:
    return ticket_keys_by_id.get(lesson.source_ticket_id, str(lesson.source_ticket_id))


def ticket_labels(
    ticket_ids: list[UUID], ticket_keys_by_id: dict[UUID, str]
) -> list[str]:
    return [
        ticket_keys_by_id.get(ticket_id, str(ticket_id)) for ticket_id in ticket_ids
    ]


def lesson_show_record(
    lesson: Lesson, ticket_keys_by_id: dict[UUID, str]
) -> dict[str, object]:
    return {
        "id": str(lesson.id),
        "title": lesson.title,
        "category": lesson.category.value,
        "status": lesson.status.value,
        "confidence": lesson.confidence,
        "tags": list(lesson.tags),
        "problem": lesson.problem,
        "solution": lesson.solution,
        "outcome": lesson.outcome,
        "source_ticket": source_ticket_label(lesson, ticket_keys_by_id),
        "related_tickets": ticket_labels(lesson.related_ticket_ids, ticket_keys_by_id),
        "related_adr_ids": [str(adr_id) for adr_id in lesson.related_adr_ids],
        "created_by": f"{lesson.created_by_type.value}:{lesson.created_by_id}",
        "created_at": lesson.created_at.isoformat(),
        "updated_at": lesson.updated_at.isoformat(),
    }


def lesson_review_row(
    lesson: Lesson,
    ticket_keys_by_id: dict[UUID, str],
    *,
    context_pack_count: int | None = None,
) -> dict[str, object]:
    row: dict[str, object] = {
        "id": str(lesson.id),
        "title": lesson.title,
        "source_ticket": source_ticket_label(lesson, ticket_keys_by_id),
        "created_at": lesson.created_at.isoformat(),
        "status": lesson.status.value,
    }
    if context_pack_count is not None:
        row["context_pack_count"] = context_pack_count
        row["last_operator_action_at"] = lesson.updated_at.isoformat()
    return row
