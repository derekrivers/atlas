"""ATLAS-19: dangling polymorphic target detection (knowledge-core
"Testing strategy"). Positive detections for missing table-backed
targets, a clean pass, and fail-closed handling of storeless and
unknown target types — each with typed, attributable results."""

from pathlib import Path
from uuid import uuid4

import pytest
from test_models_validation import adr_kwargs, dependency_kwargs, ticket_kwargs

from atlas.core.models import ArchitectureDecisionRecord, Ticket, TicketDependency
from atlas.storage import ADRRepo, Database, TicketDependencyRepo, TicketRepo
from atlas.storage.validation import DanglingTarget, find_dangling_targets


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{tmp_path}/atlas.db")
    database.create_all()
    return database


def dependency_targeting(target_type: str, target_id: object) -> TicketDependency:
    return TicketDependency(
        **dependency_kwargs()
        | {
            "id": uuid4(),
            "target_entity_type": target_type,
            "target_entity_id": target_id,
        }
    )


def test_missing_ticket_target_detected(db: Database) -> None:
    dependency = dependency_targeting("ticket", uuid4())
    findings = find_dangling_targets(db, [dependency])
    assert findings == [
        DanglingTarget(
            dependency_id=dependency.id,
            target_entity_type="ticket",
            target_entity_id=dependency.target_entity_id,
            reason="missing ticket",
        )
    ]


def test_missing_adr_target_detected(db: Database) -> None:
    dependency = dependency_targeting("adr", uuid4())
    findings = find_dangling_targets(db, [dependency])
    assert [finding.reason for finding in findings] == ["missing adr"]


def test_resolving_targets_pass_clean(db: Database) -> None:
    ticket = Ticket(**ticket_kwargs())
    adr = ArchitectureDecisionRecord(**adr_kwargs())
    TicketRepo(db).add(ticket)
    ADRRepo(db).add(adr)
    dependencies = [
        dependency_targeting("ticket", ticket.id),
        dependency_targeting("adr", adr.id),
    ]
    assert find_dangling_targets(db, dependencies) == []


def test_storeless_component_fails_closed(db: Database) -> None:
    dependency = dependency_targeting("component", uuid4())
    findings = find_dangling_targets(db, [dependency])
    assert len(findings) == 1
    assert "storeless" in findings[0].reason
    assert "component" in findings[0].reason


def test_unknown_target_type_fails_closed(db: Database) -> None:
    dependency = dependency_targeting("microservice", uuid4())
    findings = find_dangling_targets(db, [dependency])
    assert len(findings) == 1
    assert "unknown target type" in findings[0].reason
    assert findings[0].dependency_id == dependency.id


def test_default_loads_dependencies_from_storage(db: Database) -> None:
    repo = TicketDependencyRepo(db)
    stored = dependency_targeting("ticket", uuid4())
    repo.add(stored)
    findings = find_dangling_targets(db)
    assert [finding.dependency_id for finding in findings] == [stored.id]


def test_mixed_set_attributes_each_finding(db: Database) -> None:
    ticket = Ticket(**ticket_kwargs())
    TicketRepo(db).add(ticket)
    clean = dependency_targeting("ticket", ticket.id)
    missing = dependency_targeting("ticket", uuid4())
    unknown = dependency_targeting("widget", uuid4())
    findings = find_dangling_targets(db, [clean, missing, unknown])
    assert {finding.dependency_id for finding in findings} == {missing.id, unknown.id}
