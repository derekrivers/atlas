"""Bounded audit model for one retrospective merged-publication decision."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from atlas.core.enums import ActorType


class RetrospectiveCompletionDecision(StrEnum):
    """The only workflow outcome owned by retrospective completion."""

    HOLD = "hold"
    DONE = "done"


class RetrospectiveCompletionReason(StrEnum):
    """Closed, secret-free outcomes for historical proof and fenced writes."""

    COMPLETE_DELIVERY_PROOF = "complete_delivery_proof"
    TICKET_NOT_CI_PENDING = "ticket_not_ci_pending"
    TICKET_IDENTITY_MISMATCH = "ticket_identity_mismatch"
    PUBLICATION_UNAVAILABLE = "historical_publication_unavailable"
    PUBLICATION_AMBIGUOUS = "historical_publication_ambiguous"
    CONTRIBUTOR_HEAD_UNAVAILABLE = "contributor_head_unavailable"
    MERGED_PR_UNPROVEN = "merged_pr_unproven"
    MERGED_PR_IDENTITY_MISMATCH = "merged_pr_identity_mismatch"
    CANONICAL_MAIN_UNPROVEN = "canonical_main_unproven"
    MERGE_ANCESTRY_UNPROVEN = "merge_ancestry_unproven"
    POLICY_UNAVAILABLE = "policy_unavailable"
    POLICY_CHANGED = "policy_changed"
    SNAPSHOT_INCOMPLETE = "snapshot_incomplete"
    SNAPSHOT_CHANGED = "snapshot_changed"
    VERIFICATION_UNPROVEN = "verification_unproven"
    ACCEPTANCE_UNPROVEN = "human_acceptance_unproven"
    MERGED_EVIDENCE_UNPROVEN = "merged_evidence_unproven"
    BOARD_REVALIDATION_FAILED = "board_revalidation_failed"
    BOARD_STATE_MOVED = "board_state_moved"
    PROOF_CHANGED = "proof_changed"
    LEASE_UNAVAILABLE = "lease_unavailable"
    LEASE_LOST = "lease_lost"
    CONCURRENT_WRITE_FENCE = "concurrent_write_fence"
    FENCE_STILL_UNRESOLVED = "fence_still_unresolved"
    FENCE_RECONCILED_TARGET = "fence_reconciled_target"
    FENCE_RECONCILED_SOURCE = "fence_reconciled_source"
    FENCE_RECONCILED_MOVED = "fence_reconciled_moved"
    WRITE_CONFIRMED = "write_confirmed"
    WRITE_INDETERMINATE = "write_indeterminate"


class RetrospectiveCompletionReconciliation(BaseModel):
    """Immutable proof projection for one exact historical candidate evaluation."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["retrospective-completion-reconciliation-v1"] = (
        "retrospective-completion-reconciliation-v1"
    )
    id: UUID
    product_id: UUID
    ticket_id: UUID
    ticket_key: str = Field(min_length=1, max_length=64)
    linear_issue_id: str | None = Field(default=None, min_length=1, max_length=256)
    recovery_episode_id: UUID | None = None
    publication_attachment_id: str | None = Field(
        default=None, min_length=1, max_length=256
    )
    repository_owner: str | None = Field(default=None, min_length=1, max_length=128)
    repository_name: str | None = Field(default=None, min_length=1, max_length=128)
    pr_number: int | None = Field(default=None, gt=0)
    contributor_head: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    merge_commit: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    canonical_main: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    policy_id: UUID | None = None
    policy_revision: int | None = Field(default=None, ge=1)
    policy_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    snapshot_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    acceptance_session_id: UUID | None = None
    verification_verdict_id: UUID | None = None
    criteria_fingerprint: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    verification_check_ids: tuple[UUID, ...] = Field(default=(), max_length=64)
    deciding_evidence_ids: tuple[UUID, ...] = Field(default=(), max_length=256)
    merged_evidence_id: UUID | None = None
    reason: RetrospectiveCompletionReason
    decision: RetrospectiveCompletionDecision
    observed_at: datetime
    created_by_type: ActorType
    created_by_id: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def _complete_decision_has_complete_exact_proof(self) -> Self:
        if self.created_by_type is not ActorType.SYSTEM:
            raise ValueError("retrospective decisions are system-authored")
        if self.observed_at.utcoffset() is None:
            raise ValueError("retrospective observed_at must be timezone-aware")
        expected = (
            RetrospectiveCompletionDecision.DONE
            if self.reason is RetrospectiveCompletionReason.COMPLETE_DELIVERY_PROOF
            else RetrospectiveCompletionDecision.HOLD
        )
        if self.decision is not expected:
            raise ValueError("retrospective decision contradicts its reason")
        if self.decision is RetrospectiveCompletionDecision.DONE:
            required = (
                self.linear_issue_id,
                self.recovery_episode_id,
                self.publication_attachment_id,
                self.repository_owner,
                self.repository_name,
                self.pr_number,
                self.contributor_head,
                self.merge_commit,
                self.canonical_main,
                self.policy_id,
                self.policy_revision,
                self.policy_fingerprint,
                self.snapshot_fingerprint,
                self.acceptance_session_id,
                self.verification_verdict_id,
                self.criteria_fingerprint,
                self.merged_evidence_id,
            )
            if any(value is None for value in required):
                raise ValueError(
                    "retrospective Done requires every exact proof identity"
                )
            if not self.verification_check_ids or not self.deciding_evidence_ids:
                raise ValueError("retrospective Done requires retained deciding proof")
            if len(set(self.verification_check_ids)) != len(
                self.verification_check_ids
            ) or len(set(self.deciding_evidence_ids)) != len(
                self.deciding_evidence_ids
            ):
                raise ValueError("retrospective proof identities must be unique")
        return self
