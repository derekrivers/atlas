"""ATLAS-247 coherent delivery occupancy and review-pressure snapshots."""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path
from textwrap import dedent
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from test_models_validation import dependency_kwargs, ticket_kwargs

from atlas.core.enums import RiskLevel
from atlas.core.models import DeliveryAdmissionPolicyRevision, Ticket, TicketDependency
from atlas.core.models.ticket import TicketStatus
from atlas.linear.client import LinearIssue
from atlas.linear.ownership import LinearStatusMap
from atlas.pm import (
    DeliverySnapshot,
    LinearBoardPull,
    OccupancyDimension,
    SnapshotIncompletenessCode,
    build_delivery_snapshot,
)

NOW = datetime(2026, 8, 3, 9, 30, tzinfo=UTC)
PRODUCT_ID = UUID("11111111-1111-4111-8111-111111111111")
POLICY_ID = UUID("22222222-2222-4222-8222-222222222222")
PROJECT_ID = "linear-project-atlas"

STATE_IDS: dict[TicketStatus, str] = {
    status: f"state-{status.value}" for status in TicketStatus
}
STATE_TYPES: dict[TicketStatus, str] = {
    TicketStatus.BACKLOG: "backlog",
    TicketStatus.PLANNED: "unstarted",
    TicketStatus.BLOCKED: "unstarted",
    TicketStatus.READY_FOR_AGENT: "unstarted",
    TicketStatus.IN_PROGRESS: "started",
    TicketStatus.PR_OPEN: "started",
    TicketStatus.CI_PENDING: "started",
    TicketStatus.REVIEW_REQUIRED: "started",
    TicketStatus.CHANGES_REQUESTED: "started",
    TicketStatus.DONE: "completed",
    TicketStatus.REJECTED: "canceled",
    TicketStatus.NEEDS_HUMAN_DECISION: "backlog",
}
STATUS_MAP = LinearStatusMap(
    {state_id: status for status, state_id in STATE_IDS.items()}
)


class FrozenClock:
    def __init__(self, instant: datetime = NOW) -> None:
        self.instant = instant

    def __call__(self) -> datetime:
        return self.instant


def policy(**overrides: Any) -> DeliveryAdmissionPolicyRevision:
    values: dict[str, Any] = {
        "id": POLICY_ID,
        "product_id": PRODUCT_ID,
        "revision": 7,
        "mode": "running",
        "approved_symphony_ceiling": 10,
        "working_budget": 10,
        "integration_budget": 10,
        "review_budget": 10,
        "changes_requested_reserve": 1,
        "risk_lane_limits": [],
        "component_lane_limits": [],
        "created_by_type": "human",
        "created_by_id": "operator",
        "created_at": NOW,
    }
    return DeliveryAdmissionPolicyRevision(**(values | overrides))


def ticket(
    key: str,
    status: TicketStatus,
    *,
    issue_id: str | None = None,
    product_id: UUID = PRODUCT_ID,
    **overrides: Any,
) -> Ticket:
    values = ticket_kwargs() | {
        "id": uuid4(),
        "product_id": product_id,
        "key": key,
        "status": status,
        "external_linear_id": issue_id or f"issue-{key}",
        "acceptance_criteria": ["deterministic proof"],
    }
    return Ticket(**(values | overrides))


def issue(
    item: Ticket,
    *,
    status: TicketStatus | None = None,
    issue_id: str | None = None,
    identifier: str | None = None,
    state_id: str | None = None,
    state_type: str | None = None,
) -> LinearIssue:
    observed = item.status if status is None else status
    return LinearIssue(
        id=issue_id or item.external_linear_id or f"issue-{item.key}",
        identifier=identifier or item.key,
        title=item.title,
        state_id=STATE_IDS[observed] if state_id is None else state_id,
        state_name=f"display-{observed.value}",
        state_type=STATE_TYPES[observed] if state_type is None else state_type,
    )


def dependency(
    source: Ticket, target: Ticket, *, reason: str = "ordered"
) -> TicketDependency:
    return TicketDependency(
        **dependency_kwargs()
        | {
            "id": uuid4(),
            "source_ticket_id": source.id,
            "target_entity_id": target.id,
            "reason": reason,
        }
    )


