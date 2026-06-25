"""ATLAS-15: ContextPack model matches data-model §3.9.

Expected tables are transcribed from the document, not derived from the
model. The reference-list pin makes the ratified operator decision
falsifiable: relevant_adrs, related_tickets, and historical_lessons are
list[UUID], not human-readable keys.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from atlas.core.models import ContextPack

REQUIRED = object()  # sentinel: field has no default
LIST_FACTORY = object()  # sentinel: default_factory=list
DICT_FACTORY = object()  # sentinel: default_factory=dict

# data-model §3.9, in documented order: field -> (annotation, default).
DOCUMENTED_FIELDS: dict[str, tuple[Any, Any]] = {
    "id": (UUID, REQUIRED),
    "product_id": (UUID, REQUIRED),
    "ticket_id": (UUID | None, None),
    "title": (str, REQUIRED),
    "objective": (str, REQUIRED),
    "constraints": (list[str], LIST_FACTORY),
    "relevant_docs": (list[str], LIST_FACTORY),
    "relevant_adrs": (list[UUID], LIST_FACTORY),
    "related_tickets": (list[UUID], LIST_FACTORY),
    "historical_lessons": (list[UUID], LIST_FACTORY),
    "acceptance_criteria": (list[str], LIST_FACTORY),
    "risks": (list[str], LIST_FACTORY),
    "test_commands": (list[str], LIST_FACTORY),
    "definition_of_done": (list[str], LIST_FACTORY),
    "rendered_markdown": (str, REQUIRED),
    "compression_applied": (list[str], LIST_FACTORY),
    "input_doc_shas": (dict[str, str], DICT_FACTORY),
    "token_estimate": (int | None, None),
    "created_at": (datetime, REQUIRED),
}


def context_pack_kwargs() -> dict[str, Any]:
    return {
        "id": uuid4(),
        "product_id": uuid4(),
        "title": "ATLAS-15 execution brief",
        "objective": "Implement the run models.",
        "rendered_markdown": "# Brief\n",
        "created_at": datetime(2026, 6, 12, tzinfo=UTC),
    }


def test_field_set_matches_documented() -> None:
    # Exact set and order: nothing missing, nothing extra.
    assert list(ContextPack.model_fields) == list(DOCUMENTED_FIELDS)


def test_annotations_requiredness_defaults() -> None:
    for name, (annotation, default) in DOCUMENTED_FIELDS.items():
        field = ContextPack.model_fields[name]
        assert field.annotation == annotation, name
        if default is REQUIRED:
            assert field.is_required(), name
        elif default is LIST_FACTORY:
            assert field.default_factory is list, name
        elif default is DICT_FACTORY:
            assert field.default_factory is dict, name


def test_reference_lists_are_uuid_typed() -> None:
    # Ratified operator decision: UUIDs for DB traceability;
    # rendered_markdown carries the human-readable form. A drift back to
    # key strings fails here.
    for name in ("relevant_adrs", "related_tickets", "historical_lessons"):
        assert ContextPack.model_fields[name].annotation == list[UUID], name


def test_reference_lists_reject_key_strings() -> None:
    # The §7 example's historical key forms (e.g. "ADR-0003") are not
    # valid values for the UUID-typed lists.
    with pytest.raises(ValidationError):
        ContextPack(**context_pack_kwargs() | {"relevant_adrs": ["ADR-0003"]})


def test_missing_required_field_rejected() -> None:
    incomplete = context_pack_kwargs()
    del incomplete["rendered_markdown"]
    with pytest.raises(ValidationError, match="rendered_markdown"):
        ContextPack(**incomplete)


def test_wrong_type_rejected() -> None:
    with pytest.raises(ValidationError):
        ContextPack(**context_pack_kwargs() | {"input_doc_shas": ["not-a-dict"]})


def test_factory_defaults_are_independent_instances() -> None:
    first = ContextPack(**context_pack_kwargs())
    second = ContextPack(**context_pack_kwargs())
    first.constraints.append("no Neo4j")
    first.input_doc_shas["AGENTS.md"] = "abc123"
    assert second.constraints == []
    assert second.input_doc_shas == {}
