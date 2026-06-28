"""ATLAS-71: VerificationCheck model and VerificationCheckType match
data-model §5.1.

Expected fields are transcribed from the document, not derived from the
model, so a divergence fails. The exact field set is the falsifiable proof
of the append-only field shape (created_at/completed_at, no updated_at). The
structural tests pin that a VerificationCheck is NOT evidence: it is not the
Evidence type and carries no trust/commit-pin fields, even though its
``status`` is the shared EvidenceStatus outcome (§2.5). Append-only
enforcement belongs to the repository layer, not the model.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

import atlas.core.enums
import atlas.core.models
from atlas.core.enums import EvidenceStatus
from atlas.core.models import VerificationCheck, VerificationCheckType

REQUIRED = object()  # sentinel: field has no default

# data-model §5.1, in documented order: field -> (annotation, default).
DOCUMENTED_FIELDS: dict[str, tuple[Any, Any]] = {
    "id": (UUID, REQUIRED),
    "ticket_id": (UUID, REQUIRED),
    "check_type": (VerificationCheckType, REQUIRED),
    "status": (EvidenceStatus, REQUIRED),
    "summary": (str, REQUIRED),
    "required": (bool, True),
    "evidence_ids": (list[UUID], REQUIRED),  # default_factory: not "required"
    "created_at": (datetime, REQUIRED),
    "completed_at": (datetime | None, None),
}

# data-model §5.1 (seven members, in documented order).
DOCUMENTED_TYPES = {
    "ACCEPTANCE_CRITERIA": "acceptance_criteria",
    "TESTS": "tests",
    "LINT": "lint",
    "DOCUMENTATION": "documentation",
    "SCOPE": "scope",
    "SECURITY": "security",
    "HUMAN_APPROVAL": "human_approval",
}


def check_kwargs() -> dict[str, Any]:
    return {
        "id": uuid4(),
        "ticket_id": uuid4(),
        "check_type": "tests",
        "status": "pending",
        "summary": "tests check awaiting system-tier evidence at the head commit",
        "created_at": datetime(2026, 6, 28, tzinfo=UTC),
    }


def test_field_set_and_order_match_documented() -> None:
    # Exact set and order. Also the append-only field-shape proof: no
    # updated_at can exist without failing here.
    assert list(VerificationCheck.model_fields) == list(DOCUMENTED_FIELDS)


def test_no_updated_at_field() -> None:
    # §5.1 intent directly: an append-only record never updates. The wrong
    # answer (an updated_at field) is named and rejected.
    assert "updated_at" not in VerificationCheck.model_fields


def test_annotations_and_defaults() -> None:
    for name, (annotation, default) in DOCUMENTED_FIELDS.items():
        field = VerificationCheck.model_fields[name]
        assert field.annotation == annotation, name
        if name == "evidence_ids":
            # default_factory=list: optional but not a plain default value.
            assert not field.is_required()
            assert field.default_factory is list
        elif default is REQUIRED:
            assert field.is_required(), name
        else:
            assert field.default == default, name


def test_check_type_members_and_values_match_documented() -> None:
    actual = {member.name: member.value for member in VerificationCheckType}
    assert actual == DOCUMENTED_TYPES


def test_check_type_is_string_valued() -> None:
    assert issubclass(VerificationCheckType, str)
    for member in VerificationCheckType:
        assert member == member.value


def test_is_not_evidence() -> None:
    # A VerificationCheck is NOT evidence (ADR-0008): it is not the Evidence
    # type and carries no trust/commit-pin fields. status IS EvidenceStatus
    # (the shared §2.5 outcome) — that is expected and must remain so.
    from atlas.core.models import Evidence

    assert not issubclass(VerificationCheck, Evidence)
    names = set(VerificationCheck.model_fields)
    for forbidden in (
        "commit_sha",
        "payload_hash",
        "created_by_type",
        "external_run_id",
    ):
        assert forbidden not in names, forbidden
    assert VerificationCheck.model_fields["status"].annotation is (
        atlas.core.enums.EvidenceStatus
    )


def test_model_is_not_frozen() -> None:
    # Append-only enforcement lives in the repository layer, not a frozen
    # model config — mirroring Evidence/DebtItem.
    assert VerificationCheck.model_config.get("frozen") is not True


def test_defaults_applied() -> None:
    check = VerificationCheck(**check_kwargs())
    assert check.required is True
    assert check.evidence_ids == []
    assert check.completed_at is None


def test_enum_identity() -> None:
    # Identity, not equality: check_type is the model-local VerificationCheckType
    # (§5.1); status is the §2 shared EvidenceStatus.
    assert VerificationCheck.model_fields["check_type"].annotation is (
        atlas.core.models.VerificationCheckType
    )
    assert VerificationCheck.model_fields["status"].annotation is (
        atlas.core.enums.EvidenceStatus
    )


def test_missing_required_field_rejected() -> None:
    incomplete = check_kwargs()
    del incomplete["summary"]
    with pytest.raises(ValidationError, match="summary"):
        VerificationCheck(**incomplete)


def test_wrong_check_type_rejected() -> None:
    with pytest.raises(ValidationError):
        VerificationCheck(**check_kwargs() | {"check_type": "not-a-check"})
