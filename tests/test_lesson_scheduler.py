"""Continuous learning scheduler (ATLAS-106)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from schema_drift_helpers import assert_schema_drift_message, drifted_database
from test_models_validation import ticket_kwargs

from atlas.cli import EXIT_OK, EXIT_PRECONDITION, build_parser, main
from atlas.core.enums import ActorType, EvidenceStatus, RiskLevel
from atlas.core.models.debt_item import AnomalyType, DebtItem
from atlas.core.models.ticket import Ticket, TicketStatus, TicketType
from atlas.core.models.verification_check import VerificationCheck
from atlas.learning import ExtractionTrigger
from atlas.learning.scheduler import (
    DEFAULT_INTERVAL_SECONDS,
    LessonSchedulerConfig,
    find_tickets_needing_extraction,
    run_poll_cycle,
    run_scheduler,
)
from atlas.storage import (
    Database,
    DebtItemRepo,
    LessonRepo,
    TicketRepo,
    VerificationCheckRepo,
)
from atlas.verification import required_checks

NOW = datetime(2026, 7, 14, 10, tzinfo=UTC)


class FakeLessonClient:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return json.dumps(
            {
                "category": "failure_pattern",
                "title": "Review repeated delivery failures",
                "problem": "A ticket reached a failure trigger.",
                "solution": "Extract the recurring cause into reviewable memory.",
                "outcome": "Future agents can avoid the same delivery pattern.",
                "tags": ["learning-system"],
            }
        )


class FakeSleep:
    def __init__(self) -> None:
        self.intervals: list[float] = []

    def __call__(self, interval: float) -> bool:
        self.intervals.append(interval)
        return True


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{tmp_path}/atlas.db")
    database.create_all()
    return database


def make_ticket(
    key: str,
    *,
    product_id: UUID | None = None,
    status: TicketStatus = TicketStatus.IN_PROGRESS,
    ticket_type: TicketType = TicketType.FEATURE,
    attempted_at: datetime | None = None,
) -> Ticket:
    return Ticket(
        **ticket_kwargs()
        | {
            "id": uuid4(),
            "product_id": product_id or uuid4(),
            "key": key,
            "status": status,
            "ticket_type": ticket_type,
            "risk_level": RiskLevel.LOW,
            "created_at": NOW - timedelta(hours=6),
            "updated_at": NOW - timedelta(hours=6),
            "status_entered_at": NOW - timedelta(hours=1),
            "completed_at": NOW
            if status in {TicketStatus.DONE, TicketStatus.REJECTED}
            else None,
            "lesson_extraction_attempted_at": attempted_at,
        }
    )


def make_debt(
    ticket: Ticket,
    *,
    kind: AnomalyType = AnomalyType.DWELL_BREACH,
    created_at: datetime = NOW,
) -> DebtItem:
    return DebtItem(
        id=uuid4(),
        product_id=ticket.product_id,
        ticket_id=ticket.id,
        anomaly_type=kind,
        summary=f"{ticket.key} {kind.value}",
        observed_at=created_at,
        created_by_type=ActorType.SYSTEM,
        created_by_id="pm-engine",
        created_at=created_at,
    )


def seed_verification_check(db: Database, ticket: Ticket) -> VerificationCheck:
    check = required_checks(ticket)[0]
    return VerificationCheckRepo(db).add(
        VerificationCheck(
            id=uuid4(),
            ticket_id=ticket.id,
            check_type=check.check_type,
            status=EvidenceStatus.FAILED,
            summary=f"{check.check_type.value} failed",
            required=check.required,
            evidence_ids=[],
            created_at=NOW,
            completed_at=NOW,
        )
    )


def scheduler_config(
    db: Database,
    *,
    client: FakeLessonClient | None = None,
    extractor: Any | None = None,
) -> LessonSchedulerConfig:
    return LessonSchedulerConfig(
        db=db,
        tickets=TicketRepo(db),
        debt_items=DebtItemRepo(db),
        client=client or FakeLessonClient(),
        extractor=extractor
        if extractor is not None
        else (lambda *args, **kwargs: None),
    )


def test_identifies_done_and_rejected_tickets_without_prior_attempt() -> None:
    done = make_ticket("ATLAS-270", status=TicketStatus.DONE)
    rejected = make_ticket("ATLAS-271", status=TicketStatus.REJECTED)
    active = make_ticket("ATLAS-272", status=TicketStatus.IN_PROGRESS)

    work = find_tickets_needing_extraction([active, rejected, done], [])

    assert [(item.ticket.key, item.trigger) for item in work] == [
        ("ATLAS-270", ExtractionTrigger.DONE),
        ("ATLAS-271", ExtractionTrigger.REJECTED),
    ]


def test_tickets_with_prior_attempt_are_skipped() -> None:
    attempted = make_ticket(
        "ATLAS-270",
        status=TicketStatus.REJECTED,
        attempted_at=NOW - timedelta(minutes=5),
    )

    assert find_tickets_needing_extraction([attempted], []) == []


def test_pm_failure_analysis_debt_since_last_attempt_is_eligible() -> None:
    ticket = make_ticket(
        "ATLAS-270",
        attempted_at=NOW - timedelta(minutes=10),
    )
    old_debt = make_debt(ticket, created_at=NOW - timedelta(minutes=20))
    new_debt = make_debt(ticket, kind=AnomalyType.REVIEW_CYCLE, created_at=NOW)

    work = find_tickets_needing_extraction([ticket], [old_debt, new_debt])

    assert len(work) == 1
    assert work[0].ticket.key == "ATLAS-270"
    assert work[0].trigger is ExtractionTrigger.PM_FAILURE_ANALYSIS
    assert work[0].failure_event == new_debt


def test_failed_extraction_does_not_prevent_processing_other_tickets(
    db: Database, caplog: pytest.LogCaptureFixture
) -> None:
    repo = TicketRepo(db)
    first = repo.add(make_ticket("ATLAS-270", status=TicketStatus.REJECTED))
    second = repo.add(make_ticket("ATLAS-271", status=TicketStatus.REJECTED))
    calls: list[str] = []

    def extractor(ticket: Ticket, **_: Any) -> None:
        calls.append(ticket.key)
        if ticket.key == first.key:
            raise RuntimeError("model down")

    caplog.set_level(logging.WARNING, logger="atlas.learning.scheduler")
    attempted = run_poll_cycle(
        scheduler_config(db, extractor=extractor),
        now=NOW,
    )

    assert [item.ticket.key for item in attempted] == [first.key, second.key]
    assert calls == [first.key, second.key]
    assert "lesson-scheduler: extraction failed for ATLAS-270" in caplog.text
    assert repo.get(first.id).lesson_extraction_attempted_at == NOW  # type: ignore[union-attr]
    assert repo.get(second.id).lesson_extraction_attempted_at == NOW  # type: ignore[union-attr]


def test_run_scheduler_once_exits_after_one_cycle(db: Database) -> None:
    TicketRepo(db).add(make_ticket("ATLAS-270", status=TicketStatus.REJECTED))
    sleep = FakeSleep()
    calls: list[str] = []

    def extractor(ticket: Ticket, **_: Any) -> None:
        calls.append(ticket.key)

    run_scheduler(
        scheduler_config(db, extractor=extractor),
        once=True,
        now=lambda: NOW,
        sleep=sleep,
    )

    assert calls == ["ATLAS-270"]
    assert sleep.intervals == []


def test_parser_exposes_lessons_schedule_flags() -> None:
    args = build_parser().parse_args(
        ["lessons", "schedule", "-v", "--once", "--interval", "42"]
    )

    assert args.command == "lessons"
    assert args.lessons_command == "schedule"
    assert args.verbose is True
    assert args.once is True
    assert args.interval == 42


def test_parser_defaults_lessons_schedule_interval() -> None:
    args = build_parser().parse_args(["lessons", "schedule"])

    assert args.interval == DEFAULT_INTERVAL_SECONDS


def test_cli_lessons_schedule_once_triggers_extraction_for_fixture_tickets(
    db: Database,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = TicketRepo(db)
    declined = repo.add(make_ticket("ATLAS-269", status=TicketStatus.DONE))
    eligible = repo.add(make_ticket("ATLAS-270", status=TicketStatus.REJECTED))
    seed_verification_check(db, eligible)
    skipped = repo.add(
        make_ticket(
            "ATLAS-271",
            status=TicketStatus.REJECTED,
            attempted_at=NOW - timedelta(minutes=5),
        )
    )
    client = FakeLessonClient()

    code = main(
        ["lessons", "schedule", "--once"],
        database=db,
        client=client,
    )

    assert code == EXIT_OK
    assert len(client.prompts) == 1
    out = capsys.readouterr().out
    assert "lessons schedule: completed" in out
    assert "attempted=2" in out
    assert "extracted=1" in out
    assert "declined-as-not-notable=1" in out
    assert "failed=0" in out
    lessons = LessonRepo(db).list()
    assert len(lessons) == 1
    assert lessons[0].source_ticket_id == eligible.id
    assert lessons[0].related_ticket_ids == []
    assert repo.get(declined.id).lesson_extraction_attempted_at is not None  # type: ignore[union-attr]
    assert repo.get(eligible.id).lesson_extraction_attempted_at is not None  # type: ignore[union-attr]
    assert repo.get(skipped.id).lesson_extraction_attempted_at == (  # type: ignore[union-attr]
        NOW - timedelta(minutes=5)
    )


def test_lessons_schedule_drift_exits_before_llm_or_write(
    db: Database,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = TicketRepo(db)
    eligible = repo.add(make_ticket("ATLAS-270", status=TicketStatus.REJECTED))
    client = FakeLessonClient()
    head, parent = drifted_database(db)

    code = main(
        ["lessons", "schedule", "--once"],
        database=db,
        client=client,
    )

    assert code == EXIT_PRECONDITION
    assert client.prompts == []
    assert LessonRepo(db).list() == []
    stored = repo.get(eligible.id)
    assert stored is not None
    assert stored.lesson_extraction_attempted_at is None
    assert_schema_drift_message(
        capsys.readouterr(), store_revision=parent, code_head=head
    )
