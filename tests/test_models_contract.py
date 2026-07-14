"""ATLAS-12: model field contracts match data-model-and-schemas.md §3.1-3.5.

Each expected table below is transcribed from the document — field names,
annotations, requiredness, and defaults — not from the model's own
definition, so any divergence (missing field, extra field, changed
default) fails.
"""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

import pytest
from pydantic import BaseModel

from atlas.core.enums import ActorType, EntityStatus, RiskLevel
from atlas.core.models import (
    ADRStatus,
    ArchitectureDecisionRecord,
    DependencyType,
    Epic,
    EpicStatus,
    Product,
    Ticket,
    TicketDependency,
    TicketStatus,
    TicketType,
)

REQUIRED = object()  # sentinel: field has no default
LIST_FACTORY = object()  # sentinel: default_factory=list

# (annotation, default) per field, in documented order.
FieldSpec = tuple[Any, Any]

DOCUMENTED_FIELDS: dict[type[BaseModel], dict[str, FieldSpec]] = {
    # data-model §3.1
    Product: {
        "id": (UUID, REQUIRED),
        "key": (str, REQUIRED),
        "name": (str, REQUIRED),
        "description": (str, REQUIRED),
        "vision": (str, REQUIRED),
        "status": (EntityStatus, REQUIRED),
        "goals": (list[str], LIST_FACTORY),
        "non_goals": (list[str], LIST_FACTORY),
        "constraints": (list[str], LIST_FACTORY),
        "created_by_type": (ActorType, REQUIRED),
        "created_by_id": (str, REQUIRED),
        "created_at": (datetime, REQUIRED),
        "updated_at": (datetime, REQUIRED),
        "archived_at": (datetime | None, None),
    },
    # data-model §3.2
    ArchitectureDecisionRecord: {
        "id": (UUID, REQUIRED),
        "product_id": (UUID, REQUIRED),
        "number": (int, REQUIRED),
        "title": (str, REQUIRED),
        "status": (ADRStatus, REQUIRED),
        "context": (str, REQUIRED),
        "decision": (str, REQUIRED),
        "rationale": (str, REQUIRED),
        "consequences": (list[str], LIST_FACTORY),
        "alternatives_considered": (list[str], LIST_FACTORY),
        "supersedes_adr_id": (UUID | None, None),
        "created_by_type": (ActorType, REQUIRED),
        "created_by_id": (str, REQUIRED),
        "created_at": (datetime, REQUIRED),
        "updated_at": (datetime, REQUIRED),
    },
    # data-model §3.3
    Epic: {
        "id": (UUID, REQUIRED),
        "product_id": (UUID, REQUIRED),
        "key": (str, REQUIRED),
        "title": (str, REQUIRED),
        "description": (str, REQUIRED),
        "objective": (str, REQUIRED),
        "status": (EpicStatus, REQUIRED),
        "priority": (int, REQUIRED),
        "risk_level": (RiskLevel, REQUIRED),
        "source_anchor": (str, REQUIRED),
        "created_by_type": (ActorType, REQUIRED),
        "created_by_id": (str, REQUIRED),
        "created_at": (datetime, REQUIRED),
        "updated_at": (datetime, REQUIRED),
        "completed_at": (datetime | None, None),
    },
    # data-model §3.4
    Ticket: {
        "id": (UUID, REQUIRED),
        "product_id": (UUID, REQUIRED),
        "epic_id": (UUID | None, None),
        "key": (str, REQUIRED),
        "title": (str, REQUIRED),
        "objective": (str, REQUIRED),
        "context": (str, REQUIRED),
        "status": (TicketStatus, REQUIRED),
        "ticket_type": (TicketType, REQUIRED),
        "risk_level": (RiskLevel, REQUIRED),
        "priority": (int, REQUIRED),
        "relevant_docs": (list[str], LIST_FACTORY),
        "acceptance_criteria": (list[str], LIST_FACTORY),
        "non_goals": (list[str], LIST_FACTORY),
        "implementation_notes": (list[str], LIST_FACTORY),
        "test_requirements": (list[str], LIST_FACTORY),
        "documentation_requirements": (list[str], LIST_FACTORY),
        "definition_of_done": (list[str], LIST_FACTORY),
        # Exists from Phase 1, stays null until critical-path analysis
        # lands in Phase 3 (ATLAS-32).
        "estimated_effort": (int | None, None),
        "external_linear_id": (str | None, None),
        "external_github_issue_id": (str | None, None),
        # Free-form retrieval facets; planner-populated later (ATLAS-127/-128).
        "tags": (list[str], LIST_FACTORY),
        "component": (str | None, None),
        # PM-Engine sync cursor; written only by the sync loop (ATLAS-42).
        "linear_synced_at": (datetime | None, None),
        # PM-Engine out-of-ownership transition signal (ATLAS-118).
        "last_observed_linear_state_id": (str | None, None),
        # PM-Engine dwell clock / episode boundary (ATLAS-119).
        "status_entered_at": (datetime | None, None),
        # PM-Engine review-cycling round-trip counter (ATLAS-120).
        "review_cycle_count": (int, 0),
        # Learning-system extraction cursor (ATLAS-106).
        "lesson_extraction_attempted_at": (datetime | None, None),
        "source_anchor": (str, REQUIRED),
        "created_by_type": (ActorType, REQUIRED),
        "created_by_id": (str, REQUIRED),
        "created_at": (datetime, REQUIRED),
        "updated_at": (datetime, REQUIRED),
        "completed_at": (datetime | None, None),
    },
    # data-model §3.5
    TicketDependency: {
        "id": (UUID, REQUIRED),
        "source_ticket_id": (UUID, REQUIRED),
        "target_entity_type": (str, REQUIRED),
        "target_entity_id": (UUID, REQUIRED),
        "dependency_type": (DependencyType, REQUIRED),
        "reason": (str, REQUIRED),
        "created_by_type": (ActorType, REQUIRED),
        "created_by_id": (str, REQUIRED),
        "created_at": (datetime, REQUIRED),
    },
}