def snapshot(
    items: list[Ticket],
    issues: list[LinearIssue],
    *,
    selected_policy: DeliveryAdmissionPolicyRevision | None = None,
    selected_status_map: LinearStatusMap = STATUS_MAP,
    board_pull: LinearBoardPull | None = None,
    dependencies: list[TicketDependency] | None = None,
    clock: FrozenClock | None = None,
    product_id: UUID = PRODUCT_ID,
    project_id: str = PROJECT_ID,
    graph: Any | None = None,
) -> DeliverySnapshot:
    return build_delivery_snapshot(
        product_id=product_id,
        linear_project_id=project_id,
        policy=selected_policy or policy(),
        status_map=selected_status_map,
        board_pull=board_pull or LinearBoardPull.complete_project_pull(issues),
        tickets=items,
        dependencies=dependencies or [],
        clock=clock or FrozenClock(),
        graph=graph,
    )


@pytest.mark.parametrize(
    ("status", "working", "integration", "review", "changes_requested"),
    [
        (TicketStatus.BACKLOG, 0, 0, 0, 0),
        (TicketStatus.PLANNED, 0, 0, 0, 0),
        (TicketStatus.BLOCKED, 0, 0, 0, 0),
        (TicketStatus.READY_FOR_AGENT, 1, 0, 0, 0),
        (TicketStatus.IN_PROGRESS, 1, 0, 0, 0),
        (TicketStatus.PR_OPEN, 1, 0, 0, 0),
        (TicketStatus.CI_PENDING, 0, 1, 0, 0),
        (TicketStatus.REVIEW_REQUIRED, 0, 0, 1, 0),
        (TicketStatus.CHANGES_REQUESTED, 1, 0, 0, 1),
        (TicketStatus.DONE, 0, 0, 0, 0),
        (TicketStatus.REJECTED, 0, 0, 0, 0),
        (TicketStatus.NEEDS_HUMAN_DECISION, 0, 0, 1, 0),
    ],
    ids=lambda status: status.value if isinstance(status, TicketStatus) else None,
)
def test_ac1_every_configured_state_id_has_named_working_and_review_occupancy(
    status: TicketStatus,
    working: int,
    integration: int,
    review: int,
    changes_requested: int,
) -> None:
    item = ticket("ATLAS-1", status)

    result = snapshot([item], [issue(item)])

    counts = {entry.status: entry.count for entry in result.status_occupancy}
    assert len(counts) == len(TicketStatus)
    assert counts[status] == 1
    assert result.working_occupancy == working
    assert result.integration_occupancy == integration
    assert result.review_occupancy == review
    assert result.changes_requested_occupancy == changes_requested
    assert result.admission_allowed is True


def test_ac1_display_names_never_override_the_configured_state_id() -> None:
    item = ticket("ATLAS-1", TicketStatus.IN_PROGRESS)
    observed = issue(item)
    misleading = LinearIssue(
        id=observed.id,
        identifier=observed.identifier,
        title=observed.title,
        state_id=observed.state_id,
        state_name="Done",
        state_type=observed.state_type,
    )

    result = snapshot([item], [misleading])

    assert result.working_occupancy == 1
    assert result.review_occupancy == 0
    assert result.incompleteness_reasons == ()


def test_ac2_working_tickets_consume_every_matching_lane_and_reserve() -> None:
    first = ticket(
        "ATLAS-1",
        TicketStatus.READY_FOR_AGENT,
        risk_level="high",
        component="  Atlas.PM  ",
    )
    rework = ticket(
        "ATLAS-2",
        TicketStatus.CHANGES_REQUESTED,
        risk_level="high",
        component="atlas.pm",
    )
    review = ticket(
        "ATLAS-3",
        TicketStatus.REVIEW_REQUIRED,
        risk_level="high",
        component="atlas.pm",
    )
    selected_policy = policy(
        approved_symphony_ceiling=5,
        working_budget=5,
        review_budget=5,
        changes_requested_reserve=2,
        risk_lane_limits=[{"risk_level": "high", "limit": 4}],
        component_lane_limits=[{"component": "Atlas.PM", "limit": 4}],
    )

    result = snapshot(
        [first, rework, review],
        [issue(first), issue(rework), issue(review)],
        selected_policy=selected_policy,
    )

    assert result.working_occupancy == 2
    assert result.review_occupancy == 1
    assert result.changes_requested_occupancy == 1
    assert result.changes_requested_reserve_remaining == 1
    assert result.new_admission_working_capacity == 2
    assert result.risk_lane_occupancy[0].count == 2
    assert result.component_lane_occupancy[0].count == 2


