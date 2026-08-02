"""Operator action receipt model.

The receipt is the append-only audit record for governed operator writes. It
stores server-attributed command identity and bounded non-secret outcome
metadata; idempotency reservation state is a storage concern.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from atlas.core.enums import ActorType

MAX_OPERATOR_ACTION_METADATA_BYTES = 4096


class OperatorActionOutcome(StrEnum):
    """Terminal outcome stored on an operator action receipt."""

    SUCCEEDED = "succeeded"
    REFUSED = "refused"
    FAILED = "failed"
    CONFLICT = "conflict"


class OperatorActionReceipt(BaseModel):
    """Append-only receipt for one governed operator command."""

    id: UUID
    correlation_id: UUID
    action: str = Field(min_length=1, max_length=128)
    target_type: str = Field(min_length=1, max_length=128)
    target_id: str = Field(min_length=1, max_length=256)
    created_by_type: ActorType
    created_by_id: str = Field(min_length=1, max_length=128)
    idempotency_key_identity: str = Field(min_length=1, max_length=80)
    request_fingerprint: str = Field(min_length=1, max_length=80)
    outcome: OperatorActionOutcome
    result_code: str = Field(min_length=1, max_length=128)
    result_metadata: dict[str, Any] = Field(default_factory=dict)
    before_status: str | None = Field(default=None, max_length=128)
    after_status: str | None = Field(default=None, max_length=128)
    created_at: datetime
    completed_at: datetime

    @field_validator("result_metadata")
    @classmethod
    def _metadata_is_bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        import json

        rendered = json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        if len(rendered.encode("utf-8")) > MAX_OPERATOR_ACTION_METADATA_BYTES:
            raise ValueError(
                "result_metadata must serialise to at most "
                f"{MAX_OPERATOR_ACTION_METADATA_BYTES} bytes"
            )
        return value
