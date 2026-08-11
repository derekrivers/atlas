"""Read-only repository probe for the seeded Operator UI live milestone."""

from __future__ import annotations

import argparse
import json
from typing import Any

from atlas.context import retrieve_lessons
from atlas.orchestration import present_operator_action_receipt
from atlas.storage import Database, LessonRepo, OperatorActionReceiptRepo, TicketRepo


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="isolated milestone database URL")
    parser.add_argument(
        "--context-ticket",
        default="ATLAS-2",
        help="ticket used for ACTIVE-only lesson retrieval",
    )
    return parser


def build_probe(database: Database, *, context_ticket: str) -> dict[str, Any]:
    """Return safe repository observables without writing or using raw SQL."""

    lessons = LessonRepo(database).list()
    receipts = OperatorActionReceiptRepo(database).list()
    ticket = TicketRepo(database).get_by_key(context_ticket)
    if ticket is None:
        raise ValueError(f"context ticket {context_ticket!r} was not seeded")
    retrieved = retrieve_lessons(ticket, database, cap=100)
    return {
        "context_lesson_ids": [str(lesson.id) for lesson in retrieved],
        "lessons": {
            str(lesson.id): {
                "confidence": lesson.confidence,
                "status": lesson.status.value,
                "updated_at": lesson.updated_at.isoformat(),
            }
            for lesson in lessons
        },
        "receipts": [present_operator_action_receipt(receipt) for receipt in receipts],
    }


def main() -> int:
    args = _parser().parse_args()
    database = Database(args.db)
    try:
        print(
            json.dumps(
                build_probe(database, context_ticket=args.context_ticket),
                sort_keys=True,
            )
        )
    finally:
        database.engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
