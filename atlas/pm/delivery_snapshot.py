"""Immutable, fail-closed delivery occupancy observations (ATLAS-247).

The builder is a pure read-side calculation.  It consumes the existing
project-scoped Linear pull after the caller freezes it in a
``LinearBoardPull`` plus materialised Atlas ticket/dependency state.  It has no
client, repository, Linear mutation, ticket mutation or Symphony dependency.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

import networkx as nx
from pydantic import BaseModel, ConfigDict, Field

from atlas.core.enums import RiskLevel
from atlas.core.models.delivery_admission_policy import (
    DeliveryAdmissionMode,
    DeliveryAdmissionPolicyRevision,
    canonical_component_selector,
)
from atlas.core.models.dependency import TicketDependency
from atlas.core.models.ticket import Ticket, TicketStatus
from atlas.linear.client import LinearIssue
from atlas.linear.ownership import LinearStatusMap

WORKING_STATUSES: frozenset[TicketStatus] = frozenset(
    {
        TicketStatus.READY_FOR_AGENT,
        TicketStatus.IN_PROGRESS,
        TicketStatus.PR_OPEN,
        TicketStatus.CHANGES_REQUESTED,
    }
)
INTEGRATION_STATUSES: frozenset[TicketStatus] = frozenset({TicketStatus.CI_PENDING})
REVIEW_STATUSES: frozenset[TicketStatus] = frozenset(
    {TicketStatus.REVIEW_REQUIRED, TicketStatus.NEEDS_HUMAN_DECISION}
)
DELIVERY_OCCUPANCY_STATUSES: frozenset[TicketStatus] = (
    WORKING_STATUSES | INTEGRATION_STATUSES | REVIEW_STATUSES
)
TERMINAL_STATUSES: frozenset[TicketStatus] = frozenset(
    {TicketStatus.DONE, TicketStatus.REJECTED}
)


class SnapshotIncompletenessCode(StrEnum):
    """Closed reasons why an occupancy observation is unsafe for admission."""

    INCOMPLETE_PULL = "incomplete_pull"
    PAGINATION_GAP = "pagination_gap"
    MISSING_ISSUE_IDENTITY = "missing_issue_identity"
    DUPLICATE_ISSUE_ID = "duplicate_issue_id"
    DUPLICATE_ISSUE_IDENTIFIER = "duplicate_issue_identifier"
    DUPLICATE_ATLAS_JOIN = "duplicate_atlas_join"
    UNMAPPED_STATE = "unmapped_state"
    CONTRADICTORY_STATE = "contradictory_state"
    MISSING_EXTERNAL_LINEAR_ID = "missing_external_linear_id"
    MISSING_JOINED_ISSUE = "missing_joined_issue"
    MISSING_ATLAS_TICKET = "missing_atlas_ticket"
    ATLAS_LINEAR_STATE_MISMATCH = "atlas_linear_state_mismatch"


class OccupancyDimension(StrEnum):
    """Configured capacity dimensions that existing work can breach."""

    WORKING = "working"
    INTEGRATION = "integration"
    REVIEW = "review"
    RISK_LANE = "risk_lane"
    COMPONENT_LANE = "component_lane"


class SnapshotIncompletenessReason(BaseModel):
    """One typed, bounded and deterministically sortable defect."""

    model_config = ConfigDict(frozen=True)

    code: SnapshotIncompletenessCode
    issue_id: str | None = None
    issue_identifier: str | None = None
    ticket_key: str | None = None
    state_id: str | None = None
    pagination_cursor: str | None = None


class StatusOccupancy(BaseModel):
    """Observed issue count for one canonical Atlas workflow status."""

    model_config = ConfigDict(frozen=True)

    status: TicketStatus
    count: int = Field(ge=0)


class RiskLaneOccupancy(BaseModel):
    """Working occupancy and configured limit for one exact risk lane."""

    model_config = ConfigDict(frozen=True)

    risk_level: RiskLevel
    count: int = Field(ge=0)
    limit: int = Field(ge=0)


class ComponentLaneOccupancy(BaseModel):
    """Working occupancy and configured limit for one canonical component."""

    model_config = ConfigDict(frozen=True)

    component: str
    count: int = Field(ge=0)
    limit: int = Field(ge=0)


class OccupancyBreach(BaseModel):
    """One existing-occupancy dimension whose configured limit is exceeded."""

    model_config = ConfigDict(frozen=True)

    dimension: OccupancyDimension
    selector: str | None = None
    count: int = Field(ge=0)
    limit: int = Field(ge=0)


@dataclass(frozen=True)
class LinearBoardPull:
    """Frozen result of the existing project-scoped Linear pull.

    ``complete`` means the client reached ``hasNextPage=false``.  Callers that
    detect a discontinuous cursor chain retain each missing/invalid cursor in
    ``pagination_gaps``.  The envelope records provenance only; constructing
    it performs no request.
    """

    issues: tuple[LinearIssue, ...]
    complete: bool = True
    pagination_gaps: tuple[str, ...] = ()

    @classmethod
    def complete_project_pull(cls, issues: Iterable[LinearIssue]) -> LinearBoardPull:
        """Freeze the list returned by ``fetch_project_issues`` as complete."""

        return cls(issues=tuple(issues))


class DeliverySnapshot(BaseModel):
    """One immutable canonical view used as an admission precondition."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["delivery-snapshot-v1"] = "delivery-snapshot-v1"
    product_id: UUID
    linear_project_id: str
    policy_id: UUID
    policy_revision: int = Field(ge=1)
    policy_mode: DeliveryAdmissionMode
    policy_fingerprint: str
    status_map_fingerprint: str
    fetched_board_fingerprint: str
    fetched_board_issue_count: int = Field(ge=0)
    atlas_store_revision: str
    atlas_graph_revision: str
    observed_at: datetime
    status_occupancy: tuple[StatusOccupancy, ...]
    working_occupancy: int = Field(ge=0)
    integration_occupancy: int = Field(ge=0)
    integration_ticket_keys: tuple[str, ...]
    new_admission_integration_capacity: int = Field(ge=0)
    review_occupancy: int = Field(ge=0)
    changes_requested_occupancy: int = Field(ge=0)
    changes_requested_reserve_remaining: int = Field(ge=0)
    new_admission_working_capacity: int = Field(ge=0)
    risk_lane_occupancy: tuple[RiskLaneOccupancy, ...]
    component_lane_occupancy: tuple[ComponentLaneOccupancy, ...]
    incompleteness_reasons: tuple[SnapshotIncompletenessReason, ...]
    over_capacity: tuple[OccupancyBreach, ...]
    admission_allowed: bool

    def canonical_bytes(self) -> bytes:
        """Return the byte-stable canonical representation excluding its hash."""

        payload = self.model_dump(mode="json")
        rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return rendered.encode("utf-8")

    @property
    def fingerprint(self) -> str:
        """SHA-256 of :meth:`canonical_bytes`."""

        return hashlib.sha256(self.canonical_bytes()).hexdigest()


