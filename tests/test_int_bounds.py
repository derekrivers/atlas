"""Pre-Phase-2 R5(a): SQL INTEGER bounds on integer-backed fields.

Documented in data-model §3.3/§3.4/§3.8/§3.9 as Field(ge=-2147483648,
le=2147483647); pinned here against the documented values (the Lesson
confidence pattern), with boundary positives and out-of-bounds
negatives per field.
"""

from typing import Any

import pytest
from annotated_types import Ge, Le
from pydantic import BaseModel, ValidationError
from test_agent_run_model import agent_run_kwargs
from test_context_pack_model import context_pack_kwargs
from test_models_validation import epic_kwargs, ticket_kwargs

from atlas.core.models import AgentRun, ContextPack, Epic, Ticket

INT_MIN = -2147483648
INT_MAX = 2147483647

# (model, field, valid-kwargs factory)
BOUNDED_FIELDS = [
    (Epic, "priority", epic_kwargs),
    (Ticket, "priority", ticket_kwargs),
    (Ticket, "estimated_effort", ticket_kwargs),
    (AgentRun, "prompt_tokens", agent_run_kwargs),
    (AgentRun, "completion_tokens", agent_run_kwargs),
    (ContextPack, "token_estimate", context_pack_kwargs),
]
IDS = [f"{model.__name__}.{field}" for model, field, _ in BOUNDED_FIELDS]


@pytest.mark.parametrize(("model_cls", "field", "_"), BOUNDED_FIELDS, ids=IDS)
def test_field_declares_documented_bounds(
    model_cls: type[BaseModel], field: str, _: Any
) -> None:
    metadata = model_cls.model_fields[field].metadata
    assert Ge(INT_MIN) in metadata
    assert Le(INT_MAX) in metadata


@pytest.mark.parametrize(("model_cls", "field", "kwargs"), BOUNDED_FIELDS, ids=IDS)
@pytest.mark.parametrize("boundary", [INT_MIN, INT_MAX])
def test_boundary_values_accepted(
    model_cls: type[BaseModel], field: str, kwargs: Any, boundary: int
) -> None:
    instance = model_cls(**kwargs() | {field: boundary})
    assert getattr(instance, field) == boundary


@pytest.mark.parametrize(("model_cls", "field", "kwargs"), BOUNDED_FIELDS, ids=IDS)
@pytest.mark.parametrize("out_of_bounds", [INT_MIN - 1, INT_MAX + 1])
def test_out_of_bounds_rejected(
    model_cls: type[BaseModel], field: str, kwargs: Any, out_of_bounds: int
) -> None:
    with pytest.raises(ValidationError, match=field):
        model_cls(**kwargs() | {field: out_of_bounds})
