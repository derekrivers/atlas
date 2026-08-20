"""Immutable identity and capability contract for one runtime source."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, ClassVar, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RUNTIME_SOURCE_SCHEMA_VERSION: Literal["runtime-source-descriptor/v1"] = (
    "runtime-source-descriptor/v1"
)
MAX_RUNTIME_SOURCE_ID_LENGTH = 128
MAX_PRODUCT_SCOPE_LENGTH = 128
MAX_RUNTIME_VERSION_LENGTH = 128

_CANONICAL_IDENTIFIER = re.compile(r"^[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?$")
_RUNTIME_VERSION = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._+/-]*[A-Za-z0-9])?$")
_GIT_SHA = re.compile(r"^[0-9a-fA-F]{40}$")
_SHA256_DIGEST = re.compile(r"^[0-9a-fA-F]{64}$")


class RuntimeProvider(StrEnum):
    """Closed Phase 16 source provider boundary."""

    SYMPHONY_CODEX_APP_SERVER = "symphony_codex_app_server"


class RuntimeSourceSequenceSemantics(StrEnum):
    """Closed source ordering contract for Phase 16 runtime events."""

    MONOTONIC_PER_RUNTIME_ATTEMPT_STARTING_AT_ONE = (
        "monotonic_per_runtime_attempt_starting_at_one"
    )


class RuntimeCapabilitySupport(StrEnum):
    """Explicit observability status; omission is never a support signal."""

    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNINSTRUMENTED = "uninstrumented"


class RuntimeEventFamily(StrEnum):
    """Bounded durable event families from the Phase 16 v1 design."""

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


class RuntimeIdentityField(StrEnum):
    """Separate identities in the Phase 16 required runtime identity stack."""

    PRODUCT_ID = "product_id"
    ATLAS_TICKET_ID = "atlas_ticket_id"
    EXTERNAL_ISSUE_ID = "external_issue_id"
    AGENT_RUN_ID = "agent_run_id"
    RUNTIME_ATTEMPT_ID = "runtime_attempt_id"
    CODEX_THREAD_ID = "codex_thread_id"
    CODEX_TURN_ID = "codex_turn_id"
    SESSION_ID = "session_id"
    SOURCE_SEQUENCE_NO = "source_sequence_no"


class RuntimeCoordinationChannel(StrEnum):
    """Closed coordination surfaces observable by a runtime source."""

    TYPED_HANDOFF = "typed_handoff"
    ARTIFACT = "artifact"
    INTERFACE = "interface"
    PEER_MESSAGE = "peer_message"


class _FrozenCapability(BaseModel):
    """Shared immutable configuration for typed capability declarations."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    support: RuntimeCapabilitySupport


class RuntimeEventFamilyCapability(_FrozenCapability):
    """One explicit event-family observability declaration."""

    event_family: RuntimeEventFamily


class RuntimeIdentityFieldCapability(_FrozenCapability):
    """One explicit runtime-identity observability declaration."""

    identity_field: RuntimeIdentityField


class RuntimeCoordinationChannelCapability(_FrozenCapability):
    """One explicit coordination-channel observability declaration."""

    coordination_channel: RuntimeCoordinationChannel


def _canonical_identifier(value: Any, *, name: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"runtime_source_{name}_invalid")
    if (
        not 1 <= len(value) <= max_length
        or _CANONICAL_IDENTIFIER.fullmatch(value) is None
    ):
        raise ValueError(f"runtime_source_{name}_invalid")
    return value


def _canonical_version(value: Any, *, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"runtime_source_{name}_invalid")
    if (
        not 1 <= len(value) <= MAX_RUNTIME_VERSION_LENGTH
        or _RUNTIME_VERSION.fullmatch(value) is None
    ):
        raise ValueError(f"runtime_source_{name}_invalid")
    return value


def _canonical_digest(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or _SHA256_DIGEST.fullmatch(value) is None:
        raise ValueError(f"runtime_source_{name}_invalid")
    return value.lower()


def _bounded_list(value: Any, *, name: str, expected_length: int) -> Any:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"runtime_source_{name}_list_invalid")
    if not 1 <= len(value) <= expected_length:
        raise ValueError(f"runtime_source_{name}_list_out_of_bounds")
    return value


