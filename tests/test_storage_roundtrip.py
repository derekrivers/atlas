"""ATLAS-18: representative model -> DB -> model identity per entity,
including JSONB-shimmed dict fields on SQLite. Exhaustive property
suites are ATLAS-19. Also pins the package boundary: the public storage
surface trades in Pydantic models only."""

from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from pydantic import BaseModel
from test_agent_run_model import agent_run_kwargs
from test_context_pack_model import context_pack_kwargs
from test_evidence_model import evidence_kwargs
from test_lesson_model import lesson_kwargs
from test_models_validation import (
    adr_kwargs,
    dependency_kwargs,
    epic_kwargs,
    product_kwargs,
    ticket_kwargs,
)
from test_operator_action_receipt_model import operator_action_receipt_kwargs
from test_plan_run_model import plan_run_kwargs
from test_pm_sync_receipt_model import pm_sync_receipt_kwargs

import atlas.storage as storage
from atlas.core.models import (
    AgentRun,
    ArchitectureDecisionRecord,
    ContextPack,
    Epic,
    Evidence,
    Lesson,
    OperatorActionReceipt,
    PlanRun,
    PmSyncReceipt,
    Product,
    Ticket,
    TicketDependency,
)
from atlas.storage import (
    ADRRepo,
    AgentRunRepo,
    ContextPackRepo,
    Database,
    EpicRepo,
    EvidenceRepo,
    LessonRepo,
    OperatorActionReceiptRepo,
    PlanRunRepo,
    PmSyncReceiptRepo,
    ProductRepo,
    TicketDependencyRepo,
    TicketRepo,
)


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{tmp_path}/atlas.db")
    database.create_all()
    return database


CASES = [
    (ProductRepo, Product, product_kwargs),
    (ADRRepo, ArchitectureDecisionRecord, adr_kwargs),
    (EpicRepo, Epic, epic_kwargs),
    (TicketRepo, Ticket, ticket_kwargs),
    (TicketDependencyRepo, TicketDependency, dependency_kwargs),
    (LessonRepo, Lesson, lesson_kwargs),
    (OperatorActionReceiptRepo, OperatorActionReceipt, operator_action_receipt_kwargs),
    (EvidenceRepo, Evidence, evidence_kwargs),
    (AgentRunRepo, AgentRun, agent_run_kwargs),
    (ContextPackRepo, ContextPack, context_pack_kwargs),
    (PlanRunRepo, PlanRun, plan_run_kwargs),
    (PmSyncReceiptRepo, PmSyncReceipt, pm_sync_receipt_kwargs),
]
CASE_IDS = [model.__name__ for _, model, _ in CASES]


@pytest.mark.parametrize(("repo_cls", "model_cls", "kwargs"), CASES, ids=CASE_IDS)
def test_round_trip_is_identity(
    db: Database, repo_cls: type, model_cls: type[BaseModel], kwargs: Any
) -> None:
    repo = repo_cls(db)
    original = model_cls(**kwargs())
    repo.add(original)
    assert repo.get(original.id) == original  # type: ignore[attr-defined]


def test_jsonb_shimmed_dict_fields_round_trip(db: Database) -> None:
    payload = {"suite": "pytest", "passed": 97, "nested": {"flaky": []}}
    record = Evidence(**evidence_kwargs() | {"raw_payload": payload})
    repo = EvidenceRepo(db)
    repo.add(record)
    stored = repo.get(record.id)
    assert stored is not None
    assert stored.raw_payload == payload

    shas = {"docs/a.md": "1111111", "docs/b.md": "2222222"}
    run = PlanRun(**plan_run_kwargs() | {"input_doc_shas": shas})
    plan_repo = PlanRunRepo(db)
    plan_repo.add(run)
    stored_run = plan_repo.get(run.id)
    assert stored_run is not None
    assert stored_run.input_doc_shas == shas


def test_generation_stages_server_default_backfills_existing_rows(db: Database) -> None:
    # ATLAS-105 gap 3: a plan_runs row written before this field existed (here,
    # inserted without generation_stages) is backfilled by the NOT NULL '[]'
    # server default and reads back as the empty list — no data surgery.
    import sqlalchemy as sa

    from atlas.storage.tables import PlanRunRow

    kwargs = plan_run_kwargs()
    with db.session() as session, session.begin():
        session.execute(
            sa.insert(PlanRunRow).values(
                id=kwargs["id"],
                product_id=kwargs["product_id"],
                status=kwargs["status"],
                model_provider=kwargs["model_provider"],
                model_name=kwargs["model_name"],
                prompt_version=kwargs["prompt_version"],
                prompt_hash=kwargs["prompt_hash"],
                similarity_threshold=kwargs["similarity_threshold"],
                raw_output_hash=kwargs["raw_output_hash"],
                created_at=kwargs["created_at"],
            )
        )
    stored = PlanRunRepo(db).get(kwargs["id"])
    assert stored is not None
    assert stored.generation_stages == []


