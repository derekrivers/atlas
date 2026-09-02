"""Durable PM recovery episodes, fairness cursors, and blocker state.

These models are deliberately lower than :mod:`atlas.pm`.  Storage persists
them without importing the PM runtime, while the PM boundary may project the
bounded blocker shape into the pure health calculus.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID, uuid5

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

MAX_PM_RECOVERY_NAME_LENGTH = 128
MAX_PM_RECOVERY_SCHEMA_LENGTH = 64
MAX_PM_RECOVERY_FINGERPRINT_LENGTH = 64
MAX_PM_STARVED_CANDIDATES = 128
MAX_PM_RECURRENCE_COUNT = 2_147_483_647
MAX_PM_SEQUENCE = 9_223_372_036_854_775_807

PM_RECOVERY_EPISODE_NAMESPACE = UUID("65d277f0-9b84-4ad0-8933-d9f1588b5475")


def _bounded_identifier(value: str, *, name: str) -> str:
    if not value:
        raise ValueError(f"{name} must not be empty")
    if value != value.strip():
        raise ValueError(f"{name} must not have surrounding whitespace")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{name} must not contain control characters")
    return value


def _aware_utc(value: datetime, *, name: str) -> datetime:
    if value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _canonical_bytes(payload: object) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


class PmBlockerKind(StrEnum):
    """Bounded recovery posture shared by persistence and PM health."""

    ROUTINE_WAIT = "routine_wait"
    RETRYABLE = "retryable"
    UNRESOLVED_FENCE = "unresolved_fence"
    UNKNOWN = "unknown"


class PmBlockerCode(StrEnum):
    """Closed machine-readable blocker causes accepted by durable storage."""

    LEASE_UNAVAILABLE = "lease_unavailable"
    PUBLICATION_NOT_YET_COMPLETE = "publication_not_yet_complete"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PUBLICATION_AMBIGUOUS = "publication_ambiguous"
    CI_EVIDENCE_NOT_YET_COMPLETE = "ci_evidence_not_yet_complete"
    CI_EVIDENCE_AMBIGUOUS = "ci_evidence_ambiguous"
    AUTHORITY_CHANGED = "authority_changed"
    WRITE_FENCE_UNRESOLVED = "write_fence_unresolved"
    RETROSPECTIVE_PROOF_INCOMPLETE = "retrospective_proof_incomplete"
    RETROSPECTIVE_PROOF_AMBIGUOUS = "retrospective_proof_ambiguous"


class PmBlockerAuthorityKind(StrEnum):
    """Kind of exact durable authority named by a blocker."""

    OPERATION = "operation"
    LEASE = "lease"
    FENCE = "fence"
    INTENT = "intent"


class PmBlockerSupersessionKind(StrEnum):
    """Bounded class of observation that made a blocker obsolete."""

    PROGRESS = "progress"
    RECOVERY = "recovery"


class PmRecoveryEpisodeClosureKind(StrEnum):
    """Why an authoritative episode stopped being active."""

    AUTHORITATIVE_LIFECYCLE_ENTRY = "authoritative_lifecycle_entry"
    PUBLICATION_REPLACEMENT = "publication_replacement"
    RECOVERY_COMPLETED = "recovery_completed"


class PmRecoveryEpisodeIdentity(BaseModel):
    """Stable authoritative identity from which one episode UUID is derived."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["pm-recovery-episode-v1"] = "pm-recovery-episode-v1"
    product_id: UUID
    operation: str = Field(min_length=1, max_length=MAX_PM_RECOVERY_NAME_LENGTH)
    authority_id: str = Field(min_length=1, max_length=MAX_PM_RECOVERY_NAME_LENGTH)
    authoritative_episode_id: str = Field(
        min_length=1, max_length=MAX_PM_RECOVERY_NAME_LENGTH
    )
    candidate_ticket_id: UUID | None = None
    candidate_ticket_key: str | None = Field(
        default=None, max_length=MAX_PM_RECOVERY_NAME_LENGTH
    )

    @field_validator("operation", "authority_id", "authoritative_episode_id")
    @classmethod
    def _validate_identifiers(cls, value: str, info: ValidationInfo) -> str:
        return _bounded_identifier(value, name=info.field_name or "identifier")

    @field_validator("candidate_ticket_key")
    @classmethod
    def _validate_candidate_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _bounded_identifier(value, name="candidate_ticket_key")

    @model_validator(mode="after")
    def _validate_candidate_pair(self) -> PmRecoveryEpisodeIdentity:
        if (self.candidate_ticket_id is None) != (self.candidate_ticket_key is None):
            raise ValueError("candidate ticket id and key must be present together")
        return self

    def identity_bytes(self) -> bytes:
        return _canonical_bytes(
            {
                "authority_id": self.authority_id,
                "authoritative_episode_id": self.authoritative_episode_id,
                "candidate_ticket_id": (
                    None
                    if self.candidate_ticket_id is None
                    else str(self.candidate_ticket_id)
                ),
                "candidate_ticket_key": self.candidate_ticket_key,
                "operation": self.operation,
                "product_id": str(self.product_id),
                "schema_version": self.schema_version,
            }
        )

    @property
    def computed_identity_fingerprint(self) -> str:
        return hashlib.sha256(self.identity_bytes()).hexdigest()

    @property
    def episode_id(self) -> UUID:
        return uuid5(PM_RECOVERY_EPISODE_NAMESPACE, self.computed_identity_fingerprint)