class StoredDeliveryOccupancy(BaseModel):
    """Current capacity use derived from materialised Atlas ticket statuses.

    This is the read-side companion to :class:`DeliverySnapshot`.  It never
    claims to be a fresh Linear observation: callers pair it with the latest
    successful sync timestamp so the age of the materialised state remains
    explicit.  It performs no repository read or external request.
    """

    model_config = ConfigDict(frozen=True)

    status_occupancy: tuple[StatusOccupancy, ...]
    working_occupancy: int = Field(ge=0)
    integration_occupancy: int = Field(ge=0)
    integration_ticket_keys: tuple[str, ...]
    new_admission_integration_capacity: int = Field(ge=0)
    review_occupancy: int = Field(ge=0)
    changes_requested_occupancy: int = Field(ge=0)
    changes_requested_reserve_remaining: int = Field(ge=0)
    new_admission_working_capacity: int = Field(ge=0)
    risk_lane_occupancy: tuple[RiskLaneOccupancy, ...]
    component_lane_occupancy: tuple[ComponentLaneOccupancy, ...]
    over_capacity: tuple[OccupancyBreach, ...]


def _canonical_hash(payload: object) -> str:
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _normalise_time(value: datetime) -> datetime:
    if value.utcoffset() is None:
        raise ValueError("delivery snapshot observation time must be timezone-aware")
    return value.astimezone(UTC)


