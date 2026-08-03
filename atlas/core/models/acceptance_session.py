"""Durable exact-head acceptance-session model.

An acceptance session is mutable only through its append-oriented lifecycle
summary.  Repository, PR, head/base, close-set, criteria and actor identity are
pinned at creation and additionally protected by database triggers.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from atlas.core.enums import ActorType


class AcceptanceSessionLifecycle(StrEnum):
    """Stored state-machine milestones and terminal outcomes."""

    PREFLIGHT_PASSED = "preflight_passed"
    EVIDENCE_READY = "evidence_ready"
    CONFIRMATIONS_READY = "confirmations_ready"
    VERIFICATION_PASSED = "verification_passed"
    MERGE_READY = "merge_ready"
    STALE = "stale"
    BLOCKED = "blocked"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return self in {
            AcceptanceSessionLifecycle.STALE,
            AcceptanceSessionLifecycle.BLOCKED,
            AcceptanceSessionLifecycle.FAILED,
        }


class AcceptanceSessionStep(StrEnum):
    """The historical summaries retained on every acceptance session."""

    PREFLIGHT = "preflight"
    EVIDENCE = "evidence"
    CONFIRMATIONS = "confirmations"
    VERIFICATION = "verification"
    READINESS = "readiness"


class AcceptanceSessionStepState(StrEnum):
    """Stored state for one historical step summary."""

    PENDING = "pending"
    COMPLETE = "complete"
    BLOCKED = "blocked"
    FAILED = "failed"


class AcceptanceSessionBlockingReason(StrEnum):
    """Closed, secret-free reasons used by preflight, freshness and history."""

    PR_UNKNOWN = "pr_unknown"
    PR_MERGED = "pr_merged"
    PR_CLOSED = "pr_closed"
    PR_DRAFT = "pr_draft"
    PR_FORK_HEAD = "pr_fork_head"
    PR_NON_MAIN = "pr_non_main"
    INTEGRATION_BEHIND = "integration_behind"
    INTEGRATION_DIVERGED = "integration_diverged"
    INTEGRATION_CONFLICTED = "integration_conflicted"
    INTEGRATION_INDETERMINATE = "integration_indeterminate"
    EXTERNAL_STATE_INDETERMINATE = "external_state_indeterminate"
    CLOSE_SET_EMPTY = "close_set_empty"
    UNKNOWN_TICKET = "unknown_ticket"
    TICKET_NOT_REVIEW_REQUIRED = "ticket_not_review_required"
    ACTIVE_SESSION_EXISTS = "active_session_exists"
    IDEMPOTENCY_KEY_REUSED = "idempotency_key_reused"
    REPOSITORY_MISMATCH = "repository_mismatch"
    PR_NUMBER_MISMATCH = "pr_number_mismatch"
    HEAD_REF_MISMATCH = "head_ref_mismatch"
    HEAD_SHA_MISMATCH = "head_sha_mismatch"
    HEAD_REPOSITORY_MISMATCH = "head_repository_mismatch"
    BASE_REF_MISMATCH = "base_ref_mismatch"
    BASE_SHA_MISMATCH = "base_sha_mismatch"
    BASE_REPOSITORY_MISMATCH = "base_repository_mismatch"
    CLOSE_SET_MISMATCH = "close_set_mismatch"
    ELIGIBILITY_MISMATCH = "eligibility_mismatch"
    INTEGRATION_STATUS_MISMATCH = "integration_status_mismatch"
    CRITERIA_MISMATCH = "criteria_mismatch"
    SESSION_STALE = "session_stale"
    EVIDENCE_NOT_READY = "evidence_not_ready"
    CONFIRMATIONS_NOT_READY = "confirmations_not_ready"
    VERIFICATION_NOT_PASSED = "verification_not_passed"


class AcceptanceCriterionSnapshot(BaseModel):
    """One server-read criterion identified by ticket key and stored index."""

    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)

    ticket_key: str = Field(pattern=r"^ATLAS-[0-9]+$")
    criterion_index: int = Field(ge=0)
    text: str


class AcceptanceAssessmentSnapshot(BaseModel):
    """Structured derived fields from the initial exact-head assessment."""

    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)

    pr_state: str
    pr_draft: bool
    pr_merged: bool
    base_sha_source: Literal["live_branch", "historical_pr_snapshot"]
    merge_base_sha: str | None = None
    ahead_by: int | None = Field(default=None, ge=0)
    behind_by: int | None = Field(default=None, ge=0)
    compare_status: Literal["ahead", "behind", "diverged", "identical"] | None = None
    mergeability: Literal["mergeable", "conflicted", "indeterminate"]
    ancestry: Literal["current", "behind", "diverged", "indeterminate"]
    eligibility: Literal[
        "eligible", "merged", "closed", "draft", "fork_head", "non_main"
    ]
    integration_status: Literal[
        "current", "behind", "diverged", "conflicted", "indeterminate", "ineligible"
    ]

    @field_validator("merge_base_sha")
    @classmethod
    def _optional_sha_is_exact(cls, value: str | None) -> str | None:
        if value is not None and not _is_sha(value):
            raise ValueError("merge_base_sha must be a 40-character hexadecimal SHA")
        return value


class AcceptanceEvidenceSummary(BaseModel):
    """Bounded, payload-free projection of evidence at one exact head."""

    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)

    total_count: int = Field(ge=0, le=1_000_000)
    new_count: int = Field(ge=0, le=1_000_000)
    checks_count: int = Field(ge=0, le=1_000_000)
    reviews_count: int = Field(ge=0, le=1_000_000)
    docs_count: int = Field(ge=0, le=1_000_000)
    system_count: int = Field(ge=0, le=1_000_000)
    human_count: int = Field(ge=0, le=1_000_000)
    agent_count: int = Field(ge=0, le=1_000_000)
    pending_count: int = Field(ge=0, le=1_000_000)
    passed_count: int = Field(ge=0, le=1_000_000)
    failed_count: int = Field(ge=0, le=1_000_000)
    warning_count: int = Field(ge=0, le=1_000_000)
    not_applicable_count: int = Field(ge=0, le=1_000_000)
    complete_pin_count: int = Field(ge=0, le=1_000_000)
    exact_head_pin_count: int = Field(ge=0, le=1_000_000)
    pin_complete: bool
    exact_head_pin_complete: bool
    oldest_source_event_at: datetime | None = None
    latest_source_event_at: datetime | None = None

    @model_validator(mode="after")
    def _counts_and_timestamps_are_coherent(self) -> Self:
        if self.new_count > self.total_count:
            raise ValueError("new_count cannot exceed total_count")
        if self.checks_count + self.reviews_count + self.docs_count != self.total_count:
            raise ValueError("source counts must sum to total_count")
        if self.system_count + self.human_count + self.agent_count != self.total_count:
            raise ValueError("trust counts must sum to total_count")
        if (
            self.pending_count
            + self.passed_count
            + self.failed_count
            + self.warning_count
            + self.not_applicable_count
            != self.total_count
        ):
            raise ValueError("status counts must sum to total_count")
        if self.complete_pin_count > self.total_count:
            raise ValueError("complete_pin_count cannot exceed total_count")
        if self.exact_head_pin_count > self.complete_pin_count:
            raise ValueError("exact-head pins must also be complete pins")
        if self.pin_complete != (self.complete_pin_count == self.total_count):
            raise ValueError("pin_complete contradicts the pin counts")
        if self.exact_head_pin_complete != (
            self.exact_head_pin_count == self.total_count
        ):
            raise ValueError("exact_head_pin_complete contradicts the pin counts")
        timestamps = (self.oldest_source_event_at, self.latest_source_event_at)
        if (timestamps[0] is None) != (timestamps[1] is None):
            raise ValueError("source timestamp bounds must both be present or absent")
        if (
            timestamps[0] is not None
            and timestamps[1] is not None
            and timestamps[1] < timestamps[0]
        ):
            raise ValueError("latest source timestamp cannot precede oldest")
        return self


class AcceptanceStepSummary(BaseModel):
    """Bounded historical status for one acceptance step."""

    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)

    state: AcceptanceSessionStepState
    reasons: tuple[AcceptanceSessionBlockingReason, ...] = ()
    receipt_ids: tuple[UUID, ...] = ()
    occurred_at: datetime | None = None
    evidence: AcceptanceEvidenceSummary | None = None


class AcceptanceSession(BaseModel):
    """One immutable-head, durable PR acceptance attempt."""

    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)

    id: UUID
    repository_owner: str = Field(min_length=1, max_length=128)
    repository_name: str = Field(min_length=1, max_length=128)
    pr_number: int = Field(gt=0)
    close_set: tuple[str, ...] = Field(min_length=1)
    head_ref: str = Field(min_length=1, max_length=256)
    head_sha: str
    head_repository: str = Field(min_length=1, max_length=257)
    base_ref: str = Field(min_length=1, max_length=256)
    base_sha: str
    base_repository: str = Field(min_length=1, max_length=257)
    initial_assessment: AcceptanceAssessmentSnapshot
    criteria_snapshot: tuple[AcceptanceCriterionSnapshot, ...]
    criteria_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    creation_idempotency_key_identity: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    created_by_type: ActorType
    created_by_id: str = Field(min_length=1, max_length=128)
    lifecycle: AcceptanceSessionLifecycle
    step_summaries: dict[AcceptanceSessionStep, AcceptanceStepSummary]
    blocking_reasons: tuple[AcceptanceSessionBlockingReason, ...] = ()
    stored_merge_ready: bool = False
    historical_readiness_reasons: tuple[AcceptanceSessionBlockingReason, ...] = ()
    created_at: datetime
    updated_at: datetime
    staled_at: datetime | None = None

    @field_validator("head_sha", "base_sha")
    @classmethod
    def _sha_is_exact(cls, value: str) -> str:
        if not _is_sha(value):
            raise ValueError("pinned SHAs must be 40-character hexadecimal values")
        return value.lower()

    @model_validator(mode="after")
    def _pinned_contract_is_coherent(self) -> Self:
        if (
            self.created_by_type is not ActorType.HUMAN
            or self.created_by_id != "operator"
        ):
            raise ValueError("acceptance sessions require the human/operator actor")
        if tuple(sorted(set(self.close_set))) != self.close_set:
            raise ValueError("close_set must be unique and sorted by ticket key")
        if any(
            not key.startswith("ATLAS-") or not key.removeprefix("ATLAS-").isdigit()
            for key in self.close_set
        ):
            raise ValueError("close_set contains a non-canonical ticket key")

        expected_criteria: list[tuple[str, int]] = []
        for key in self.close_set:
            indexes = [
                criterion.criterion_index
                for criterion in self.criteria_snapshot
                if criterion.ticket_key == key
            ]
            expected_criteria.extend((key, index) for index in range(len(indexes)))
        actual_criteria = [
            (criterion.ticket_key, criterion.criterion_index)
            for criterion in self.criteria_snapshot
        ]
        if actual_criteria != expected_criteria:
            raise ValueError(
                "criteria_snapshot must follow close-set ticket-key/index order"
            )

        expected_steps = set(AcceptanceSessionStep)
        if set(self.step_summaries) != expected_steps:
            raise ValueError("step_summaries must contain every acceptance step")

        assessment = self.initial_assessment
        if (
            assessment.pr_state != "open"
            or assessment.pr_draft
            or assessment.pr_merged
            or assessment.base_sha_source != "live_branch"
            or assessment.eligibility != "eligible"
            or assessment.ancestry != "current"
            or assessment.mergeability != "mergeable"
            or assessment.integration_status != "current"
        ):
            raise ValueError("a stored acceptance session requires passed preflight")
        if self.base_ref != "main":
            raise ValueError("a stored acceptance session must pin literal main")
        expected_repository = f"{self.repository_owner}/{self.repository_name}"
        if (
            self.head_repository != expected_repository
            or self.base_repository != expected_repository
        ):
            raise ValueError("pinned repositories must match the requested repository")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        if self.lifecycle is AcceptanceSessionLifecycle.STALE:
            if self.staled_at is None:
                raise ValueError("a stale acceptance session requires staled_at")
        elif self.staled_at is not None:
            raise ValueError("only a stale acceptance session may have staled_at")
        return self


def _is_sha(value: str) -> bool:
    return len(value) == 40 and all(
        character in "0123456789abcdefABCDEF" for character in value
    )
