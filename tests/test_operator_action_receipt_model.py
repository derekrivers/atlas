"""OperatorActionReceipt model contract."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from atlas.core.enums import ActorType
from atlas.core.models import OperatorActionOutcome, OperatorActionReceipt
from atlas.core.models.operator_action_receipt import (
    OperatorActionMetadataKey,
    OperatorActionMetadataValue,
)

REQUIRED = object()
DICT_FACTORY = object()

DOCUMENTED_FIELDS: dict[str, tuple[Any, Any]] = {
    "id": (UUID, REQUIRED),
    "correlation_id": (UUID, REQUIRED),
    "action": (str, REQUIRED),
    "target_type": (str, REQUIRED),
    "target_id": (str, REQUIRED),
    "created_by_type": (ActorType, REQUIRED),
    "created_by_id": (str, REQUIRED),
    "idempotency_key_identity": (str, REQUIRED),
    "request_fingerprint": (str, REQUIRED),
    "outcome": (OperatorActionOutcome, REQUIRED),
    "result_code": (str, REQUIRED),
    "result_metadata": (
        dict[OperatorActionMetadataKey, OperatorActionMetadataValue],
        DICT_FACTORY,
    ),
    "before_status": (str | None, None),
    "after_status": (str | None, None),
    "created_at": (datetime, REQUIRED),
    "completed_at": (datetime, REQUIRED),
}


def operator_action_receipt_kwargs() -> dict[str, Any]:
    return {
        "id": uuid4(),
        "correlation_id": uuid4(),
        "action": "lesson.promote",
        "target_type": "lesson",
        "target_id": str(uuid4()),
        "created_by_type": "human",
        "created_by_id": "operator",
        "idempotency_key_identity": "sha256:" + ("a" * 64),
        "request_fingerprint": "sha256:" + ("b" * 64),
        "outcome": "succeeded",
        "result_code": "lesson_promoted",
        "result_metadata": {"confidence": 0.8},
        "before_status": "draft",
        "after_status": "active",
        "created_at": datetime(2026, 8, 2, 12, tzinfo=UTC),
        "completed_at": datetime(2026, 8, 2, 12, 0, 1, tzinfo=UTC),
    }


def test_field_set_matches_documented() -> None:
    assert list(OperatorActionReceipt.model_fields) == list(DOCUMENTED_FIELDS)


def test_annotations_requiredness_defaults() -> None:
    for name, (annotation, default) in DOCUMENTED_FIELDS.items():
        field = OperatorActionReceipt.model_fields[name]
        assert field.annotation == annotation, name
        if default is REQUIRED:
            assert field.is_required(), name
        elif default is DICT_FACTORY:
            assert field.default_factory is dict, name
        else:
            assert not field.is_required(), name
            assert field.default == default, name


def test_outcome_is_bounded_terminal_enum() -> None:
    assert {outcome.value for outcome in OperatorActionOutcome} == {
        "succeeded",
        "refused",
        "failed",
        "conflict",
    }
    with pytest.raises(ValidationError, match="outcome"):
        OperatorActionReceipt(**operator_action_receipt_kwargs() | {"outcome": "ok"})


def test_no_raw_secret_or_payload_fields() -> None:
    field_names = set(OperatorActionReceipt.model_fields)
    assert "idempotency_key" not in field_names
    for forbidden in (
        "request_body",
        "raw_payload",
        "raw_evidence",
        "lesson_content",
        "exception_trace",
        "updated_at",
    ):
        assert forbidden not in field_names


@pytest.mark.parametrize(
    "metadata",
    [
        {"unclassified": "opaque-value"},
        {"changed": "opaque-value"},
        {"affected_count": -1},
        {"affected_count": 1_000_001},
        {"confidence": float("nan")},
        {"confidence": 2.0},
    ],
    ids=[
        "unapproved-field",
        "wrong-approved-primitive",
        "negative-count",
        "oversized-count",
        "non-finite-confidence",
        "out-of-range-confidence",
    ],
)
def test_metadata_is_default_deny_and_bounded(metadata: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="result_metadata"):
        OperatorActionReceipt(
            **operator_action_receipt_kwargs() | {"result_metadata": metadata}
        )


def test_result_code_accepts_only_bounded_machine_codes() -> None:
    with pytest.raises(ValidationError, match="result_code"):
        OperatorActionReceipt(
            **operator_action_receipt_kwargs()
            | {"result_code": "opaque result content"}
        )
