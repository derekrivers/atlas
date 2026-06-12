"""ATLAS-12: construction and validation behaviour of the five models.

Per-model unit tests only; the cross-cutting round-trip and
dangling-target suites are ATLAS-19.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from pydantic import BaseModel, ValidationError

from atlas.core.models import (
    ArchitectureDecisionRecord,
    Epic,
    Product,
    Ticket,
    TicketDependency,
)

NOW = datetime(2026, 6, 12, tzinfo=UTC)


def product_kwargs() -> dict[str, Any]:
    return {
        "id": uuid4(),
        "key": "ATLAS",
        "name": "Atlas",
        "description": "Organisational operating system.",
        "vision": "Repeatable software delivery.",
        "status": "active",
        "created_by_type": "human",
        "created_by_id": "operator",
        "created_at": NOW,
        "updated_at": NOW,
    }


def adr_kwargs() -> dict[str, Any]:
    return {
        "id": uuid4(),
        "product_id": uuid4(),
        "number": 8,
        "title": "CI-sourced evidence with trust tiers",
        "status": "accepted",
        "context": "Agent claims are not evidence.",
        "decision": "CI is the system-tier producer.",
        "rationale": "Completion signals come from the environment.",
        "created_by_type": "human",
        "created_by_id": "operator",
        "created_at": NOW,
        "updated_at": NOW,
    }


def epic_kwargs() -> dict[str, Any]:
    return {
        "id": uuid4(),
        "product_id": uuid4(),
        "key": "EPIC-1",
        "title": "Knowledge Core",
        "description": "Models are the single contract.",
        "objective": "Every entity round-trips through YAML and the DB.",
        "status": "planned",
        "priority": 1,
        "risk_level": "medium",
        "created_by_type": "human",
        "created_by_id": "operator",
        "created_at": NOW,
        "updated_at": NOW,
    }


def ticket_kwargs() -> dict[str, Any]:
    return {
        "id": uuid4(),
        "product_id": uuid4(),
        "key": "ATLAS-12",
        "title": "Core models",
        "objective": "Implement the five canonical models.",
        "context": "Phase 1 Knowledge Core.",
        "status": "in_progress",
        "ticket_type": "feature",
        "risk_level": "low",
        "priority": 10,
        "created_by_type": "agent",
        "created_by_id": "claude",
        "created_at": NOW,
        "updated_at": NOW,
    }


def dependency_kwargs() -> dict[str, Any]:
    return {
        "id": uuid4(),
        "source_ticket_id": uuid4(),
        "target_entity_type": "ticket",
        "target_entity_id": uuid4(),
        "dependency_type": "depends_on",
        "reason": "Models must exist before serialisation.",
        "created_at": NOW,
    }


# (model, valid kwargs factory, a required field, a wrong-type override)
CASES = [
    (Product, product_kwargs, "vision", {"goals": "not-a-list"}),
    (ArchitectureDecisionRecord, adr_kwargs, "rationale", {"number": "eight"}),
    (Epic, epic_kwargs, "objective", {"priority": "high"}),
    (Ticket, ticket_kwargs, "context", {"status": 123}),
    (TicketDependency, dependency_kwargs, "reason", {"target_entity_id": "ATLAS-3"}),
]
CASE_IDS = [model.__name__ for model, *_ in CASES]


@pytest.mark.parametrize(("model_cls", "kwargs", "_", "__"), CASES, ids=CASE_IDS)
def test_valid_construction(
    model_cls: type[BaseModel],
    kwargs: Any,
    _: str,
    __: dict[str, Any],
) -> None:
    instance = model_cls(**kwargs())
    assert isinstance(instance, model_cls)


@pytest.mark.parametrize(("model_cls", "kwargs", "missing", "_"), CASES, ids=CASE_IDS)
def test_missing_required_field_rejected(
    model_cls: type[BaseModel],
    kwargs: Any,
    missing: str,
    _: dict[str, Any],
) -> None:
    incomplete = kwargs()
    del incomplete[missing]
    with pytest.raises(ValidationError, match=missing):
        model_cls(**incomplete)


@pytest.mark.parametrize(("model_cls", "kwargs", "_", "bad"), CASES, ids=CASE_IDS)
def test_wrong_type_rejected(
    model_cls: type[BaseModel],
    kwargs: Any,
    _: str,
    bad: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        model_cls(**(kwargs() | bad))


def test_ticket_estimated_effort_defaults_to_none() -> None:
    # Exists from Phase 1, stays null until Phase 3 (ATLAS-32).
    assert Ticket(**ticket_kwargs()).estimated_effort is None


def test_list_defaults_are_independent_instances() -> None:
    # default_factory=list per the doc: two instances must not share a
    # mutable default.
    first, second = Product(**product_kwargs()), Product(**product_kwargs())
    first.goals.append("prove the core loop")
    assert second.goals == []
