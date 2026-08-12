"""OperatorActionReceipt model contract."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from atlas.core.enums import ActorType, EntityStatus
from atlas.core.models import (
    OperatorActionOutcome,
    OperatorActionReceipt,
    OperatorActionResultCode,
)
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
    "result_code": (OperatorActionResultCode, REQUIRED),
    "result_metadata": (
        dict[OperatorActionMetadataKey, OperatorActionMetadataValue],
        DICT_FACTORY,
    ),
    "before_status": (EntityStatus | None, None),
    "after_status": (EntityStatus | None, None),
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
        "result_code": "action_succeeded",
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


@pytest.mark.parametrize(
    ("field_name", "prohibited"),
    [
        ("result_code", "mf9kq7vlc2xp8nr4wt6yb3dh5js1ag0z"),
        ("before_status", "Promote this private lesson narrative verbatim."),
        ("after_status", '{"private_command":"do not copy"}'),
        ("after_status", "raw-test-output-with-customer-data"),
    ],
    ids=["opaque-result-code", "lesson-before", "request-after", "evidence-after"],
)
def test_command_controlled_strings_use_closed_vocabularies(
    field_name: str,
    prohibited: str,
) -> None:
    with pytest.raises(ValidationError, match=field_name) as raised:
        OperatorActionReceipt(
            **operator_action_receipt_kwargs() | {field_name: prohibited}
        )

    assert prohibited not in str(raised.value)


def test_result_code_and_status_vocabularies_are_bounded() -> None:
    assert {code.value for code in OperatorActionResultCode} == {
        "action_succeeded",
        "action_refused",
        "stale_state",
        "action_failed",
        "external_timeout",
        "evidence_transport_failed",
        "evidence_authentication_failed",
        "evidence_rate_limit_failed",
        "evidence_malformed_source",
        "action_conflict",
    }
    assert {status.value for status in EntityStatus} == {
        "draft",
        "active",
        "archived",
        "deprecated",
    }


VALID_OUTCOME_RESULT_PAIRS: tuple[
    tuple[OperatorActionOutcome, OperatorActionResultCode], ...
] = (
    (OperatorActionOutcome.SUCCEEDED, OperatorActionResultCode.ACTION_SUCCEEDED),
    (OperatorActionOutcome.REFUSED, OperatorActionResultCode.ACTION_REFUSED),
    (OperatorActionOutcome.REFUSED, OperatorActionResultCode.STALE_STATE),
    (OperatorActionOutcome.FAILED, OperatorActionResultCode.ACTION_FAILED),
    (
        OperatorActionOutcome.FAILED,
        OperatorActionResultCode.EXTERNAL_TIMEOUT,
    ),
    (
        OperatorActionOutcome.FAILED,
        OperatorActionResultCode.EVIDENCE_TRANSPORT_FAILED,
    ),
    (
        OperatorActionOutcome.FAILED,
        OperatorActionResultCode.EVIDENCE_AUTHENTICATION_FAILED,
    ),
    (
        OperatorActionOutcome.FAILED,
        OperatorActionResultCode.EVIDENCE_RATE_LIMIT_FAILED,
    ),
    (
        OperatorActionOutcome.FAILED,
        OperatorActionResultCode.EVIDENCE_MALFORMED_SOURCE,
    ),
    (OperatorActionOutcome.CONFLICT, OperatorActionResultCode.ACTION_CONFLICT),
)


@pytest.mark.parametrize(
    ("outcome", "result_code"),
    VALID_OUTCOME_RESULT_PAIRS,
)
def test_valid_outcome_result_code_matrix(
    outcome: OperatorActionOutcome,
    result_code: OperatorActionResultCode,
) -> None:
    receipt = OperatorActionReceipt(
        **operator_action_receipt_kwargs()
        | {"outcome": outcome, "result_code": result_code}
    )

    assert (receipt.outcome, receipt.result_code) == (outcome, result_code)


@pytest.mark.parametrize(
    ("outcome", "result_code"),
    [
        (outcome, result_code)
        for outcome in OperatorActionOutcome
        for result_code in OperatorActionResultCode
        if (outcome, result_code) not in VALID_OUTCOME_RESULT_PAIRS
    ],
)
def test_invalid_outcome_result_code_matrix_is_rejected(
    outcome: OperatorActionOutcome,
    result_code: OperatorActionResultCode,
) -> None:
    with pytest.raises(ValidationError, match="outcome and result_code"):
        OperatorActionReceipt(
            **operator_action_receipt_kwargs()
            | {"outcome": outcome, "result_code": result_code}
        )
