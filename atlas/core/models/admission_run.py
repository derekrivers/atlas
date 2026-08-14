"""Deterministic delivery-admission decisions and their audit record."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from atlas.core.enums import ActorType, RiskLevel


class AdmissionDecisionType(StrEnum):
    """The bounded policy result for one dependency-ready candidate."""

    ADMIT = "admit"
    HOLD = "hold"


class AdmissionHoldCode(StrEnum):
    """Closed reasons why a dependency-ready candidate cannot enter now."""

    POLICY_PAUSED = "policy_paused"
    POLICY_DRAINING = "policy_draining"
    SNAPSHOT_POLICY_MISMATCH = "snapshot_policy_mismatch"
    SNAPSHOT_INCOMPLETE = "snapshot_incomplete"
    WORKING_BUDGET = "working_budget"
    INTEGRATION_BUDGET = "integration_budget"
    REVIEW_BUDGET = "review_budget"
    CHANGES_REQUESTED_RESERVE = "changes_requested_reserve"
    RISK_LANE = "risk_lane"
    COMPONENT_LANE = "component_lane"
    MISSING_EXTERNAL_LINEAR_ID = "missing_external_linear_id"
    SINGLE_WRITE_LIMIT = "single_write_limit"


class AdmissionHoldReason(BaseModel):
    """One typed and deterministically sortable hold explanation.

    The optional identity fields retain bounded snapshot diagnostics without
    embedding a raw Linear response. Capacity reasons use ``observed`` and
    ``limit``; lane reasons also carry their exact canonical ``selector``.
    """

    model_config = ConfigDict(frozen=True)

    code: AdmissionHoldCode
    source_code: str | None = None
    selector: str | None = None
    observed: int | None = Field(default=None, ge=0)
    limit: int | None = Field(default=None, ge=0)
    reserved_capacity: int | None = Field(default=None, ge=0)
    issue_id: str | None = None
    issue_identifier: str | None = None
    ticket_key: str | None = None
    state_id: str | None = None
    pagination_cursor: str | None = None


class AdmissionRankInputs(BaseModel):
    """Every input to the documented stable candidate ordering."""

    model_config = ConfigDict(frozen=True)

    unlock_count: int = Field(ge=0)
    critical_path_member: bool
    critical_path_position: int | None = Field(default=None, ge=0)
    priority: int
    risk_level: RiskLevel
    risk_severity: int = Field(ge=0, le=3)
    continuously_eligible_since: datetime
    continuously_eligible_age_microseconds: int = Field(ge=0)

    @model_validator(mode="after")
    def _critical_path_fields_are_coherent(self) -> Self:
        if self.critical_path_member != (self.critical_path_position is not None):
            raise ValueError(
                "critical_path_member and critical_path_position are inconsistent"
            )
        return self


class AdmissionCandidateDecision(BaseModel):
    """The rank, policy result and complete reasons for one candidate."""

    model_config = ConfigDict(frozen=True)

    ticket_id: UUID
    ticket_key: str
    external_linear_id: str | None = None
    rank: int = Field(ge=1)
    rank_inputs: AdmissionRankInputs
    decision: AdmissionDecisionType
    reasons: tuple[AdmissionHoldReason, ...] = ()

    @model_validator(mode="after")
    def _decision_matches_reasons(self) -> Self:
        if self.decision is AdmissionDecisionType.ADMIT and self.reasons:
            raise ValueError("an admitted candidate cannot carry hold reasons")
        if self.decision is AdmissionDecisionType.HOLD and not self.reasons:
            raise ValueError("a held candidate requires at least one reason")
        return self


class AdmissionRun(BaseModel):
    """One immutable, append-only deterministic admission evaluation."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["admission-run-v1"] = "admission-run-v1"
    id: UUID
    product_id: UUID
    policy_id: UUID
    policy_revision: int = Field(ge=1)
    policy_fingerprint: str = Field(min_length=64, max_length=64)
    snapshot_fingerprint: str = Field(min_length=64, max_length=64)
    snapshot_observed_at: datetime
    evaluated_at: datetime
    selected_ticket_id: UUID | None = None
    selected_ticket_key: str | None = None
    decisions: tuple[AdmissionCandidateDecision, ...]
    created_by_type: ActorType
    created_by_id: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def _selection_matches_decisions(self) -> Self:
        ranks = [decision.rank for decision in self.decisions]
        if ranks != list(range(1, len(ranks) + 1)):
            raise ValueError("admission decision ranks must be contiguous and ordered")
        ticket_keys = [decision.ticket_key for decision in self.decisions]
        if len(ticket_keys) != len(set(ticket_keys)):
            raise ValueError("admission decisions must have unique ticket keys")

        admitted = [
            decision
            for decision in self.decisions
            if decision.decision is AdmissionDecisionType.ADMIT
        ]
        if len(admitted) > 1:
            raise ValueError("an admission run may select at most one candidate")
        if not admitted:
            if (
                self.selected_ticket_id is not None
                or self.selected_ticket_key is not None
            ):
                raise ValueError("an empty selection cannot name a selected ticket")
            return self

        selected = admitted[0]
        if (
            self.selected_ticket_id != selected.ticket_id
            or self.selected_ticket_key != selected.ticket_key
        ):
            raise ValueError("selected ticket fields must identify the admit decision")
        return self

    def canonical_bytes(self) -> bytes:
        """Return a compact, key-sorted representation of the complete run."""

        rendered = json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )
        return rendered.encode("utf-8")

    @property
    def fingerprint(self) -> str:
        """SHA-256 of :meth:`canonical_bytes`."""

        return hashlib.sha256(self.canonical_bytes()).hexdigest()
