"""Key-addressed evidence projection assembly from persisted records."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from test_apply import _epic_model_kwargs, _ticket_model_kwargs
from test_plan_pipeline import fresh_db

from atlas.core.enums import ActorType, EvidenceStatus
from atlas.core.models import Epic, Evidence, EvidenceType, Ticket
from atlas.orchestration import TicketEvidenceRecordState, ticket_evidence
from atlas.storage import Database, EpicRepo, EvidenceRepo, ProductRepo, TicketRepo

NOW = datetime(2026, 7, 25, 9, tzinfo=UTC)


@pytest.fixture
def store(tmp_path: Path) -> tuple[Database, UUID, UUID]:
    db = fresh_db(tmp_path)
    product = ProductRepo(db).get_by_key("ATLAS")
    assert product is not None
    epic = Epic(**_epic_model_kwargs(product.id, key="ATLAS-E1"))
    EpicRepo(db).add(epic)
    return db, product.id, epic.id


def _add_ticket(store: tuple[Database, UUID, UUID], key: str) -> Ticket:
    db, product_id, epic_id = store
    ticket = Ticket(**_ticket_model_kwargs(product_id, epic_id, key=key))
    return TicketRepo(db).add(ticket)


def _evidence(
    ticket: Ticket,
    *,
    created_by_type: ActorType,
    evidence_type: EvidenceType = EvidenceType.TEST_RESULT,
    status: EvidenceStatus = EvidenceStatus.PASSED,
    offset: int = 0,
) -> Evidence:
    is_system = created_by_type is ActorType.SYSTEM
    return Evidence(
        id=uuid4(),
        product_id=ticket.product_id,
        ticket_id=ticket.id,
        evidence_type=evidence_type,
        status=status,
        summary=evidence_type.value,
        commit_sha=f"commit-{offset}" if is_system else None,
        external_run_id=f"run-{offset}" if is_system else None,
        payload_hash=f"hash-{offset}" if is_system else None,
        raw_payload={"secret": f"payload-{offset}"},
        created_by_type=created_by_type,
        created_by_id="ticket-evidence-test",
        created_at=NOW + timedelta(seconds=offset),
    )


def test_ticket_evidence_resolves_key_and_projects_records_oldest_first(
    store: tuple[Database, UUID, UUID],
) -> None:
    db, _, _ = store
    ticket = _add_ticket(store, "ATLAS-200")
    repo = EvidenceRepo(db)
    later = repo.add(
        _evidence(
            ticket,
            created_by_type=ActorType.HUMAN,
            evidence_type=EvidenceType.MANUAL_APPROVAL,
            offset=2,
        )
    )
    earlier = repo.add(_evidence(ticket, created_by_type=ActorType.SYSTEM, offset=1))

    assert ticket_evidence(db, ticket.key) == (
        TicketEvidenceRecordState(
            evidence_type=earlier.evidence_type,
            trust_level=ActorType.SYSTEM,
            status=earlier.status,
            has_system_pin_triple=True,
        ),
        TicketEvidenceRecordState(
            evidence_type=later.evidence_type,
            trust_level=ActorType.HUMAN,
            status=later.status,
            has_system_pin_triple=False,
        ),
    )


def test_ticket_evidence_distinguishes_unknown_ticket_from_empty_evidence(
    store: tuple[Database, UUID, UUID],
) -> None:
    db, _, _ = store
    ticket = _add_ticket(store, "ATLAS-201")

    assert ticket_evidence(db, ticket.key) == ()
    assert ticket_evidence(db, "ATLAS-404") is None