class PmRecoveryEpisode(PmRecoveryEpisodeIdentity):
    """Durable episode projection with a product-global fairness cursor."""

    id: UUID
    identity_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    episode_created_sequence: int = Field(ge=1, le=MAX_PM_SEQUENCE)
    last_evaluated_sequence: int | None = Field(default=None, ge=1, le=MAX_PM_SEQUENCE)
    last_evaluation_id: str | None = Field(
        default=None, max_length=MAX_PM_RECOVERY_NAME_LENGTH
    )
    last_evaluation_fingerprint: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    created_at: datetime
    last_evaluated_at: datetime | None = None
    closed_at: datetime | None = None
    closure_event_id: str | None = Field(
        default=None, max_length=MAX_PM_RECOVERY_NAME_LENGTH
    )
    closure_kind: PmRecoveryEpisodeClosureKind | None = None
    replaces_episode_id: UUID | None = None
    replacement_event_id: str | None = Field(
        default=None, max_length=MAX_PM_RECOVERY_NAME_LENGTH
    )

    @field_validator("last_evaluation_id", "closure_event_id", "replacement_event_id")
    @classmethod
    def _validate_optional_identifiers(
        cls, value: str | None, info: ValidationInfo
    ) -> str | None:
        if value is None:
            return None
        return _bounded_identifier(value, name=info.field_name or "identifier")

    @model_validator(mode="after")
    def _validate_episode(self) -> PmRecoveryEpisode:
        if self.id != self.episode_id:
            raise ValueError("episode id does not match its stable identity")
        if self.identity_fingerprint != self.computed_identity_fingerprint:
            raise ValueError("episode fingerprint does not match its stable identity")
        created = _aware_utc(self.created_at, name="episode created_at")
        evaluation_fields = (
            self.last_evaluated_sequence,
            self.last_evaluation_id,
            self.last_evaluation_fingerprint,
            self.last_evaluated_at,
        )
        if any(value is None for value in evaluation_fields) and any(
            value is not None for value in evaluation_fields
        ):
            raise ValueError("last evaluation fields must be present together")
        if self.last_evaluated_sequence is not None:
            if self.last_evaluated_sequence <= self.episode_created_sequence:
                raise ValueError(
                    "last evaluation sequence must follow episode creation"
                )
            assert self.last_evaluated_at is not None
            if (
                _aware_utc(self.last_evaluated_at, name="episode last_evaluated_at")
                < created
            ):
                raise ValueError("episode evaluation precedes creation")
        closure_fields = (self.closed_at, self.closure_event_id, self.closure_kind)
        if any(value is None for value in closure_fields) and any(
            value is not None for value in closure_fields
        ):
            raise ValueError("episode closure fields must be present together")
        if self.closed_at is not None:
            closed = _aware_utc(self.closed_at, name="episode closed_at")
            if closed < created:
                raise ValueError("episode closure precedes creation")
            if self.last_evaluated_at is not None and closed < _aware_utc(
                self.last_evaluated_at, name="episode last_evaluated_at"
            ):
                raise ValueError("episode closure precedes its last evaluation")
        replacement_fields = (self.replaces_episode_id, self.replacement_event_id)
        if any(value is None for value in replacement_fields) and any(
            value is not None for value in replacement_fields
        ):
            raise ValueError("episode replacement lineage fields travel together")
        if self.replaces_episode_id == self.id:
            raise ValueError("an episode cannot replace itself")
        return self

    @property
    def fairness_cursor(self) -> int:
        return self.last_evaluated_sequence or self.episode_created_sequence


