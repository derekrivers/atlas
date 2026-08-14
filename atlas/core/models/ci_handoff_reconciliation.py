"""Bounded audit record for one system-tier CI handoff observation."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from atlas.core.enums import ActorType, EvidenceStatus
from atlas.core.models.ticket import TicketStatus
from atlas.core.models.verification_check import VerificationCheckType


class CIHandoffClassification(StrEnum):
    """Closed evidence classes that can drive or hold the CI-pending edge."""

    PASSED = "passed"
    IMPLEMENTATION_FAILURE = "implementation_failure"
    PENDING = "pending"
    MISSING = "missing"
    INFRASTRUCTURE = "infrastructure"
    STALE = "stale"
    MALFORMED = "malformed"
    INDETERMINATE = "indeterminate"


class CIHandoffDecision(StrEnum):
    """The only external-state decision represented by a reconciliation."""

    HOLD = "hold"
    REVIEW_REQUIRED = "review_required"
    CHANGES_REQUESTED = "changes_requested"


class CIHandoffReason(StrEnum):
    """Bounded, secret-free reason for the final decision of one operation."""

    COMPLETE_REQUIRED_CHECKS_PASSED = "complete_required_checks_passed"
    COMPLETE_IMPLEMENTATION_FAILURE = "complete_implementation_failure"
    REQUIRED_CHECKS_PENDING = "required_checks_pending"
    REQUIRED_CHECKS_MISSING = "required_checks_missing"
    INFRASTRUCTURE_EVIDENCE = "infrastructure_evidence"
    STALE_EVIDENCE = "stale_evidence"
    MALFORMED_EVIDENCE = "malformed_evidence"
    CONTRADICTORY_EVIDENCE = "contradictory_evidence"
    INDETERMINATE_EVIDENCE = "indeterminate_evidence"
    NO_CI_REQUIRED_CHECKS = "no_ci_required_checks"
    LEASE_UNAVAILABLE = "lease_unavailable"
    LEASE_LOST = "lease_lost"
    POLICY_UNAVAILABLE = "policy_unavailable"
    SNAPSHOT_INCOMPLETE = "snapshot_incomplete"
    TICKET_NOT_CI_PENDING = "ticket_not_ci_pending"
    TICKET_IDENTITY_MISMATCH = "ticket_identity_mismatch"
    LINEAR_ISSUE_MISSING = "linear_issue_missing"
    LINEAR_STATE_MISMATCH = "linear_state_mismatch"
    PR_IDENTITY_MALFORMED = "pr_identity_malformed"
    PR_HEAD_MOVED = "pr_head_moved"
    GITHUB_INFRASTRUCTURE = "github_infrastructure"
    BOARD_REVALIDATION_FAILED = "board_revalidation_failed"
    BOARD_STATE_MOVED = "board_state_moved"
    POLICY_CHANGED = "policy_changed"
    SNAPSHOT_CHANGED = "snapshot_changed"
    CONCURRENT_WRITE_FENCE = "concurrent_write_fence"
    FENCE_STILL_UNRESOLVED = "fence_still_unresolved"
    FENCE_RECONCILED_TARGET = "fence_reconciled_target"
    FENCE_RECONCILED_SOURCE = "fence_reconciled_source"
    FENCE_RECONCILED_MOVED = "fence_reconciled_moved"
    WRITE_CONFIRMED = "write_confirmed"
    WRITE_INDETERMINATE = "write_indeterminate"


class CIHandoffCheckResult(BaseModel):
    """One required system-tier check's bounded classification and proof ids."""

    model_config = ConfigDict(frozen=True)

    check_type: VerificationCheckType
    status: EvidenceStatus
    classification: CIHandoffClassification
    evidence_ids: tuple[UUID, ...] = Field(default=(), max_length=128)


class CIHandoffReconciliation(BaseModel):
    """One immutable CI-pending observation and its zero/one transition decision."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["ci-handoff-reconciliation-v1"] = (
        "ci-handoff-reconciliation-v1"
    )
    id: UUID
    product_id: UUID
    ticket_id: UUID
    ticket_key: str = Field(min_length=1, max_length=64)
    linear_issue_id: str | None = Field(default=None, min_length=1, max_length=256)
    repository_owner: str = Field(min_length=1, max_length=128)
    repository_name: str = Field(min_length=1, max_length=128)
    pr_number: int = Field(gt=0)
    head_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    policy_id: UUID | None = None
    policy_revision: int | None = Field(default=None, ge=1)
    policy_fingerprint: str | None = Field(default=None, min_length=64, max_length=64)
    snapshot_fingerprint: str | None = Field(default=None, min_length=64, max_length=64)
    classification: CIHandoffClassification
    reason: CIHandoffReason
    decision: CIHandoffDecision
    check_results: tuple[CIHandoffCheckResult, ...] = Field(default=(), max_length=16)
    observed_at: datetime
    created_by_type: ActorType
    created_by_id: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def _decision_is_exactly_bounded(self) -> Self:
        policy_fields = (
            self.policy_id,
            self.policy_revision,
            self.policy_fingerprint,
            self.snapshot_fingerprint,
        )
        if any(value is None for value in policy_fields) and any(
            value is not None for value in policy_fields
        ):
            raise ValueError(
                "policy and snapshot identity must be all present or absent"
            )
        expected_decision = {
            CIHandoffClassification.PASSED: CIHandoffDecision.REVIEW_REQUIRED,
            CIHandoffClassification.IMPLEMENTATION_FAILURE: (
                CIHandoffDecision.CHANGES_REQUESTED
            ),
        }.get(self.classification, CIHandoffDecision.HOLD)
        if self.decision is not expected_decision:
            raise ValueError("CI handoff decision does not match its classification")
        if self.decision is not CIHandoffDecision.HOLD and not self.check_results:
            raise ValueError("a transition decision requires a non-empty check set")
        if self.created_by_type is not ActorType.SYSTEM:
            raise ValueError("CI handoff reconciliations are system-authored")
        return self

    @property
    def target_status(self) -> TicketStatus | None:
        """Return the sole permitted Linear target for this decision."""

        return {
            CIHandoffDecision.REVIEW_REQUIRED: TicketStatus.REVIEW_REQUIRED,
            CIHandoffDecision.CHANGES_REQUESTED: TicketStatus.CHANGES_REQUESTED,
        }.get(self.decision)
