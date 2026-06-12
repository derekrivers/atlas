"""ATLAS-23: Proposal models transcribe data-model §3.11 exactly,
including the §1 inheritance rule (shared-name fields carry the
canonical models' types and constraints)."""

import pytest
from annotated_types import Ge, Le, MaxLen, MinLen
from proposal_fixtures import (
    dependency_payload,
    epic_payload,
    proposal_payload,
    ticket_payload,
)
from pydantic import BaseModel, ValidationError

from atlas.core.enums import RiskLevel
from atlas.core.models import DependencyType, TicketType
from atlas.planning import (
    Proposal,
    ProposalDependency,
    ProposalEpic,
    ProposalTicket,
)

# Transcribed from §3.11: required-field sets, in documented order.
DOCUMENTED_FIELDS: dict[type[BaseModel], list[str]] = {
    Proposal: ["epics", "tickets", "dependencies", "planner_notes"],
    ProposalEpic: [
        "key",
        "title",
        "description",
        "objective",
        "priority",
        "risk_level",
        "source_anchor",
    ],
    ProposalTicket: [
        "key",
        "epic_ref",
        "title",
        "objective",
        "context",
        "ticket_type",
        "risk_level",
        "priority",
        "source_anchor",
        "relevant_docs",
        "acceptance_criteria",
        "non_goals",
        "test_requirements",
        "implementation_notes",
        "documentation_requirements",
        "definition_of_done",
    ],
    ProposalDependency: ["source", "target", "dependency_type", "reason"],
}


@pytest.mark.parametrize("model_cls", DOCUMENTED_FIELDS, ids=lambda cls: cls.__name__)
def test_field_sets_match_documented(model_cls: type[BaseModel]) -> None:
    assert sorted(model_cls.model_fields) == sorted(DOCUMENTED_FIELDS[model_cls])


@pytest.mark.parametrize("model_cls", DOCUMENTED_FIELDS, ids=lambda cls: cls.__name__)
def test_every_field_is_required(model_cls: type[BaseModel]) -> None:
    # §3.11: proposal items carry no defaults; every field is required.
    for name, field in model_cls.model_fields.items():
        assert field.is_required(), name


@pytest.mark.parametrize("model_cls", DOCUMENTED_FIELDS, ids=lambda cls: cls.__name__)
def test_system_owned_fields_are_forbidden(model_cls: type[BaseModel]) -> None:
    # §3.11: no id, status, timestamps, or created_by fields; enforced
    # mechanically via extra="forbid".
    assert model_cls.model_config.get("extra") == "forbid"
    for banned in ("id", "status", "created_at", "created_by_type"):
        assert banned not in model_cls.model_fields


def test_nullable_key_rules() -> None:
    assert ProposalEpic(**epic_payload()).key is None
    assert ProposalTicket(**ticket_payload()).key is None
    echoed = ProposalTicket(**ticket_payload(key="ATLAS-1"))
    assert echoed.key == "ATLAS-1"


def test_acceptance_criteria_entry_bounds() -> None:
    metadata = ProposalTicket.model_fields["acceptance_criteria"].metadata
    assert MinLen(1) in metadata
    assert MaxLen(7) in metadata


@pytest.mark.parametrize(
    "field", ["non_goals", "test_requirements", "definition_of_done"]
)
def test_minimum_one_entry_fields(field: str) -> None:
    assert MinLen(1) in ProposalTicket.model_fields[field].metadata
    with pytest.raises(ValidationError, match=field):
        ProposalTicket(**ticket_payload(**{field: []}))


def test_priority_inherits_canonical_bounds() -> None:
    for model_cls in (ProposalEpic, ProposalTicket):
        metadata = model_cls.model_fields["priority"].metadata
        assert Ge(-2147483648) in metadata
        assert Le(2147483647) in metadata


def test_enums_are_canonical_identity() -> None:
    # §1 inheritance: shared-name fields carry the canonical types.
    assert ProposalTicket.model_fields["risk_level"].annotation is RiskLevel
    assert ProposalEpic.model_fields["risk_level"].annotation is RiskLevel
    assert ProposalTicket.model_fields["ticket_type"].annotation is TicketType
    annotation = ProposalDependency.model_fields["dependency_type"].annotation
    assert annotation is DependencyType


def test_dependency_type_depends_on_only_in_m1() -> None:
    with pytest.raises(ValidationError, match="depends_on only"):
        ProposalDependency(**dependency_payload(dependency_type="relates_to"))


def test_context_must_be_non_empty() -> None:
    with pytest.raises(ValidationError, match="context"):
        ProposalTicket(**ticket_payload(context=""))


def test_envelope_is_exactly_four_keys() -> None:
    proposal = Proposal(**proposal_payload())
    assert list(type(proposal).model_fields) == [
        "epics",
        "tickets",
        "dependencies",
        "planner_notes",
    ]
    with pytest.raises(ValidationError):
        Proposal(**proposal_payload() | {"extra_section": []})