def _issue_sort_key(issue: LinearIssue) -> tuple[str, str, str, str, str]:
    return (
        issue.id,
        issue.identifier or "",
        issue.state_id or "",
        issue.state_type or "",
        issue.state_name or "",
    )


def _board_payload(issues: Iterable[LinearIssue]) -> list[dict[str, str | None]]:
    return [
        {
            "id": issue.id,
            "identifier": issue.identifier,
            "state_id": issue.state_id,
            "state_name": issue.state_name,
            "state_type": issue.state_type,
        }
        for issue in sorted(issues, key=_issue_sort_key)
    ]


def _policy_payload(policy: DeliveryAdmissionPolicyRevision) -> dict[str, object]:
    return {
        "id": str(policy.id),
        "product_id": str(policy.product_id),
        "revision": policy.revision,
        "mode": policy.mode.value,
        "approved_symphony_ceiling": policy.approved_symphony_ceiling,
        "working_budget": policy.working_budget,
        "integration_budget": policy.integration_budget,
        "review_budget": policy.review_budget,
        "changes_requested_reserve": policy.changes_requested_reserve,
        "risk_lane_limits": sorted(
            (
                {"risk_level": lane.risk_level.value, "limit": lane.limit}
                for lane in policy.risk_lane_limits
            ),
            key=lambda lane: str(lane["risk_level"]),
        ),
        "component_lane_limits": sorted(
            (
                {"component": lane.component, "limit": lane.limit}
                for lane in policy.component_lane_limits
            ),
            key=lambda lane: str(lane["component"]),
        ),
    }


def delivery_policy_fingerprint(policy: DeliveryAdmissionPolicyRevision) -> str:
    """Canonical fingerprint shared by snapshot and admission evaluation."""

    return _canonical_hash(_policy_payload(policy))


def _store_payload(tickets: Iterable[Ticket]) -> list[dict[str, object]]:
    return [
        {
            "id": str(ticket.id),
            "product_id": str(ticket.product_id),
            "epic_id": str(ticket.epic_id) if ticket.epic_id is not None else None,
            "key": ticket.key,
            "external_linear_id": ticket.external_linear_id,
            "status": ticket.status.value,
            "priority": ticket.priority,
            "risk_level": ticket.risk_level.value,
            "component": ticket.component,
            "estimated_effort": ticket.estimated_effort,
            "acceptance_criteria": ticket.acceptance_criteria,
        }
        for ticket in sorted(tickets, key=lambda item: (item.key, str(item.id)))
    ]


def delivery_store_revision(product_id: UUID, tickets: Iterable[Ticket]) -> str:
    """Fingerprint every materialised ticket input admission may consume."""

    return _canonical_hash(
        _store_payload(ticket for ticket in tickets if ticket.product_id == product_id)
    )


def _graph_payload(graph: nx.DiGraph[str]) -> dict[str, object]:
    """Canonicalise the exact projected state used by dependency analyses."""

    nodes = [
        {
            "key": str(key),
            "node_type": data.get("node_type"),
            "entity_id": (
                str(data["entity_id"]) if data.get("entity_id") is not None else None
            ),
            "present": data.get("present", True),
            "status": data.get("status"),
            "priority": data.get("priority"),
            "risk_level": data.get("risk_level"),
            "estimated_effort": data.get("estimated_effort"),
            "acceptance_criteria_count": data.get("acceptance_criteria_count"),
        }
        for key, data in sorted(graph.nodes(data=True), key=lambda item: str(item[0]))
    ]
    edges = [
        {
            "source": str(source),
            "target": str(target),
            "dependency_id": (
                str(data["dependency_id"])
                if data.get("dependency_id") is not None
                else None
            ),
            "dependency_type": data.get("dependency_type"),
            "reason": data.get("reason"),
        }
        for source, target, data in sorted(
            graph.edges(data=True),
            key=lambda item: (
                str(item[0]),
                str(item[1]),
                str(item[2].get("dependency_id", "")),
            ),
        )
    ]
    return {"nodes": nodes, "edges": edges}