def test_ac3_snapshot_pins_every_revision_input_and_injected_observation_time() -> None:
    source = ticket("ATLAS-1", TicketStatus.IN_PROGRESS, risk_level="low")
    target = ticket("ATLAS-2", TicketStatus.PLANNED)
    edge = dependency(source, target)
    baseline = snapshot(
        [source, target],
        [issue(source), issue(target)],
        dependencies=[edge],
    )

    assert baseline.product_id == PRODUCT_ID
    assert baseline.linear_project_id == PROJECT_ID
    assert baseline.policy_revision == 7
    assert baseline.observed_at == NOW
    for value in (
        baseline.policy_fingerprint,
        baseline.status_map_fingerprint,
        baseline.fetched_board_fingerprint,
        baseline.atlas_store_revision,
        baseline.atlas_graph_revision,
        baseline.fingerprint,
    ):
        assert len(value) == 64

    policy_changed = snapshot(
        [source, target],
        [issue(source), issue(target)],
        selected_policy=policy(revision=8),
        dependencies=[edge],
    )
    board_changed = snapshot(
        [source, target],
        [issue(source, state_type="backlog"), issue(target)],
        dependencies=[edge],
    )
    store_changed_source = source.model_copy(update={"risk_level": RiskLevel.MEDIUM})
    store_changed = snapshot(
        [store_changed_source, target],
        [issue(store_changed_source), issue(target)],
        dependencies=[edge],
    )
    graph_changed_edge = edge.model_copy(update={"reason": "different graph input"})
    graph_changed = snapshot(
        [source, target],
        [issue(source), issue(target)],
        dependencies=[graph_changed_edge],
    )
    clock_changed = snapshot(
        [source, target],
        [issue(source), issue(target)],
        dependencies=[edge],
        clock=FrozenClock(NOW + timedelta(seconds=1)),
    )
    project_changed = snapshot(
        [source, target],
        [issue(source), issue(target)],
        dependencies=[edge],
        project_id="linear-project-other",
    )

    assert policy_changed.policy_fingerprint != baseline.policy_fingerprint
    assert board_changed.fetched_board_fingerprint != baseline.fetched_board_fingerprint
    assert store_changed.atlas_store_revision != baseline.atlas_store_revision
    assert graph_changed.atlas_graph_revision != baseline.atlas_graph_revision
    assert clock_changed.fingerprint != baseline.fingerprint
    assert project_changed.fingerprint != baseline.fingerprint


@pytest.mark.parametrize(
    "case",
    [
        "incomplete-pull",
        "pagination-gap",
        "duplicate-id",
        "duplicate-identifier",
        "unmapped-state",
        "contradictory-state",
        "missing-joined-issue",
        "missing-atlas-ticket",
        "atlas-linear-mismatch",
    ],
)
def test_ac4_incomplete_or_contradictory_inputs_fail_closed_with_typed_reasons(
    case: str,
) -> None:
    item = ticket("ATLAS-1", TicketStatus.IN_PROGRESS)
    items = [item]
    issues = [issue(item)]
    pull = LinearBoardPull.complete_project_pull(issues)
    expected = SnapshotIncompletenessCode.INCOMPLETE_PULL

    if case == "incomplete-pull":
        pull = LinearBoardPull(tuple(issues), complete=False)
    elif case == "pagination-gap":
        pull = LinearBoardPull(tuple(issues), pagination_gaps=("cursor-250",))
        expected = SnapshotIncompletenessCode.PAGINATION_GAP
    elif case == "duplicate-id":
        issues.append(issue(item))
        pull = LinearBoardPull.complete_project_pull(issues)
        expected = SnapshotIncompletenessCode.DUPLICATE_ISSUE_ID
    elif case == "duplicate-identifier":
        other = ticket("ATLAS-2", TicketStatus.IN_PROGRESS)
        items.append(other)
        issues.append(issue(other, identifier=item.key))
        pull = LinearBoardPull.complete_project_pull(issues)
        expected = SnapshotIncompletenessCode.DUPLICATE_ISSUE_IDENTIFIER
    elif case == "unmapped-state":
        pull = LinearBoardPull.complete_project_pull(
            [issue(item, state_id="state-unknown")]
        )
        expected = SnapshotIncompletenessCode.UNMAPPED_STATE
    elif case == "contradictory-state":
        pull = LinearBoardPull.complete_project_pull(
            [issue(item, state_type="completed")]
        )
        expected = SnapshotIncompletenessCode.CONTRADICTORY_STATE
    elif case == "missing-joined-issue":
        pull = LinearBoardPull.complete_project_pull([])
        expected = SnapshotIncompletenessCode.MISSING_JOINED_ISSUE
    elif case == "missing-atlas-ticket":
        items = []
        expected = SnapshotIncompletenessCode.MISSING_ATLAS_TICKET
    elif case == "atlas-linear-mismatch":
        pull = LinearBoardPull.complete_project_pull(
            [issue(item, status=TicketStatus.PR_OPEN)]
        )
        expected = SnapshotIncompletenessCode.ATLAS_LINEAR_STATE_MISMATCH

    result = snapshot(items, issues, board_pull=pull)

    assert expected in {reason.code for reason in result.incompleteness_reasons}
    assert result.admission_allowed is False


