"""Read-only delivery-control assembly for authenticated operator surfaces."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from atlas.core.enums import EvidenceStatus, RiskLevel
from atlas.core.models import (
    AcceptanceSession,
    AcceptanceSessionBlockingReason,
    AcceptanceSessionLifecycle,
    CIHandoffClassification,
    CIHandoffDecision,
    CIHandoffReason,
    DeliveryAdmissionPolicyRevision,
)
from atlas.core.models.admission_run import (
    AdmissionDecisionType,
    AdmissionHoldCode,
    AdmissionHoldReason,
    AdmissionRankInputs,
)
from atlas.core.models.ci_handoff_reconciliation import CIHandoffCheckResult
from atlas.pm import (
    AdmissionSyncReason,
    StoredDeliveryOccupancy,
    build_stored_delivery_occupancy,
    delivery_policy_fingerprint,
    delivery_store_revision,
)
from atlas.storage import (
    Database,
    DeliveryControlSnapshotRepo,
    DeliveryControlSourceSnapshot,
)
from atlas.verification.validation_plan import REGISTRY_SHA256, REGISTRY_VERSION

MAX_DELIVERY_CONTROL_DECISIONS = 100
MAX_DELIVERY_CONTROL_CI_TICKETS = 100
MAX_DELIVERY_CONTROL_EVIDENCE_IDS = 100
MAX_DELIVERY_CONTROL_CHECK_EVIDENCE_IDS = 32
MAX_DELIVERY_CONTROL_PROTECTED_HOLDS = 3_200
MAX_DELIVERY_CONTROL_TEXT = 128
MAX_DELIVERY_CONTROL_COUNT = 1_000_000


class DeliveryControlReadStatus(StrEnum):
    """Whether the singleton delivery-control projection can be assembled."""

    AVAILABLE = "available"
    PRODUCT_UNAVAILABLE = "product_unavailable"
    POLICY_UNAVAILABLE = "policy_unavailable"


class DeliveryControlSnapshotStatus(StrEnum):
    """Freshness of the complete server-owned projection."""

    COHERENT = "coherent"
    STALE = "stale"
    INDETERMINATE = "indeterminate"


class DeliveryControlProjectionReason(StrEnum):
    """Closed reasons why pressure or candidate provenance is fail-closed."""

    SUCCESSFUL_BOARD_UNAVAILABLE = "successful_board_unavailable"
    NEWER_BOARD_REFRESH_UNSUCCESSFUL = "newer_board_refresh_unsuccessful"
    EVIDENCE_IDENTITY_MISSING = "evidence_identity_missing"
    PROTECTED_LANE_CLASSIFICATION_INVALID = "protected_lane_classification_invalid"
    CI_RECONCILIATION_UNAVAILABLE = "ci_reconciliation_unavailable"
    CI_HANDOFF_WRITE_INDETERMINATE = "ci_handoff_write_indeterminate"
    VALIDATION_PLAN_PROVENANCE_UNAVAILABLE = "validation_plan_provenance_unavailable"
    EXACT_BASE_ASSESSMENT_UNAVAILABLE = "exact_base_assessment_unavailable"
    INTEGRATION_IDENTITY_MISMATCH = "integration_identity_mismatch"
    ACCEPTANCE_ASSESSMENT_STALE = "acceptance_assessment_stale"
    INTEGRATION_BEHIND = "integration_behind"
    INTEGRATION_DIVERGED = "integration_diverged"
    INTEGRATION_CONFLICTED = "integration_conflicted"
    INTEGRATION_INDETERMINATE = "integration_indeterminate"


class DeliveryControlValidationPlanStatus(StrEnum):
    """Whether exact local-validation provenance is stored for a candidate."""

    AVAILABLE = "available"
    STALE = "stale"
    INDETERMINATE = "indeterminate"


class DeliveryControlExactBaseStatus(StrEnum):
    """Stored exact-base assessment without a live GitHub refresh."""

    EXACT_BRANCH = "exact_branch"
    REBASE_REQUIRED = "rebase_required"
    STALE = "stale"
    INDETERMINATE = "indeterminate"


@dataclass(frozen=True)
class DeliveryControlHoldReason:
    """Bounded reason projection without raw Linear identity fields."""

    code: AdmissionHoldCode
    source_code: str | None = None
    selector: str | None = None
    observed: int | None = None
    limit: int | None = None
    reserved_capacity: int | None = None
    owner_ticket_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class DeliveryControlRankInputs:
    """Fixed safe projection of every persisted deterministic rank input."""

    unlock_count: int
    critical_path_member: bool
    critical_path_position: int | None
    priority: int
    risk_level: RiskLevel
    risk_severity: int
    continuously_eligible_since: datetime
    continuously_eligible_age_microseconds: int


@dataclass(frozen=True)
class DeliveryControlDecision:
    """One bounded candidate decision from the latest immutable run."""

    ticket_key: str
    rank: int
    rank_inputs: DeliveryControlRankInputs
    decision: AdmissionDecisionType
    reasons: tuple[DeliveryControlHoldReason, ...]
    protected_lanes: tuple[str, ...]
    protected_lane_registry_version: str | None
    protected_lane_registry_fingerprint: str | None


@dataclass(frozen=True)
class DeliveryControlAdmissionState:
    """Bounded latest admission-run projection."""

    run_id: UUID
    policy_revision: int
    policy_fingerprint: str
    snapshot_fingerprint: str
    snapshot_observed_at: datetime
    evaluated_at: datetime
    selected_ticket_key: str | None
    decision_count: int
    decisions_truncated: bool
    decisions: tuple[DeliveryControlDecision, ...]


@dataclass(frozen=True)
class DeliveryControlIndeterminateReason:
    """Current unresolved admission external-write fence, if one exists."""

    reason: AdmissionSyncReason
    state: Literal["pending", "indeterminate"]
    admission_run_id: UUID
    ticket_key: str
    policy_revision: int
    observed_at: datetime


@dataclass(frozen=True)
class DeliveryControlBoardIdentity:
    """Last successful materialised board and the newest attempted refresh."""

    status: DeliveryControlSnapshotStatus
    reasons: tuple[DeliveryControlProjectionReason, ...]
    receipt_id: UUID | None
    status_map_fingerprint: str | None
    fetched_board_fingerprint: str | None
    fetched_board_issue_count: int | None
    observed_at: datetime | None
    latest_attempt_receipt_id: UUID | None
    latest_attempt_result: str | None
    latest_attempt_finished_at: datetime | None
    materialized_ticket_fingerprint: str


@dataclass(frozen=True)
class DeliveryControlEvidenceSetIdentity:
    """Exact bounded identity of evidence selected by CI reconciliations."""

    fingerprint: str
    evidence_count: int
    evidence_ids: tuple[UUID, ...]
    evidence_ids_truncated: bool


@dataclass(frozen=True)
class DeliveryControlIntegrationIdentity:
    """Exact identities of the persisted integration-pressure inputs."""

    fingerprint: str
    reconciliation_count: int
    reconciliation_ids: tuple[UUID, ...]
    reconciliation_ids_truncated: bool
    acceptance_session_count: int
    acceptance_session_ids: tuple[UUID, ...]
    acceptance_session_ids_truncated: bool
    protected_lane_registry_version: str
    protected_lane_registry_fingerprint: str
    protected_lane_state_fingerprint: str
    validation_registry_version: str
    validation_registry_fingerprint: str


@dataclass(frozen=True)
class DeliveryControlSnapshotIdentity:
    """One coherent policy/board/evidence/integration identity."""

    fingerprint: str
    status: DeliveryControlSnapshotStatus
    reasons: tuple[DeliveryControlProjectionReason, ...]
    policy_id: UUID
    policy_revision: int
    policy_fingerprint: str
    board: DeliveryControlBoardIdentity
    evidence: DeliveryControlEvidenceSetIdentity
    integration: DeliveryControlIntegrationIdentity


@dataclass(frozen=True)
class DeliveryControlCICheck:
    """One persisted typed CI check without provider payloads."""

    check_type: str
    status: EvidenceStatus
    classification: CIHandoffClassification
    evidence_count: int
    evidence_ids: tuple[UUID, ...]
    evidence_ids_truncated: bool


@dataclass(frozen=True)
class DeliveryControlCIOutcome:
    """Latest canonical CI reconciliation for one pending ticket."""

    reconciliation_id: UUID | None
    classification: CIHandoffClassification
    decision: CIHandoffDecision
    reason: CIHandoffReason | None
    observed_at: datetime | None
    check_results: tuple[DeliveryControlCICheck, ...]
    projection_reasons: tuple[DeliveryControlProjectionReason, ...] = ()


@dataclass(frozen=True)
class DeliveryControlValidationPlanIdentity:
    """Stored plan identity, or an explicit fail-closed absence."""

    status: DeliveryControlValidationPlanStatus
    registry_version: str
    registry_fingerprint: str
    plan_fingerprint: str | None
    base_sha: str | None
    head_sha: str | None
    profiles: tuple[str, ...]
    reasons: tuple[DeliveryControlProjectionReason, ...]


@dataclass(frozen=True)
class DeliveryControlExactBaseAssessment:
    """Persisted acceptance assessment; never a live provider inference."""

    status: DeliveryControlExactBaseStatus
    assessment_id: UUID | None
    head_sha: str | None
    base_sha: str | None
    observed_at: datetime | None
    reasons: tuple[DeliveryControlProjectionReason, ...]


@dataclass(frozen=True)
class DeliveryControlCIPendingTicket:
    """One bounded CI-pending candidate and all available source provenance."""

    ticket_key: str
    repository_owner: str | None
    repository_name: str | None
    pr_number: int | None
    head_sha: str | None
    outcome: DeliveryControlCIOutcome
    validation_plan: DeliveryControlValidationPlanIdentity
    exact_base: DeliveryControlExactBaseAssessment


@dataclass(frozen=True)
class DeliveryControlProtectedLaneHold:
    """One persisted admission hold for a repository-protected lane."""

    ticket_key: str
    lane: str
    observed: int | None
    limit: int | None
    owner_ticket_keys: tuple[str, ...]


@dataclass(frozen=True)
class DeliveryControlState:
    """Complete observational state returned by one application-service call."""

    status: DeliveryControlReadStatus
    policy: DeliveryAdmissionPolicyRevision | None = None
    last_linear_sync_at: datetime | None = None
    occupancy: StoredDeliveryOccupancy | None = None
    snapshot: DeliveryControlSnapshotIdentity | None = None
    ci_pending_ticket_count: int = 0
    ci_pending_tickets_truncated: bool = False
    ci_pending_tickets: tuple[DeliveryControlCIPendingTicket, ...] = ()
    protected_lane_holds: tuple[DeliveryControlProtectedLaneHold, ...] = ()
    latest_admission: DeliveryControlAdmissionState | None = None
    indeterminate_reasons: tuple[DeliveryControlIndeterminateReason, ...] = ()


def _bounded_text(value: str | None) -> str | None:
    if value is None:
        return None
    return value[:MAX_DELIVERY_CONTROL_TEXT]


def _bounded_count(value: int | None) -> int | None:
    if value is None:
        return None
    return min(value, MAX_DELIVERY_CONTROL_COUNT)


def _hash(payload: object) -> str:
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _source_code(value: str | None) -> str | None:
    return _bounded_text(value)


def _hold_reasons(
    reasons: tuple[AdmissionHoldReason, ...],
) -> tuple[DeliveryControlHoldReason, ...]:
    """Deduplicate per-issue diagnostics while retaining every typed reason."""

    bounded: dict[tuple[object, ...], DeliveryControlHoldReason] = {}
    for raw in reasons:
        reason = DeliveryControlHoldReason(
            code=raw.code,
            source_code=_source_code(raw.source_code),
            selector=_bounded_text(raw.selector),
            observed=_bounded_count(raw.observed),
            limit=_bounded_count(raw.limit),
            reserved_capacity=_bounded_count(raw.reserved_capacity),
            owner_ticket_keys=tuple(
                sorted(
                    {
                        _bounded_text(key) or ""
                        for key in raw.owner_ticket_keys
                        if _bounded_text(key)
                    }
                )
            )[:100],
        )
        key = (
            reason.code.value,
            reason.source_code or "",
            reason.selector or "",
            -1 if reason.observed is None else reason.observed,
            -1 if reason.limit is None else reason.limit,
            -1 if reason.reserved_capacity is None else reason.reserved_capacity,
            reason.owner_ticket_keys,
        )
        bounded[key] = reason
    return tuple(bounded[key] for key in sorted(bounded))


def _rank_inputs(inputs: AdmissionRankInputs) -> DeliveryControlRankInputs:
    return DeliveryControlRankInputs(
        unlock_count=inputs.unlock_count,
        critical_path_member=inputs.critical_path_member,
        critical_path_position=inputs.critical_path_position,
        priority=inputs.priority,
        risk_level=inputs.risk_level,
        risk_severity=inputs.risk_severity,
        continuously_eligible_since=inputs.continuously_eligible_since,
        continuously_eligible_age_microseconds=(
            inputs.continuously_eligible_age_microseconds
        ),
    )


def _latest_admission(
    source: DeliveryControlSourceSnapshot,
) -> DeliveryControlAdmissionState | None:
    run = source.latest_admission
    if run is None:
        return None
    selected = run.decisions[:MAX_DELIVERY_CONTROL_DECISIONS]
    return DeliveryControlAdmissionState(
        run_id=run.id,
        policy_revision=run.policy_revision,
        policy_fingerprint=run.policy_fingerprint,
        snapshot_fingerprint=run.snapshot_fingerprint,
        snapshot_observed_at=run.snapshot_observed_at,
        evaluated_at=run.evaluated_at,
        selected_ticket_key=_bounded_text(run.selected_ticket_key),
        decision_count=len(run.decisions),
        decisions_truncated=len(run.decisions) > len(selected),
        decisions=tuple(
            DeliveryControlDecision(
                ticket_key=_bounded_text(decision.ticket_key) or "",
                rank=min(decision.rank, MAX_DELIVERY_CONTROL_COUNT),
                rank_inputs=_rank_inputs(decision.rank_inputs),
                decision=decision.decision,
                reasons=_hold_reasons(decision.reasons),
                protected_lanes=tuple(
                    sorted(
                        {
                            _bounded_text(lane) or ""
                            for lane in decision.protected_lanes
                            if _bounded_text(lane)
                        }
                    )
                ),
                protected_lane_registry_version=_bounded_text(
                    decision.protected_lane_registry_version
                ),
                protected_lane_registry_fingerprint=(
                    decision.protected_lane_registry_fingerprint
                ),
            )
            for decision in selected
        ),
    )


def _admission_indeterminate_reasons(
    source: DeliveryControlSourceSnapshot,
) -> tuple[DeliveryControlIndeterminateReason, ...]:
    fence = source.admission_fence
    if fence is None:
        return ()
    state: Literal["pending", "indeterminate"] = (
        "pending" if fence.state == "pending" else "indeterminate"
    )
    return (
        DeliveryControlIndeterminateReason(
            reason=AdmissionSyncReason.WRITE_INDETERMINATE,
            state=state,
            admission_run_id=fence.admission_run_id,
            ticket_key=_bounded_text(fence.ticket_key) or "",
            policy_revision=fence.policy_revision,
            observed_at=fence.updated_at,
        ),
    )


def _board_identity(
    source: DeliveryControlSourceSnapshot,
    *,
    product_id: UUID,
) -> DeliveryControlBoardIdentity:
    successful = source.latest_successful_sync
    latest = source.latest_sync
    reasons: list[DeliveryControlProjectionReason] = []
    status = DeliveryControlSnapshotStatus.COHERENT
    if successful is None:
        status = DeliveryControlSnapshotStatus.INDETERMINATE
        reasons.append(DeliveryControlProjectionReason.SUCCESSFUL_BOARD_UNAVAILABLE)
    elif latest is not None and latest.id != successful.id:
        status = DeliveryControlSnapshotStatus.STALE
        reasons.append(DeliveryControlProjectionReason.NEWER_BOARD_REFRESH_UNSUCCESSFUL)
    return DeliveryControlBoardIdentity(
        status=status,
        reasons=tuple(reasons),
        receipt_id=None if successful is None else successful.id,
        status_map_fingerprint=(
            None if successful is None else successful.status_map_fingerprint
        ),
        fetched_board_fingerprint=(
            None if successful is None else successful.fetched_board_fingerprint
        ),
        fetched_board_issue_count=(
            None if successful is None else successful.fetched_board_issue_count
        ),
        observed_at=None if successful is None else successful.finished_at,
        latest_attempt_receipt_id=None if latest is None else latest.id,
        latest_attempt_result=None if latest is None else latest.result.value,
        latest_attempt_finished_at=None if latest is None else latest.finished_at,
        materialized_ticket_fingerprint=delivery_store_revision(
            product_id, source.tickets
        ),
    )


def _evidence_identity(
    source: DeliveryControlSourceSnapshot,
) -> tuple[DeliveryControlEvidenceSetIdentity, bool]:
    referenced = tuple(
        sorted(
            {
                evidence_id
                for reconciliation in source.ci_reconciliations
                for check in reconciliation.check_results
                for evidence_id in check.evidence_ids
            },
            key=str,
        )
    )
    observed_by_id = {item.id: item for item in source.evidence_identities}
    payload = [
        {
            "id": str(item.id),
            "commit_sha": item.commit_sha,
            "external_run_id": item.external_run_id,
            "job_name": item.job_name,
            "payload_hash": item.payload_hash,
            "status": item.status.value,
            "source_event_at": (
                None
                if item.source_event_at is None
                else item.source_event_at.isoformat()
            ),
            "created_at": item.created_at.isoformat(),
        }
        for item in sorted(source.evidence_identities, key=lambda item: str(item.id))
    ]
    selected = referenced[:MAX_DELIVERY_CONTROL_EVIDENCE_IDS]
    return (
        DeliveryControlEvidenceSetIdentity(
            fingerprint=_hash(payload),
            evidence_count=len(referenced),
            evidence_ids=selected,
            evidence_ids_truncated=len(referenced) > len(selected),
        ),
        any(evidence_id not in observed_by_id for evidence_id in referenced),
    )


def _integration_identity(
    source: DeliveryControlSourceSnapshot,
    occupancy: StoredDeliveryOccupancy,
) -> DeliveryControlIntegrationIdentity:
    all_reconciliation_ids = tuple(
        reconciliation.id for reconciliation in source.ci_reconciliations
    )
    all_session_ids = tuple(session.id for session in source.acceptance_sessions)
    reconciliation_ids = all_reconciliation_ids[:MAX_DELIVERY_CONTROL_CI_TICKETS]
    session_ids = all_session_ids[:MAX_DELIVERY_CONTROL_CI_TICKETS]
    payload = {
        "ci_pending_ticket_keys": list(occupancy.integration_ticket_keys),
        "reconciliations": [
            {
                "id": str(item.id),
                "ticket_key": item.ticket_key,
                "head_commit": item.head_commit,
                "classification": item.classification.value,
                "decision": item.decision.value,
                "reason": item.reason.value,
                "observed_at": item.observed_at.isoformat(),
            }
            for item in source.ci_reconciliations
        ],
        "acceptance_sessions": [
            {
                "id": str(item.id),
                "head_sha": item.head_sha,
                "base_sha": item.base_sha,
                "lifecycle": item.lifecycle.value,
                "updated_at": item.updated_at.isoformat(),
            }
            for item in source.acceptance_sessions
        ],
        "protected_lane_state_fingerprint": (
            occupancy.protected_lane_state_fingerprint
        ),
        "validation_registry_fingerprint": REGISTRY_SHA256,
    }
    return DeliveryControlIntegrationIdentity(
        fingerprint=_hash(payload),
        reconciliation_count=len(all_reconciliation_ids),
        reconciliation_ids=reconciliation_ids,
        reconciliation_ids_truncated=(
            len(all_reconciliation_ids) > len(reconciliation_ids)
        ),
        acceptance_session_count=len(all_session_ids),
        acceptance_session_ids=session_ids,
        acceptance_session_ids_truncated=len(all_session_ids) > len(session_ids),
        protected_lane_registry_version=(occupancy.protected_lane_registry_version),
        protected_lane_registry_fingerprint=(
            occupancy.protected_lane_registry_fingerprint
        ),
        protected_lane_state_fingerprint=(occupancy.protected_lane_state_fingerprint),
        validation_registry_version=REGISTRY_VERSION,
        validation_registry_fingerprint=REGISTRY_SHA256,
    )


def _ci_check(check: CIHandoffCheckResult) -> DeliveryControlCICheck:
    evidence_ids = check.evidence_ids[:MAX_DELIVERY_CONTROL_CHECK_EVIDENCE_IDS]
    return DeliveryControlCICheck(
        check_type=check.check_type.value,
        status=check.status,
        classification=check.classification,
        evidence_count=len(check.evidence_ids),
        evidence_ids=evidence_ids,
        evidence_ids_truncated=len(check.evidence_ids) > len(evidence_ids),
    )


_REBASE_REASONS = {
    AcceptanceSessionBlockingReason.INTEGRATION_BEHIND: (
        DeliveryControlProjectionReason.INTEGRATION_BEHIND
    ),
    AcceptanceSessionBlockingReason.INTEGRATION_DIVERGED: (
        DeliveryControlProjectionReason.INTEGRATION_DIVERGED
    ),
    AcceptanceSessionBlockingReason.INTEGRATION_CONFLICTED: (
        DeliveryControlProjectionReason.INTEGRATION_CONFLICTED
    ),
}


def _exact_base(
    head_sha: str | None,
    session: AcceptanceSession | None,
) -> DeliveryControlExactBaseAssessment:
    if session is None:
        return DeliveryControlExactBaseAssessment(
            status=DeliveryControlExactBaseStatus.INDETERMINATE,
            assessment_id=None,
            head_sha=head_sha,
            base_sha=None,
            observed_at=None,
            reasons=(
                DeliveryControlProjectionReason.EXACT_BASE_ASSESSMENT_UNAVAILABLE,
            ),
        )
    if head_sha is None or session.head_sha != head_sha:
        return DeliveryControlExactBaseAssessment(
            status=DeliveryControlExactBaseStatus.INDETERMINATE,
            assessment_id=session.id,
            head_sha=head_sha,
            base_sha=session.base_sha,
            observed_at=session.updated_at,
            reasons=(DeliveryControlProjectionReason.INTEGRATION_IDENTITY_MISMATCH,),
        )
    rebase_reasons = tuple(
        sorted(
            {
                projected
                for reason in session.blocking_reasons
                if (projected := _REBASE_REASONS.get(reason)) is not None
            },
            key=lambda reason: reason.value,
        )
    )
    if rebase_reasons:
        return DeliveryControlExactBaseAssessment(
            status=DeliveryControlExactBaseStatus.REBASE_REQUIRED,
            assessment_id=session.id,
            head_sha=head_sha,
            base_sha=session.base_sha,
            observed_at=session.updated_at,
            reasons=rebase_reasons,
        )
    if any(
        reason
        in {
            AcceptanceSessionBlockingReason.INTEGRATION_INDETERMINATE,
            AcceptanceSessionBlockingReason.EXTERNAL_STATE_INDETERMINATE,
        }
        for reason in session.blocking_reasons
    ):
        return DeliveryControlExactBaseAssessment(
            status=DeliveryControlExactBaseStatus.INDETERMINATE,
            assessment_id=session.id,
            head_sha=head_sha,
            base_sha=session.base_sha,
            observed_at=session.updated_at,
            reasons=(DeliveryControlProjectionReason.INTEGRATION_INDETERMINATE,),
        )
    if session.lifecycle is AcceptanceSessionLifecycle.STALE:
        return DeliveryControlExactBaseAssessment(
            status=DeliveryControlExactBaseStatus.STALE,
            assessment_id=session.id,
            head_sha=head_sha,
            base_sha=session.base_sha,
            observed_at=session.updated_at,
            reasons=(DeliveryControlProjectionReason.ACCEPTANCE_ASSESSMENT_STALE,),
        )
    return DeliveryControlExactBaseAssessment(
        status=DeliveryControlExactBaseStatus.EXACT_BRANCH,
        assessment_id=session.id,
        head_sha=head_sha,
        base_sha=session.base_sha,
        observed_at=session.updated_at,
        reasons=(),
    )


def _ci_pending_tickets(
    source: DeliveryControlSourceSnapshot,
) -> tuple[
    int,
    bool,
    tuple[DeliveryControlCIPendingTicket, ...],
    tuple[DeliveryControlProjectionReason, ...],
]:
    tickets = tuple(
        ticket for ticket in source.tickets if ticket.status.value == "ci_pending"
    )
    reconciliation_by_ticket = {
        reconciliation.ticket_id: reconciliation
        for reconciliation in source.ci_reconciliations
    }
    session_by_pr = {
        (session.repository_owner, session.repository_name, session.pr_number): session
        for session in source.acceptance_sessions
    }
    projected: list[DeliveryControlCIPendingTicket] = []
    snapshot_reasons: set[DeliveryControlProjectionReason] = set()
    for ticket in tickets[:MAX_DELIVERY_CONTROL_CI_TICKETS]:
        reconciliation = reconciliation_by_ticket.get(ticket.id)
        if reconciliation is None:
            projection_reasons = (
                DeliveryControlProjectionReason.CI_RECONCILIATION_UNAVAILABLE,
            )
            snapshot_reasons.update(projection_reasons)
            outcome = DeliveryControlCIOutcome(
                reconciliation_id=None,
                classification=CIHandoffClassification.INDETERMINATE,
                decision=CIHandoffDecision.HOLD,
                reason=None,
                observed_at=None,
                check_results=(),
                projection_reasons=projection_reasons,
            )
            owner = name = None
            pr_number = None
            head_sha = None
            session = None
        else:
            outcome = DeliveryControlCIOutcome(
                reconciliation_id=reconciliation.id,
                classification=reconciliation.classification,
                decision=reconciliation.decision,
                reason=reconciliation.reason,
                observed_at=reconciliation.observed_at,
                check_results=tuple(
                    _ci_check(check) for check in reconciliation.check_results
                ),
            )
            owner = _bounded_text(reconciliation.repository_owner)
            name = _bounded_text(reconciliation.repository_name)
            pr_number = reconciliation.pr_number
            head_sha = reconciliation.head_commit
            session = session_by_pr.get(
                (
                    reconciliation.repository_owner,
                    reconciliation.repository_name,
                    reconciliation.pr_number,
                )
            )

        validation = DeliveryControlValidationPlanIdentity(
            status=DeliveryControlValidationPlanStatus.INDETERMINATE,
            registry_version=REGISTRY_VERSION,
            registry_fingerprint=REGISTRY_SHA256,
            plan_fingerprint=None,
            base_sha=None,
            head_sha=head_sha,
            profiles=(),
            reasons=(
                DeliveryControlProjectionReason.VALIDATION_PLAN_PROVENANCE_UNAVAILABLE,
            ),
        )
        snapshot_reasons.update(validation.reasons)
        exact_base = _exact_base(head_sha, session)
        if exact_base.status in {
            DeliveryControlExactBaseStatus.INDETERMINATE,
            DeliveryControlExactBaseStatus.STALE,
        }:
            snapshot_reasons.update(exact_base.reasons)
        projected.append(
            DeliveryControlCIPendingTicket(
                ticket_key=_bounded_text(ticket.key) or "",
                repository_owner=owner,
                repository_name=name,
                pr_number=pr_number,
                head_sha=head_sha,
                outcome=outcome,
                validation_plan=validation,
                exact_base=exact_base,
            )
        )
    return (
        len(tickets),
        len(tickets) > len(projected),
        tuple(projected),
        tuple(sorted(snapshot_reasons, key=lambda reason: reason.value)),
    )


def _protected_lane_holds(
    latest: DeliveryControlAdmissionState | None,
) -> tuple[DeliveryControlProtectedLaneHold, ...]:
    if latest is None:
        return ()
    holds = {
        (
            decision.ticket_key,
            reason.selector or "",
            -1 if reason.observed is None else reason.observed,
            -1 if reason.limit is None else reason.limit,
            reason.owner_ticket_keys,
        ): DeliveryControlProtectedLaneHold(
            ticket_key=decision.ticket_key,
            lane=reason.selector or "",
            observed=reason.observed,
            limit=reason.limit,
            owner_ticket_keys=reason.owner_ticket_keys,
        )
        for decision in latest.decisions
        for reason in decision.reasons
        if reason.code is AdmissionHoldCode.PROTECTED_LANE and reason.selector
    }
    return tuple(
        holds[key] for key in sorted(holds)[:MAX_DELIVERY_CONTROL_PROTECTED_HOLDS]
    )


def delivery_control_status(database: Database) -> DeliveryControlState:
    """Read one coherent snapshot without a lease, provider call, or write."""

    source = DeliveryControlSnapshotRepo(database).read()
    if len(source.products) != 1:
        return DeliveryControlState(
            status=DeliveryControlReadStatus.PRODUCT_UNAVAILABLE
        )
    product = source.products[0]
    policy = source.policy
    if policy is None:
        return DeliveryControlState(status=DeliveryControlReadStatus.POLICY_UNAVAILABLE)

    occupancy = build_stored_delivery_occupancy(policy=policy, tickets=source.tickets)
    board = _board_identity(source, product_id=product.id)
    evidence, evidence_missing = _evidence_identity(source)
    integration = _integration_identity(source, occupancy)
    ci_count, ci_truncated, ci_tickets, ci_reasons = _ci_pending_tickets(source)
    reasons = set(board.reasons) | set(ci_reasons)
    if evidence_missing:
        reasons.add(DeliveryControlProjectionReason.EVIDENCE_IDENTITY_MISSING)
    if occupancy.protected_lane_incompleteness_reasons:
        reasons.add(
            DeliveryControlProjectionReason.PROTECTED_LANE_CLASSIFICATION_INVALID
        )
    if source.ci_handoff_fence is not None:
        reasons.add(DeliveryControlProjectionReason.CI_HANDOFF_WRITE_INDETERMINATE)

    if board.status is DeliveryControlSnapshotStatus.STALE:
        snapshot_status = DeliveryControlSnapshotStatus.STALE
    elif reasons:
        snapshot_status = DeliveryControlSnapshotStatus.INDETERMINATE
    else:
        snapshot_status = DeliveryControlSnapshotStatus.COHERENT

    policy_fingerprint = delivery_policy_fingerprint(policy)
    ordered_reasons = tuple(sorted(reasons, key=lambda reason: reason.value))
    snapshot_fingerprint = _hash(
        {
            "policy": {
                "id": str(policy.id),
                "revision": policy.revision,
                "fingerprint": policy_fingerprint,
            },
            "board": {
                "receipt_id": None
                if board.receipt_id is None
                else str(board.receipt_id),
                "status": board.status.value,
                "status_map_fingerprint": board.status_map_fingerprint,
                "fetched_board_fingerprint": board.fetched_board_fingerprint,
                "materialized_ticket_fingerprint": (
                    board.materialized_ticket_fingerprint
                ),
            },
            "evidence_fingerprint": evidence.fingerprint,
            "integration_fingerprint": integration.fingerprint,
            "status": snapshot_status.value,
            "reasons": [reason.value for reason in ordered_reasons],
        }
    )
    snapshot = DeliveryControlSnapshotIdentity(
        fingerprint=snapshot_fingerprint,
        status=snapshot_status,
        reasons=ordered_reasons,
        policy_id=policy.id,
        policy_revision=policy.revision,
        policy_fingerprint=policy_fingerprint,
        board=board,
        evidence=evidence,
        integration=integration,
    )

    # Fail closed: a stale/indeterminate snapshot never advertises unused
    # integration slots as currently available capacity.
    if snapshot_status is not DeliveryControlSnapshotStatus.COHERENT:
        occupancy = occupancy.model_copy(
            update={
                "new_admission_integration_capacity": 0,
                "new_admission_working_capacity": 0,
            }
        )

    latest = _latest_admission(source)
    return DeliveryControlState(
        status=DeliveryControlReadStatus.AVAILABLE,
        policy=policy,
        last_linear_sync_at=(
            None
            if source.latest_successful_sync is None
            else source.latest_successful_sync.finished_at
        ),
        occupancy=occupancy,
        snapshot=snapshot,
        ci_pending_ticket_count=ci_count,
        ci_pending_tickets_truncated=ci_truncated,
        ci_pending_tickets=ci_tickets,
        protected_lane_holds=_protected_lane_holds(latest),
        latest_admission=latest,
        indeterminate_reasons=_admission_indeterminate_reasons(source),
    )