@pytest.mark.parametrize(
    "model_cls",
    DOCUMENTED_FIELDS,
    ids=lambda cls: cls.__name__,
)
def test_field_set_matches_documented(model_cls: type[BaseModel]) -> None:
    # Exact set: a missing field (e.g. an omitted estimated_effort) or an
    # undocumented extra both fail.
    assert list(model_cls.model_fields) == list(DOCUMENTED_FIELDS[model_cls])


@pytest.mark.parametrize(
    "model_cls",
    DOCUMENTED_FIELDS,
    ids=lambda cls: cls.__name__,
)
def test_annotations_requiredness_defaults(model_cls: type[BaseModel]) -> None:
    for name, (annotation, default) in DOCUMENTED_FIELDS[model_cls].items():
        field = model_cls.model_fields[name]
        assert field.annotation == annotation, name
        if default is REQUIRED:
            assert field.is_required(), name
        elif default is LIST_FACTORY:
            assert field.default_factory is list, name
        else:
            assert not field.is_required(), name
            assert field.default == default, name


def test_status_fields_use_shared_enums() -> None:
    # §3.1/§3.3/§3.4 reuse the shared types from §2; identity proves the
    # models consume ATLAS-11's definitions rather than copies.
    assert Product.model_fields["status"].annotation is EntityStatus
    assert Epic.model_fields["risk_level"].annotation is RiskLevel
    assert Ticket.model_fields["risk_level"].annotation is RiskLevel
    for model_cls in (
        Product,
        ArchitectureDecisionRecord,
        Epic,
        Ticket,
        TicketDependency,
    ):
        assert model_cls.model_fields["created_by_type"].annotation is ActorType


def test_no_model_stores_an_inverse_edge() -> None:
    # depends_on is the single stored direction; no model carries a
    # "blocks"-shaped field (data-model §3.5, ADR-0007).
    for model_cls in DOCUMENTED_FIELDS:
        for name in model_cls.model_fields:
            assert "block" not in name.lower(), f"{model_cls.__name__}.{name}"


def test_model_local_enum_homes() -> None:
    # The model-local enums land with their models (ATLAS-12 scope), not
    # in the shared enums module.
    for enum_cls, module in [
        (ADRStatus, "atlas.core.models.adr"),
        (EpicStatus, "atlas.core.models.epic"),
        (TicketStatus, "atlas.core.models.ticket"),
        (TicketType, "atlas.core.models.ticket"),
        (DependencyType, "atlas.core.models.dependency"),
    ]:
        assert enum_cls.__module__ == module


def test_enum_annotations_are_not_shared_enum_duplicates() -> None:
    # The enums referenced by model annotations are the canonical classes,
    # not re-declarations with the same name.
    assert ArchitectureDecisionRecord.model_fields["status"].annotation is ADRStatus
    assert Epic.model_fields["status"].annotation is EpicStatus
    assert Ticket.model_fields["status"].annotation is TicketStatus
    assert Ticket.model_fields["ticket_type"].annotation is TicketType
    dep_annotation = TicketDependency.model_fields["dependency_type"].annotation
    assert dep_annotation is DependencyType


def test_documented_order_includes_every_enum() -> None:
    # Guard for the test file itself: all five model-local enums appear in
    # some annotation above, so a renamed enum cannot silently vanish.
    annotated: set[type[Enum]] = set()
    for model_cls in DOCUMENTED_FIELDS:
        for field in model_cls.model_fields.values():
            if isinstance(field.annotation, type) and issubclass(
                field.annotation, Enum
            ):
                annotated.add(field.annotation)
    assert {ADRStatus, EpicStatus, TicketStatus, TicketType, DependencyType} <= (
        annotated
    )