def pm_blocker_identity_bytes(
    *,
    schema_version: str,
    operation: str,
    code: str,
    kind: PmBlockerKind,
    authority_kind: PmBlockerAuthorityKind,
    authority_id: str,
    episode_id: str,
    candidate_key: str | None,
) -> bytes:
    """Canonical accepted PM blocker identity, excluding mutable state."""

    return _canonical_bytes(
        {
            "schema_version": schema_version,
            "operation": operation,
            "code": code,
            "kind": kind.value,
            "authority_kind": authority_kind.value,
            "authority_id": authority_id,
            "episode_id": episode_id,
            "candidate_key": candidate_key,
        }
    )


def pm_blocker_fingerprint(
    *,
    schema_version: str,
    operation: str,
    code: str,
    kind: PmBlockerKind,
    authority_kind: PmBlockerAuthorityKind,
    authority_id: str,
    episode_id: str,
    candidate_key: str | None,
) -> str:
    return hashlib.sha256(
        pm_blocker_identity_bytes(
            schema_version=schema_version,
            operation=operation,
            code=code,
            kind=kind,
            authority_kind=authority_kind,
            authority_id=authority_id,
            episode_id=episode_id,
            candidate_key=candidate_key,
        )
    ).hexdigest()


class PmBlockerIdentity(BaseModel):
    """Stable cause identity for one or more bounded blocker occurrences."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal["pm-blocker-observation-v1"] = "pm-blocker-observation-v1"
    product_id: UUID
    operation: str = Field(min_length=1, max_length=MAX_PM_RECOVERY_NAME_LENGTH)
    code: PmBlockerCode
    kind: PmBlockerKind
    authority_kind: PmBlockerAuthorityKind
    authority_id: str = Field(min_length=1, max_length=MAX_PM_RECOVERY_NAME_LENGTH)
    recovery_episode_id: UUID
    candidate_ticket_id: UUID | None = None
    candidate_ticket_key: str | None = Field(
        default=None, max_length=MAX_PM_RECOVERY_NAME_LENGTH
    )

    @field_validator("operation", "authority_id")
    @classmethod
    def _validate_identifiers(cls, value: str, info: ValidationInfo) -> str:
        return _bounded_identifier(value, name=info.field_name or "identifier")

    @field_validator("candidate_ticket_key")
    @classmethod
    def _validate_candidate_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _bounded_identifier(value, name="candidate_ticket_key")

    @model_validator(mode="after")
    def _validate_identity(self) -> PmBlockerIdentity:
        if (self.candidate_ticket_id is None) != (self.candidate_ticket_key is None):
            raise ValueError("candidate ticket id and key must be present together")
        if (
            self.kind is PmBlockerKind.UNRESOLVED_FENCE
            and self.authority_kind is not PmBlockerAuthorityKind.FENCE
        ):
            raise ValueError("an unresolved fence must name a fence authority")
        return self

    def identity_bytes(self) -> bytes:
        return pm_blocker_identity_bytes(
            schema_version=self.schema_version,
            operation=self.operation,
            code=self.code,
            kind=self.kind,
            authority_kind=self.authority_kind,
            authority_id=self.authority_id,
            episode_id=str(self.recovery_episode_id),
            candidate_key=self.candidate_ticket_key,
        )

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.identity_bytes()).hexdigest()


class PmStarvedCandidateRef(BaseModel):
    """Identity-only observation; the repository owns starvation start time."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ticket_id: UUID
    ticket_key: str = Field(min_length=1, max_length=MAX_PM_RECOVERY_NAME_LENGTH)

    @field_validator("ticket_key")
    @classmethod
    def _validate_ticket_key(cls, value: str) -> str:
        return _bounded_identifier(value, name="starved candidate ticket_key")


