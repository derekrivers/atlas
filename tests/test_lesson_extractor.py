"""Lesson extraction (ATLAS-99): bounded bundle, triggers, and CLI."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from schema_drift_helpers import assert_schema_drift_message, drifted_database
from test_agent_run_model import agent_run_kwargs
from test_evidence_model import evidence_kwargs
from test_models_validation import ticket_kwargs

from atlas.cli import EXIT_OK, EXIT_PRECONDITION, EXIT_RECORDED_FAILURE, main
from atlas.context import select_lessons
from atlas.core.enums import ActorType, EvidenceStatus, RiskLevel
from atlas.core.models.agent_run import AgentRun
from atlas.core.models.debt_item import AnomalyType, DebtItem
from atlas.core.models.evidence import Evidence
from atlas.core.models.lesson import Lesson
from atlas.core.models.ticket import Ticket, TicketStatus, TicketType
from atlas.core.models.verification_check import VerificationCheck
from atlas.learning import (
    ExtractionTrigger,
    assemble_evidence_bundle,
    extract_lesson_for_ticket,
)
from atlas.learning.extractor import (
    LESSON_EXTRACTOR_VERSION,
    PROMPTS_DIR,
    render_extraction_prompt,
)
from atlas.storage import (
    AgentRunRepo,
    Database,
    LessonRepo,
    TicketRepo,
    VerificationCheckRepo,
)
from atlas.verification import required_checks

NOW = datetime(2026, 7, 14, 10, tzinfo=UTC)
LESSON_EXTRACTOR_V1_0_SHA256 = (
    "e669d91334d267024389306d663af5ca3cdecb4c61653bca080cffa2ede86179"
)
LESSON_ALLOWED_OUTPUT_KEYS = [
    "category",
    "title",
    "problem",
    "solution",
    "outcome",
    "related_adr_ids",
    "tags",
]


class FakeLessonClient:
    def __init__(
        self,
        payload: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.payload = payload or {
            "category": "failure_pattern",
            "title": "Keep tickets narrow",
            "problem": "The evidence shows a delivery pattern worth reviewing.",
            "solution": "Use smaller scoped tickets and explicit verification.",
            "outcome": "Future work should avoid the observed failure mode.",
            "tags": ["feature", "learning-system"],
        }
        self.error = error
        self.call_count = 0
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.call_count += 1
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        return json.dumps(self.payload)


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
    created_at: datetime = NOW - timedelta(hours=6),
    status_entered_at: datetime | None = None,
    tags: list[str] | None = None,
    component: str | None = None,
    review_cycle_count: int = 0,
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
            "created_at": created_at,
            "updated_at": created_at,
            "status_entered_at": status_entered_at,
            "tags": tags or [],
            "component": component,
            "review_cycle_count": review_cycle_count,
        }
    )


def render_prompt_for_ticket(ticket: Ticket) -> str:
    bundle = assemble_evidence_bundle(
        ticket=ticket,
        agent_runs=[],
        pr_review_history=[],
        verification_checks=[],
        trigger=ExtractionTrigger.OPERATOR_REQUEST,
    )
    rendered = render_extraction_prompt(
        {
            "trigger": ExtractionTrigger.OPERATOR_REQUEST.value,
            "ticket_key": ticket.key,
            "raw_diff_size_cap_bytes": 8192,
            "source_ticket_tags_json": json.dumps(ticket.tags),
            "source_ticket_component_json": json.dumps(ticket.component),
            "source_ticket_has_tag_vocabulary": bool(ticket.tags or ticket.component),
            "lesson_json_schema": json.dumps(
                Lesson.model_json_schema(),
                indent=2,
                sort_keys=True,
                default=str,
            ),
            "evidence_bundle_json": json.dumps(
                bundle,
                indent=2,
                sort_keys=True,
                default=str,
            ),
        }
    )
    return rendered.text


def allowed_output_keys(prompt: str) -> list[str]:
    section = prompt.split("Allowed output keys:\n\n", 1)[1].split("\n\n", 1)[0]
    return [line.removeprefix("- `").removesuffix("`") for line in section.splitlines()]


def seed_ticket(db: Database, ticket: Ticket) -> Ticket:
    TicketRepo(db).add(ticket)
    return ticket


def pass_checks(db: Database, ticket: Ticket) -> list[VerificationCheck]:
    repo = VerificationCheckRepo(db)
    rows: list[VerificationCheck] = []
    for check in required_checks(ticket):
        if not check.required:
            continue
        row = VerificationCheck(
            id=uuid4(),
            ticket_id=ticket.id,
            check_type=check.check_type,
            status=EvidenceStatus.PASSED,
            summary=f"{check.check_type.value} passed",
            required=True,
            evidence_ids=[],
            created_at=NOW,
            completed_at=NOW,
        )
        rows.append(repo.add(row))
    return rows


def seed_agent_run(db: Database, ticket: Ticket) -> AgentRun:
    return AgentRunRepo(db).add(
        AgentRun(
            **agent_run_kwargs()
            | {
                "id": uuid4(),
                "product_id": ticket.product_id,
                "ticket_id": ticket.id,
                "created_at": NOW - timedelta(minutes=30),
                "started_at": NOW - timedelta(minutes=25),
                "completed_at": NOW - timedelta(minutes=5),
            }
        )
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


def make_debt(ticket: Ticket, kind: AnomalyType = AnomalyType.DWELL_BREACH) -> DebtItem:
    return DebtItem(
        id=uuid4(),
        product_id=ticket.product_id,
        ticket_id=ticket.id,
        anomaly_type=kind,
        summary=f"{ticket.key} breached {kind.value}",
        observed_at=NOW,
        created_by_type=ActorType.SYSTEM,
        created_by_id="pm-engine",
        created_at=NOW,
    )


def test_evidence_bundle_includes_expected_fields_and_excludes_oversized_diff() -> None:
    ticket = make_ticket("ATLAS-270")
    run = AgentRun(
        **agent_run_kwargs()
        | {"id": uuid4(), "product_id": ticket.product_id, "ticket_id": ticket.id}
    )
    review = Evidence(
        **evidence_kwargs()
        | {
            "id": uuid4(),
            "product_id": ticket.product_id,
            "ticket_id": ticket.id,
            "evidence_type": "pr_review",
            "raw_payload": {"diff": "x" * 1000, "state": "CHANGES_REQUESTED"},
        }
    )
    check = VerificationCheck(
        id=uuid4(),
        ticket_id=ticket.id,
        check_type=required_checks(ticket)[0].check_type,
        status=EvidenceStatus.FAILED,
        summary="tests failed",
        created_at=NOW,
        completed_at=NOW,
    )

    bundle = assemble_evidence_bundle(
        ticket=ticket,
        agent_runs=[run],
        pr_review_history=[review],
        verification_checks=[check],
        trigger=ExtractionTrigger.REJECTED,
        raw_diff_size_cap_bytes=300,
    )

    assert bundle["ticket"]["key"] == "ATLAS-270"
    assert bundle["agent_runs"][0]["ticket_id"] == str(ticket.id)
    assert bundle["verification_verdicts"][0]["status"] == "failed"
    raw_payload = bundle["pr_review_history"][0]["raw_payload"]
    assert raw_payload["diff"]["_excluded"] is True
    assert "x" * 1000 not in json.dumps(bundle)


def test_default_extractor_loads_v1_1_and_preserves_v1_0_template() -> None:
    ticket = make_ticket("ATLAS-270")
    prompt = render_extraction_prompt(
        {
            "trigger": ExtractionTrigger.OPERATOR_REQUEST.value,
            "ticket_key": ticket.key,
            "raw_diff_size_cap_bytes": 8192,
            "source_ticket_tags_json": json.dumps(ticket.tags),
            "source_ticket_component_json": json.dumps(ticket.component),
            "source_ticket_has_tag_vocabulary": bool(ticket.tags or ticket.component),
            "lesson_json_schema": "{}",
            "evidence_bundle_json": "{}",
        }
    )

    v1_0_template = PROMPTS_DIR / "lesson-extractor-v1.0.0.md.j2"
    assert LESSON_EXTRACTOR_VERSION == "lesson-extractor-v1.1.0"
    assert prompt.prompt_version == "lesson-extractor-v1.1.0"
    assert (PROMPTS_DIR / "lesson-extractor-v1.1.0.md.j2").is_file()
    assert hashlib.sha256(v1_0_template.read_bytes()).hexdigest() == (
        LESSON_EXTRACTOR_V1_0_SHA256
    )


def test_prompt_renders_source_ticket_vocabulary_and_tag_guidance() -> None:
    ticket = make_ticket(
        "ATLAS-270",
        tags=["learning-system", "linear-sync"],
        component="lesson-extractor",
    )

    prompt = render_prompt_for_ticket(ticket)

    assert json.dumps(ticket.tags) in prompt
    assert json.dumps(ticket.component) in prompt
    assert (
        "draw tags primarily from the source ticket's `tags` and `component`" in prompt
    )
    assert "Add at most 2 novel tags" in prompt


def test_prompt_without_ticket_vocabulary_does_not_reuse_empty_facets() -> None:
    ticket = make_ticket("ATLAS-270", tags=[], component=None)

    prompt = render_prompt_for_ticket(ticket)
    flat = " ".join(prompt.split())
    source_vocabulary_guidance = (
        "draw tags primarily from the source ticket's `tags` and `component`"
    )

    assert "Source ticket tag vocabulary: none supplied" in prompt
    assert "`tags` is [] and `component` is null" in flat
    assert source_vocabulary_guidance not in prompt
    assert "without pretending there is source ticket vocabulary to reuse" in flat


def test_prompt_preserves_lesson_output_key_contract() -> None:
    prompt = render_prompt_for_ticket(make_ticket("ATLAS-270"))
    flat = " ".join(prompt.split())

    assert allowed_output_keys(prompt) == LESSON_ALLOWED_OUTPUT_KEYS
    assert (
        "Do not output `status`, `confidence`, `source_ticket_id`, "
        "`related_ticket_ids`, identity fields, or timestamps."
    ) in flat


def test_done_ticket_without_prior_failures_is_not_extracted(db: Database) -> None:
    ticket = seed_ticket(
        db,
        make_ticket(
            "ATLAS-270",
            status=TicketStatus.DONE,
            status_entered_at=NOW,
        ),
    )
    pass_checks(db, ticket)
    client = FakeLessonClient()

    lesson = extract_lesson_for_ticket(
        ticket,
        db=db,
        client=client,
        now=NOW,
        trigger=ExtractionTrigger.DONE,
    )

    assert lesson is None
    assert client.prompts == []
    assert LessonRepo(db).list() == []
    stored = TicketRepo(db).get(ticket.id)
    assert stored is not None
    assert stored.lesson_extraction_attempted_at == NOW


def test_done_ticket_with_prior_same_type_failure_persists_draft(db: Database) -> None:
    product_id = uuid4()
    seed_ticket(
        db,
        make_ticket(
            "ATLAS-269",
            product_id=product_id,
            status=TicketStatus.REJECTED,
            ticket_type=TicketType.FEATURE,
            created_at=NOW - timedelta(days=4),
            status_entered_at=NOW - timedelta(days=3),
        ),
    )
    ticket = seed_ticket(
        db,
        make_ticket(
            "ATLAS-270",
            product_id=product_id,
            status=TicketStatus.DONE,
            ticket_type=TicketType.FEATURE,
            created_at=NOW - timedelta(hours=6),
            status_entered_at=NOW,
        ),
    )
    pass_checks(db, ticket)

    lesson = extract_lesson_for_ticket(
        ticket,
        db=db,
        client=FakeLessonClient(),
        now=NOW,
        trigger=ExtractionTrigger.DONE,
    )

    assert lesson is not None
    assert lesson.status.value == "draft"
    assert lesson.confidence is None
    assert lesson.source_ticket_id == ticket.id
    assert lesson.related_ticket_ids == []
    assert LessonRepo(db).list() == [lesson]
    stored = TicketRepo(db).get(ticket.id)
    assert stored is not None
    assert stored.lesson_extraction_attempted_at == NOW


def test_rejected_ticket_without_attempted_work_is_not_extracted(
    db: Database,
) -> None:
    ticket = seed_ticket(
        db,
        make_ticket(
            "ATLAS-270",
            status=TicketStatus.REJECTED,
            status_entered_at=NOW,
        ),
    )
    client = FakeLessonClient()

    lesson = extract_lesson_for_ticket(
        ticket,
        db=db,
        client=client,
        now=NOW,
        trigger=ExtractionTrigger.REJECTED,
    )

    assert lesson is None
    assert client.call_count == 0
    assert client.prompts == []
    assert AgentRunRepo(db).list_for_ticket(ticket.id) == []
    assert VerificationCheckRepo(db).list_for_ticket(ticket.id) == []
    assert LessonRepo(db).list() == []
    stored = TicketRepo(db).get(ticket.id)
    assert stored is not None
    assert stored.lesson_extraction_attempted_at == NOW


def test_rejected_ticket_with_agent_run_persists_draft_and_calls_model_once(
    db: Database,
) -> None:
    ticket = seed_ticket(
        db,
        make_ticket(
            "ATLAS-270",
            status=TicketStatus.REJECTED,
            status_entered_at=NOW,
        ),
    )
    seed_agent_run(db, ticket)
    client = FakeLessonClient()

    lesson = extract_lesson_for_ticket(
        ticket,
        db=db,
        client=client,
        now=NOW,
        trigger=ExtractionTrigger.REJECTED,
    )

    assert lesson is not None
    assert client.call_count == 1
    assert lesson.status.value == "draft"
    assert lesson.confidence is None
    assert lesson.source_ticket_id == ticket.id
    assert lesson.related_ticket_ids == []
    assert VerificationCheckRepo(db).list_for_ticket(ticket.id) == []
    assert LessonRepo(db).list() == [lesson]
    stored = TicketRepo(db).get(ticket.id)
    assert stored is not None
    assert stored.lesson_extraction_attempted_at == NOW


def test_rejected_ticket_with_verification_check_without_agent_run_extracts(
    db: Database,
) -> None:
    ticket = seed_ticket(
        db,
        make_ticket(
            "ATLAS-270",
            status=TicketStatus.REJECTED,
            status_entered_at=NOW,
        ),
    )
    seed_verification_check(db, ticket)
    client = FakeLessonClient()

    lesson = extract_lesson_for_ticket(
        ticket,
        db=db,
        client=client,
        now=NOW,
        trigger=ExtractionTrigger.REJECTED,
    )

    assert lesson is not None
    assert client.call_count == 1
    assert lesson.status.value == "draft"
    assert lesson.source_ticket_id == ticket.id
    assert AgentRunRepo(db).list_for_ticket(ticket.id) == []
    assert LessonRepo(db).list() == [lesson]


def test_rejected_ticket_force_bypasses_attempted_work_gate(
    db: Database,
) -> None:
    ticket = seed_ticket(
        db,
        make_ticket(
            "ATLAS-270",
            status=TicketStatus.REJECTED,
            status_entered_at=NOW,
        ),
    )
    client = FakeLessonClient()

    lesson = extract_lesson_for_ticket(
        ticket,
        db=db,
        client=client,
        now=NOW,
        trigger=ExtractionTrigger.REJECTED,
        force=True,
    )

    assert lesson is not None
    assert client.call_count == 1
    assert lesson.source_ticket_id == ticket.id
    assert AgentRunRepo(db).list_for_ticket(ticket.id) == []
    assert VerificationCheckRepo(db).list_for_ticket(ticket.id) == []
    assert LessonRepo(db).list() == [lesson]
    stored = TicketRepo(db).get(ticket.id)
    assert stored is not None
    assert stored.lesson_extraction_attempted_at == NOW


def test_pm_failure_analysis_event_persists_draft(db: Database) -> None:
    ticket = seed_ticket(db, make_ticket("ATLAS-270"))
    event = make_debt(ticket, AnomalyType.REVIEW_CYCLE)

    lesson = extract_lesson_for_ticket(
        ticket,
        db=db,
        client=FakeLessonClient(),
        now=NOW,
        trigger=ExtractionTrigger.PM_FAILURE_ANALYSIS,
        failure_event=event,
        force=True,
    )

    assert lesson is not None
    assert lesson.confidence is None
    assert lesson.source_ticket_id == ticket.id
    assert lesson.related_ticket_ids == []
    stored = TicketRepo(db).get(ticket.id)
    assert stored is not None
    assert stored.lesson_extraction_attempted_at == NOW


def test_cli_lessons_extract_persists_draft_with_null_confidence(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    ticket = seed_ticket(db, make_ticket("ATLAS-270"))

    code = main(
        ["lessons", "extract", "ATLAS-270"],
        database=db,
        client=FakeLessonClient(),
    )

    out = capsys.readouterr().out
    assert code == EXIT_OK
    assert "Extracted DRAFT lesson" in out
    (lesson,) = LessonRepo(db).list()
    assert lesson.status.value == "draft"
    assert lesson.confidence is None
    assert lesson.source_ticket_id == ticket.id
    assert lesson.related_ticket_ids == []


def test_lessons_extract_drift_exits_before_llm_or_write(
    db: Database,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ticket = seed_ticket(db, make_ticket("ATLAS-270"))
    client = FakeLessonClient()
    head, parent = drifted_database(db)

    code = main(
        ["lessons", "extract", "ATLAS-270"],
        database=db,
        client=client,
    )

    assert code == EXIT_PRECONDITION
    assert client.prompts == []
    assert LessonRepo(db).list() == []
    stored = TicketRepo(db).get(ticket.id)
    assert stored is not None
    assert stored.lesson_extraction_attempted_at is None
    assert_schema_drift_message(
        capsys.readouterr(), store_revision=parent, code_head=head
    )


def test_failed_llm_call_logs_warning_and_persists_no_partial(
    db: Database, caplog: pytest.LogCaptureFixture
) -> None:
    ticket = seed_ticket(db, make_ticket("ATLAS-270"))
    caplog.set_level(logging.WARNING, logger="atlas.learning.extractor")

    code = main(
        ["lessons", "extract", "ATLAS-270"],
        database=db,
        client=FakeLessonClient(error=RuntimeError("model down")),
    )

    assert code == EXIT_RECORDED_FAILURE
    assert LessonRepo(db).list() == []
    assert "ATLAS-270" in caplog.text
    assert "RuntimeError" in caplog.text
    stored = TicketRepo(db).get(ticket.id)
    assert stored is not None
    assert stored.lesson_extraction_attempted_at is not None


def test_extracted_draft_is_not_context_retrievable(db: Database) -> None:
    ticket = seed_ticket(db, make_ticket("ATLAS-270", tags=["feature"]))
    lesson = extract_lesson_for_ticket(
        ticket,
        db=db,
        client=FakeLessonClient(),
        now=NOW,
        trigger=ExtractionTrigger.OPERATOR_REQUEST,
        force=True,
    )

    assert lesson is not None
    assert select_lessons([lesson], ticket) == []
