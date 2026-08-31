"""Pure deterministic PM operational-health calculus.

This module deliberately owns no persistence, provider client, scheduler, CLI,
or recovery action.  It reduces bounded durable observations to one replayable
health assessment; future adapters may persist and present those inputs and
outputs without changing the calculation.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

MAX_SCHEMA_LENGTH = 64
MAX_NAME_LENGTH = 128
MAX_KEY_LENGTH = 128
MAX_EPISODE_ID_LENGTH = 128
MAX_BLOCKER_OBSERVATIONS = 128
MAX_STARVED_CANDIDATES = 128
MAX_HEALTH_REASONS = 512


class PmHealthStatus(StrEnum):
    """Operational health, ordered separately by ``_STATUS_RANK``."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    BLOCKED = "blocked"


class PmBlockerKind(StrEnum):
    """Bounded recovery posture of one durable PM observation."""

    ROUTINE_WAIT = "routine_wait"
    RETRYABLE = "retryable"
    UNRESOLVED_FENCE = "unresolved_fence"
    UNKNOWN = "unknown"


class PmHealthReasonCode(StrEnum):
    """Stable diagnostic codes emitted by :func:`assess_pm_health`."""

    ROUTINE_WAIT = "routine_wait"
    TRANSIENT_RETRY = "transient_retry"
    RECURRING_BLOCKER = "recurring_blocker"
    UNRESOLVED_FENCE = "unresolved_fence"
    CAPACITY_IMPACT = "capacity_impact"
    STARVATION = "starvation"
    HEARTBEAT_MISSING = "heartbeat_missing"
    HEARTBEAT_STALE = "heartbeat_stale"
    COHERENT_BOARD_MISSING = "coherent_board_missing"
    COHERENT_BOARD_STALE = "coherent_board_stale"
    CONVERGENCE_MISSING = "convergence_missing"
    CONVERGENCE_STALE = "convergence_stale"
    PROGRESS_MISSING = "progress_missing"
    PROGRESS_STALE = "progress_stale"
    UNKNOWN_OR_LEGACY_INPUT = "unknown_or_legacy_input"


_STATUS_RANK: dict[PmHealthStatus, int] = {
    PmHealthStatus.HEALTHY: 0,
    PmHealthStatus.DEGRADED: 1,
    PmHealthStatus.BLOCKED: 2,
}


def _normalise_time(value: datetime) -> datetime:
    return value.astimezone(UTC)


def _bounded_identifier(value: str, *, name: str) -> str:
    if not value:
        raise ValueError(f"{name} must not be empty")
    if value != value.strip():
        raise ValueError(f"{name} must not have surrounding whitespace")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{name} must not contain control characters")
    return value