class PmStarvedCandidate(PmStarvedCandidateRef):
    """Durable current starvation member with repository-owned start time."""

    started_at: datetime

    @field_validator("started_at")
    @classmethod
    def _validate_started_at(cls, value: datetime) -> datetime:
        return _aware_utc(value, name="starvation started_at")


class PmBlockerObservationIntent(BaseModel):
    """Caller intent for one evaluation's bounded blocker observation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: PmBlockerCode
    kind: PmBlockerKind
    authority_kind: PmBlockerAuthorityKind
    authority_id: str = Field(min_length=1, max_length=MAX_PM_RECOVERY_NAME_LENGTH)
    next_safe_retry_at: datetime | None = None
    capacity_impact: bool = False
    starved_candidates: tuple[PmStarvedCandidateRef, ...] = Field(
        default=(), max_length=MAX_PM_STARVED_CANDIDATES
    )
    starved_candidates_truncated: bool = False
    policy_namespace: str | None = Field(
        default=None, max_length=MAX_PM_RECOVERY_NAME_LENGTH
    )
    policy_revision: int | None = Field(default=None, ge=1, le=MAX_PM_SEQUENCE)
    policy_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @field_validator("authority_id", "policy_namespace")
    @classmethod
    def _validate_optional_identifiers(
        cls, value: str | None, info: ValidationInfo
    ) -> str | None:
        if value is None:
            return None
        return _bounded_identifier(value, name=info.field_name or "identifier")

    @field_validator("next_safe_retry_at")
    @classmethod
    def _validate_retry_time(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _aware_utc(value, name="next_safe_retry_at")

    @field_validator("starved_candidates")
    @classmethod
    def _canonicalise_starved_candidates(
        cls, value: tuple[PmStarvedCandidateRef, ...]
    ) -> tuple[PmStarvedCandidateRef, ...]:
        ids = [item.ticket_id for item in value]
        keys = [item.ticket_key for item in value]
        if len(ids) != len(set(ids)) or len(keys) != len(set(keys)):
            raise ValueError("starved candidate identities must be unique")
        return tuple(sorted(value, key=lambda item: (item.ticket_key, item.ticket_id)))

    @model_validator(mode="after")
    def _validate_observation(self) -> PmBlockerObservationIntent:
        policy = (
            self.policy_namespace,
            self.policy_revision,
            self.policy_fingerprint,
        )
        if any(value is None for value in policy) and any(
            value is not None for value in policy
        ):
            raise ValueError(
                "policy namespace, revision and fingerprint travel together"
            )
        if (
            self.kind is PmBlockerKind.UNRESOLVED_FENCE
            and self.authority_kind is not PmBlockerAuthorityKind.FENCE
        ):
            raise ValueError("an unresolved fence must name a fence authority")
        if self.starved_candidates_truncated and (
            len(self.starved_candidates) != MAX_PM_STARVED_CANDIDATES
            or not self.capacity_impact
        ):
            raise ValueError(
                "truncated starvation requires a full projection and capacity impact"
            )
        return self

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes(self.model_dump(mode="json"))


class DurablePmBlocker(PmBlockerIdentity):
    """One retained blocker occurrence; only explicit progress supersedes it."""

    id: UUID
    blocker_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    active_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    first_evaluation_id: str = Field(
        min_length=1, max_length=MAX_PM_RECOVERY_NAME_LENGTH
    )
    latest_evaluation_id: str = Field(
        min_length=1, max_length=MAX_PM_RECOVERY_NAME_LENGTH
    )
    first_observed_at: datetime
    latest_observed_at: datetime
    consecutive_observations: int = Field(ge=1, le=MAX_PM_RECURRENCE_COUNT)
    next_safe_retry_at: datetime | None = None
    capacity_impact: bool = False
    starved_candidates: tuple[PmStarvedCandidate, ...] = Field(
        default=(), max_length=MAX_PM_STARVED_CANDIDATES
    )
    starved_candidates_truncated: bool = False
    policy_namespace: str | None = Field(
        default=None, max_length=MAX_PM_RECOVERY_NAME_LENGTH
    )
    policy_revision: int | None = Field(default=None, ge=1, le=MAX_PM_SEQUENCE)
    policy_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    superseded_at: datetime | None = None
    superseded_by_event_id: str | None = Field(
        default=None, max_length=MAX_PM_RECOVERY_NAME_LENGTH
    )
    supersession_kind: PmBlockerSupersessionKind | None = None

    @field_validator(
        "first_evaluation_id",
        "latest_evaluation_id",
        "policy_namespace",
        "superseded_by_event_id",
    )
    @classmethod
    def _validate_optional_identifiers(
        cls, value: str | None, info: ValidationInfo
    ) -> str | None:
        if value is None:
            return None
        return _bounded_identifier(value, name=info.field_name or "identifier")

    @model_validator(mode="after")
    def _validate_blocker(self) -> DurablePmBlocker:
        if self.blocker_fingerprint != self.fingerprint:
            raise ValueError("stored blocker fingerprint does not match identity")
        first = _aware_utc(self.first_observed_at, name="blocker first_observed_at")
        latest = _aware_utc(self.latest_observed_at, name="blocker latest_observed_at")
        if latest < first:
            raise ValueError("latest blocker observation precedes first observation")
        if self.next_safe_retry_at is not None:
            _aware_utc(self.next_safe_retry_at, name="next_safe_retry_at")
        if self.starved_candidates_truncated and (
            len(self.starved_candidates) != MAX_PM_STARVED_CANDIDATES
            or not self.capacity_impact
        ):
            raise ValueError(
                "truncated starvation requires a full projection and capacity impact"
            )
        policy = (
            self.policy_namespace,
            self.policy_revision,
            self.policy_fingerprint,
        )
        if any(value is None for value in policy) and any(
            value is not None for value in policy
        ):
            raise ValueError(
                "policy namespace, revision and fingerprint travel together"
            )
        supersession = (
            self.superseded_at,
            self.superseded_by_event_id,
            self.supersession_kind,
        )
        if any(value is None for value in supersession) and any(
            value is not None for value in supersession
        ):
            raise ValueError("blocker supersession fields must be present together")
        if self.superseded_at is None:
            if self.active_fingerprint != self.blocker_fingerprint:
                raise ValueError("active blocker must carry its active fingerprint")
        else:
            if self.active_fingerprint is not None:
                raise ValueError("superseded blocker cannot remain active")
            if _aware_utc(self.superseded_at, name="blocker superseded_at") < latest:
                raise ValueError("blocker supersession precedes latest observation")
        return self

    @property
    def starvation_started_at(self) -> datetime | None:
        if not self.starved_candidates:
            return None
        return min(candidate.started_at for candidate in self.starved_candidates)