@pytest.mark.parametrize(
    "status",
    [
        TicketStatus.READY_FOR_AGENT,
        TicketStatus.IN_PROGRESS,
        TicketStatus.PR_OPEN,
        TicketStatus.CI_PENDING,
        TicketStatus.CHANGES_REQUESTED,
        TicketStatus.REVIEW_REQUIRED,
        TicketStatus.NEEDS_HUMAN_DECISION,
    ],
    ids=lambda status: status.value,
)
def test_ac4_unjoined_delivery_occupancy_ticket_fails_closed_without_guessing_count(
    status: TicketStatus,
) -> None:
    item = ticket("ATLAS-1", status, external_linear_id=None)

    result = snapshot([item], [])

    assert result.working_occupancy == 0
    assert result.review_occupancy == 0
    assert len(result.incompleteness_reasons) == 1
    reason = result.incompleteness_reasons[0]
    assert reason.code is SnapshotIncompletenessCode.MISSING_EXTERNAL_LINEAR_ID
    assert reason.ticket_key == item.key
    assert reason.issue_id is None
    assert result.admission_allowed is False


@pytest.mark.parametrize(
    "status",
    [TicketStatus.BACKLOG, TicketStatus.PLANNED, TicketStatus.BLOCKED],
    ids=lambda status: status.value,
)
def test_ac4_unjoined_pre_delivery_ticket_is_not_an_occupancy_join_gap(
    status: TicketStatus,
) -> None:
    item = ticket("ATLAS-1", status, external_linear_id=None)

    result = snapshot([item], [])

    assert result.incompleteness_reasons == ()
    assert result.working_occupancy == 0
    assert result.review_occupancy == 0
    assert result.admission_allowed is True


def test_ac5_over_capacity_reports_every_breached_dimension_without_action() -> None:
    working = [
        ticket(
            f"ATLAS-{number}",
            TicketStatus.IN_PROGRESS,
            risk_level="critical",
            component="atlas.pm",
        )
        for number in range(1, 4)
    ]
    reviewing = [
        ticket(
            f"ATLAS-{number}",
            TicketStatus.REVIEW_REQUIRED,
            risk_level="critical",
            component="atlas.pm",
        )
        for number in range(4, 6)
    ]
    integrating = [
        ticket(f"ATLAS-{number}", TicketStatus.CI_PENDING) for number in range(6, 9)
    ]
    items = working + reviewing + integrating
    selected_policy = policy(
        approved_symphony_ceiling=3,
        working_budget=2,
        integration_budget=2,
        review_budget=1,
        risk_lane_limits=[{"risk_level": "critical", "limit": 1}],
        component_lane_limits=[{"component": "atlas.pm", "limit": 1}],
    )
    before = tuple(items)

    result = snapshot(
        items,
        [issue(item) for item in items],
        selected_policy=selected_policy,
    )

    assert {
        (breach.dimension, breach.selector, breach.count, breach.limit)
        for breach in result.over_capacity
    } == {
        (OccupancyDimension.WORKING, None, 3, 2),
        (OccupancyDimension.INTEGRATION, None, 3, 2),
        (OccupancyDimension.REVIEW, None, 2, 1),
        (OccupancyDimension.RISK_LANE, "critical", 3, 1),
        (OccupancyDimension.COMPONENT_LANE, "atlas.pm", 3, 1),
    }
    assert result.admission_allowed is False
    assert tuple(items) == before


def test_atlas_255_ci_pending_identity_and_budget_change_snapshot_fingerprint() -> None:
    first = ticket("ATLAS-1", TicketStatus.CI_PENDING)
    second = ticket("ATLAS-2", TicketStatus.CI_PENDING)
    baseline = snapshot(
        [first], [issue(first)], selected_policy=policy(integration_budget=2)
    )
    identity_changed = snapshot(
        [second], [issue(second)], selected_policy=policy(integration_budget=2)
    )
    budget_changed = snapshot(
        [first], [issue(first)], selected_policy=policy(integration_budget=3)
    )

    assert baseline.integration_ticket_keys == (first.key,)
    assert identity_changed.integration_ticket_keys == (second.key,)
    assert identity_changed.fingerprint != baseline.fingerprint
    assert budget_changed.policy_fingerprint != baseline.policy_fingerprint
    assert budget_changed.fingerprint != baseline.fingerprint


