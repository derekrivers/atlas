"""Pure deterministic capacity-aware delivery admission (ATLAS-248)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

import networkx as nx

from atlas.core.enums import ActorType, RiskLevel
from atlas.core.keys import natural_key
from atlas.core.models import (
    AdmissionRun,
    DeliveryAdmissionPolicyRevision,
    Ticket,
)
from atlas.core.models.admission_run import (
    AdmissionCandidateDecision,
    AdmissionDecisionType,
    AdmissionHoldCode,
    AdmissionHoldReason,
    AdmissionRankInputs,
)
from atlas.core.models.delivery_admission_policy import (
    DeliveryAdmissionMode,
    canonical_component_selector,
)
from atlas.dependencies import critical_path, ready_tickets, unlocks
from atlas.pm.delivery_snapshot import (
    DeliverySnapshot,
    OccupancyDimension,
    SnapshotIncompletenessReason,
    delivery_policy_fingerprint,
)

_RISK_SEVERITY: dict[RiskLevel, int] = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}


def _normalise_time(value: datetime, *, name: str) -> datetime:
    if value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _age_microseconds(now: datetime, since: datetime) -> int:
    delta = now - since
    microseconds = (
        delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds
    )
    if microseconds < 0:
        raise ValueError("continuously eligible timestamp cannot be in the future")
    return microseconds


def _reason_sort_key(reason: AdmissionHoldReason) -> tuple[object, ...]:
    return (
        reason.code.value,
        reason.source_code or "",
        reason.selector or "",
        -1 if reason.observed is None else reason.observed,
        -1 if reason.limit is None else reason.limit,
        -1 if reason.reserved_capacity is None else reason.reserved_capacity,
        reason.issue_id or "",
        reason.issue_identifier or "",
        reason.ticket_key or "",
        reason.state_id or "",
        reason.pagination_cursor or "",
    )


def _ordered_reasons(
    reasons: Iterable[AdmissionHoldReason],
) -> tuple[AdmissionHoldReason, ...]:
    by_key = {_reason_sort_key(reason): reason for reason in reasons}
    return tuple(by_key[key] for key in sorted(by_key))


def _snapshot_reason(reason: SnapshotIncompletenessReason) -> AdmissionHoldReason:
    return AdmissionHoldReason(
        code=AdmissionHoldCode.SNAPSHOT_INCOMPLETE,
        source_code=reason.code.value,
        issue_id=reason.issue_id,
        issue_identifier=reason.issue_identifier,
        ticket_key=reason.ticket_key,
        state_id=reason.state_id,
        pagination_cursor=reason.pagination_cursor,
    )


def _capacity_reason(
    dimension: OccupancyDimension,
    *,
    selector: str | None,
    observed: int,
    limit: int,
) -> AdmissionHoldReason:
    codes = {
        OccupancyDimension.WORKING: AdmissionHoldCode.WORKING_BUDGET,
        OccupancyDimension.REVIEW: AdmissionHoldCode.REVIEW_BUDGET,
        OccupancyDimension.RISK_LANE: AdmissionHoldCode.RISK_LANE,
        OccupancyDimension.COMPONENT_LANE: AdmissionHoldCode.COMPONENT_LANE,
    }
    return AdmissionHoldReason(
        code=codes[dimension],
        selector=selector,
        observed=observed,
        limit=limit,
    )


def _global_reasons(
    policy: DeliveryAdmissionPolicyRevision,
    snapshot: DeliverySnapshot,
) -> list[AdmissionHoldReason]:
    reasons: list[AdmissionHoldReason] = []
    if policy.mode is DeliveryAdmissionMode.PAUSED:
        reasons.append(AdmissionHoldReason(code=AdmissionHoldCode.POLICY_PAUSED))
    elif policy.mode is DeliveryAdmissionMode.DRAINING:
        reasons.append(AdmissionHoldReason(code=AdmissionHoldCode.POLICY_DRAINING))

    if (
        snapshot.product_id != policy.product_id
        or snapshot.policy_id != policy.id
        or snapshot.policy_revision != policy.revision
        or snapshot.policy_mode is not policy.mode
        or snapshot.policy_fingerprint != delivery_policy_fingerprint(policy)
    ):
        reasons.append(
            AdmissionHoldReason(code=AdmissionHoldCode.SNAPSHOT_POLICY_MISMATCH)
        )

    reasons.extend(
        _snapshot_reason(reason) for reason in snapshot.incompleteness_reasons
    )
    reasons.extend(
        _capacity_reason(
            breach.dimension,
            selector=breach.selector,
            observed=breach.count,
            limit=breach.limit,
        )
        for breach in snapshot.over_capacity
    )

    if snapshot.review_occupancy >= policy.review_budget:
        reasons.append(
            AdmissionHoldReason(
                code=AdmissionHoldCode.REVIEW_BUDGET,
                observed=snapshot.review_occupancy,
                limit=policy.review_budget,
            )
        )
    return reasons


def _candidate_capacity_reasons(
    ticket: Ticket,
    policy: DeliveryAdmissionPolicyRevision,
    snapshot: DeliverySnapshot,
) -> list[AdmissionHoldReason]:
    reasons: list[AdmissionHoldReason] = []
    simulated_working = snapshot.working_occupancy + 1
    if simulated_working > policy.working_budget:
        reasons.append(
            AdmissionHoldReason(
                code=AdmissionHoldCode.WORKING_BUDGET,
                observed=simulated_working,
                limit=policy.working_budget,
            )
        )

    reserve = snapshot.changes_requested_reserve_remaining
    if reserve > 0 and simulated_working + reserve > policy.working_budget:
        reasons.append(
            AdmissionHoldReason(
                code=AdmissionHoldCode.CHANGES_REQUESTED_RESERVE,
                observed=simulated_working,
                limit=policy.working_budget,
                reserved_capacity=reserve,
            )
        )

    risk_lanes = {lane.risk_level: lane for lane in snapshot.risk_lane_occupancy}
    risk_lane = risk_lanes.get(ticket.risk_level)
    if risk_lane is not None and risk_lane.count + 1 > risk_lane.limit:
        reasons.append(
            AdmissionHoldReason(
                code=AdmissionHoldCode.RISK_LANE,
                selector=risk_lane.risk_level.value,
                observed=risk_lane.count + 1,
                limit=risk_lane.limit,
            )
        )

    if ticket.component is not None:
        try:
            component = canonical_component_selector(ticket.component)
        except ValueError:
            component = None
        component_lanes = {
            lane.component: lane for lane in snapshot.component_lane_occupancy
        }
        component_lane = (
            component_lanes.get(component) if component is not None else None
        )
        if (
            component_lane is not None
            and component_lane.count + 1 > component_lane.limit
        ):
            reasons.append(
                AdmissionHoldReason(
                    code=AdmissionHoldCode.COMPONENT_LANE,
                    selector=component_lane.component,
                    observed=component_lane.count + 1,
                    limit=component_lane.limit,
                )
            )

    if ticket.external_linear_id is None:
        reasons.append(
            AdmissionHoldReason(
                code=AdmissionHoldCode.MISSING_EXTERNAL_LINEAR_ID,
                ticket_key=ticket.key,
            )
        )
    return reasons


def _run_id_payload(
    *,
    product_id: UUID,
    policy: DeliveryAdmissionPolicyRevision,
    snapshot: DeliverySnapshot,
    evaluated_at: datetime,
    decisions: tuple[AdmissionCandidateDecision, ...],
) -> str:
    payload = {
        "product_id": str(product_id),
        "policy_id": str(policy.id),
        "policy_revision": policy.revision,
        "policy_fingerprint": delivery_policy_fingerprint(policy),
        "snapshot_fingerprint": snapshot.fingerprint,
        "evaluated_at": evaluated_at.isoformat(),
        "decisions": [decision.model_dump(mode="json") for decision in decisions],
    }
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def evaluate_admission(
    *,
    graph: nx.DiGraph[str],
    tickets: Iterable[Ticket],
    policy: DeliveryAdmissionPolicyRevision,
    snapshot: DeliverySnapshot,
    continuously_eligible_since: Mapping[str, datetime],
    clock: Callable[[], datetime],
) -> AdmissionRun:
    """Return one side-effect-free, deterministic zero/one admission run.

    Candidate identity is derived only from :func:`ready_tickets`. The caller
    supplies the start of each resulting candidate's uninterrupted eligibility
    episode; extra map entries are ignored, while a missing candidate entry is
    rejected rather than guessed from creation or status timestamps.
    """

    evaluated_at = _normalise_time(clock(), name="admission evaluation time")
    candidates = ready_tickets(graph)
    ticket_by_key: dict[str, Ticket] = {}
    for item in tickets:
        if item.product_id != snapshot.product_id:
            continue
        if item.key in ticket_by_key:
            raise ValueError(f"duplicate admission ticket key: {item.key}")
        ticket_by_key[item.key] = item

    path_positions = {
        key: position for position, key in enumerate(critical_path(graph).keys)
    }
    ranked: list[tuple[tuple[object, ...], Ticket, AdmissionRankInputs]] = []
    for candidate in candidates:
        ticket = ticket_by_key.get(candidate.key)
        if ticket is None:
            raise ValueError(
                f"dependency-ready candidate has no product ticket: {candidate.key}"
            )
        try:
            eligible_since = _normalise_time(
                continuously_eligible_since[candidate.key],
                name=f"{candidate.key} continuously eligible time",
            )
        except KeyError as error:
            raise ValueError(
                f"missing continuously eligible time for {candidate.key}"
            ) from error

        path_position = path_positions.get(candidate.key)
        rank_inputs = AdmissionRankInputs(
            unlock_count=unlocks(graph, candidate.key).count,
            critical_path_member=path_position is not None,
            critical_path_position=path_position,
            priority=ticket.priority,
            risk_level=ticket.risk_level,
            risk_severity=_RISK_SEVERITY[ticket.risk_level],
            continuously_eligible_since=eligible_since,
            continuously_eligible_age_microseconds=_age_microseconds(
                evaluated_at, eligible_since
            ),
        )
        sort_key: tuple[object, ...] = (
            -rank_inputs.unlock_count,
            0 if rank_inputs.critical_path_member else 1,
            rank_inputs.critical_path_position
            if rank_inputs.critical_path_position is not None
            else len(path_positions),
            -rank_inputs.priority,
            rank_inputs.risk_severity,
            rank_inputs.continuously_eligible_since,
            natural_key(ticket.key),
            ticket.key,
        )
        ranked.append((sort_key, ticket, rank_inputs))
    ranked.sort(key=lambda item: item[0])

    global_reasons = _global_reasons(policy, snapshot)
    selected: Ticket | None = None
    decisions: list[AdmissionCandidateDecision] = []
    for rank, (_sort_key, ticket, rank_inputs) in enumerate(ranked, start=1):
        reasons = global_reasons + _candidate_capacity_reasons(ticket, policy, snapshot)
        if not reasons and selected is not None:
            reasons.append(
                AdmissionHoldReason(code=AdmissionHoldCode.SINGLE_WRITE_LIMIT)
            )
        ordered_reasons = _ordered_reasons(reasons)
        decision = (
            AdmissionDecisionType.HOLD
            if ordered_reasons
            else AdmissionDecisionType.ADMIT
        )
        if decision is AdmissionDecisionType.ADMIT:
            selected = ticket
        decisions.append(
            AdmissionCandidateDecision(
                ticket_id=ticket.id,
                ticket_key=ticket.key,
                external_linear_id=ticket.external_linear_id,
                rank=rank,
                rank_inputs=rank_inputs,
                decision=decision,
                reasons=ordered_reasons,
            )
        )

    frozen_decisions = tuple(decisions)
    run_id_hash = _run_id_payload(
        product_id=snapshot.product_id,
        policy=policy,
        snapshot=snapshot,
        evaluated_at=evaluated_at,
        decisions=frozen_decisions,
    )
    return AdmissionRun(
        id=uuid5(NAMESPACE_URL, f"atlas:admission-run:{run_id_hash}"),
        product_id=snapshot.product_id,
        policy_id=policy.id,
        policy_revision=policy.revision,
        policy_fingerprint=delivery_policy_fingerprint(policy),
        snapshot_fingerprint=snapshot.fingerprint,
        snapshot_observed_at=snapshot.observed_at,
        evaluated_at=evaluated_at,
        selected_ticket_id=selected.id if selected is not None else None,
        selected_ticket_key=selected.key if selected is not None else None,
        decisions=frozen_decisions,
        created_by_type=ActorType.SYSTEM,
        created_by_id="atlas.pm.admission",
    )
