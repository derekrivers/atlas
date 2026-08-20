"""Bounded Phase 16 runtime transport and canonical event contracts.

The source-side transport envelope deliberately carries no Atlas-owned UUIDs.
Joining source identity to Atlas product, ticket, or AgentRun identity is a
later importer concern and does not belong in this module.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any, Literal, Self, TypeAlias
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

RUNTIME_TRANSPORT_EVENT_SCHEMA_VERSION: Literal["runtime-transport-event/v1"] = (
    "runtime-transport-event/v1"
)
RUNTIME_EVENT_SCHEMA_VERSION: Literal["runtime-event/v1"] = "runtime-event/v1"

MAX_RUNTIME_IDENTITY_LENGTH = 256
MAX_RUNTIME_OPERATION_LENGTH = 128
MAX_RUNTIME_PATH_LENGTH = 512
MAX_RUNTIME_TOUCHED_PATHS = 256
MAX_RUNTIME_COORDINATION_IDS = 128
MAX_RUNTIME_METADATA_FIELDS = 32
MAX_RUNTIME_METADATA_VALUE_LENGTH = 256
MAX_RUNTIME_METADATA_SET_ITEMS = 64
MAX_RUNTIME_METADATA_BYTES = 4096
MAX_RUNTIME_COUNTER = 2**63 - 1

_CANONICAL_SCOPE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?$")
_EXTERNAL_IDENTITY = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._:-]*[A-Za-z0-9])?$")
_ISSUE_IDENTIFIER = re.compile(r"^[A-Z][A-Z0-9]*-[1-9][0-9]*$")
_CANONICAL_OPERATION = re.compile(r"^[a-z0-9](?:[a-z0-9._:-]*[a-z0-9])?$")
_SHA256_DIGEST = re.compile(r"^[0-9a-fA-F]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-fA-F]{40}$")


class RuntimeEventFamily(StrEnum):
    """Closed durable event-family vocabulary from the Phase 16 v1 design."""

    RUN_STARTED = "run_started"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    SESSION_STARTED = "session_started"
    TURN_COMPLETED = "turn_completed"
    TURN_FAILED = "turn_failed"
    TURN_CANCELLED = "turn_cancelled"
    TURN_INPUT_REQUIRED = "turn_input_required"
    OPERATION_STARTED = "operation_started"
    OPERATION_COMPLETED = "operation_completed"
    OPERATION_FAILED = "operation_failed"
    TOOL_CALL_REQUESTED = "tool_call_requested"
    TOOL_CALL_COMPLETED = "tool_call_completed"
    TOOL_CALL_FAILED = "tool_call_failed"
    APPROVAL_REQUIRED = "approval_required"
    APPROVAL_AUTO_RESOLVED = "approval_auto_resolved"
    NOTIFICATION = "notification"
    MALFORMED_PROTOCOL_MESSAGE = "malformed_protocol_message"
    ROLE_STARTED = "role_started"
    ROLE_COMPLETED = "role_completed"
    HANDOFF_CREATED = "handoff_created"
    HANDOFF_CONSUMED = "handoff_consumed"
    ARTIFACT_READ = "artifact_read"
    ARTIFACT_WRITTEN = "artifact_written"
    INTERFACE_CONSUMED = "interface_consumed"
    INTERFACE_CHANGED = "interface_changed"
    INTERFACE_DECISION = "interface_decision"
    PEER_MESSAGE = "peer_message"
    CAPABILITY_REQUESTED = "capability_requested"
    CAPABILITY_ALLOWED = "capability_allowed"
    CAPABILITY_DENIED = "capability_denied"
    STEERING_REQUESTED = "steering_requested"
    STEERING_APPLIED = "steering_applied"
    STEERING_REJECTED_STALE = "steering_rejected_stale"
    STEERING_INDETERMINATE = "steering_indeterminate"


class RuntimePhaseClassification(StrEnum):
    """Closed deterministic trajectory phase vocabulary."""

    UNDERSTAND = "understand"
    LOCALISE = "localise"
    REPRODUCE = "reproduce"
    PATCH = "patch"
    VALIDATE = "validate"
    INTEGRATE = "integrate"
    HANDOFF = "handoff"
    UNKNOWN = "unknown"


RuntimeSourceEventKey: TypeAlias = tuple[UUID, int]
RuntimeMetadataValue: TypeAlias = str | StrictInt | tuple[str, ...]

_STRING_METADATA_FIELDS = frozenset(
    {
        "raw_method_name",
        "item_type",
        "tool_name",
        "capability_name",
        "error_class",
        "failure_class",
        "worker_host_id",
        "policy_decision_id",
        "effect_decision_id",
        "interface_id",
        "handoff_id",
    }
)
_INTEGER_METADATA_FIELDS = frozenset(
    {
        "token_delta",
        "token_count",
        "input_token_delta",
        "output_token_delta",
        "cached_input_token_delta",
        "reasoning_output_token_delta",
        "total_token_delta",
        "input_token_count",
        "output_token_count",
        "cached_input_token_count",
        "reasoning_output_token_count",
        "total_token_count",
        "retry_ordinal",
        "attempt_ordinal",
    }
)
_SET_METADATA_FIELDS = frozenset(
    {
        "validation_profile_ids",
        "policy_decision_ids",
        "effect_decision_ids",
        "interface_ids",
        "handoff_ids",
    }
)
APPROVED_RUNTIME_METADATA_FIELDS = frozenset(
    _STRING_METADATA_FIELDS | _INTEGER_METADATA_FIELDS | _SET_METADATA_FIELDS
)


def _canonical_string(
    value: Any,
    *,
    name: str,
    pattern: re.Pattern[str],
    max_length: int,
) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= max_length
        or pattern.fullmatch(value) is None
    ):
        raise ValueError(f"runtime_event_{name}_invalid")
    return value


def _canonical_optional_string(
    value: Any,
    *,
    name: str,
    pattern: re.Pattern[str] = _EXTERNAL_IDENTITY,
    max_length: int = MAX_RUNTIME_IDENTITY_LENGTH,
) -> str | None:
    if value is None:
        return None
    return _canonical_string(
        value,
        name=name,
        pattern=pattern,
        max_length=max_length,
    )


def _canonical_digest(value: Any, *, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _SHA256_DIGEST.fullmatch(value) is None:
        raise ValueError(f"runtime_event_{name}_invalid")
    return value.lower()


def _canonical_git_sha(value: Any, *, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _GIT_SHA.fullmatch(value) is None:
        raise ValueError(f"runtime_event_{name}_invalid")
    return value.lower()


def _canonical_relative_path(value: Any) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= MAX_RUNTIME_PATH_LENGTH:
        raise ValueError("runtime_event_touched_paths_item_invalid")
    if "\\" in value or "\x00" in value or value.startswith("/"):
        raise ValueError("runtime_event_touched_paths_item_invalid")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("runtime_event_touched_paths_item_invalid")
    return value


def _canonical_string_set(
    value: Any,
    *,
    name: str,
    max_items: int,
    item_validator: Any,
) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"runtime_event_{name}_collection_invalid")
    if len(value) > max_items:
        raise ValueError(f"runtime_event_{name}_collection_out_of_bounds")
    return tuple(sorted({item_validator(item) for item in value}))


def _canonical_metadata(value: Any) -> dict[str, RuntimeMetadataValue]:
    if not isinstance(value, dict):
        raise ValueError("runtime_event_bounded_metadata_invalid")
    if len(value) > MAX_RUNTIME_METADATA_FIELDS:
        raise ValueError("runtime_event_bounded_metadata_too_many_fields")
    if any(
        type(key) is not str or key not in APPROVED_RUNTIME_METADATA_FIELDS
        for key in value
    ):
        raise ValueError("runtime_event_bounded_metadata_field_forbidden")

    canonical: dict[str, RuntimeMetadataValue] = {}
    for key, item in value.items():
        if key in _STRING_METADATA_FIELDS:
            if (
                not isinstance(item, str)
                or not 1 <= len(item) <= MAX_RUNTIME_METADATA_VALUE_LENGTH
                or any(
                    ord(character) < 32 or ord(character) == 127 for character in item
                )
            ):
                raise ValueError(f"runtime_event_bounded_metadata_{key}_invalid")
            canonical[key] = item
        elif key in _INTEGER_METADATA_FIELDS:
            if type(item) is not int or not 0 <= item <= MAX_RUNTIME_COUNTER:
                raise ValueError(f"runtime_event_bounded_metadata_{key}_invalid")
            canonical[key] = item
        else:
            canonical[key] = _canonical_string_set(
                item,
                name=f"bounded_metadata_{key}",
                max_items=MAX_RUNTIME_METADATA_SET_ITEMS,
                item_validator=lambda member, field=key: _canonical_string(
                    member,
                    name=f"bounded_metadata_{field}_item",
                    pattern=_EXTERNAL_IDENTITY,
                    max_length=MAX_RUNTIME_METADATA_VALUE_LENGTH,
                ),
            )

    rendered = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    if len(rendered.encode("utf-8")) > MAX_RUNTIME_METADATA_BYTES:
        raise ValueError("runtime_event_bounded_metadata_too_large")
    return {key: canonical[key] for key in sorted(canonical)}


def _canonical_bytes(value: BaseModel) -> bytes:
    rendered = json.dumps(
        value.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return rendered.encode("utf-8")


def _validate_cross_field_identity(value: Any) -> None:
    if value.codex_turn_id is not None and value.codex_thread_id is None:
        raise ValueError("runtime_event_codex_turn_requires_thread")
    if value.base_sha is not None and value.head_sha is None:
        raise ValueError("runtime_event_base_sha_requires_head_sha")
    if value.peer_role_id is not None and value.role_id is None:
        raise ValueError("runtime_event_peer_role_requires_role")
    if value.peer_role_id is not None and value.peer_role_id == value.role_id:
        raise ValueError("runtime_event_peer_role_must_differ")


class RuntimeTransportEvent(BaseModel):
    """Sanitised Symphony-authored source envelope with no Atlas UUID join."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    schema_version: Literal["runtime-transport-event/v1"] = (
        RUNTIME_TRANSPORT_EVENT_SCHEMA_VERSION
    )
    product_scope: str = Field(min_length=1, max_length=128)
    external_issue_id: str = Field(min_length=1, max_length=128)
    issue_identifier: str = Field(min_length=3, max_length=64)
    runtime_attempt_id: UUID
    codex_thread_id: str | None = Field(
        default=None, min_length=1, max_length=MAX_RUNTIME_IDENTITY_LENGTH
    )
    codex_turn_id: str | None = Field(
        default=None, min_length=1, max_length=MAX_RUNTIME_IDENTITY_LENGTH
    )
    session_id: str | None = Field(
        default=None, min_length=1, max_length=MAX_RUNTIME_IDENTITY_LENGTH
    )
    source_sequence_no: StrictInt = Field(ge=1, le=MAX_RUNTIME_COUNTER)
    observed_at: datetime
    source_descriptor_fingerprint: str
    event_family: RuntimeEventFamily
    operation_kind: str = Field(min_length=1, max_length=MAX_RUNTIME_OPERATION_LENGTH)
    operation_identity_hash: str | None = None
    result_class: str | None = Field(
        default=None, min_length=1, max_length=MAX_RUNTIME_OPERATION_LENGTH
    )
    duration_ms: StrictInt | None = Field(default=None, ge=0, le=MAX_RUNTIME_COUNTER)
    exit_code_class: str | None = Field(
        default=None, min_length=1, max_length=MAX_RUNTIME_OPERATION_LENGTH
    )
    touched_paths: tuple[str, ...]
    head_sha: str | None = None
    base_sha: str | None = None
    role_id: str | None = Field(
        default=None, min_length=1, max_length=MAX_RUNTIME_IDENTITY_LENGTH
    )
    topology_id: str | None = Field(
        default=None, min_length=1, max_length=MAX_RUNTIME_IDENTITY_LENGTH
    )
    interface_ids: tuple[str, ...]
    artifact_ids: tuple[str, ...]
    peer_role_id: str | None = Field(
        default=None, min_length=1, max_length=MAX_RUNTIME_IDENTITY_LENGTH
    )
    bounded_metadata: dict[str, RuntimeMetadataValue]
    payload_digest: str | None = None

    @field_validator("product_scope", mode="before")
    @classmethod
    def _product_scope_is_canonical(cls, value: Any) -> str:
        return _canonical_string(
            value, name="product_scope", pattern=_CANONICAL_SCOPE, max_length=128
        )

    @field_validator("external_issue_id", mode="before")
    @classmethod
    def _external_issue_id_is_canonical(cls, value: Any) -> str:
        return _canonical_string(
            value,
            name="external_issue_id",
            pattern=_EXTERNAL_IDENTITY,
            max_length=128,
        )

    @field_validator("issue_identifier", mode="before")
    @classmethod
    def _issue_identifier_is_canonical(cls, value: Any) -> str:
        return _canonical_string(
            value,
            name="issue_identifier",
            pattern=_ISSUE_IDENTIFIER,
            max_length=64,
        )

    @field_validator(
        "codex_thread_id",
        "codex_turn_id",
        "session_id",
        "role_id",
        "topology_id",
        "peer_role_id",
        mode="before",
    )
    @classmethod
    def _optional_identities_are_canonical(cls, value: Any, info: Any) -> str | None:
        return _canonical_optional_string(value, name=info.field_name)

    @field_validator("operation_kind", mode="before")
    @classmethod
    def _operation_kind_is_canonical(cls, value: Any) -> str:
        return _canonical_string(
            value,
            name="operation_kind",
            pattern=_CANONICAL_OPERATION,
            max_length=MAX_RUNTIME_OPERATION_LENGTH,
        )

    @field_validator("result_class", "exit_code_class", mode="before")
    @classmethod
    def _optional_classes_are_canonical(cls, value: Any, info: Any) -> str | None:
        return _canonical_optional_string(
            value,
            name=info.field_name,
            pattern=_CANONICAL_OPERATION,
            max_length=MAX_RUNTIME_OPERATION_LENGTH,
        )

    @field_validator(
        "source_descriptor_fingerprint",
        "operation_identity_hash",
        "payload_digest",
        mode="before",
    )
    @classmethod
    def _digests_are_canonical(cls, value: Any, info: Any) -> str | None:
        return _canonical_digest(value, name=info.field_name)

    @field_validator("head_sha", "base_sha", mode="before")
    @classmethod
    def _git_shas_are_canonical(cls, value: Any, info: Any) -> str | None:
        return _canonical_git_sha(value, name=info.field_name)

    @field_validator("touched_paths", mode="before")
    @classmethod
    def _touched_paths_are_canonical(cls, value: Any) -> tuple[str, ...]:
        return _canonical_string_set(
            value,
            name="touched_paths",
            max_items=MAX_RUNTIME_TOUCHED_PATHS,
            item_validator=_canonical_relative_path,
        )

    @field_validator("interface_ids", "artifact_ids", mode="before")
    @classmethod
    def _coordination_ids_are_canonical(cls, value: Any, info: Any) -> tuple[str, ...]:
        return _canonical_string_set(
            value,
            name=info.field_name,
            max_items=MAX_RUNTIME_COORDINATION_IDS,
            item_validator=lambda item: _canonical_string(
                item,
                name=f"{info.field_name}_item",
                pattern=_EXTERNAL_IDENTITY,
                max_length=MAX_RUNTIME_IDENTITY_LENGTH,
            ),
        )

    @field_validator("bounded_metadata", mode="before")
    @classmethod
    def _bounded_metadata_is_canonical(
        cls, value: Any
    ) -> dict[str, RuntimeMetadataValue]:
        return _canonical_metadata(value)

    @field_validator("observed_at")
    @classmethod
    def _observed_at_is_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("runtime_event_observed_at_timezone_required")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _identity_is_coherent(self) -> Self:
        _validate_cross_field_identity(self)
        return self

    @property
    def source_event_key(self) -> RuntimeSourceEventKey:
        """Return the descriptor-scoped source sequencing identity."""

        return (self.runtime_attempt_id, self.source_sequence_no)

    def canonical_bytes(self) -> bytes:
        """Return compact deterministic bytes for the bounded source record."""

        return _canonical_bytes(self)

    @property
    def transport_fingerprint(self) -> str:
        """SHA-256 of the complete canonical source envelope."""

        return hashlib.sha256(self.canonical_bytes()).hexdigest()


