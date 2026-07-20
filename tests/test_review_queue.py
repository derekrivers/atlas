"""Ticket-centric review queue assembly from persisted records."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from test_apply import _epic_model_kwargs, _ticket_model_kwargs
from test_plan_pipeline import fresh_db

from atlas.core.enums import ActorType, EvidenceStatus
from atlas.core.models import (
    Epic,
    Evidence,
    EvidenceType,
    Ticket,
    TicketStatus,
    VerificationCheck,
    VerificationCheckType,
)
from atlas.orchestration import ReviewCheckState, review_queue
from atlas.storage import (
    Database,
    EpicRepo,
    EvidenceRepo,
    ProductRepo,
    TicketRepo,
    VerificationCheckRepo,
)
from atlas.verification import required_checks

NOW = datetime(2026, 7, 20, 12, tzinfo=UTC)


@pytest.fixture
def store(tmp_path: Path) -> tuple[Database, UUID, UUID]:
    db = fresh_db(tmp_path)
    product = ProductRepo(db).get_by_key("ATLAS")
    assert product is not None
    epic = Epic(**_epic_model_kwargs(product.id, key="ATLAS-E1"))
    EpicRepo(db).add(epic)
    return db, product.id, epic.id


def _add_ticket(
    store: tuple[Database, UUID, UUID],
    key: str,
    *,
    status: TicketStatus = TicketStatus.REVIEW_REQUIRED,
) -> Ticket:
    db, product_id, epic_id = store
    ticket = Ticket(
        **(
            _ticket_model_kwargs(product_id, epic_id, key=key)
            | {
                "title": f"Review {key}",
                "status": status,
            }
        )
    )
    return TicketRepo(db).add(ticket)


def _check(
    ticket: Ticket,
    check_type: VerificationCheckType,
    status: EvidenceStatus,
    *,
    offset: int,
) -> VerificationCheck:
    return VerificationCheck(
        id=uuid4(),
        ticket_id=ticket.id,
        check_type=check_type,
        status=status,
        summary=f"{check_type.value}: {status.value}",
        required=True,
        created_at=NOW + timedelta(seconds=offset),
    )


def _add_required_checks(
    db: Database,
    ticket: Ticket,
    *,
    failing_type: VerificationCheckType | None = None,
) -> list[VerificationCheck]:
    rows = [
        _check(
            ticket,
            required.check_type,
            (
                EvidenceStatus.FAILED
                if required.check_type is failing_type
                else EvidenceStatus.PASSED
            ),
            offset=index,
        )
        for index, required in enumerate(required_checks(ticket))
        if required.required
    ]
    repo = VerificationCheckRepo(db)
    for row in rows:
        repo.add(row)
    return rows


def _evidence(
    ticket: Ticket,
    *,
    created_by_type: ActorType,
    evidence_type: EvidenceType = EvidenceType.TEST_RESULT,
    offset: int = 0,
) -> Evidence:
    is_system = created_by_type is ActorType.SYSTEM
    return Evidence(
        id=uuid4(),
        product_id=ticket.product_id,
        ticket_id=ticket.id,
        evidence_type=evidence_type,
        status=EvidenceStatus.PASSED if is_system else EvidenceStatus.PENDING,
        summary=evidence_type.value,
        commit_sha="abc123" if is_system else None,
        external_run_id=f"run-{offset}" if is_system else None,
        payload_hash=f"hash-{offset}" if is_system else None,
        created_by_type=created_by_type,
        created_by_id="review-queue-test",
        created_at=NOW + timedelta(seconds=offset),
    )


def test_passed_persisted_checks_produce_passed_review_state(
    store: tuple[Database, UUID, UUID],
) -> None:
    db, _, _ = store
    ticket = _add_ticket(store, "ATLAS-20")
    rows = _add_required_checks(db, ticket)

    state = review_queue(db)[0]

    assert state.key == ticket.key
    assert state.title == ticket.title
    assert state.status is TicketStatus.REVIEW_REQUIRED
    assert state.ticket_type is ticket.ticket_type
    assert state.verdict is EvidenceStatus.PASSED
    assert state.checks == tuple(
        ReviewCheckState(row.check_type, row.status) for row in rows
    )


def test_failed_persisted_check_is_in_breakdown_and_sinks_verdict(
    store: tuple[Database, UUID, UUID],
) -> None:
    db, _, _ = store
    ticket = _add_ticket(store, "ATLAS-21")
    failing_type = next(
        required.check_type for required in required_checks(ticket) if required.required
    )
    _add_required_checks(db, ticket, failing_type=failing_type)

    state = review_queue(db)[0]

    assert state.verdict is EvidenceStatus.FAILED
    assert ReviewCheckState(failing_type, EvidenceStatus.FAILED) in state.checks


def test_no_persisted_checks_is_pending_not_vacuous_pass(
    store: tuple[Database, UUID, UUID],
) -> None:
    db, _, _ = store
    _add_ticket(store, "ATLAS-22")

    state = review_queue(db)[0]

    assert state.verdict is EvidenceStatus.PENDING
    assert state.checks == ()


def test_evidence_signals_are_derived_from_ticket_evidence(
    store: tuple[Database, UUID, UUID],
) -> None:
    db, _, _ = store
    ticket = _add_ticket(store, "ATLAS-23")
    evidence_repo = EvidenceRepo(db)
    evidence_repo.add(_evidence(ticket, created_by_type=ActorType.AGENT))

    without_system = review_queue(db)[0]
    assert not without_system.has_system_evidence
    assert not without_system.has_pr_merged_evidence

    evidence_repo.add(
        _evidence(
            ticket,
            created_by_type=ActorType.SYSTEM,
            offset=1,
        )
    )

    with_system = review_queue(db)[0]
    assert with_system.has_system_evidence
    assert not with_system.has_pr_merged_evidence

    evidence_repo.add(
        _evidence(
            ticket,
            created_by_type=ActorType.SYSTEM,
            evidence_type=EvidenceType.PR_MERGED,
            offset=2,
        )
    )

    with_system_merge = review_queue(db)[0]
    assert with_system_merge.has_system_evidence
    assert with_system_merge.has_pr_merged_evidence


def test_non_review_required_tickets_are_excluded(
    store: tuple[Database, UUID, UUID],
) -> None:
    db, _, _ = store
    _add_ticket(store, "ATLAS-24", status=TicketStatus.PR_OPEN)
    included = _add_ticket(store, "ATLAS-25")

    assert tuple(state.key for state in review_queue(db)) == (included.key,)


def test_queue_is_ordered_by_ticket_key(
    store: tuple[Database, UUID, UUID],
) -> None:
    db, _, _ = store
    _add_ticket(store, "ATLAS-30")
    _add_ticket(store, "ATLAS-10")
    _add_ticket(store, "ATLAS-20")

    assert tuple(state.key for state in review_queue(db)) == (
        "ATLAS-10",
        "ATLAS-20",
        "ATLAS-30",
    )


def test_empty_queue_is_empty_tuple(
    store: tuple[Database, UUID, UUID],
) -> None:
    db, _, _ = store

    result = review_queue(db)

    assert result == ()
    assert isinstance(result, tuple)
