"""Provider-neutral planner execution and call-telemetry contracts.

These immutable contracts describe the evidence hierarchy only.  They do not
invoke a provider, persist telemetry, price usage, or alter ``PlanRun``.  Raw
prompt/response bodies and extensible metadata are deliberately absent so the
contract cannot become an accidental secret or payload store.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

PLANNING_EXECUTION_SCHEMA_VERSION: Literal["planning-execution/v1"] = (
    "planning-execution/v1"
)
PLANNER_LOGICAL_CALL_SCHEMA_VERSION: Literal["planner-logical-call/v1"] = (
    "planner-logical-call/v1"
)
PLANNER_PHYSICAL_ATTEMPT_SCHEMA_VERSION: Literal[
    "planner-physical-transport-attempt/v1"
] = "planner-physical-transport-attempt/v1"
PLANNING_EXECUTION_OUTCOME_SCHEMA_VERSION: Literal["planning-execution-outcome/v1"] = (
    "planning-execution-outcome/v1"
)

MAX_IDENTITY_NAME_LENGTH = 512
MAX_PROVIDER_VALUE_LENGTH = 256
MAX_STAGE_LENGTH = 128
MAX_PROMPT_SEGMENTS = 128
MAX_INPUT_IDENTITIES = 2048
MAX_PROMPT_TEMPLATES = 64
MAX_LOGICAL_CALLS = 4096
MAX_PHYSICAL_ATTEMPTS = 64
MAX_COUNTER = 2**63 - 1

_STAGE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?$")
_SAFE_IDENTITY = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._:/@+-]*[A-Za-z0-9])?$")
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_GIT_SHA1 = re.compile(r"^[0-9a-fA-F]{40}$")
_SENSITIVE_IDENTITY_PARTS = frozenset(
    {
        "access_token",
        "api_key",
        "auth_token",
        "bearer_token",
        "command_line",
        "cookie",
        "credential",
        "environment",
        "password",
        "private_key",
        "raw_prompt",
        "raw_response",
        "secret",
    }
)


class _CanonicalContract(BaseModel):
    """Deeply immutable JSON contract with deterministic serialization."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        hide_input_in_errors=True,
        protected_namespaces=(),
    )

    def canonical_bytes(self) -> bytes:
        """Return compact, key-sorted JSON for the complete contract."""

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


def _canonical_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("planner_telemetry_timezone_required")
    return value.astimezone(UTC)


def _safe_identity(value: Any, *, field_name: str, max_length: int) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= max_length
        or _SAFE_IDENTITY.fullmatch(value) is None
    ):
        raise ValueError(f"planner_telemetry_{field_name}_invalid")
    return value


def _safe_input_name(value: Any) -> str:
    name = _safe_identity(
        value,
        field_name="input_name",
        max_length=MAX_IDENTITY_NAME_LENGTH,
    )
    normalised = name.casefold().replace("-", "_").replace("/", "_")
    if any(part in normalised for part in _SENSITIVE_IDENTITY_PARTS):
        raise ValueError("planner_telemetry_input_name_sensitive")
    return name


class PlannerDigestAlgorithm(StrEnum):
    """Closed digest algorithms admitted for deterministic planning inputs."""

    SHA256 = "sha256"
    GIT_SHA1 = "git_sha1"


class ProviderEvidenceAvailability(StrEnum):
    """Whether a provider supplied one exact usage value."""

    REPORTED = "reported"
    UNSUPPORTED = "unsupported"
    UNAVAILABLE = "unavailable"


class MeasurementAvailability(StrEnum):
    """Whether Atlas measured one optional timing value."""

    MEASURED = "measured"
    UNSUPPORTED = "unsupported"
    UNAVAILABLE = "unavailable"


