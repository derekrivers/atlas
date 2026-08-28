"""ATLAS-14: Evidence model and EvidenceType match data-model §3.7.

Expected tables are transcribed from the document, not derived from the
model, so a divergence fails. The exact field set is the falsifiable
proof of the append-only field shape (created_at only, no updated_at,
no completed_at). The non-policing tests pin that enforcement —
append-only and the agent PENDING cap — belongs to the repository
layer (ATLAS-18), not the model.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

import atlas.core.enums
from atlas.core.enums import ActorType, EvidenceStatus
from atlas.core.models import Evidence, EvidenceType
from atlas.core.models.evidence import (
    MAX_DOCUMENTATION_PATH_LENGTH,
    MAX_DOCUMENTATION_PATHS,
    DocumentationPath,
)

REQUIRED = object()  # sentinel: field has no default
LIST_FACTORY = object()  # sentinel: default_factory=list
DICT_FACTORY = object()  # sentinel: default_factory=dict

# data-model §3.7, in documented order: field -> (annotation, default).
DOCUMENTED_FIELDS: dict[str, tuple[Any, Any]] = {
    "id": (UUID, REQUIRED),
    "product_id": (UUID, REQUIRED),
    "ticket_id": (UUID | None, None),
    "agent_run_id": (UUID | None, None),
    "evidence_type": (EvidenceType, REQUIRED),
    "status": (EvidenceStatus, REQUIRED),
    "summary": (str, REQUIRED),
    "commit_sha": (str | None, None),
    "external_run_id": (str | None, None),
    "job_name": (str | None, None),
    "source_event_at": (datetime | None, None),
    "payload_hash": (str | None, None),
    "source_uri": (str | None, None),
    "raw_payload": (dict[str, Any], DICT_FACTORY),
    "docs_paths": (tuple[DocumentationPath, ...] | None, None),
    "created_by_type": (ActorType, REQUIRED),
    "created_by_id": (str, REQUIRED),
    "created_at": (datetime, REQUIRED),
}

# data-model §3.7 (ten members)
DOCUMENTED_TYPES = {
    "TEST_RESULT": "test_result",
    "BUILD_RESULT": "build_result",
    "LINT_RESULT": "lint_result",
    "COVERAGE_REPORT": "coverage_report",
    "SCREENSHOT": "screenshot",
    "PR_REVIEW": "pr_review",
    "DEPLOYMENT_RESULT": "deployment_result",
    "DOCUMENTATION_UPDATE": "documentation_update",
    "MANUAL_APPROVAL": "manual_approval",
    "PR_MERGED": "pr_merged",
}


def evidence_kwargs() -> dict[str, Any]:
    # Default is a valid system-tier record: ADR-0008 requires the pinning
    # triple (commit_sha/external_run_id/payload_hash) for system-tier
    # evidence, enforced in EvidenceRepo.add (ATLAS-61). Call sites that
    # build agent/human records override created_by_type; the model-layer
    # tests assert field shape, not kwargs contents.
    return {
        "id": uuid4(),
        "product_id": uuid4(),
        "evidence_type": "test_result",
        "status": "passed",
        "summary": "pytest: 97 passed",
        "commit_sha": "a" * 40,
        "external_run_id": "run-1",
        "payload_hash": "sha256:" + "0" * 64,
        "created_by_type": "system",
        "created_by_id": "github-actions",
        "created_at": datetime(2026, 6, 12, tzinfo=UTC),
    }


def test_field_set_matches_documented() -> None:
    # Exact set and order. This is also the append-only field-shape
    # proof: created_at is present and no updated_at or completed_at can
    # exist without failing here.
    assert list(Evidence.model_fields) == list(DOCUMENTED_FIELDS)


def test_no_mutation_timestamps() -> None:
    # Redundant with the exact-set test, but states the §3.7 intent
    # directly: append-only rows never update or complete.
    assert "updated_at" not in Evidence.model_fields
    assert "completed_at" not in Evidence.model_fields


def test_annotations_requiredness_defaults() -> None:
    for name, (annotation, default) in DOCUMENTED_FIELDS.items():
        field = Evidence.model_fields[name]
        assert field.annotation == annotation, name
        if default is REQUIRED:
            assert field.is_required(), name
        elif default is LIST_FACTORY:
            assert field.default_factory is list, name
        elif default is DICT_FACTORY:
            assert field.default_factory is dict, name
        else:
            assert not field.is_required(), name
            assert field.default == default, name


def test_type_members_and_values_match_documented() -> None:
    actual = {member.name: member.value for member in EvidenceType}
    assert actual == DOCUMENTED_TYPES


def test_type_is_string_valued() -> None:
    assert issubclass(EvidenceType, str)
    for member in EvidenceType:
        assert member == member.value


def test_agent_created_passed_is_valid_at_model_layer() -> None:
    # Pre-made decision: the PENDING cap is repository-layer enforcement
    # (EvidenceRepo.add, ATLAS-18, via evidence_tier) — the model is the
    # contract, not the police. A validator coupling status to
    # created_by_type silently appearing here fails this test.
    evidence = Evidence(
        **evidence_kwargs() | {"created_by_type": "agent", "status": "passed"}
    )
    assert evidence.created_by_type is ActorType.AGENT
    assert evidence.status is EvidenceStatus.PASSED


def test_model_is_not_frozen() -> None:
    # Pre-made decision: append-only enforcement lives in the repository
    # layer (ATLAS-18), not in a frozen model config. A mutation guard
    # silently appearing here fails this test.
    assert Evidence.model_config.get("frozen") is not True


def test_missing_required_field_rejected() -> None:
    incomplete = evidence_kwargs()
    del incomplete["summary"]
    with pytest.raises(ValidationError, match="summary"):
        Evidence(**incomplete)


def test_wrong_type_rejected() -> None:
    with pytest.raises(ValidationError):
        Evidence(**evidence_kwargs() | {"raw_payload": "not-a-dict"})


def test_raw_payload_defaults_to_empty_independent_dict() -> None:
    first, second = Evidence(**evidence_kwargs()), Evidence(**evidence_kwargs())
    assert first.raw_payload == {}
    first.raw_payload["run"] = 1
    assert second.raw_payload == {}


def test_structured_documentation_paths_are_nullable_bounded_and_canonical() -> None:
    head = "c" * 40
    record = Evidence(
        **evidence_kwargs()
        | {
            "evidence_type": "documentation_update",
            "commit_sha": head,
            "external_run_id": f"docs:v2:{head}",
            "docs_paths": (
                "docs/architecture/data-model-and-schemas.md",
                "docs/atlas/evidence-pipeline.md",
            ),
        }
    )
    assert record.docs_paths == (
        "docs/architecture/data-model-and-schemas.md",
        "docs/atlas/evidence-pipeline.md",
    )
    schema = Evidence.model_json_schema()["properties"]["docs_paths"]["anyOf"][0]
    assert schema["minItems"] == 1
    assert schema["maxItems"] == MAX_DOCUMENTATION_PATHS
    assert schema["items"]["maxLength"] == MAX_DOCUMENTATION_PATH_LENGTH


@pytest.mark.parametrize(
    "changes",
    [
        {"docs_paths": ()},
        {"docs_paths": ("docs/z.md", "docs/a.md")},
        {"docs_paths": ("docs/a.md", "docs/a.md")},
        {"docs_paths": ("../docs/a.md",)},
        {"docs_paths": ("docs/../a.md",)},
        {"docs_paths": ("docs\\a.md",)},
        {"docs_paths": ("docs/",)},
        {"docs_paths": ("docs/" + "a" * MAX_DOCUMENTATION_PATH_LENGTH,)},
        {"docs_paths": tuple(f"docs/{index}.md" for index in range(257))},
        {"evidence_type": "test_result", "docs_paths": ("docs/a.md",)},
        {"external_run_id": "docs:v2:" + "d" * 40},
        {"docs_paths": None},
    ],
)
def test_invalid_or_contradictory_structured_documentation_paths_fail_closed(
    changes: dict[str, Any],
) -> None:
    head = "c" * 40
    base = evidence_kwargs() | {
        "evidence_type": "documentation_update",
        "commit_sha": head,
        "external_run_id": f"docs:v2:{head}",
        "docs_paths": ("docs/a.md",),
    }
    with pytest.raises(ValidationError):
        Evidence(**base | changes)


def test_shared_enum_identity() -> None:
    # Identity, not equality: the annotations are ATLAS-11's classes.
    assert Evidence.model_fields["status"].annotation is (
        atlas.core.enums.EvidenceStatus
    )
    assert Evidence.model_fields["created_by_type"].annotation is (
        atlas.core.enums.ActorType
    )
