"""Read-only delivery-control assembly for authenticated operator surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from atlas.core.enums import RiskLevel
from atlas.core.models import DeliveryAdmissionPolicyRevision
from atlas.core.models.admission_run import (
    AdmissionDecisionType,
    AdmissionHoldCode,
    AdmissionHoldReason,
    AdmissionRankInputs,
)
from atlas.pm import (
    AdmissionSyncReason,
    StoredDeliveryOccupancy,
    build_stored_delivery_occupancy,
)
from atlas.storage import (
    AdmissionCoordinationRepo,
    AdmissionRunRepo,
    Database,
    DeliveryAdmissionPolicyRepo,
    PmSyncReceiptRepo,
    ProductRepo,
    TicketRepo,
)

MAX_DELIVERY_CONTROL_DECISIONS = 100
MAX_DELIVERY_CONTROL_TEXT = 128
MAX_DELIVERY_CONTROL_COUNT = 1_000_000


class DeliveryControlReadStatus(StrEnum):
    """Whether the singleton delivery-control projection can be assembled."""

    AVAILABLE = "available"
    PRODUCT_UNAVAILABLE = "product_unavailable"
    POLICY_UNAVAILABLE = "policy_unavailable"


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
    """Current unresolved external-write fence, if one exists."""

    reason: AdmissionSyncReason
    state: Literal["pending", "indeterminate"]
    admission_run_id: UUID
    ticket_key: str
    policy_revision: int
    observed_at: datetime


@dataclass(frozen=True)
class DeliveryControlState:
    """Complete observational state returned by one application-service call."""

    status: DeliveryControlReadStatus
    policy: DeliveryAdmissionPolicyRevision | None = None
    last_linear_sync_at: datetime | None = None
    occupancy: StoredDeliveryOccupancy | None = None
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
    """Project only immutable ordering inputs, never stored ticket identities."""

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
    database: Database, product_id: UUID
) -> DeliveryControlAdmissionState | None:
    run = AdmissionRunRepo(database).latest_for_product(product_id)
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


def _indeterminate_reasons(
    database: Database, product_id: UUID
) -> tuple[DeliveryControlIndeterminateReason, ...]:
    fence = AdmissionCoordinationRepo(database).get_fence(product_id)
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


def delivery_control_status(database: Database) -> DeliveryControlState:
    """Read current delivery control without a lease, Linear call, or write."""

    products = ProductRepo(database).list()
    if len(products) != 1:
        return DeliveryControlState(
            status=DeliveryControlReadStatus.PRODUCT_UNAVAILABLE
        )
    product = products[0]
    policy = DeliveryAdmissionPolicyRepo(database).get_active(product.id)
    if policy is None:
        return DeliveryControlState(status=DeliveryControlReadStatus.POLICY_UNAVAILABLE)

    tickets = TicketRepo(database).list()
    return DeliveryControlState(
        status=DeliveryControlReadStatus.AVAILABLE,
        policy=policy,
        last_linear_sync_at=PmSyncReceiptRepo(database).latest_successful_finished_at(
            product.id
        ),
        occupancy=build_stored_delivery_occupancy(policy=policy, tickets=tickets),
        latest_admission=_latest_admission(database, product.id),
        indeterminate_reasons=_indeterminate_reasons(database, product.id),
    )
