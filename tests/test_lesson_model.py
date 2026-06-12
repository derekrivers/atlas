"""ATLAS-13: Lesson model and LessonCategory match data-model §3.6.

Expected tables are transcribed from the document, not derived from the
model, so a divergence fails. The DRAFT-default and non-policing tests
pin the ADR-0009 decision split: the model carries the default, the
operator gate (ATLAS-97) and retrieval filter (ATLAS-53) do the
enforcing.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from annotated_types import Ge, Le
from pydantic import ValidationError

import atlas.core.enums
from atlas.core.enums import ActorType, EntityStatus
from atlas.core.models import Lesson, LessonCategory

REQUIRED = object()  # sentinel: field has no default
LIST_FACTORY = object()  # sentinel: default_factory=list

# data-model §3.6, in documented order: field -> (annotation, default).
DOCUMENTED_FIELDS: dict[str, tuple[Any, Any]] = {
    "id": (UUID, REQUIRED),
    "product_id": (UUID, REQUIRED),
    "status": (EntityStatus, EntityStatus.DRAFT),
    "category": (LessonCategory, REQUIRED),
    "title": (str, REQUIRED),
    "problem": (str, REQUIRED),
    "solution": (str, REQUIRED),
    "outcome": (str, REQUIRED),
    # Required, bounded 0..1 (ratified; SQL CHECK mirrors it).
    "confidence": (float, REQUIRED),
    "related_ticket_ids": (list[UUID], LIST_FACTORY),
    "related_adr_ids": (list[UUID], LIST_FACTORY),
    "tags": (list[str], LIST_FACTORY),
    "created_by_type": (ActorType, REQUIRED),
    "created_by_id": (str, REQUIRED),
    "created_at": (datetime, REQUIRED),
    "updated_at": (datetime, REQUIRED),
}

# data-model §3.6
DOCUMENTED_CATEGORIES = {
    "SUCCESS_PATTERN": "success_pattern",
    "FAILURE_PATTERN": "failure_pattern",
    "ARCHITECTURE": "architecture",
    "TESTING": "testing",
    "DELIVERY": "delivery",
    "PRODUCT": "product",
    "RESEARCH": "research",
    "TECH_DEBT": "tech_debt",
}


def lesson_kwargs() -> dict[str, Any]:
    return {
        "id": uuid4(),
        "product_id": uuid4(),
        "category": "failure_pattern",
        "title": "Oversized tickets reduce agent success rate",
        "problem": "Large tickets caused broad, hard-to-review changes.",
        "solution": "Split into narrow, dependency-aware units.",
        "outcome": "Agent PRs became easier to review.",
        "confidence": 0.9,
        "created_by_type": "agent",
        "created_by_id": "claude",
        "created_at": datetime(2026, 6, 12, tzinfo=UTC),
        "updated_at": datetime(2026, 6, 12, tzinfo=UTC),
    }


def test_field_set_matches_documented() -> None:
    # Exact set and order: nothing missing, nothing extra.
    assert list(Lesson.model_fields) == list(DOCUMENTED_FIELDS)


def test_annotations_requiredness_defaults() -> None:
    for name, (annotation, default) in DOCUMENTED_FIELDS.items():
        field = Lesson.model_fields[name]
        assert field.annotation == annotation, name
        if default is REQUIRED:
            assert field.is_required(), name
        elif default is LIST_FACTORY:
            assert field.default_factory is list, name
        else:
            assert not field.is_required(), name
            assert field.default == default, name


def test_category_members_and_values_match_documented() -> None:
    actual = {member.name: member.value for member in LessonCategory}
    assert actual == DOCUMENTED_CATEGORIES


def test_category_is_string_valued() -> None:
    assert issubclass(LessonCategory, str)
    for member in LessonCategory:
        assert member == member.value


def test_status_defaults_to_draft() -> None:
    # ADR-0009 lesson promotion gate: a lesson constructed without status
    # (the agent-authored path) starts DRAFT.
    lesson = Lesson(**lesson_kwargs())
    assert lesson.status is EntityStatus.DRAFT


def test_explicit_active_construction_succeeds() -> None:
    # Pre-made decision: the model is the contract, not the police. No
    # validator blocks ACTIVE at construction, even agent-authored —
    # enforcement is ATLAS-97 (promotion gate) and ATLAS-53 (retrieval).
    # A future validator silently appearing here fails this test.
    lesson = Lesson(**lesson_kwargs() | {"status": "active"})
    assert lesson.status is EntityStatus.ACTIVE
    assert lesson.created_by_type is ActorType.AGENT


def test_missing_required_field_rejected() -> None:
    incomplete = lesson_kwargs()
    del incomplete["problem"]
    with pytest.raises(ValidationError, match="problem"):
        Lesson(**incomplete)


def test_wrong_type_rejected() -> None:
    with pytest.raises(ValidationError):
        Lesson(**lesson_kwargs() | {"confidence": "high"})


def test_confidence_declares_documented_bounds() -> None:
    # §3.6: Field(ge=0, le=1). Asserted against the documented bounds so
    # a silently loosened constraint fails.
    metadata = Lesson.model_fields["confidence"].metadata
    assert Ge(0) in metadata
    assert Le(1) in metadata


@pytest.mark.parametrize("out_of_bounds", [-0.1, 1.1])
def test_confidence_out_of_bounds_rejected(out_of_bounds: float) -> None:
    with pytest.raises(ValidationError, match="confidence"):
        Lesson(**lesson_kwargs() | {"confidence": out_of_bounds})


@pytest.mark.parametrize("in_bounds", [0, 1, 0.9])
def test_confidence_boundaries_accepted(in_bounds: float) -> None:
    assert Lesson(**lesson_kwargs() | {"confidence": in_bounds}).confidence == (
        in_bounds
    )


def test_status_uses_canonical_shared_enum() -> None:
    # Identity, not equality: the annotation is ATLAS-11's EntityStatus.
    assert Lesson.model_fields["status"].annotation is atlas.core.enums.EntityStatus
    field = Lesson.model_fields["created_by_type"]
    assert field.annotation is atlas.core.enums.ActorType