def _duplicate_identities(values: Sequence[StrEnum]) -> tuple[str, ...]:
    return tuple(
        sorted({identity.value for identity in values if values.count(identity) > 1})
    )


def _missing_identities(
    values: Sequence[StrEnum], expected: Sequence[StrEnum]
) -> tuple[str, ...]:
    return tuple(sorted(member.value for member in expected if member not in values))


class RuntimeSourceDescriptor(BaseModel):
    """Canonical immutable descriptor for exactly one runtime configuration."""

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    _REQUIRED_SOURCE_IDENTITIES: ClassVar[frozenset[RuntimeIdentityField]] = frozenset(
        {
            RuntimeIdentityField.EXTERNAL_ISSUE_ID,
            RuntimeIdentityField.RUNTIME_ATTEMPT_ID,
            RuntimeIdentityField.SOURCE_SEQUENCE_NO,
        }
    )

    source_id: str = Field(min_length=1, max_length=MAX_RUNTIME_SOURCE_ID_LENGTH)
    schema_version: Literal["runtime-source-descriptor/v1"] = (
        RUNTIME_SOURCE_SCHEMA_VERSION
    )
    product_scope: str = Field(min_length=1, max_length=MAX_PRODUCT_SCOPE_LENGTH)
    runtime_provider: RuntimeProvider
    symphony_release_sha: str
    event_projector_version: str = Field(
        min_length=1, max_length=MAX_RUNTIME_VERSION_LENGTH
    )
    codex_cli_version: str = Field(min_length=1, max_length=MAX_RUNTIME_VERSION_LENGTH)
    codex_protocol_fingerprint: str
    source_sequence_semantics: RuntimeSourceSequenceSemantics
    supported_event_families: tuple[RuntimeEventFamilyCapability, ...]
    supported_identity_fields: tuple[RuntimeIdentityFieldCapability, ...]
    supported_coordination_channels: tuple[RuntimeCoordinationChannelCapability, ...]
    advertised_dynamic_tool_fingerprint: str | None = None
    mcp_inventory_fingerprint: str | None = None
    governed_channel_inventory_fingerprint: str | None = None
    created_at: datetime

    @field_validator("source_id", mode="before")
    @classmethod
    def _source_id_is_canonical(cls, value: Any) -> str:
        return _canonical_identifier(
            value,
            name="source_id",
            max_length=MAX_RUNTIME_SOURCE_ID_LENGTH,
        )

    @field_validator("product_scope", mode="before")
    @classmethod
    def _product_scope_is_canonical(cls, value: Any) -> str:
        return _canonical_identifier(
            value,
            name="product_scope",
            max_length=MAX_PRODUCT_SCOPE_LENGTH,
        )

    @field_validator("symphony_release_sha", mode="before")
    @classmethod
    def _symphony_release_sha_is_canonical(cls, value: Any) -> str:
        if not isinstance(value, str) or _GIT_SHA.fullmatch(value) is None:
            raise ValueError("runtime_source_symphony_release_sha_invalid")
        return value.lower()

    @field_validator("event_projector_version", "codex_cli_version", mode="before")
    @classmethod
    def _versions_are_canonical(cls, value: Any, info: Any) -> str:
        return _canonical_version(value, name=info.field_name)

    @field_validator(
        "codex_protocol_fingerprint",
        "advertised_dynamic_tool_fingerprint",
        "mcp_inventory_fingerprint",
        "governed_channel_inventory_fingerprint",
        mode="before",
    )
    @classmethod
    def _fingerprints_are_canonical(cls, value: Any, info: Any) -> str | None:
        if value is None and info.field_name != "codex_protocol_fingerprint":
            return None
        return _canonical_digest(value, name=info.field_name)

    @field_validator("supported_event_families", mode="before")
    @classmethod
    def _event_family_list_is_bounded(cls, value: Any) -> Any:
        return _bounded_list(
            value,
            name="supported_event_families",
            expected_length=len(RuntimeEventFamily),
        )

    @field_validator("supported_identity_fields", mode="before")
    @classmethod
    def _identity_field_list_is_bounded(cls, value: Any) -> Any:
        return _bounded_list(
            value,
            name="supported_identity_fields",
            expected_length=len(RuntimeIdentityField),
        )

    @field_validator("supported_coordination_channels", mode="before")
    @classmethod
    def _coordination_channel_list_is_bounded(cls, value: Any) -> Any:
        return _bounded_list(
            value,
            name="supported_coordination_channels",
            expected_length=len(RuntimeCoordinationChannel),
        )

    @field_validator("supported_event_families")
    @classmethod
    def _event_families_are_canonical(
        cls, value: tuple[RuntimeEventFamilyCapability, ...]
    ) -> tuple[RuntimeEventFamilyCapability, ...]:
        return tuple(sorted(value, key=lambda item: item.event_family.value))

    @field_validator("supported_identity_fields")
    @classmethod
    def _identity_fields_are_canonical(
        cls, value: tuple[RuntimeIdentityFieldCapability, ...]
    ) -> tuple[RuntimeIdentityFieldCapability, ...]:
        return tuple(sorted(value, key=lambda item: item.identity_field.value))

    @field_validator("supported_coordination_channels")
    @classmethod
    def _coordination_channels_are_canonical(
        cls, value: tuple[RuntimeCoordinationChannelCapability, ...]
    ) -> tuple[RuntimeCoordinationChannelCapability, ...]:
        return tuple(sorted(value, key=lambda item: item.coordination_channel.value))

    @field_validator("created_at")
    @classmethod
    def _created_at_is_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("runtime_source_created_at_timezone_required")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _capability_sets_are_complete_and_coherent(self) -> Self:
        event_identities = [item.event_family for item in self.supported_event_families]
        identity_identities = [
            item.identity_field for item in self.supported_identity_fields
        ]
        coordination_identities = [
            item.coordination_channel for item in self.supported_coordination_channels
        ]

        capability_sets: tuple[
            tuple[str, Sequence[StrEnum], Sequence[StrEnum]], ...
        ] = (
            (
                "supported_event_families",
                event_identities,
                tuple(RuntimeEventFamily),
            ),
            (
                "supported_identity_fields",
                identity_identities,
                tuple(RuntimeIdentityField),
            ),
            (
                "supported_coordination_channels",
                coordination_identities,
                tuple(RuntimeCoordinationChannel),
            ),
        )
        for name, identities, expected in capability_sets:
            duplicates = _duplicate_identities(identities)
            if duplicates:
                raise ValueError(
                    f"runtime_source_{name}_duplicate:" + ",".join(duplicates)
                )
            missing = _missing_identities(identities, expected)
            if missing:
                raise ValueError(
                    f"runtime_source_{name}_incomplete:" + ",".join(missing)
                )

        event_support = {
            item.event_family: item.support for item in self.supported_event_families
        }
        if RuntimeCapabilitySupport.SUPPORTED not in event_support.values():
            raise ValueError("runtime_source_event_family_support_empty")

        identity_support = {
            item.identity_field: item.support for item in self.supported_identity_fields
        }
        unsupported_required = tuple(
            sorted(
                identity.value
                for identity in self._REQUIRED_SOURCE_IDENTITIES
                if identity_support[identity] is not RuntimeCapabilitySupport.SUPPORTED
            )
        )
        if unsupported_required:
            raise ValueError(
                "runtime_source_required_identity_not_supported:"
                + ",".join(unsupported_required)
            )

        coordination_support = {
            item.coordination_channel: item.support
            for item in self.supported_coordination_channels
        }
        if (
            event_support[RuntimeEventFamily.PEER_MESSAGE]
            is not coordination_support[RuntimeCoordinationChannel.PEER_MESSAGE]
        ):
            raise ValueError("runtime_source_peer_message_capability_conflict")
        return self

    def canonical_bytes(self) -> bytes:
        """Return compact canonical bytes for the complete source identity."""

        rendered = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        return rendered.encode("utf-8")

    @property
    def fingerprint(self) -> str:
        """Return the SHA-256 fingerprint of :meth:`canonical_bytes`."""

        return hashlib.sha256(self.canonical_bytes()).hexdigest()
