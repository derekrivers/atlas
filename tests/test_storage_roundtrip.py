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
from test_plan_run_model import plan_run_kwargs

import atlas.storage as storage
from atlas.core.models import (
    AgentRun,
    ArchitectureDecisionRecord,
    ContextPack,
    Epic,
    Evidence,
    Lesson,
    PlanRun,
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
    PlanRunRepo,
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
    (EvidenceRepo, Evidence, evidence_kwargs),
    (AgentRunRepo, AgentRun, agent_run_kwargs),
    (ContextPackRepo, ContextPack, context_pack_kwargs),
    (PlanRunRepo, PlanRun, plan_run_kwargs),
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
