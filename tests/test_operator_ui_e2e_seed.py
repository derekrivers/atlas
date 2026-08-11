from __future__ import annotations

from pathlib import Path

from atlas.core.enums import ActorType
from atlas.core.keys import natural_key
from atlas.core.models import EvidenceType
from atlas.dependencies import NotReadyCode
from atlas.dependencies.validation import TERMINAL_STATUSES
from atlas.orchestration import (
    review_queue,
    system_status,
    ticket_board,
    ticket_dependencies,
)
from atlas.orchestration.ticket_evidence import ticket_evidence
from atlas.storage import EpicRepo, LessonRepo, PmSyncReceiptRepo, TicketRepo
from atlas.tools.operator_ui_e2e_seed import seed_store


def test_operator_ui_e2e_seed_reproduces_live_api_edge_shapes(tmp_path: Path) -> None:
    db = seed_store(f"sqlite:///{tmp_path / 'atlas.db'}")
    try:
        tickets = TicketRepo(db).list()
        board = ticket_board(db)
        board_keys = [item.key for item in board]
        epic_keys = {epic.key for epic in EpicRepo(db).list()}

        terminal = [
            ticket for ticket in tickets if ticket.status.value in TERMINAL_STATUSES
        ]
        assert len(tickets) == 17
        assert epic_keys == {"ATLAS-E1", "ATLAS-E2"}
        assert len(terminal) == 16
        assert len(terminal) / len(tickets) > 0.9
        assert {item.epic_key for item in board} >= {
            "ATLAS-E1",
            "ATLAS-E2",
            None,
        }
        [receipt] = PmSyncReceiptRepo(db).list()
        assert system_status(db).last_linear_sync_at == receipt.finished_at

        assert board_keys.index("ATLAS-10") < board_keys.index("ATLAS-2")
        assert board_keys != sorted(board_keys, key=natural_key)

        assert review_queue(db) == ()
        evidence = ticket_evidence(db, "ATLAS-1")
        assert evidence is not None
        assert [record.evidence_type for record in evidence] == [
            EvidenceType.MANUAL_APPROVAL,
            EvidenceType.TEST_RESULT,
        ]
        assert [record.trust_level for record in evidence] == [
            ActorType.AGENT,
            ActorType.SYSTEM,
        ]
        assert [record.has_system_pin_triple for record in evidence] == [
            False,
            True,
        ]
        assert ticket_evidence(db, "ATLAS-10") == ()

        dependencies = ticket_dependencies(db, "ATLAS-2")
        assert dependencies is not None
        assert dependencies.readiness.ready is False
        reason_codes = {reason.code for reason in dependencies.readiness.reasons}
        assert len(reason_codes) > 1
        assert {
            NotReadyCode.WRONG_STATUS,
            NotReadyCode.ADR_NOT_ACCEPTED,
            NotReadyCode.NO_ACCEPTANCE_CRITERIA,
        }.issubset(reason_codes)

        stored_ticket_ids = {ticket.id for ticket in tickets}
        lessons = LessonRepo(db).list()
        assert len(lessons) == 10
        for lesson in lessons:
            assert lesson.source_ticket_id not in stored_ticket_ids
            assert set(lesson.related_ticket_ids).isdisjoint(stored_ticket_ids)
    finally:
        db.engine.dispose()