def delivery_graph_revision(graph: nx.DiGraph[str]) -> str:
    """Fingerprint the exact graph input used by readiness and ranking."""

    return _canonical_hash(_graph_payload(graph))


def build_stored_delivery_occupancy(
    *,
    policy: DeliveryAdmissionPolicyRevision,
    tickets: Iterable[Ticket],
) -> StoredDeliveryOccupancy:
    """Project current stored occupancy without refreshing or mutating Linear."""

    product_tickets = tuple(
        ticket for ticket in tickets if ticket.product_id == policy.product_id
    )
    status_counts = Counter(ticket.status for ticket in product_tickets)
    working_tickets = tuple(
        ticket for ticket in product_tickets if ticket.status in WORKING_STATUSES
    )
    integration_tickets = tuple(
        ticket for ticket in product_tickets if ticket.status in INTEGRATION_STATUSES
    )

    configured_risks = {lane.risk_level for lane in policy.risk_lane_limits}
    configured_components = {lane.component for lane in policy.component_lane_limits}
    risk_counts: Counter[RiskLevel] = Counter()
    component_counts: Counter[str] = Counter()
    for ticket in working_tickets:
        if ticket.risk_level in configured_risks:
            risk_counts[ticket.risk_level] += 1
        if ticket.component is None:
            continue
        try:
            component = canonical_component_selector(ticket.component)
        except ValueError:
            continue
        if component in configured_components:
            component_counts[component] += 1

    status_occupancy = tuple(
        StatusOccupancy(status=status, count=status_counts[status])
        for status in TicketStatus
    )
    working = len(working_tickets)
    integration = len(integration_tickets)
    integration_ticket_keys = tuple(
        sorted(ticket.key for ticket in integration_tickets)
    )
    new_integration_capacity = max(0, policy.integration_budget - integration)
    review = sum(status_counts[status] for status in REVIEW_STATUSES)
    changes_requested = status_counts[TicketStatus.CHANGES_REQUESTED]
    reserve_remaining = max(0, policy.changes_requested_reserve - changes_requested)
    new_admission_capacity = max(0, policy.working_budget - working - reserve_remaining)
    risk_lanes = tuple(
        RiskLaneOccupancy(
            risk_level=lane.risk_level,
            count=risk_counts[lane.risk_level],
            limit=lane.limit,
        )
        for lane in sorted(
            policy.risk_lane_limits, key=lambda item: item.risk_level.value
        )
    )
    component_lanes = tuple(
        ComponentLaneOccupancy(
            component=lane.component,
            count=component_counts[lane.component],
            limit=lane.limit,
        )
        for lane in sorted(
            policy.component_lane_limits, key=lambda item: item.component
        )
    )

    breaches: list[OccupancyBreach] = []
    if working > policy.working_budget:
        breaches.append(
            OccupancyBreach(
                dimension=OccupancyDimension.WORKING,
                count=working,
                limit=policy.working_budget,
            )
        )
    if integration > policy.integration_budget:
        breaches.append(
            OccupancyBreach(
                dimension=OccupancyDimension.INTEGRATION,
                count=integration,
                limit=policy.integration_budget,
            )
        )
    if review > policy.review_budget:
        breaches.append(
            OccupancyBreach(
                dimension=OccupancyDimension.REVIEW,
                count=review,
                limit=policy.review_budget,
            )
        )
    breaches.extend(
        OccupancyBreach(
            dimension=OccupancyDimension.RISK_LANE,
            selector=lane.risk_level.value,
            count=lane.count,
            limit=lane.limit,
        )
        for lane in risk_lanes
        if lane.count > lane.limit
    )
    breaches.extend(
        OccupancyBreach(
            dimension=OccupancyDimension.COMPONENT_LANE,
            selector=lane.component,
            count=lane.count,
            limit=lane.limit,
        )
        for lane in component_lanes
        if lane.count > lane.limit
    )

    return StoredDeliveryOccupancy(
        status_occupancy=status_occupancy,
        working_occupancy=working,
        integration_occupancy=integration,
        integration_ticket_keys=integration_ticket_keys,
        new_admission_integration_capacity=new_integration_capacity,
        review_occupancy=review,
        changes_requested_occupancy=changes_requested,
        changes_requested_reserve_remaining=reserve_remaining,
        new_admission_working_capacity=new_admission_capacity,
        risk_lane_occupancy=risk_lanes,
        component_lane_occupancy=component_lanes,
        over_capacity=tuple(sorted(breaches, key=_breach_sort_key)),
    )