class PmHealthPolicy(BaseModel):
    """Explicit thresholds for one operational-health policy revision.

    Defaults describe the initial contract, but callers must persist the exact
    policy/fingerprint with any authoritative assessment.  No threshold is a
    hidden module constant.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["pm-health-policy-v1"] = "pm-health-policy-v1"
    expected_cadence: timedelta = timedelta(seconds=60)
    heartbeat_stale_after: timedelta = timedelta(minutes=3)
    coherent_board_stale_after: timedelta = timedelta(minutes=3)
    convergence_degraded_after: timedelta = timedelta(minutes=5)
    convergence_blocked_after: timedelta = timedelta(minutes=15)
    progress_degraded_after: timedelta = timedelta(minutes=5)
    progress_blocked_after: timedelta = timedelta(minutes=15)
    routine_retry_window: timedelta = timedelta(minutes=1)
    retryable_degraded_after: int = Field(default=2, ge=1)
    retryable_blocked_after: int = Field(default=5, ge=1)
    starvation_blocked_after: timedelta = timedelta(minutes=5)

    @model_validator(mode="after")
    def _validate_thresholds(self) -> PmHealthPolicy:
        durations = (
            self.expected_cadence,
            self.heartbeat_stale_after,
            self.coherent_board_stale_after,
            self.convergence_degraded_after,
            self.convergence_blocked_after,
            self.progress_degraded_after,
            self.progress_blocked_after,
            self.routine_retry_window,
            self.starvation_blocked_after,
        )
        if any(duration <= timedelta(0) for duration in durations):
            raise ValueError("PM health durations must be positive")
        if self.heartbeat_stale_after < self.expected_cadence:
            raise ValueError("heartbeat freshness cannot be shorter than cadence")
        if self.coherent_board_stale_after < self.expected_cadence:
            raise ValueError("board freshness cannot be shorter than cadence")
        if self.convergence_blocked_after < self.convergence_degraded_after:
            raise ValueError("convergence blocked threshold precedes degradation")
        if self.progress_blocked_after < self.progress_degraded_after:
            raise ValueError("progress blocked threshold precedes degradation")
        if self.retryable_blocked_after < self.retryable_degraded_after:
            raise ValueError("retryable blocked threshold precedes degradation")
        return self

    def canonical_bytes(self) -> bytes:
        payload = self.model_dump(mode="json")
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


class PmBlockerObservation(BaseModel):
    """One bounded durable observation of a PM action that did not progress."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(
        default="pm-blocker-observation-v1", min_length=1, max_length=MAX_SCHEMA_LENGTH
    )
    operation: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    code: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    kind: PmBlockerKind
    authority_id: str = Field(min_length=1, max_length=MAX_NAME_LENGTH)
    episode_id: str = Field(min_length=1, max_length=MAX_EPISODE_ID_LENGTH)
    candidate_key: str | None = Field(default=None, max_length=MAX_KEY_LENGTH)
    first_observed_at: datetime
    last_observed_at: datetime
    consecutive_observations: int = Field(ge=1)
    next_safe_retry_at: datetime | None = None
    capacity_impact: bool = False
    starved_candidate_keys: tuple[str, ...] = Field(
        default=(), max_length=MAX_STARVED_CANDIDATES
    )
    starvation_started_at: datetime | None = None
    superseded_at: datetime | None = None

    @field_validator(
        "schema_version", "operation", "code", "authority_id", "episode_id"
    )
    @classmethod
    def _validate_identity(cls, value: str, info: ValidationInfo) -> str:
        return _bounded_identifier(value, name=info.field_name or "identity")

    @field_validator("candidate_key")
    @classmethod
    def _validate_candidate_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _bounded_identifier(value, name="candidate_key")

    @field_validator("starved_candidate_keys")
    @classmethod
    def _order_starved_keys(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(len(key) > MAX_KEY_LENGTH for key in value):
            raise ValueError("starved candidate key exceeds maximum length")
        return tuple(
            sorted(
                {
                    _bounded_identifier(key, name="starved_candidate_key")
                    for key in value
                }
            )
        )

    @model_validator(mode="after")
    def _validate_observation(self) -> PmBlockerObservation:
        timestamps = (
            self.first_observed_at,
            self.last_observed_at,
            self.next_safe_retry_at,
            self.starvation_started_at,
            self.superseded_at,
        )
        if any(value is not None and value.utcoffset() is None for value in timestamps):
            raise ValueError("PM blocker timestamps must be timezone-aware")
        if self.first_observed_at > self.last_observed_at:
            raise ValueError("blocker first observation follows its last observation")
        if self.starvation_started_at is not None and not self.starved_candidate_keys:
            raise ValueError("starvation start requires at least one starved candidate")
        if (
            self.superseded_at is not None
            and self.superseded_at < self.last_observed_at
        ):
            raise ValueError("blocker supersession precedes its last observation")
        return self

    def identity_bytes(self) -> bytes:
        """Canonical stable cause/episode identity, excluding mutable state."""

        payload = {
            "schema_version": self.schema_version,
            "operation": self.operation,
            "code": self.code,
            "kind": self.kind.value,
            "authority_id": self.authority_id,
            "episode_id": self.episode_id,
            "candidate_key": self.candidate_key,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.identity_bytes()).hexdigest()


class PmHealthInputs(BaseModel):
    """Complete bounded inputs for one health calculation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = Field(
        default="pm-health-inputs-v1", min_length=1, max_length=MAX_SCHEMA_LENGTH
    )
    observed_at: datetime
    last_heartbeat_at: datetime | None
    last_coherent_board_at: datetime | None
    last_convergence_at: datetime | None
    last_progress_at: datetime | None
    progress_expected: bool
    blocker_observations: tuple[PmBlockerObservation, ...] = Field(
        default=(), max_length=MAX_BLOCKER_OBSERVATIONS
    )

    @field_validator("schema_version")
    @classmethod
    def _validate_schema_version(cls, value: str) -> str:
        return _bounded_identifier(value, name="schema_version")

    @model_validator(mode="after")
    def _validate_times(self) -> PmHealthInputs:
        timestamps = (
            self.observed_at,
            self.last_heartbeat_at,
            self.last_coherent_board_at,
            self.last_convergence_at,
            self.last_progress_at,
        )
        if any(value is not None and value.utcoffset() is None for value in timestamps):
            raise ValueError("PM health input timestamps must be timezone-aware")
        return self


class PmHealthReason(BaseModel):
    """One deterministic bounded reason contributing to health."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: PmHealthReasonCode
    status: PmHealthStatus
    operation: str | None = Field(default=None, max_length=MAX_NAME_LENGTH)
    candidate_key: str | None = Field(default=None, max_length=MAX_KEY_LENGTH)
    blocker_fingerprint: str | None = Field(default=None, max_length=64)


class PmHealthAssessment(BaseModel):
    """Replayable PM health result with deterministic ordering and hashes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["pm-health-assessment-v1"] = "pm-health-assessment-v1"
    status: PmHealthStatus
    observed_at: datetime
    progress_expected: bool
    policy_fingerprint: str = Field(min_length=64, max_length=64)
    active_blocker_fingerprints: tuple[str, ...] = Field(
        max_length=MAX_BLOCKER_OBSERVATIONS
    )
    reasons: tuple[PmHealthReason, ...] = Field(max_length=MAX_HEALTH_REASONS)

    def canonical_bytes(self) -> bytes:
        payload = self.model_dump(mode="json")
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


def _reason_sort_key(reason: PmHealthReason) -> tuple[object, ...]:
    return (
        _STATUS_RANK[reason.status],
        reason.code.value,
        reason.operation or "",
        reason.candidate_key or "",
        reason.blocker_fingerprint or "",
    )


def _reason(
    code: PmHealthReasonCode,
    status: PmHealthStatus,
    blocker: PmBlockerObservation | None = None,
) -> PmHealthReason:
    return PmHealthReason(
        code=code,
        status=status,
        operation=None if blocker is None else blocker.operation,
        candidate_key=None if blocker is None else blocker.candidate_key,
        blocker_fingerprint=None if blocker is None else blocker.fingerprint,
    )


def _freshness_reason(
    *,
    observed_at: datetime,
    value: datetime | None,
    degraded_after: timedelta | None,
    blocked_after: timedelta,
    missing_code: PmHealthReasonCode,
    stale_code: PmHealthReasonCode,
    missing_status: PmHealthStatus,
) -> PmHealthReason | None:
    if value is None:
        return _reason(missing_code, missing_status)
    age = observed_at - value
    if age < timedelta(0):
        return _reason(
            PmHealthReasonCode.UNKNOWN_OR_LEGACY_INPUT,
            PmHealthStatus.BLOCKED,
        )
    if age >= blocked_after:
        return _reason(stale_code, PmHealthStatus.BLOCKED)
    if degraded_after is not None and age >= degraded_after:
        return _reason(stale_code, PmHealthStatus.DEGRADED)
    return None


def _blocker_reasons(
    blocker: PmBlockerObservation,
    *,
    observed_at: datetime,
    policy: PmHealthPolicy,
) -> list[PmHealthReason]:
    if blocker.schema_version != "pm-blocker-observation-v1":
        return [
            _reason(
                PmHealthReasonCode.UNKNOWN_OR_LEGACY_INPUT,
                PmHealthStatus.BLOCKED,
                blocker,
            )
        ]
    if (
        blocker.first_observed_at > observed_at
        or blocker.last_observed_at > observed_at
        or (
            blocker.starvation_started_at is not None
            and blocker.starvation_started_at > observed_at
        )
        or (blocker.superseded_at is not None and blocker.superseded_at > observed_at)
    ):
        return [
            _reason(
                PmHealthReasonCode.UNKNOWN_OR_LEGACY_INPUT,
                PmHealthStatus.BLOCKED,
                blocker,
            )
        ]
    if blocker.kind in {PmBlockerKind.UNRESOLVED_FENCE, PmBlockerKind.UNKNOWN}:
        code = (
            PmHealthReasonCode.UNRESOLVED_FENCE
            if blocker.kind is PmBlockerKind.UNRESOLVED_FENCE
            else PmHealthReasonCode.UNKNOWN_OR_LEGACY_INPUT
        )
        return [_reason(code, PmHealthStatus.BLOCKED, blocker)]

    reasons: list[PmHealthReason] = []
    if blocker.consecutive_observations >= policy.retryable_blocked_after:
        reasons.append(
            _reason(
                PmHealthReasonCode.RECURRING_BLOCKER,
                PmHealthStatus.BLOCKED,
                blocker,
            )
        )
    elif (
        blocker.kind is PmBlockerKind.RETRYABLE
        or blocker.consecutive_observations >= policy.retryable_degraded_after
        or blocker.next_safe_retry_at is None
        or blocker.next_safe_retry_at < observed_at
        or blocker.next_safe_retry_at > observed_at + policy.routine_retry_window
    ):
        reasons.append(
            _reason(
                PmHealthReasonCode.TRANSIENT_RETRY,
                PmHealthStatus.DEGRADED,
                blocker,
            )
        )
    else:
        reasons.append(
            _reason(
                PmHealthReasonCode.ROUTINE_WAIT,
                PmHealthStatus.HEALTHY,
                blocker,
            )
        )

    if blocker.capacity_impact:
        reasons.append(
            _reason(
                PmHealthReasonCode.CAPACITY_IMPACT,
                PmHealthStatus.DEGRADED,
                blocker,
            )
        )
    if blocker.starved_candidate_keys:
        if blocker.starvation_started_at is None:
            starvation_status = PmHealthStatus.BLOCKED
            starvation_code = PmHealthReasonCode.UNKNOWN_OR_LEGACY_INPUT
        elif (
            observed_at - blocker.starvation_started_at
            >= policy.starvation_blocked_after
        ):
            starvation_status = PmHealthStatus.BLOCKED
            starvation_code = PmHealthReasonCode.STARVATION
        else:
            starvation_status = PmHealthStatus.DEGRADED
            starvation_code = PmHealthReasonCode.CAPACITY_IMPACT
        reasons.append(_reason(starvation_code, starvation_status, blocker))
    return reasons


def _ordered_unique_reasons(
    reasons: Iterable[PmHealthReason],
) -> tuple[PmHealthReason, ...]:
    keyed = {_reason_sort_key(reason): reason for reason in reasons}
    return tuple(keyed[key] for key in sorted(keyed))


def assess_pm_health(
    inputs: PmHealthInputs,
    policy: PmHealthPolicy,
) -> PmHealthAssessment:
    """Calculate health without I/O, ambient time, or input-order dependence."""

    now = _normalise_time(inputs.observed_at)
    reasons: list[PmHealthReason] = []
    if inputs.schema_version != "pm-health-inputs-v1":
        reasons.append(
            _reason(
                PmHealthReasonCode.UNKNOWN_OR_LEGACY_INPUT,
                PmHealthStatus.BLOCKED,
            )
        )

    freshness_checks = [
        _freshness_reason(
            observed_at=now,
            value=inputs.last_heartbeat_at,
            degraded_after=None,
            blocked_after=policy.heartbeat_stale_after,
            missing_code=PmHealthReasonCode.HEARTBEAT_MISSING,
            stale_code=PmHealthReasonCode.HEARTBEAT_STALE,
            missing_status=PmHealthStatus.BLOCKED,
        ),
        _freshness_reason(
            observed_at=now,
            value=inputs.last_coherent_board_at,
            degraded_after=None,
            blocked_after=policy.coherent_board_stale_after,
            missing_code=PmHealthReasonCode.COHERENT_BOARD_MISSING,
            stale_code=PmHealthReasonCode.COHERENT_BOARD_STALE,
            missing_status=PmHealthStatus.BLOCKED,
        ),
        _freshness_reason(
            observed_at=now,
            value=inputs.last_convergence_at,
            degraded_after=policy.convergence_degraded_after,
            blocked_after=policy.convergence_blocked_after,
            missing_code=PmHealthReasonCode.CONVERGENCE_MISSING,
            stale_code=PmHealthReasonCode.CONVERGENCE_STALE,
            missing_status=PmHealthStatus.DEGRADED,
        ),
    ]
    if inputs.progress_expected:
        freshness_checks.append(
            _freshness_reason(
                observed_at=now,
                value=inputs.last_progress_at,
                degraded_after=policy.progress_degraded_after,
                blocked_after=policy.progress_blocked_after,
                missing_code=PmHealthReasonCode.PROGRESS_MISSING,
                stale_code=PmHealthReasonCode.PROGRESS_STALE,
                missing_status=PmHealthStatus.DEGRADED,
            )
        )
    reasons.extend(reason for reason in freshness_checks if reason is not None)

    active_blockers = tuple(
        blocker
        for blocker in inputs.blocker_observations
        if blocker.superseded_at is None or blocker.superseded_at > inputs.observed_at
    )
    for blocker in active_blockers:
        reasons.extend(_blocker_reasons(blocker, observed_at=now, policy=policy))

    ordered_reasons = _ordered_unique_reasons(reasons)
    status = max(
        (reason.status for reason in ordered_reasons),
        key=lambda value: _STATUS_RANK[value],
        default=PmHealthStatus.HEALTHY,
    )
    return PmHealthAssessment(
        status=status,
        observed_at=now,
        progress_expected=inputs.progress_expected,
        policy_fingerprint=policy.fingerprint,
        active_blocker_fingerprints=tuple(
            sorted(blocker.fingerprint for blocker in active_blockers)
        ),
        reasons=ordered_reasons,
    )