def test_ticket_tags_and_component_round_trip(db: Database) -> None:
    # ATLAS-127: free-form tags/component persist and read back identical —
    # tags order preserved (not set-coerced), component a real label (not "").
    repo = TicketRepo(db)
    ticket = Ticket(
        **ticket_kwargs() | {"tags": ["pm", "sync"], "component": "pm-engine"}
    )
    repo.add(ticket)
    stored = repo.get(ticket.id)
    assert stored is not None
    assert stored.tags == ["pm", "sync"]
    assert stored.component == "pm-engine"
    assert stored == ticket


def test_ticket_tags_and_component_defaults(db: Database) -> None:
    # ATLAS-127: a ticket built without the fields reads back the defaults,
    # not a null tags or a coerced-empty component.
    repo = TicketRepo(db)
    ticket = Ticket(**ticket_kwargs())
    repo.add(ticket)
    stored = repo.get(ticket.id)
    assert stored is not None
    assert stored.tags == []
    assert stored.component is None


def test_ticket_tags_component_server_default_backfills_existing_rows(
    db: Database,
) -> None:
    # ATLAS-127 migration back-fill: a tickets row written in the pre-0015 shape
    # (here, inserted without tags/component) is backfilled by the NOT NULL '[]'
    # server default and the nullable component, reading back as the empty list
    # and None — existing rows are safe, no data surgery.
    import sqlalchemy as sa

    from atlas.storage.tables import TicketRow

    kwargs = ticket_kwargs()
    with db.session() as session, session.begin():
        session.execute(
            sa.insert(TicketRow).values(
                id=kwargs["id"],
                product_id=kwargs["product_id"],
                key=kwargs["key"],
                title=kwargs["title"],
                objective=kwargs["objective"],
                context=kwargs["context"],
                status=kwargs["status"],
                ticket_type=kwargs["ticket_type"],
                risk_level=kwargs["risk_level"],
                priority=kwargs["priority"],
                source_anchor=kwargs["source_anchor"],
                created_by_type=kwargs["created_by_type"],
                created_by_id=kwargs["created_by_id"],
                created_at=kwargs["created_at"],
                updated_at=kwargs["updated_at"],
            )
        )
    stored = TicketRepo(db).get(kwargs["id"])
    assert stored is not None
    assert stored.tags == []
    assert stored.component is None


def test_get_by_key_for_keyed_aggregates(db: Database) -> None:
    repo = TicketRepo(db)
    ticket = Ticket(**ticket_kwargs())
    repo.add(ticket)
    assert repo.get_by_key(ticket.key) == ticket
    assert repo.get_by_key("ATLAS-9999") is None


def test_get_missing_returns_none(db: Database) -> None:
    assert ProductRepo(db).get(UUID(int=0)) is None


def test_public_surface_is_pydantic_only() -> None:
    # Gap-1 pin: nothing outside the storage package touches an ORM row
    # or a session. Every public repo method annotates its returns with
    # Pydantic models (or builtins) — no Session, no Row.
    #
    # Sole ratified exception: KeyCounterRepo.reserve takes a
    # caller-supplied Session by design (ATLAS-25 gap 3) so the counter
    # advance composes inside ATLAS-27's apply transaction. The caller is
    # itself storage-resident; the session does not escape the package.
    banned = ("Session", "Row")
    exempt = {("KeyCounterRepo", "reserve", "session")}
    for name in storage.__all__:
        exported = getattr(storage, name)
        if not (isinstance(exported, type) and name.endswith("Repo")):
            continue
        for method_name in dir(exported):
            if method_name.startswith("_"):
                continue
            method = getattr(exported, method_name)
            annotations = getattr(method, "__annotations__", {})
            for arg_name, annotation in annotations.items():
                if (name, method_name, arg_name) in exempt:
                    continue
                rendered = str(annotation)
                assert not any(token in rendered for token in banned), (
                    f"{name}.{method_name} leaks {rendered}"
                )