def _reason_sort_key(reason: SnapshotIncompletenessReason) -> tuple[str, ...]:
    return (
        reason.code.value,
        reason.issue_id or "",
        reason.issue_identifier or "",
        reason.ticket_key or "",
        reason.state_id or "",
        reason.pagination_cursor or "",
    )


def _breach_sort_key(breach: OccupancyBreach) -> tuple[str, str]:
    return (breach.dimension.value, breach.selector or "")


def _deduplicate_reasons(
    reasons: Iterable[SnapshotIncompletenessReason],
) -> tuple[SnapshotIncompletenessReason, ...]:
    by_key = {_reason_sort_key(reason): reason for reason in reasons}
    return tuple(by_key[key] for key in sorted(by_key))


def build_delivery_snapshot(
    *,
    product_id: UUID,
    linear_project_id: str,
    policy: DeliveryAdmissionPolicyRevision,
    status_map: LinearStatusMap,
    board_pull: LinearBoardPull,
    tickets: Iterable[Ticket],
    dependencies: Iterable[TicketDependency],
    clock: Callable[[], datetime],
    graph: nx.DiGraph[str] | None = None,
) -> DeliverySnapshot:
    """Build one deterministic snapshot without performing any side effect."""

    if policy.product_id != product_id:
        raise ValueError("delivery policy belongs to a different product")
    if not linear_project_id.strip():
        raise ValueError("linear_project_id must be non-empty")

    product_tickets = tuple(
        ticket for ticket in tickets if ticket.product_id == product_id
    )
    product_ticket_ids = {ticket.id for ticket in product_tickets}
    product_dependencies = tuple(
        dependency
        for dependency in dependencies
        if dependency.source_ticket_id in product_ticket_ids
    )
    if graph is None:
        from atlas.dependencies import project_graph

        graph = project_graph(product_tickets, (), (), product_dependencies)
    issues = tuple(board_pull.issues)
    reasons: list[SnapshotIncompletenessReason] = []

    if not board_pull.complete:
        reasons.append(
            SnapshotIncompletenessReason(
                code=SnapshotIncompletenessCode.INCOMPLETE_PULL
            )
        )
    for cursor in sorted(set(board_pull.pagination_gaps)):
        reasons.append(
            SnapshotIncompletenessReason(
                code=SnapshotIncompletenessCode.PAGINATION_GAP,
                pagination_cursor=cursor,
            )
        )

    issue_id_counts = Counter(issue.id for issue in issues)
    identifier_counts = Counter(
        issue.identifier for issue in issues if issue.identifier is not None
    )
    for issue in issues:
        if not issue.id.strip():
            reasons.append(
                SnapshotIncompletenessReason(
                    code=SnapshotIncompletenessCode.MISSING_ISSUE_IDENTITY,
                    issue_identifier=issue.identifier,
                )
            )
    for issue_id, count in issue_id_counts.items():
        if count > 1:
            reasons.append(
                SnapshotIncompletenessReason(
                    code=SnapshotIncompletenessCode.DUPLICATE_ISSUE_ID,
                    issue_id=issue_id,
                )
            )
    for identifier, count in identifier_counts.items():
        if count > 1:
            reasons.append(
                SnapshotIncompletenessReason(
                    code=SnapshotIncompletenessCode.DUPLICATE_ISSUE_IDENTIFIER,
                    issue_identifier=identifier,
                )
            )

    tickets_by_issue_id: dict[str, list[Ticket]] = defaultdict(list)
    for ticket in product_tickets:
        if ticket.external_linear_id is not None:
            tickets_by_issue_id[ticket.external_linear_id].append(ticket)
    for issue_id, joined in tickets_by_issue_id.items():
        if len(joined) > 1:
            for ticket in sorted(joined, key=lambda item: item.key):
                reasons.append(
                    SnapshotIncompletenessReason(
                        code=SnapshotIncompletenessCode.DUPLICATE_ATLAS_JOIN,
                        issue_id=issue_id,
                        ticket_key=ticket.key,
                    )
                )

    fetched_ids = {issue.id for issue in issues}
    for ticket in product_tickets:
        if (
            ticket.status in DELIVERY_OCCUPANCY_STATUSES
            and ticket.external_linear_id is None
        ):
            reasons.append(
                SnapshotIncompletenessReason(
                    code=SnapshotIncompletenessCode.MISSING_EXTERNAL_LINEAR_ID,
                    ticket_key=ticket.key,
                )
            )
        if (
            ticket.external_linear_id is not None
            and ticket.status not in TERMINAL_STATUSES
            and ticket.external_linear_id not in fetched_ids
        ):
            reasons.append(
                SnapshotIncompletenessReason(
                    code=SnapshotIncompletenessCode.MISSING_JOINED_ISSUE,
                    issue_id=ticket.external_linear_id,
                    ticket_key=ticket.key,
                )
            )

    status_counts: Counter[TicketStatus] = Counter()
    integration_ticket_keys: set[str] = set()
    risk_counts: Counter[RiskLevel] = Counter()
    component_counts: Counter[str] = Counter()
    configured_risks = {lane.risk_level for lane in policy.risk_lane_limits}
    configured_components = {lane.component for lane in policy.component_lane_limits}

    for issue in sorted(issues, key=_issue_sort_key):
        mapped = status_map.status_for(issue.state_id)
        if mapped is None:
            reasons.append(
                SnapshotIncompletenessReason(
                    code=SnapshotIncompletenessCode.UNMAPPED_STATE,
                    issue_id=issue.id,
                    issue_identifier=issue.identifier,
                    state_id=issue.state_id,
                )
            )
            continue
        status_counts[mapped] += 1
        if not status_map.state_type_is_compatible(issue.state_id, issue.state_type):
            reasons.append(
                SnapshotIncompletenessReason(
                    code=SnapshotIncompletenessCode.CONTRADICTORY_STATE,
                    issue_id=issue.id,
                    issue_identifier=issue.identifier,
                    state_id=issue.state_id,
                )
            )

        joined = tickets_by_issue_id.get(issue.id, [])
        if not joined and mapped not in TERMINAL_STATUSES:
            reasons.append(
                SnapshotIncompletenessReason(
                    code=SnapshotIncompletenessCode.MISSING_ATLAS_TICKET,
                    issue_id=issue.id,
                    issue_identifier=issue.identifier,
                    state_id=issue.state_id,
                )
            )
        for ticket in joined:
            if ticket.status != mapped:
                reasons.append(
                    SnapshotIncompletenessReason(
                        code=SnapshotIncompletenessCode.ATLAS_LINEAR_STATE_MISMATCH,
                        issue_id=issue.id,
                        issue_identifier=issue.identifier,
                        ticket_key=ticket.key,
                        state_id=issue.state_id,
                    )
                )
            if mapped in INTEGRATION_STATUSES:
                integration_ticket_keys.add(ticket.key)
            if mapped not in WORKING_STATUSES:
                continue
            if ticket.risk_level in configured_risks:
                risk_counts[ticket.risk_level] += 1
            if ticket.component is not None:
                try:
                    component = canonical_component_selector(ticket.component)
                except ValueError:
                    component = ""
                if component in configured_components:
                    component_counts[component] += 1

    status_occupancy = tuple(
        StatusOccupancy(status=status, count=status_counts[status])
        for status in TicketStatus
    )
    working = sum(status_counts[status] for status in WORKING_STATUSES)
    integration = sum(status_counts[status] for status in INTEGRATION_STATUSES)
    ordered_integration_ticket_keys = tuple(sorted(integration_ticket_keys))
    new_integration_capacity = max(0, policy.integration_budget - integration)
    review = sum(status_counts[status] for status in REVIEW_STATUSES)
    changes_requested = status_counts[TicketStatus.CHANGES_REQUESTED]
    reserve_remaining = max(0, policy.changes_requested_reserve - changes_requested)
    new_admission_capacity = max(0, policy.working_budget - working - reserve_remaining)

    risk_lanes = tuple(
        RiskLaneOccupancy(
            risk_level=lane.risk_level,
            count=risk_counts[lane.risk_level],
            limit=lane.limit,
        )
        for lane in sorted(
            policy.risk_lane_limits, key=lambda item: item.risk_level.value
        )
    )
    component_lanes = tuple(
        ComponentLaneOccupancy(
            component=lane.component,
            count=component_counts[lane.component],
            limit=lane.limit,
        )
        for lane in sorted(
            policy.component_lane_limits, key=lambda item: item.component
        )
    )

    breaches: list[OccupancyBreach] = []
    if working > policy.working_budget:
        breaches.append(
            OccupancyBreach(
                dimension=OccupancyDimension.WORKING,
                count=working,
                limit=policy.working_budget,
            )
        )
    if integration > policy.integration_budget:
        breaches.append(
            OccupancyBreach(
                dimension=OccupancyDimension.INTEGRATION,
                count=integration,
                limit=policy.integration_budget,
            )
        )
    if review > policy.review_budget:
        breaches.append(
            OccupancyBreach(
                dimension=OccupancyDimension.REVIEW,
                count=review,
                limit=policy.review_budget,
            )
        )
    for risk_lane in risk_lanes:
        if risk_lane.count > risk_lane.limit:
            breaches.append(
                OccupancyBreach(
                    dimension=OccupancyDimension.RISK_LANE,
                    selector=risk_lane.risk_level.value,
                    count=risk_lane.count,
                    limit=risk_lane.limit,
                )
            )
    for component_lane in component_lanes:
        if component_lane.count > component_lane.limit:
            breaches.append(
                OccupancyBreach(
                    dimension=OccupancyDimension.COMPONENT_LANE,
                    selector=component_lane.component,
                    count=component_lane.count,
                    limit=component_lane.limit,
                )
            )

    ordered_reasons = _deduplicate_reasons(reasons)
    ordered_breaches = tuple(sorted(breaches, key=_breach_sort_key))
    return DeliverySnapshot(
        product_id=product_id,
        linear_project_id=linear_project_id,
        policy_id=policy.id,
        policy_revision=policy.revision,
        policy_mode=policy.mode,
        policy_fingerprint=delivery_policy_fingerprint(policy),
        status_map_fingerprint=_canonical_hash(status_map.snapshot()),
        fetched_board_fingerprint=_canonical_hash(_board_payload(issues)),
        fetched_board_issue_count=len(issues),
        atlas_store_revision=delivery_store_revision(product_id, product_tickets),
        atlas_graph_revision=delivery_graph_revision(graph),
        observed_at=_normalise_time(clock()),
        status_occupancy=status_occupancy,
        working_occupancy=working,
        integration_occupancy=integration,
        integration_ticket_keys=ordered_integration_ticket_keys,
        new_admission_integration_capacity=new_integration_capacity,
        review_occupancy=review,
        changes_requested_occupancy=changes_requested,
        changes_requested_reserve_remaining=reserve_remaining,
        new_admission_working_capacity=new_admission_capacity,
        risk_lane_occupancy=risk_lanes,
        component_lane_occupancy=component_lanes,
        incompleteness_reasons=ordered_reasons,
        over_capacity=ordered_breaches,
        admission_allowed=(
            policy.mode is DeliveryAdmissionMode.RUNNING
            and integration < policy.integration_budget
            and not ordered_reasons
            and not ordered_breaches
        ),
    )