class RuntimeEvent(BaseModel):
    """Atlas-owned canonical event after an authoritative identity join."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    id: UUID
    schema_version: Literal["runtime-event/v1"] = RUNTIME_EVENT_SCHEMA_VERSION
    product_id: UUID
    ticket_id: UUID | None = None
    external_issue_id: str = Field(min_length=1, max_length=128)
    agent_run_id: UUID | None = None
    runtime_attempt_id: UUID
    codex_thread_id: str | None = Field(
        default=None, min_length=1, max_length=MAX_RUNTIME_IDENTITY_LENGTH
    )
    codex_turn_id: str | None = Field(
        default=None, min_length=1, max_length=MAX_RUNTIME_IDENTITY_LENGTH
    )
    session_id: str | None = Field(
        default=None, min_length=1, max_length=MAX_RUNTIME_IDENTITY_LENGTH
    )
    source_sequence_no: StrictInt = Field(ge=1, le=MAX_RUNTIME_COUNTER)
    observed_at: datetime
    source_descriptor_fingerprint: str
    event_family: RuntimeEventFamily
    operation_kind: str = Field(min_length=1, max_length=MAX_RUNTIME_OPERATION_LENGTH)
    operation_identity_hash: str | None = None
    phase_classification: RuntimePhaseClassification
    result_class: str | None = Field(
        default=None, min_length=1, max_length=MAX_RUNTIME_OPERATION_LENGTH
    )
    duration_ms: StrictInt | None = Field(default=None, ge=0, le=MAX_RUNTIME_COUNTER)
    exit_code_class: str | None = Field(
        default=None, min_length=1, max_length=MAX_RUNTIME_OPERATION_LENGTH
    )
    touched_paths: tuple[str, ...]
    head_sha: str | None = None
    base_sha: str | None = None
    role_id: str | None = Field(
        default=None, min_length=1, max_length=MAX_RUNTIME_IDENTITY_LENGTH
    )
    topology_id: str | None = Field(
        default=None, min_length=1, max_length=MAX_RUNTIME_IDENTITY_LENGTH
    )
    interface_ids: tuple[str, ...]
    artifact_ids: tuple[str, ...]
    peer_role_id: str | None = Field(
        default=None, min_length=1, max_length=MAX_RUNTIME_IDENTITY_LENGTH
    )
    bounded_metadata: dict[str, RuntimeMetadataValue]
    payload_digest: str | None = None

    @field_validator("external_issue_id", mode="before")
    @classmethod
    def _external_issue_id_is_canonical(cls, value: Any) -> str:
        return _canonical_string(
            value,
            name="external_issue_id",
            pattern=_EXTERNAL_IDENTITY,
            max_length=128,
        )

    @field_validator(
        "codex_thread_id",
        "codex_turn_id",
        "session_id",
        "role_id",
        "topology_id",
        "peer_role_id",
        mode="before",
    )
    @classmethod
    def _optional_identities_are_canonical(cls, value: Any, info: Any) -> str | None:
        return _canonical_optional_string(value, name=info.field_name)

    @field_validator("operation_kind", mode="before")
    @classmethod
    def _operation_kind_is_canonical(cls, value: Any) -> str:
        return _canonical_string(
            value,
            name="operation_kind",
            pattern=_CANONICAL_OPERATION,
            max_length=MAX_RUNTIME_OPERATION_LENGTH,
        )

    @field_validator("result_class", "exit_code_class", mode="before")
    @classmethod
    def _optional_classes_are_canonical(cls, value: Any, info: Any) -> str | None:
        return _canonical_optional_string(
            value,
            name=info.field_name,
            pattern=_CANONICAL_OPERATION,
            max_length=MAX_RUNTIME_OPERATION_LENGTH,
        )

    @field_validator(
        "source_descriptor_fingerprint",
        "operation_identity_hash",
        "payload_digest",
        mode="before",
    )
    @classmethod
    def _digests_are_canonical(cls, value: Any, info: Any) -> str | None:
        return _canonical_digest(value, name=info.field_name)

    @field_validator("head_sha", "base_sha", mode="before")
    @classmethod
    def _git_shas_are_canonical(cls, value: Any, info: Any) -> str | None:
        return _canonical_git_sha(value, name=info.field_name)

    @field_validator("touched_paths", mode="before")
    @classmethod
    def _touched_paths_are_canonical(cls, value: Any) -> tuple[str, ...]:
        return _canonical_string_set(
            value,
            name="touched_paths",
            max_items=MAX_RUNTIME_TOUCHED_PATHS,
            item_validator=_canonical_relative_path,
        )

    @field_validator("interface_ids", "artifact_ids", mode="before")
    @classmethod
    def _coordination_ids_are_canonical(cls, value: Any, info: Any) -> tuple[str, ...]:
        return _canonical_string_set(
            value,
            name=info.field_name,
            max_items=MAX_RUNTIME_COORDINATION_IDS,
            item_validator=lambda item: _canonical_string(
                item,
                name=f"{info.field_name}_item",
                pattern=_EXTERNAL_IDENTITY,
                max_length=MAX_RUNTIME_IDENTITY_LENGTH,
            ),
        )

    @field_validator("bounded_metadata", mode="before")
    @classmethod
    def _bounded_metadata_is_canonical(
        cls, value: Any
    ) -> dict[str, RuntimeMetadataValue]:
        return _canonical_metadata(value)

    @field_validator("observed_at")
    @classmethod
    def _observed_at_is_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("runtime_event_observed_at_timezone_required")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _identity_is_coherent(self) -> Self:
        _validate_cross_field_identity(self)
        return self

    @property
    def source_event_key(self) -> RuntimeSourceEventKey:
        """Return the original descriptor-scoped source sequencing identity."""

        return (self.runtime_attempt_id, self.source_sequence_no)

    def canonical_bytes(self) -> bytes:
        """Return compact deterministic bytes for the canonical Atlas event."""

        return _canonical_bytes(self)

    @property
    def canonical_fingerprint(self) -> str:
        """SHA-256 of the complete canonical Atlas event."""

        return hashlib.sha256(self.canonical_bytes()).hexdigest()
