"""Operator action receipt model.

The receipt is the append-only audit record for governed operator writes. It
stores server-attributed command identity and bounded non-secret outcome
metadata; idempotency reservation state is a storage concern.
"""

import json
import math
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    field_validator,
)

from atlas.core.enums import ActorType

MAX_OPERATOR_ACTION_METADATA_BYTES = 4096
MAX_OPERATOR_ACTION_AFFECTED_COUNT = 1_000_000
OperatorActionMetadataKey = Literal["affected_count", "changed", "confidence"]
OperatorActionMetadataValue = StrictBool | StrictInt | StrictFloat
APPROVED_OPERATOR_ACTION_METADATA_FIELDS = frozenset(
    {"affected_count", "changed", "confidence"}
)


class OperatorActionOutcome(StrEnum):
    """Terminal outcome stored on an operator action receipt."""

    SUCCEEDED = "succeeded"
    REFUSED = "refused"
    FAILED = "failed"
    CONFLICT = "conflict"


class OperatorActionReceipt(BaseModel):
    """Append-only receipt for one governed operator command."""

    model_config = ConfigDict(hide_input_in_errors=True)

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
    result_code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,127}$")
    result_metadata: dict[OperatorActionMetadataKey, OperatorActionMetadataValue] = (
        Field(default_factory=dict)
    )
    before_status: str | None = Field(default=None, max_length=128)
    after_status: str | None = Field(default=None, max_length=128)
    created_at: datetime
    completed_at: datetime

    @field_validator("result_metadata", mode="before")
    @classmethod
    def _metadata_is_default_deny(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        if any(
            not isinstance(key, str)
            or key not in APPROVED_OPERATOR_ACTION_METADATA_FIELDS
            for key in value
        ):
            raise ValueError("result_metadata contains an unapproved field")
        if "affected_count" in value:
            affected_count = value["affected_count"]
            if (
                type(affected_count) is not int
                or affected_count < 0
                or affected_count > MAX_OPERATOR_ACTION_AFFECTED_COUNT
            ):
                raise ValueError("result_metadata affected_count is out of bounds")
        if "changed" in value and type(value["changed"]) is not bool:
            raise ValueError("result_metadata changed must be a boolean")
        if "confidence" in value:
            confidence = value["confidence"]
            if (
                type(confidence) is not float
                or not math.isfinite(confidence)
                or confidence < 0.0
                or confidence > 1.0
            ):
                raise ValueError("result_metadata confidence is out of bounds")
        return value

    @field_validator("result_metadata")
    @classmethod
    def _metadata_is_bounded(
        cls,
        value: dict[OperatorActionMetadataKey, OperatorActionMetadataValue],
    ) -> dict[OperatorActionMetadataKey, OperatorActionMetadataValue]:
        rendered = json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        if len(rendered.encode("utf-8")) > MAX_OPERATOR_ACTION_METADATA_BYTES:
            raise ValueError(
                "result_metadata must serialise to at most "
                f"{MAX_OPERATOR_ACTION_METADATA_BYTES} bytes"
            )
        return value