class PlannerTransportDisposition(StrEnum):
    """Terminal transport result for one physical provider request."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"


class PlannerRetryCategory(StrEnum):
    """Bounded reason a failed physical request may lead to another attempt."""

    NONE = "none"
    CONNECTION = "connection"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    PROVIDER_OVERLOADED = "provider_overloaded"
    PROVIDER_ERROR = "provider_error"
    UNKNOWN = "unknown"


class PlannerProcessingDisposition(StrEnum):
    """Outcome of a post-response processing boundary."""

    NOT_REACHED = "not_reached"
    PASSED = "passed"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"


class PlanningExecutionOutcomeStatus(StrEnum):
    """Terminal vocabulary for execution, intentionally unrelated to apply."""

    COMPLETED = "completed"
    FAILED = "failed"


class PlanningExecutionFailureStage(StrEnum):
    """Bounded stage at which a terminal planning execution failed."""

    PROVIDER_BEFORE_OUTPUT = "provider_before_output"
    OUTPUT = "output"
    PARSE = "parse"
    SCHEMA = "schema"
    GATE = "gate"
    ORCHESTRATION = "orchestration"


class PlanningExecutionIdentity(_CanonicalContract):
    """Durable identity created only after deterministic preflight succeeds."""

    execution_id: UUID


class PlannerLogicalCallIdentity(_CanonicalContract):
    """Composite logical-call identity beneath one execution and stage."""

    execution: PlanningExecutionIdentity
    stage: str = Field(min_length=1, max_length=MAX_STAGE_LENGTH)
    logical_attempt_no: StrictInt = Field(ge=1, le=MAX_COUNTER)

    @field_validator("stage", mode="before")
    @classmethod
    def _stage_is_canonical(cls, value: Any) -> str:
        if (
            not isinstance(value, str)
            or _STAGE.fullmatch(value) is None
            or len(value) > MAX_STAGE_LENGTH
        ):
            raise ValueError("planner_telemetry_stage_invalid")
        return value


class PlannerPhysicalAttemptIdentity(_CanonicalContract):
    """Composite physical-attempt identity beneath one logical call."""

    logical_call: PlannerLogicalCallIdentity
    physical_attempt_no: StrictInt = Field(ge=1, le=MAX_COUNTER)


class PlannerInputIdentity(_CanonicalContract):
    """One named, exact, non-secret deterministic input digest."""

    name: str = Field(min_length=1, max_length=MAX_IDENTITY_NAME_LENGTH)
    algorithm: PlannerDigestAlgorithm
    digest: str

    @field_validator("name", mode="before")
    @classmethod
    def _name_is_bounded_and_non_sensitive(cls, value: Any) -> str:
        return _safe_input_name(value)

    @field_validator("digest", mode="before")
    @classmethod
    def _digest_is_canonical_hex(cls, value: Any) -> Any:
        return value.lower() if isinstance(value, str) else value

    @model_validator(mode="after")
    def _digest_matches_algorithm(self) -> Self:
        pattern = {
            PlannerDigestAlgorithm.SHA256: _SHA256,
            PlannerDigestAlgorithm.GIT_SHA1: _GIT_SHA1,
        }[self.algorithm]
        if not isinstance(self.digest, str) or pattern.fullmatch(self.digest) is None:
            raise ValueError("planner_telemetry_input_digest_invalid")
        return self


class PlannerIdentity(_CanonicalContract):
    """Provider-neutral planner provider and model identity."""

    provider: str = Field(min_length=1, max_length=MAX_PROVIDER_VALUE_LENGTH)
    model: str = Field(min_length=1, max_length=MAX_PROVIDER_VALUE_LENGTH)

    @field_validator("provider", "model", mode="before")
    @classmethod
    def _identity_is_safe(cls, value: Any, info: Any) -> str:
        return _safe_identity(
            value,
            field_name=info.field_name,
            max_length=MAX_PROVIDER_VALUE_LENGTH,
        )


class PlannerPromptTemplateIdentity(_CanonicalContract):
    """Exact versioned template artifact selected before provider calls."""

    stage: str = Field(min_length=1, max_length=MAX_STAGE_LENGTH)
    template_name: str = Field(min_length=1, max_length=MAX_IDENTITY_NAME_LENGTH)
    prompt_version: str = Field(min_length=1, max_length=MAX_PROVIDER_VALUE_LENGTH)
    template_sha256: str

    @field_validator("stage", mode="before")
    @classmethod
    def _stage_is_canonical(cls, value: Any) -> str:
        if (
            not isinstance(value, str)
            or _STAGE.fullmatch(value) is None
            or len(value) > MAX_STAGE_LENGTH
        ):
            raise ValueError("planner_telemetry_stage_invalid")
        return value

    @field_validator("template_name", "prompt_version", mode="before")
    @classmethod
    def _template_identity_is_safe(cls, value: Any, info: Any) -> str:
        maximum = (
            MAX_IDENTITY_NAME_LENGTH
            if info.field_name == "template_name"
            else MAX_PROVIDER_VALUE_LENGTH
        )
        return _safe_identity(value, field_name=info.field_name, max_length=maximum)

    @field_validator("template_sha256", mode="before")
    @classmethod
    def _template_digest_is_sha256(cls, value: Any) -> str:
        if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
            raise ValueError("planner_telemetry_template_sha256_invalid")
        return value.lower()


class PlannerExecutionParameters(_CanonicalContract):
    """Bounded provider-neutral request settings, with no arbitrary metadata."""

    temperature: float | None = Field(default=None, ge=0)
    max_output_tokens: StrictInt | None = Field(default=None, ge=1, le=MAX_COUNTER)
    top_p: float | None = Field(default=None, ge=0, le=1)
    top_k: StrictInt | None = Field(default=None, ge=1, le=MAX_COUNTER)
    seed: StrictInt | None = Field(default=None, ge=-MAX_COUNTER, le=MAX_COUNTER)
    request_timeout_ms: StrictInt | None = Field(default=None, ge=1, le=MAX_COUNTER)
    streaming: bool

    @field_validator("temperature", "top_p", mode="before")
    @classmethod
    def _float_is_finite_and_strict(cls, value: Any) -> Any:
        if value is None:
            return value
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("planner_telemetry_parameter_must_be_numeric")
        if not math.isfinite(value):
            raise ValueError("planner_telemetry_parameter_must_be_finite")
        return float(value)

    @field_validator(
        "max_output_tokens", "top_k", "seed", "request_timeout_ms", mode="before"
    )
    @classmethod
    def _integer_is_strict(cls, value: Any) -> Any:
        if value is not None and type(value) is not int:
            raise ValueError("planner_telemetry_parameter_must_be_integer")
        return value

    @field_validator("streaming", mode="before")
    @classmethod
    def _streaming_is_strict_boolean(cls, value: Any) -> Any:
        if type(value) is not bool:
            raise ValueError("planner_telemetry_streaming_must_be_boolean")
        return value


class PlannerPayloadSize(_CanonicalContract):
    """Exact UTF-8 byte and Unicode character counts for one payload."""

    byte_count: StrictInt = Field(ge=0, le=MAX_COUNTER)
    character_count: StrictInt = Field(ge=0, le=MAX_COUNTER)

    @model_validator(mode="after")
    def _utf8_bytes_cover_characters(self) -> Self:
        if self.byte_count < self.character_count:
            raise ValueError("planner_telemetry_byte_count_below_character_count")
        return self


class PlannerPromptSegmentSize(PlannerPayloadSize):
    """Exact size of one named, non-content prompt segment."""

    name: str = Field(min_length=1, max_length=MAX_PROVIDER_VALUE_LENGTH)

    @field_validator("name", mode="before")
    @classmethod
    def _segment_name_is_safe(cls, value: Any) -> str:
        return _safe_identity(
            value,
            field_name="prompt_segment_name",
            max_length=MAX_PROVIDER_VALUE_LENGTH,
        )


class ProviderUsageValue(_CanonicalContract):
    """One honest provider token value; missing never means zero."""

    availability: ProviderEvidenceAvailability = (
        ProviderEvidenceAvailability.UNAVAILABLE
    )
    value: StrictInt | None = Field(default=None, ge=0, le=MAX_COUNTER)

    @model_validator(mode="after")
    def _availability_matches_value(self) -> Self:
        if self.availability is ProviderEvidenceAvailability.REPORTED:
            if self.value is None:
                raise ValueError("planner_telemetry_reported_usage_requires_value")
        elif self.value is not None:
            raise ValueError("planner_telemetry_missing_usage_cannot_have_value")
        return self


class OptionalTimingValue(_CanonicalContract):
    """One optional millisecond timing with explicit missing semantics."""

    availability: MeasurementAvailability = MeasurementAvailability.UNAVAILABLE
    value_ms: StrictInt | None = Field(default=None, ge=0, le=MAX_COUNTER)

    @model_validator(mode="after")
    def _availability_matches_value(self) -> Self:
        if self.availability is MeasurementAvailability.MEASURED:
            if self.value_ms is None:
                raise ValueError("planner_telemetry_measured_timing_requires_value")
        elif self.value_ms is not None:
            raise ValueError("planner_telemetry_missing_timing_cannot_have_value")
        return self


class PlannerProviderUsage(_CanonicalContract):
    """Closed provider usage families; every absent family stays explicit."""

    input_tokens: ProviderUsageValue = Field(default_factory=ProviderUsageValue)
    output_tokens: ProviderUsageValue = Field(default_factory=ProviderUsageValue)
    cache_creation_input_tokens: ProviderUsageValue = Field(
        default_factory=ProviderUsageValue
    )
    cache_read_input_tokens: ProviderUsageValue = Field(
        default_factory=ProviderUsageValue
    )
    reasoning_tokens: ProviderUsageValue = Field(default_factory=ProviderUsageValue)


class PlannerPostResponseDisposition(_CanonicalContract):
    """Parse, schema, and gate outcomes for one provider response."""

    parse: PlannerProcessingDisposition = PlannerProcessingDisposition.NOT_REACHED
    schema_validation: PlannerProcessingDisposition = (
        PlannerProcessingDisposition.NOT_REACHED
    )
    gate: PlannerProcessingDisposition = PlannerProcessingDisposition.NOT_REACHED

    @model_validator(mode="after")
    def _processing_order_is_coherent(self) -> Self:
        if self.parse is not PlannerProcessingDisposition.PASSED:
            if (
                self.schema_validation is not PlannerProcessingDisposition.NOT_REACHED
                or self.gate is not PlannerProcessingDisposition.NOT_REACHED
            ):
                raise ValueError("planner_telemetry_parse_disposition_contradictory")
            return self
        if self.schema_validation is PlannerProcessingDisposition.FAILED:
            if self.gate is not PlannerProcessingDisposition.NOT_REACHED:
                raise ValueError("planner_telemetry_schema_disposition_contradictory")
            return self
        if self.schema_validation is PlannerProcessingDisposition.NOT_REACHED:
            if self.gate is not PlannerProcessingDisposition.NOT_REACHED:
                raise ValueError("planner_telemetry_gate_disposition_contradictory")
            return self
        return self


class PlannerPhysicalTransportAttempt(_CanonicalContract):
    """Bounded terminal evidence for one physical provider request."""

    schema_version: Literal["planner-physical-transport-attempt/v1"] = (
        PLANNER_PHYSICAL_ATTEMPT_SCHEMA_VERSION
    )
    identity: PlannerPhysicalAttemptIdentity
    started_at: datetime
    transport_disposition: PlannerTransportDisposition
    wall_latency_ms: StrictInt = Field(ge=0, le=MAX_COUNTER)
    time_to_first_token: OptionalTimingValue = Field(
        default_factory=OptionalTimingValue
    )
    retry_category: PlannerRetryCategory
    output_size: PlannerPayloadSize | None = None
    provider_usage: PlannerProviderUsage = Field(default_factory=PlannerProviderUsage)
    stop_reason: str | None = Field(
        default=None, min_length=1, max_length=MAX_PROVIDER_VALUE_LENGTH
    )
    processing: PlannerPostResponseDisposition = Field(
        default_factory=PlannerPostResponseDisposition
    )
    resulting_plan_run_id: UUID | None = None

    @field_validator("started_at", mode="after")
    @classmethod
    def _started_at_is_utc(cls, value: datetime) -> datetime:
        return _canonical_datetime(value)

    @field_validator("stop_reason", mode="before")
    @classmethod
    def _stop_reason_is_safe(cls, value: Any) -> str | None:
        if value is None:
            return None
        return _safe_identity(
            value,
            field_name="stop_reason",
            max_length=MAX_PROVIDER_VALUE_LENGTH,
        )

    @model_validator(mode="after")
    def _transport_evidence_is_coherent(self) -> Self:
        if self.transport_disposition is PlannerTransportDisposition.SUCCEEDED:
            if self.output_size is None:
                raise ValueError("planner_telemetry_success_requires_output_size")
            if self.retry_category is not PlannerRetryCategory.NONE:
                raise ValueError("planner_telemetry_success_cannot_request_retry")
        else:
            if self.output_size is not None or self.stop_reason is not None:
                raise ValueError("planner_telemetry_failed_transport_has_no_output")
            if self.processing != PlannerPostResponseDisposition():
                raise ValueError("planner_telemetry_failed_transport_not_processable")
        return self


class PlannerLogicalCall(_CanonicalContract):
    """One logical planner call and its ordered physical transport attempts."""

    schema_version: Literal["planner-logical-call/v1"] = (
        PLANNER_LOGICAL_CALL_SCHEMA_VERSION
    )
    identity: PlannerLogicalCallIdentity
    planner: PlannerIdentity
    template: PlannerPromptTemplateIdentity
    execution_parameters: PlannerExecutionParameters
    input_identities: tuple[PlannerInputIdentity, ...] = Field(
        min_length=1, max_length=MAX_INPUT_IDENTITIES
    )
    prompt_size: PlannerPayloadSize
    prompt_segments: tuple[PlannerPromptSegmentSize, ...] = Field(
        min_length=1, max_length=MAX_PROMPT_SEGMENTS
    )
    physical_attempts: tuple[PlannerPhysicalTransportAttempt, ...] = Field(
        default=(), max_length=MAX_PHYSICAL_ATTEMPTS
    )

    @field_validator("input_identities", mode="after")
    @classmethod
    def _inputs_are_canonical(
        cls, value: tuple[PlannerInputIdentity, ...]
    ) -> tuple[PlannerInputIdentity, ...]:
        names = [item.name for item in value]
        if len(names) != len(set(names)):
            raise ValueError("planner_telemetry_input_identities_duplicate")
        return tuple(sorted(value, key=lambda item: item.name))

    @field_validator("prompt_segments", mode="after")
    @classmethod
    def _segments_are_canonical(
        cls, value: tuple[PlannerPromptSegmentSize, ...]
    ) -> tuple[PlannerPromptSegmentSize, ...]:
        names = [item.name for item in value]
        if len(names) != len(set(names)):
            raise ValueError("planner_telemetry_prompt_segments_duplicate")
        return tuple(sorted(value, key=lambda item: item.name))

    @field_validator("physical_attempts", mode="after")
    @classmethod
    def _attempts_are_canonical(
        cls, value: tuple[PlannerPhysicalTransportAttempt, ...]
    ) -> tuple[PlannerPhysicalTransportAttempt, ...]:
        return tuple(
            sorted(
                value,
                key=lambda attempt: attempt.identity.physical_attempt_no,
            )
        )

    @model_validator(mode="after")
    def _logical_hierarchy_is_coherent(self) -> Self:
        if self.template.stage != self.identity.stage:
            raise ValueError("planner_telemetry_template_stage_mismatch")
        if self.prompt_size.character_count == 0:
            raise ValueError("planner_telemetry_prompt_must_be_non_empty")
        if sum(item.byte_count for item in self.prompt_segments) > (
            self.prompt_size.byte_count
        ) or sum(item.character_count for item in self.prompt_segments) > (
            self.prompt_size.character_count
        ):
            raise ValueError("planner_telemetry_prompt_segments_exceed_prompt")

        expected_numbers = list(range(1, len(self.physical_attempts) + 1))
        actual_numbers = [
            attempt.identity.physical_attempt_no for attempt in self.physical_attempts
        ]
        if actual_numbers != expected_numbers:
            raise ValueError("planner_telemetry_physical_attempts_not_contiguous")
        for attempt in self.physical_attempts:
            if attempt.identity.logical_call != self.identity:
                raise ValueError("planner_telemetry_physical_hierarchy_mismatch")
        successful = [
            attempt
            for attempt in self.physical_attempts
            if attempt.transport_disposition is PlannerTransportDisposition.SUCCEEDED
        ]
        if len(successful) > 1:
            raise ValueError("planner_telemetry_multiple_successful_transports")
        if successful and successful[0] is not self.physical_attempts[-1]:
            raise ValueError("planner_telemetry_successful_transport_not_final")
        return self


class PlanningExecutionOutcome(_CanonicalContract):
    """Terminal execution outcome and independently optional PlanRun link."""

    schema_version: Literal["planning-execution-outcome/v1"] = (
        PLANNING_EXECUTION_OUTCOME_SCHEMA_VERSION
    )
    status: PlanningExecutionOutcomeStatus
    completed_at: datetime
    raw_output_observed: bool = Field(
        description="Whether any physical call in the execution returned raw output."
    )
    failure_stage: PlanningExecutionFailureStage | None = Field(
        default=None,
        description="Terminal failure boundary, when status is failed.",
    )
    resulting_plan_run_id: UUID | None = Field(
        default=None,
        description=(
            "Exact PlanRun produced by this execution, when one legally exists."
        ),
    )

    @field_validator("completed_at", mode="after")
    @classmethod
    def _completed_at_is_utc(cls, value: datetime) -> datetime:
        return _canonical_datetime(value)

    @field_validator("raw_output_observed", mode="before")
    @classmethod
    def _raw_output_flag_is_strict(cls, value: Any) -> Any:
        if type(value) is not bool:
            raise ValueError("planner_telemetry_raw_output_flag_must_be_boolean")
        return value

    @model_validator(mode="after")
    def _plan_run_boundary_is_coherent(self) -> Self:
        if self.status is PlanningExecutionOutcomeStatus.COMPLETED:
            if (
                not self.raw_output_observed
                or self.failure_stage is not None
                or self.resulting_plan_run_id is None
            ):
                raise ValueError("planner_telemetry_completed_outcome_invalid")
            return self

        if self.failure_stage is None:
            raise ValueError("planner_telemetry_failed_outcome_requires_stage")
        if not self.raw_output_observed and self.resulting_plan_run_id is not None:
            raise ValueError("planner_telemetry_post_output_plan_run_mismatch")
        if (
            self.failure_stage is PlanningExecutionFailureStage.PROVIDER_BEFORE_OUTPUT
            and self.resulting_plan_run_id is not None
        ):
            raise ValueError("planner_telemetry_provider_pre_output_has_plan_run")
        if (
            self.failure_stage
            is not PlanningExecutionFailureStage.PROVIDER_BEFORE_OUTPUT
            and self.raw_output_observed != (self.resulting_plan_run_id is not None)
        ):
            raise ValueError("planner_telemetry_post_output_plan_run_mismatch")
        return self


class PlanningExecution(_CanonicalContract):
    """One immutable post-preflight execution/evidence hierarchy.

    ``outcome=None`` is an honest non-terminal record, including after process
    interruption.  This contract has no apply/reject vocabulary and is never
    an ``atlas apply`` input.
    """

    schema_version: Literal["planning-execution/v1"] = PLANNING_EXECUTION_SCHEMA_VERSION
    identity: PlanningExecutionIdentity
    product_id: UUID
    preflight_completed_at: datetime
    created_at: datetime
    planner: PlannerIdentity
    execution_parameters: PlannerExecutionParameters
    input_identities: tuple[PlannerInputIdentity, ...] = Field(
        min_length=1, max_length=MAX_INPUT_IDENTITIES
    )
    prompt_templates: tuple[PlannerPromptTemplateIdentity, ...] = Field(
        min_length=1, max_length=MAX_PROMPT_TEMPLATES
    )
    logical_calls: tuple[PlannerLogicalCall, ...] = Field(
        default=(), max_length=MAX_LOGICAL_CALLS
    )
    outcome: PlanningExecutionOutcome | None = None

    @field_validator("preflight_completed_at", "created_at", mode="after")
    @classmethod
    def _timestamps_are_utc(cls, value: datetime) -> datetime:
        return _canonical_datetime(value)

    @field_validator("input_identities", mode="after")
    @classmethod
    def _inputs_are_canonical(
        cls, value: tuple[PlannerInputIdentity, ...]
    ) -> tuple[PlannerInputIdentity, ...]:
        names = [item.name for item in value]
        if len(names) != len(set(names)):
            raise ValueError("planner_telemetry_input_identities_duplicate")
        return tuple(sorted(value, key=lambda item: item.name))

    @field_validator("prompt_templates", mode="after")
    @classmethod
    def _templates_are_canonical(
        cls, value: tuple[PlannerPromptTemplateIdentity, ...]
    ) -> tuple[PlannerPromptTemplateIdentity, ...]:
        keys = [(item.stage, item.template_name, item.prompt_version) for item in value]
        if len(keys) != len(set(keys)):
            raise ValueError("planner_telemetry_prompt_templates_duplicate")
        return tuple(
            sorted(
                value,
                key=lambda item: (
                    item.stage,
                    item.template_name,
                    item.prompt_version,
                ),
            )
        )

    @field_validator("logical_calls", mode="after")
    @classmethod
    def _logical_calls_are_canonical(
        cls, value: tuple[PlannerLogicalCall, ...]
    ) -> tuple[PlannerLogicalCall, ...]:
        return tuple(
            sorted(
                value,
                key=lambda call: (
                    call.identity.stage,
                    call.identity.logical_attempt_no,
                ),
            )
        )

    @model_validator(mode="after")
    def _execution_hierarchy_is_coherent(self) -> Self:
        if self.created_at < self.preflight_completed_at:
            raise ValueError("planner_telemetry_execution_precedes_preflight")

        templates = set(self.prompt_templates)
        source_inputs = {item.name: item for item in self.input_identities}
        numbers_by_stage: dict[str, list[int]] = {}
        successful_attempts: list[PlannerPhysicalTransportAttempt] = []
        for call in self.logical_calls:
            if call.identity.execution != self.identity:
                raise ValueError("planner_telemetry_logical_hierarchy_mismatch")
            if call.planner != self.planner:
                raise ValueError("planner_telemetry_planner_identity_mismatch")
            if call.execution_parameters != self.execution_parameters:
                raise ValueError("planner_telemetry_execution_parameters_mismatch")
            if call.template not in templates:
                raise ValueError("planner_telemetry_template_not_frozen")
            for call_input in call.input_identities:
                frozen_input = source_inputs.get(call_input.name)
                if frozen_input is not None and call_input != frozen_input:
                    raise ValueError("planner_telemetry_call_input_identity_mismatch")
            numbers_by_stage.setdefault(call.identity.stage, []).append(
                call.identity.logical_attempt_no
            )
            for attempt in call.physical_attempts:
                if attempt.started_at < self.created_at:
                    raise ValueError("planner_telemetry_attempt_precedes_execution")
                if (
                    attempt.transport_disposition
                    is PlannerTransportDisposition.SUCCEEDED
                ):
                    successful_attempts.append(attempt)

        for numbers in numbers_by_stage.values():
            if numbers != list(range(1, len(numbers) + 1)):
                raise ValueError("planner_telemetry_logical_attempts_not_contiguous")

        resulting_plan_run_id = (
            self.outcome.resulting_plan_run_id if self.outcome is not None else None
        )
        for call in self.logical_calls:
            for attempt in call.physical_attempts:
                link = attempt.resulting_plan_run_id
                if link is not None and link != resulting_plan_run_id:
                    raise ValueError("planner_telemetry_attempt_plan_run_mismatch")

        if self.outcome is not None:
            if self.outcome.completed_at < self.created_at:
                raise ValueError("planner_telemetry_outcome_precedes_execution")
            for call in self.logical_calls:
                for attempt in call.physical_attempts:
                    attempt_finished_at = attempt.started_at + timedelta(
                        milliseconds=attempt.wall_latency_ms
                    )
                    if self.outcome.completed_at < attempt_finished_at:
                        raise ValueError("planner_telemetry_outcome_precedes_attempt")
            if self.outcome.raw_output_observed and not successful_attempts:
                raise ValueError("planner_telemetry_raw_output_without_successful_call")
            if not self.outcome.raw_output_observed and successful_attempts:
                raise ValueError("planner_telemetry_successful_call_without_raw_output")
        return self
