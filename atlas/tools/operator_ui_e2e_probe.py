"""Read-only repository probe for the seeded Operator UI live milestone."""

from __future__ import annotations

import argparse
import json
from typing import Any

import sqlalchemy as sa

from atlas.context import retrieve_lessons
from atlas.orchestration import present_operator_action_receipt
from atlas.storage import (
    AcceptanceSessionRepo,
    Database,
    EvidenceRepo,
    LessonRepo,
    OperatorActionReceiptRepo,
    PmSyncReceiptRepo,
    TicketRepo,
    TicketStatusTransitionRepo,
    VerificationCheckRepo,
)
from atlas.storage.preconditions import database_schema_revision


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
    tickets = TicketRepo(database).list()
    ticket = next((item for item in tickets if item.key == context_ticket), None)
    retrieved = () if ticket is None else retrieve_lessons(ticket, database, cap=100)
    acceptance_sessions = AcceptanceSessionRepo(database).list()
    evidence = EvidenceRepo(database).list()
    verification_checks = VerificationCheckRepo(database).list()
    transitions = TicketStatusTransitionRepo(database).list_all()
    sync_receipts = PmSyncReceiptRepo(database).list()
    return {
        "acceptance_sessions": [
            session.model_dump(mode="json") for session in acceptance_sessions
        ],
        "context_lesson_ids": [str(lesson.id) for lesson in retrieved],
        "evidence": [
            {
                "commit_sha": record.commit_sha,
                "created_by_type": record.created_by_type.value,
                "id": str(record.id),
                "status": record.status.value,
                "ticket_id": str(record.ticket_id),
                "type": record.evidence_type.value,
            }
            for record in evidence
        ],
        "lessons": {
            str(lesson.id): {
                "confidence": lesson.confidence,
                "status": lesson.status.value,
                "updated_at": lesson.updated_at.isoformat(),
            }
            for lesson in lessons
        },
        "pm_sync_receipts": [
            {"id": str(receipt.id), "result": receipt.result.value}
            for receipt in sync_receipts
        ],
        "receipts": [present_operator_action_receipt(receipt) for receipt in receipts],
        "schema": {
            "revision": database_schema_revision(database),
            "tables": sorted(sa.inspect(database.engine).get_table_names()),
        },
        "ticket_statuses": {ticket.key: ticket.status.value for ticket in tickets},
        "ticket_transitions": [
            {
                "from": transition.from_status,
                "id": str(transition.id),
                "ticket_id": str(transition.ticket_id),
                "to": transition.to_status,
            }
            for transition in transitions
        ],
        "verification_checks": [
            {
                "id": str(check.id),
                "required": check.required,
                "status": check.status.value,
                "ticket_id": str(check.ticket_id),
                "type": check.check_type.value,
            }
            for check in verification_checks
        ],
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
