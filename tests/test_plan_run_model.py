"""ATLAS-15: PlanRun model and status enum match data-model §3.10.

§3.10 is authoritative for this model (operator ruling; the planning
engine specification §6 mirrors it, aligned in this change). Expected
tables are transcribed from the document, not derived from the model.
The non-policing tests pin that finalise-once enforcement belongs to
the repository layer's finalize method (ATLAS-18), not the model.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from atlas.core.models import PlanRun, PlanRunStatus

REQUIRED = object()  # sentinel: field has no default
DICT_FACTORY = object()  # sentinel: default_factory=dict
LIST_FACTORY = object()  # sentinel: default_factory=list

# data-model §3.10, in documented order: field -> (annotation, default).
DOCUMENTED_FIELDS: dict[str, tuple[Any, Any]] = {
    "id": (UUID, REQUIRED),
    "product_id": (UUID, REQUIRED),
    "status": (PlanRunStatus, REQUIRED),
    "input_doc_shas": (dict[str, str], REQUIRED),
    "model_provider": (str, REQUIRED),
    "model_name": (str, REQUIRED),
    "prompt_version": (str, REQUIRED),
    "prompt_hash": (str, REQUIRED),
    "model_parameters": (dict[str, Any], DICT_FACTORY),
    "similarity_threshold": (float, REQUIRED),
    "raw_output_hash": (str, REQUIRED),
    "proposal": (dict[str, Any], DICT_FACTORY),
    "generation_stages": (list[dict[str, str]], LIST_FACTORY),
    "diff_summary": (dict[str, Any], DICT_FACTORY),
    "failure_reason": (str | None, None),
    "approved_by": (str | None, None),
    "created_at": (datetime, REQUIRED),
    "applied_at": (datetime | None, None),
}

# data-model §3.10 (four members)
DOCUMENTED_STATUSES = {
    "PROPOSED": "proposed",
    "APPLIED": "applied",
    "REJECTED": "rejected",
    "FAILED": "failed",
}


def plan_run_kwargs() -> dict[str, Any]:
    return {
        "id": uuid4(),
        "product_id": uuid4(),
        "status": "proposed",
        "input_doc_shas": {"docs/atlas/atlas-master-plan.md": "9ae3f1b"},
        "model_provider": "anthropic",
        "model_name": "claude-fable-5",
        "prompt_version": "1.0.0",
        "prompt_hash": "9f2b4c6d8e0a1b3c5d7e9f0a2b4c6d8e"
        "9f2b4c6d8e0a1b3c5d7e9f0a2b4c6d8e",
        "similarity_threshold": 0.85,
        "raw_output_hash": "sha256:deadbeef",
        "created_at": datetime(2026, 6, 12, tzinfo=UTC),
    }


def test_field_set_matches_documented() -> None:
    # Exact set and order. Also the timestamp-shape proof: created_at
    # required, applied_at optional, nothing else.
    assert list(PlanRun.model_fields) == list(DOCUMENTED_FIELDS)


def test_annotations_requiredness_defaults() -> None:
    for name, (annotation, default) in DOCUMENTED_FIELDS.items():
        field = PlanRun.model_fields[name]
        assert field.annotation == annotation, name
        if default is REQUIRED:
            assert field.is_required(), name
        elif default is DICT_FACTORY:
            assert field.default_factory is dict, name
        elif default is LIST_FACTORY:
            assert field.default_factory is list, name
        else:
            assert not field.is_required(), name
            assert field.default == default, name


def test_status_members_and_values_match_documented() -> None:
    actual = {member.name: member.value for member in PlanRunStatus}
    assert actual == DOCUMENTED_STATUSES


def test_status_is_string_valued() -> None:
    assert issubclass(PlanRunStatus, str)
    for member in PlanRunStatus:
        assert member == member.value


def test_direct_construction_at_applied_succeeds() -> None:
    # Pre-made decision: finalise-once is repository-layer enforcement
    # (PlanRunRepo.finalize, ATLAS-18) — the model is a stateless
    # snapshot with no transition validator. A validator silently
    # appearing here fails this test.
    run = PlanRun(
        **plan_run_kwargs()
        | {
            "status": "applied",
            "approved_by": "operator",
            "applied_at": datetime(2026, 6, 12, 10, tzinfo=UTC),
        }
    )
    assert run.status is PlanRunStatus.APPLIED


def test_direct_construction_at_failed_succeeds() -> None:
    # Same non-policing pin for the failure leg.
    run = PlanRun(
        **plan_run_kwargs()
        | {"status": "failed", "failure_reason": "gate 2: dependency cycle"}
    )
    assert run.status is PlanRunStatus.FAILED
    assert run.failure_reason == "gate 2: dependency cycle"


def test_diff_summary_defaults_to_empty_independent_dict() -> None:
    first, second = PlanRun(**plan_run_kwargs()), PlanRun(**plan_run_kwargs())
    assert first.diff_summary == {}
    first.diff_summary["ADD"] = 3
    assert second.diff_summary == {}


def test_missing_required_field_rejected() -> None:
    incomplete = plan_run_kwargs()
    del incomplete["raw_output_hash"]
    with pytest.raises(ValidationError, match="raw_output_hash"):
        PlanRun(**incomplete)


def test_wrong_type_rejected() -> None:
    with pytest.raises(ValidationError):
        PlanRun(**plan_run_kwargs() | {"similarity_threshold": "high"})


def test_documented_model_field_names_survive_protected_namespace() -> None:
    # §3.10 names model_provider/model_name verbatim; the protected
    # namespace is cleared in config rather than renaming the contract.
    run = PlanRun(**plan_run_kwargs())
    assert run.model_provider == "anthropic"
    assert run.model_name == "claude-fable-5"
