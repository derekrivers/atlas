"""ATLAS-15: AgentRun model and enums match data-model §3.8.

Expected tables are transcribed from the document, not derived from the
model. The exact field set is the falsifiable proof of the documented
contract: no created_by_* attribution fields, and an FK-less
input_context_pack_id (Phase 8 reconstructs runs from observation).
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from atlas.core.models import AgentProvider, AgentRun, AgentRunStatus

REQUIRED = object()  # sentinel: field has no default

# data-model §3.8, in documented order: field -> (annotation, default).
DOCUMENTED_FIELDS: dict[str, tuple[Any, Any]] = {
    "id": (UUID, REQUIRED),
    "product_id": (UUID, REQUIRED),
    "ticket_id": (UUID | None, None),
    "provider": (AgentProvider, REQUIRED),
    "model": (str | None, None),
    "status": (AgentRunStatus, REQUIRED),
    "objective": (str, REQUIRED),
    "input_context_pack_id": (UUID | None, None),
    "output_summary": (str | None, None),
    "error_summary": (str | None, None),
    "cost_estimate_usd": (float | None, None),
    "prompt_tokens": (int | None, None),
    "completion_tokens": (int | None, None),
    "started_at": (datetime | None, None),
    "completed_at": (datetime | None, None),
    "created_at": (datetime, REQUIRED),
}

# data-model §3.8 (six members each)
DOCUMENTED_PROVIDERS = {
    "OPENAI": "openai",
    "SYMPHONY": "symphony",
    "CODEX": "codex",
    "CLAUDE": "claude",
    "LOCAL": "local",
    "HUMAN": "human",
}
DOCUMENTED_STATUSES = {
    "QUEUED": "queued",
    "RUNNING": "running",
    "SUCCEEDED": "succeeded",
    "FAILED": "failed",
    "CANCELLED": "cancelled",
    "NEEDS_HUMAN": "needs_human",
}


def agent_run_kwargs() -> dict[str, Any]:
    return {
        "id": uuid4(),
        "product_id": uuid4(),
        "provider": "symphony",
        "status": "succeeded",
        "objective": "Implement ATLAS-15.",
        "created_at": datetime(2026, 6, 12, tzinfo=UTC),
    }


def test_field_set_matches_documented() -> None:
    # Exact set and order. Also the falsifiable proof that the contract
    # has no created_by_type / created_by_id and no FK-bearing pack ref.
    assert list(AgentRun.model_fields) == list(DOCUMENTED_FIELDS)


def test_no_attribution_fields() -> None:
    # Redundant with the exact-set test, but states the §3.8 contract
    # directly: AgentRun carries no actor attribution.
    assert "created_by_type" not in AgentRun.model_fields
    assert "created_by_id" not in AgentRun.model_fields


def test_annotations_requiredness_defaults() -> None:
    for name, (annotation, default) in DOCUMENTED_FIELDS.items():
        field = AgentRun.model_fields[name]
        assert field.annotation == annotation, name
        if default is REQUIRED:
            assert field.is_required(), name
        else:
            assert not field.is_required(), name
            assert field.default == default, name


def test_only_created_at_timestamp_is_required() -> None:
    # started_at and completed_at are observed later (a queued run has
    # neither); created_at is the insertion record.
    assert AgentRun.model_fields["created_at"].is_required()
    assert not AgentRun.model_fields["started_at"].is_required()
    assert not AgentRun.model_fields["completed_at"].is_required()


@pytest.mark.parametrize(
    ("enum_cls", "documented"),
    [
        (AgentProvider, DOCUMENTED_PROVIDERS),
        (AgentRunStatus, DOCUMENTED_STATUSES),
    ],
    ids=["AgentProvider", "AgentRunStatus"],
)
def test_enum_members_and_values_match_documented(
    enum_cls: type[AgentProvider | AgentRunStatus],
    documented: dict[str, str],
) -> None:
    actual = {member.name: member.value for member in enum_cls}
    assert actual == documented


@pytest.mark.parametrize(
    "enum_cls",
    [AgentProvider, AgentRunStatus],
    ids=["AgentProvider", "AgentRunStatus"],
)
def test_enums_are_string_valued(
    enum_cls: type[AgentProvider | AgentRunStatus],
) -> None:
    assert issubclass(enum_cls, str)
    for member in enum_cls:
        assert member == member.value


def test_missing_required_field_rejected() -> None:
    incomplete = agent_run_kwargs()
    del incomplete["objective"]
    with pytest.raises(ValidationError, match="objective"):
        AgentRun(**incomplete)


def test_wrong_type_rejected() -> None:
    with pytest.raises(ValidationError):
        AgentRun(**agent_run_kwargs() | {"prompt_tokens": "many"})