@pytest.mark.parametrize("case", ["incomplete", "duplicate", "unmapped"])
def test_atlas_255_unsafe_ci_pending_board_state_makes_admission_unavailable(
    case: str,
) -> None:
    item = ticket("ATLAS-1", TicketStatus.CI_PENDING)
    observed = issue(item)
    pull = LinearBoardPull.complete_project_pull([observed])
    if case == "incomplete":
        pull = LinearBoardPull((observed,), complete=False)
    elif case == "duplicate":
        pull = LinearBoardPull.complete_project_pull([observed, observed])
    else:
        pull = LinearBoardPull.complete_project_pull(
            [issue(item, state_id="unmapped-ci-pending")]
        )

    result = snapshot([item], list(pull.issues), board_pull=pull)

    assert result.incompleteness_reasons
    assert result.admission_allowed is False


def test_ac5_paused_and_draining_snapshots_never_allow_admission() -> None:
    item = ticket("ATLAS-1", TicketStatus.PLANNED)

    paused = snapshot([item], [issue(item)], selected_policy=policy(mode="paused"))
    draining = snapshot([item], [issue(item)], selected_policy=policy(mode="draining"))

    assert paused.incompleteness_reasons == ()
    assert paused.over_capacity == ()
    assert draining.incompleteness_reasons == ()
    assert draining.over_capacity == ()
    assert paused.admission_allowed is False
    assert draining.admission_allowed is False


def test_ac6_snapshot_and_fingerprint_are_order_independent_and_byte_stable() -> None:
    first = ticket(
        "ATLAS-1",
        TicketStatus.IN_PROGRESS,
        risk_level="high",
        component="atlas.pm",
    )
    second = ticket(
        "ATLAS-2",
        TicketStatus.CHANGES_REQUESTED,
        risk_level="critical",
        component="atlas.core",
    )
    unjoined = ticket(
        "ATLAS-3",
        TicketStatus.REVIEW_REQUIRED,
        external_linear_id=None,
    )
    edges = [dependency(second, first), dependency(first, second)]
    issues = [issue(first), issue(second)]
    forward_policy = policy(
        risk_lane_limits=[
            {"risk_level": "high", "limit": 2},
            {"risk_level": "critical", "limit": 2},
        ],
        component_lane_limits=[
            {"component": "atlas.pm", "limit": 2},
            {"component": "atlas.core", "limit": 2},
        ],
    )
    reverse_policy = policy(
        risk_lane_limits=list(reversed(forward_policy.risk_lane_limits)),
        component_lane_limits=list(reversed(forward_policy.component_lane_limits)),
    )
    forward_pull = LinearBoardPull(
        tuple(issues), complete=False, pagination_gaps=("cursor-b", "cursor-a")
    )
    reverse_pull = LinearBoardPull(
        tuple(reversed(issues)),
        complete=False,
        pagination_gaps=("cursor-a", "cursor-b"),
    )

    forward = snapshot(
        [first, second, unjoined],
        issues,
        selected_policy=forward_policy,
        board_pull=forward_pull,
        dependencies=edges,
    )
    reverse = snapshot(
        [unjoined, second, first],
        list(reversed(issues)),
        selected_policy=reverse_policy,
        board_pull=reverse_pull,
        dependencies=list(reversed(edges)),
    )

    assert forward.model_dump() == reverse.model_dump()
    assert forward.canonical_bytes() == reverse.canonical_bytes()
    assert forward.fingerprint == reverse.fingerprint


def _mutation_calls(source: str) -> list[tuple[int, str]]:
    forbidden = {
        "create_issue",
        "delete_issue",
        "set_state",
        "update_issue",
        "apply_linear_status",
    }
    calls: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in forbidden
        ):
            calls.append((node.lineno, node.func.attr))
    return calls


def test_ac5_snapshot_module_has_no_linear_ticket_or_symphony_action_path() -> None:
    from atlas.pm import delivery_snapshot

    source = delivery_snapshot.__file__
    assert source is not None
    text = Path(source).read_text(encoding="utf-8")

    assert _mutation_calls(text) == []
    assert "symphony" not in {
        node.module.casefold()
        for node in ast.walk(ast.parse(text))
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }


def test_snapshot_mutation_sensor_fires_on_seeded_defect() -> None:
    source = dedent(
        """
        def unsafe_snapshot(client):
            assert 1 == 2
            client.set_state("issue-1", "state-ready")
        """
    )

    assert _mutation_calls(source) == [(4, "set_state")]


def test_snapshot_models_are_frozen() -> None:
    item = ticket("ATLAS-1", TicketStatus.IN_PROGRESS)
    result = snapshot([item], [issue(item)])

    with pytest.raises(ValidationError):
        result.working_occupancy = 99
