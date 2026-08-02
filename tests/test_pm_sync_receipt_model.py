"""ATLAS-245: PmSyncReceipt model matches data-model §6.7."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

import atlas.core.enums
from atlas.core.enums import ActorType
from atlas.core.models import PmSyncReceipt, PmSyncReceiptResult

REQUIRED = object()

DOCUMENTED_FIELDS: dict[str, tuple[Any, Any]] = {
    "id": (UUID, REQUIRED),
    "product_id": (UUID | None, None),
    "product_key": (str | None, None),
    "linear_project_id": (str, REQUIRED),
    "started_at": (datetime, REQUIRED),
    "finished_at": (datetime, REQUIRED),
    "status_map_fingerprint": (str, REQUIRED),
    "fetched_board_fingerprint": (str, REQUIRED),
    "fetched_board_issue_count": (int, REQUIRED),
    "result": (PmSyncReceiptResult, REQUIRED),
    "counters": (dict[str, int], REQUIRED),
    "error_summary": (str | None, None),
    "created_by_type": (ActorType, REQUIRED),
    "created_by_id": (str, REQUIRED),
}


def pm_sync_receipt_kwargs() -> dict[str, Any]:
    return {
        "id": uuid4(),
        "product_id": uuid4(),
        "product_key": "ATLAS",
        "linear_project_id": "project-1",
        "started_at": datetime(2026, 8, 2, 12, 0, tzinfo=UTC),
        "finished_at": datetime(2026, 8, 2, 12, 0, 5, tzinfo=UTC),
        "status_map_fingerprint": "a" * 64,
        "fetched_board_fingerprint": "b" * 64,
        "fetched_board_issue_count": 3,
        "result": "success_status_only",
        "counters": {"status_pulled": 1, "pushed_updated": 0},
        "error_summary": None,
        "created_by_type": "system",
        "created_by_id": "pm-engine",
    }


def test_field_set_matches_documented() -> None:
    assert list(PmSyncReceipt.model_fields) == list(DOCUMENTED_FIELDS)


def test_annotations_requiredness_defaults() -> None:
    for name, (annotation, default) in DOCUMENTED_FIELDS.items():
        field = PmSyncReceipt.model_fields[name]
        assert field.annotation == annotation, name
        if default is REQUIRED:
            assert field.is_required(), name
        else:
            assert not field.is_required(), name
            assert field.default == default, name


def test_result_classification_is_bounded() -> None:
    assert {item.value for item in PmSyncReceiptResult} == {
        "success_definition_changed",
        "success_status_only",
        "success_zero_action",
        "partial",
        "malformed_pull",
        "cancelled",
        "failed",
    }


def test_no_payload_or_credential_fields() -> None:
    forbidden_fragments = ("payload", "token", "credential", "secret", "body")
    for name in PmSyncReceipt.model_fields:
        assert not any(fragment in name for fragment in forbidden_fragments), name


def test_no_mutation_fields() -> None:
    assert "updated_at" not in PmSyncReceipt.model_fields
    assert "status" not in PmSyncReceipt.model_fields


def test_wrong_result_rejected() -> None:
    with pytest.raises(ValidationError):
        PmSyncReceipt(**pm_sync_receipt_kwargs() | {"result": "maybe"})


def test_created_by_type_is_shared_actor_type() -> None:
    assert PmSyncReceipt.model_fields["created_by_type"].annotation is (
        atlas.core.enums.ActorType
    )
