"""ATLAS-248 deterministic capacity-aware admission decisions."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st
from test_delivery_snapshot import NOW, dependency, issue, policy, snapshot, ticket

from atlas.core.enums import RiskLevel
from atlas.core.models import Ticket
from atlas.core.models.admission_run import (
    AdmissionDecisionType,
    AdmissionHoldCode,
)
from atlas.core.models.ticket import TicketStatus
from atlas.dependencies import CriticalPath, CriticalPathStep, project_graph
from atlas.pm import (
    AdmissionInputMismatchCode,
    AdmissionInputMismatchError,
    LinearBoardPull,
    delivery_policy_fingerprint,
    evaluate_admission,
)
from atlas.pm import admission as admission_module


class FrozenClock:
    def __init__(self, instant: datetime = NOW + timedelta(hours=1)) -> None:
        self.instant = instant
        self.calls = 0

    def __call__(self) -> datetime:
        self.calls += 1
        return self.instant


def evaluate(
    items: list[Ticket],
    *,
    selected_policy: Any | None = None,
    selected_snapshot: Any | None = None,
    eligible_since: dict[str, datetime] | None = None,
    clock: FrozenClock | None = None,
) -> Any:
    selected_policy = selected_policy or policy()
    observed = selected_snapshot or snapshot(
        items,
        [issue(item) for item in items if item.external_linear_id is not None],
        selected_policy=selected_policy,
    )
    ready_since = (
        eligible_since
        if eligible_since is not None
        else {
            item.key: NOW
            for item in items
            if item.status in {TicketStatus.PLANNED, TicketStatus.BACKLOG}
        }
    )
    return evaluate_admission(
        graph=project_graph(items, [], [], []),
        tickets=items,
        policy=selected_policy,
        snapshot=observed,
        continuously_eligible_since=ready_since,
        clock=clock or FrozenClock(),
    )


def decisions_by_key(run: Any) -> dict[str, Any]:
    return {decision.ticket_key: decision for decision in run.decisions}


def reason_codes(decision: Any) -> set[AdmissionHoldCode]:
    return {reason.code for reason in decision.reasons}


def test_ac1_candidates_are_exactly_existing_readiness_results() -> None:
    ready = ticket("ATLAS-1", TicketStatus.PLANNED)
    wrong_status = ticket("ATLAS-2", TicketStatus.IN_PROGRESS)
    no_criteria = ticket("ATLAS-3", TicketStatus.PLANNED, acceptance_criteria=[])

    run = evaluate([wrong_status, no_criteria, ready])

    assert [decision.ticket_key for decision in run.decisions] == [ready.key]
    assert run.selected_ticket_key == ready.key


@pytest.mark.parametrize(
    ("case", "keys", "overrides", "unlocks", "path", "ages", "expected"),
    [
        (
            "unlock-count",
            ("ATLAS-1", "ATLAS-2"),
            ({}, {}),
            {"ATLAS-1": 1, "ATLAS-2": 2},
            (),
            (0, 0),
            "ATLAS-2",
        ),
        (
            "critical-membership",
            ("ATLAS-1", "ATLAS-2"),
            ({}, {}),
            {},
            ("ATLAS-2",),
            (0, 0),
            "ATLAS-2",
        ),
        (
            "critical-position",
            ("ATLAS-1", "ATLAS-2"),
            ({}, {}),
            {},
            ("ATLAS-2", "ATLAS-1"),
            (0, 0),
            "ATLAS-2",
        ),
        (
            "priority",
            ("ATLAS-1", "ATLAS-2"),
            ({"priority": 1}, {"priority": 2}),
            {},
            (),
            (0, 0),
            "ATLAS-2",
        ),
        (
            "risk",
            ("ATLAS-1", "ATLAS-2"),
            ({"risk_level": "high"}, {"risk_level": "low"}),
            {},
            (),
            (0, 0),
            "ATLAS-2",
        ),
        (
            "continuous-age",
            ("ATLAS-1", "ATLAS-2"),
            ({}, {}),
            {},
            (),
            (0, 1),
            "ATLAS-2",
        ),
        (
            "natural-key",
            ("ATLAS-10", "ATLAS-2"),
            ({}, {}),
            {},
            (),
            (0, 0),
            "ATLAS-2",
        ),
    ],
)
def test_ac2_ranking_exhausts_each_documented_tie_break(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    keys: tuple[str, str],
    overrides: tuple[dict[str, Any], dict[str, Any]],
    unlocks: dict[str, int],
    path: tuple[str, ...],
    ages: tuple[int, int],
    expected: str,
) -> None:
    del case
    items = [
        ticket(key, TicketStatus.PLANNED, **values)
        for key, values in zip(keys, overrides, strict=True)
    ]
    steps = tuple(
        CriticalPathStep(key, 1, position + 1) for position, key in enumerate(path)
    )
    monkeypatch.setattr(
        admission_module, "critical_path", lambda graph: CriticalPath(steps)
    )
    monkeypatch.setattr(
        admission_module,
        "unlocks",
        lambda graph, key: SimpleNamespace(count=unlocks.get(key, 0)),
    )
    eligible_since = {
        item.key: NOW - timedelta(days=age)
        for item, age in zip(items, ages, strict=True)
    }

    run = evaluate(items, eligible_since=eligible_since)

    assert run.decisions[0].ticket_key == expected
    assert run.decisions[0].rank == 1


@pytest.mark.parametrize(
    ("mode", "code"),
    [
        ("paused", AdmissionHoldCode.POLICY_PAUSED),
        ("draining", AdmissionHoldCode.POLICY_DRAINING),
    ],
)
def test_ac3_paused_and_draining_have_explicit_typed_reasons(
    mode: str, code: AdmissionHoldCode
) -> None:
    candidate = ticket("ATLAS-1", TicketStatus.PLANNED)
    selected_policy = policy(mode=mode)

    run = evaluate([candidate], selected_policy=selected_policy)

    assert run.selected_ticket_id is None
    assert reason_codes(run.decisions[0]) == {code}


def test_ac3_incomplete_snapshot_retains_every_typed_snapshot_reason() -> None:
    candidate = ticket("ATLAS-1", TicketStatus.PLANNED)
    observed = snapshot(
        [candidate],
        [issue(candidate)],
        board_pull=LinearBoardPull(
            (issue(candidate),),
            complete=False,
            pagination_gaps=("cursor-b", "cursor-a"),
        ),
    )

    run = evaluate([candidate], selected_snapshot=observed)

    reasons = run.decisions[0].reasons
    assert [reason.source_code for reason in reasons] == [
        "incomplete_pull",
        "pagination_gap",
        "pagination_gap",
    ]
    assert run.selected_ticket_id is None


def test_ac3_combined_budget_and_lane_reasons_are_retained() -> None:
    candidate = ticket(
        "ATLAS-1",
        TicketStatus.PLANNED,
        priority=10,
        risk_level="high",
        component="Atlas.PM",
    )
    working = [
        ticket(
            f"ATLAS-{number}",
            TicketStatus.IN_PROGRESS,
            risk_level="high",
            component="atlas.pm",
        )
        for number in range(2, 5)
    ]
    reviewing = ticket("ATLAS-5", TicketStatus.REVIEW_REQUIRED)
    selected_policy = policy(
        approved_symphony_ceiling=3,
        working_budget=3,
        review_budget=1,
        changes_requested_reserve=1,
        risk_lane_limits=[{"risk_level": "high", "limit": 3}],
        component_lane_limits=[{"component": "atlas.pm", "limit": 3}],
    )

    run = evaluate([candidate, *working, reviewing], selected_policy=selected_policy)

    decision = decisions_by_key(run)[candidate.key]
    assert reason_codes(decision) == {
        AdmissionHoldCode.WORKING_BUDGET,
        AdmissionHoldCode.REVIEW_BUDGET,
        AdmissionHoldCode.CHANGES_REQUESTED_RESERVE,
        AdmissionHoldCode.RISK_LANE,
        AdmissionHoldCode.COMPONENT_LANE,
    }
    assert run.selected_ticket_id is None


@pytest.mark.parametrize("pending_count", [1, 2], ids=["full", "over-capacity"])
def test_atlas_255_full_or_breached_integration_budget_holds_new_admission(
    pending_count: int,
) -> None:
    candidate = ticket("ATLAS-1", TicketStatus.PLANNED)
    integrating = [
        ticket(f"ATLAS-{number}", TicketStatus.CI_PENDING)
        for number in range(2, pending_count + 2)
    ]
    selected_policy = policy(integration_budget=1)

    run = evaluate([candidate, *integrating], selected_policy=selected_policy)

    decision = decisions_by_key(run)[candidate.key]
    assert AdmissionHoldCode.INTEGRATION_BUDGET in reason_codes(decision)
    assert run.selected_ticket_id is None


def test_atlas_255_changed_snapshot_integration_budget_fails_closed() -> None:
    candidate = ticket("ATLAS-1", TicketStatus.PLANNED)
    snapshot_policy = policy(integration_budget=2)
    observed = snapshot(
        [candidate],
        [issue(candidate)],
        selected_policy=snapshot_policy,
    )
    evaluated_policy = policy(integration_budget=3)

    run = evaluate(
        [candidate],
        selected_policy=evaluated_policy,
        selected_snapshot=observed,
    )

    assert observed.policy_fingerprint == delivery_policy_fingerprint(evaluated_policy)
    assert observed.integration_budget == 2
    assert reason_codes(run.decisions[0]) == {
        AdmissionHoldCode.SNAPSHOT_POLICY_MISMATCH
    }
    assert run.selected_ticket_id is None


def test_ac4_zero_or_one_selection_continues_past_a_held_higher_rank() -> None:
    lane_blocked = ticket(
        "ATLAS-1",
        TicketStatus.PLANNED,
        priority=10,
        risk_level="high",
    )
    admissible = ticket("ATLAS-2", TicketStatus.PLANNED, priority=1, risk_level="low")
    third = ticket("ATLAS-3", TicketStatus.PLANNED, priority=0, risk_level="low")
    selected_policy = policy(risk_lane_limits=[{"risk_level": "high", "limit": 0}])

    run = evaluate([third, admissible, lane_blocked], selected_policy=selected_policy)

    decisions = decisions_by_key(run)
    assert decisions[lane_blocked.key].decision is AdmissionDecisionType.HOLD
    assert reason_codes(decisions[lane_blocked.key]) == {AdmissionHoldCode.RISK_LANE}
    assert decisions[admissible.key].decision is AdmissionDecisionType.ADMIT
    assert reason_codes(decisions[third.key]) == {AdmissionHoldCode.SINGLE_WRITE_LIMIT}
    assert run.selected_ticket_key == admissible.key
    assert (
        sum(
            decision.decision is AdmissionDecisionType.ADMIT
            for decision in run.decisions
        )
        == 1
    )


def test_ac4_missing_external_identity_is_an_explicit_no_write_hold() -> None:
    candidate = ticket("ATLAS-1", TicketStatus.PLANNED, external_linear_id=None)

    run = evaluate([candidate])

    assert reason_codes(run.decisions[0]) == {
        AdmissionHoldCode.MISSING_EXTERNAL_LINEAR_ID
    }
    assert run.selected_ticket_id is None


def test_ac5_run_pins_fingerprints_rank_inputs_and_no_raw_linear_payload() -> None:
    candidate = ticket("ATLAS-1", TicketStatus.PLANNED)
    clock = FrozenClock()

    run = evaluate([candidate], clock=clock)

    assert len(run.policy_fingerprint) == 64
    assert len(run.snapshot_fingerprint) == 64
    assert run.evaluated_at == clock.instant
    assert run.decisions[0].rank_inputs.continuously_eligible_since == NOW
    assert run.decisions[0].rank_inputs.unlock_count == 0
    assert "raw" not in run.canonical_bytes().decode().casefold()
    assert clock.calls == 1


@given(st.permutations((0, 1, 2)))
def test_ac6_property_source_order_produces_byte_identical_run(
    order: list[int],
) -> None:
    items = [
        ticket("ATLAS-2", TicketStatus.PLANNED, priority=2),
        ticket("ATLAS-10", TicketStatus.PLANNED, priority=1),
        ticket("ATLAS-1", TicketStatus.IN_PROGRESS),
    ]
    selected_policy = policy(working_budget=3, approved_symphony_ceiling=3)
    issues = [issue(item) for item in items]
    baseline_snapshot = snapshot(items, issues, selected_policy=selected_policy)
    baseline = evaluate_admission(
        graph=project_graph(items, [], [], []),
        tickets=items,
        policy=selected_policy,
        snapshot=baseline_snapshot,
        continuously_eligible_since={"ATLAS-2": NOW, "ATLAS-10": NOW},
        clock=FrozenClock(),
    )
    permuted = [items[index] for index in order]
    permuted_issues = [issues[index] for index in order]
    reordered_snapshot = snapshot(
        permuted, permuted_issues, selected_policy=selected_policy
    )
    reordered = evaluate_admission(
        graph=project_graph(permuted, [], [], []),
        tickets=permuted,
        policy=selected_policy,
        snapshot=reordered_snapshot,
        continuously_eligible_since={"ATLAS-10": NOW, "ATLAS-2": NOW},
        clock=FrozenClock(),
    )

    assert reordered.canonical_bytes() == baseline.canonical_bytes()


@given(
    working_budget=st.integers(min_value=1, max_value=5),
    existing_work=st.integers(min_value=0, max_value=5),
    reserve=st.integers(min_value=0, max_value=5),
)
def test_ac6_property_selection_never_oversubscribes_working_or_reserve(
    working_budget: int, existing_work: int, reserve: int
) -> None:
    reserve = min(reserve, working_budget)
    selected_policy = policy(
        approved_symphony_ceiling=5,
        working_budget=working_budget,
        changes_requested_reserve=reserve,
    )
    candidate = ticket("ATLAS-1", TicketStatus.PLANNED)
    working = [
        ticket(f"ATLAS-{number}", TicketStatus.IN_PROGRESS)
        for number in range(2, existing_work + 2)
    ]

    run = evaluate([candidate, *working], selected_policy=selected_policy)

    if run.selected_ticket_id is not None:
        assert existing_work + 1 <= working_budget
        assert existing_work + 1 + reserve <= working_budget


def test_ac6_uninjected_time_and_random_ids_do_not_change_selection() -> None:
    first = ticket("ATLAS-2", TicketStatus.PLANNED, created_at=NOW)
    second = ticket(
        "ATLAS-2",
        TicketStatus.PLANNED,
        created_at=NOW - timedelta(days=100),
    )

    first_run = evaluate([first], eligible_since={first.key: NOW})
    second_run = evaluate([second], eligible_since={second.key: NOW})

    assert first.id != second.id
    assert first_run.selected_ticket_key == second_run.selected_ticket_key == "ATLAS-2"
    assert first_run.decisions[0].decision is second_run.decisions[0].decision


def test_snapshot_from_unrelated_ticket_set_is_rejected_before_decision() -> None:
    snapshotted = ticket("ATLAS-1", TicketStatus.PLANNED)
    live = ticket("ATLAS-2", TicketStatus.PLANNED)
    observed = snapshot([snapshotted], [issue(snapshotted)])
    clock = FrozenClock()

    with pytest.raises(AdmissionInputMismatchError) as raised:
        evaluate_admission(
            graph=project_graph([live], [], [], []),
            tickets=[live],
            policy=policy(),
            snapshot=observed,
            continuously_eligible_since={live.key: NOW},
            clock=clock,
        )

    assert raised.value.mismatches == (
        AdmissionInputMismatchCode.ATLAS_STORE_REVISION,
        AdmissionInputMismatchCode.ATLAS_GRAPH_REVISION,
    )
    assert clock.calls == 0


@pytest.mark.parametrize(
    ("case", "overrides", "expected_mismatches"),
    [
        (
            "ticket-id",
            {"id": uuid4()},
            {
                AdmissionInputMismatchCode.ATLAS_STORE_REVISION,
                AdmissionInputMismatchCode.ATLAS_GRAPH_REVISION,
            },
        ),
        (
            "status",
            {"status": TicketStatus.BACKLOG},
            {
                AdmissionInputMismatchCode.ATLAS_STORE_REVISION,
                AdmissionInputMismatchCode.ATLAS_GRAPH_REVISION,
            },
        ),
        (
            "external-linear-id",
            {"external_linear_id": "issue-replaced"},
            {AdmissionInputMismatchCode.ATLAS_STORE_REVISION},
        ),
        (
            "priority",
            {"priority": 99},
            {
                AdmissionInputMismatchCode.ATLAS_STORE_REVISION,
                AdmissionInputMismatchCode.ATLAS_GRAPH_REVISION,
            },
        ),
        (
            "risk",
            {"risk_level": RiskLevel.CRITICAL},
            {
                AdmissionInputMismatchCode.ATLAS_STORE_REVISION,
                AdmissionInputMismatchCode.ATLAS_GRAPH_REVISION,
            },
        ),
        (
            "component",
            {"component": "atlas.linear"},
            {AdmissionInputMismatchCode.ATLAS_STORE_REVISION},
        ),
        (
            "acceptance-criteria",
            {"acceptance_criteria": ["changed but still non-empty"]},
            {AdmissionInputMismatchCode.ATLAS_STORE_REVISION},
        ),
    ],
)
def test_same_key_with_changed_decision_state_is_rejected(
    case: str,
    overrides: dict[str, Any],
    expected_mismatches: set[AdmissionInputMismatchCode],
) -> None:
    del case
    snapshotted = ticket(
        "ATLAS-1",
        TicketStatus.PLANNED,
        priority=3,
        risk_level="low",
        component="atlas.pm",
    )
    observed = snapshot([snapshotted], [issue(snapshotted)])
    live = snapshotted.model_copy(update=overrides)

    with pytest.raises(AdmissionInputMismatchError) as raised:
        evaluate_admission(
            graph=project_graph([live], [], [], []),
            tickets=[live],
            policy=policy(),
            snapshot=observed,
            continuously_eligible_since={live.key: NOW},
            clock=FrozenClock(),
        )

    assert set(raised.value.mismatches) == expected_mismatches


def test_changed_dependency_state_is_rejected_before_readiness() -> None:
    candidate = ticket("ATLAS-1", TicketStatus.PLANNED)
    prerequisite = ticket("ATLAS-2", TicketStatus.DONE)
    edge = dependency(candidate, prerequisite)
    items = [candidate, prerequisite]
    observed = snapshot(
        items,
        [issue(candidate), issue(prerequisite)],
        dependencies=[edge],
    )
    changed_edge = edge.model_copy(update={"reason": "changed after snapshot"})

    with pytest.raises(AdmissionInputMismatchError) as raised:
        evaluate_admission(
            graph=project_graph(items, [], [], [changed_edge]),
            tickets=items,
            policy=policy(),
            snapshot=observed,
            continuously_eligible_since={candidate.key: NOW},
            clock=FrozenClock(),
        )

    assert raised.value.mismatches == (AdmissionInputMismatchCode.ATLAS_GRAPH_REVISION,)


def test_changed_adr_target_state_is_rejected_before_readiness() -> None:
    candidate = ticket("ATLAS-1", TicketStatus.PLANNED)
    graph = project_graph([candidate], [], [], [])
    graph.add_node(
        "ADR-0001",
        node_type="adr",
        entity_id=uuid4(),
        present=True,
        status="accepted",
    )
    graph.add_edge(
        candidate.key,
        "ADR-0001",
        dependency_id=uuid4(),
        dependency_type="depends_on",
        reason="governing decision",
    )
    observed = snapshot([candidate], [issue(candidate)], graph=graph)
    changed = graph.copy()
    changed.nodes["ADR-0001"]["status"] = "proposed"

    with pytest.raises(AdmissionInputMismatchError) as raised:
        evaluate_admission(
            graph=changed,
            tickets=[candidate],
            policy=policy(),
            snapshot=observed,
            continuously_eligible_since={candidate.key: NOW},
            clock=FrozenClock(),
        )

    assert raised.value.mismatches == (AdmissionInputMismatchCode.ATLAS_GRAPH_REVISION,)


def test_exact_snapshot_graph_and_ticket_inputs_retain_one_selection() -> None:
    candidate = ticket("ATLAS-1", TicketStatus.PLANNED)
    prerequisite = ticket("ATLAS-2", TicketStatus.DONE)
    edge = dependency(candidate, prerequisite)
    items = [candidate, prerequisite]
    graph = project_graph(items, [], [], [edge])
    observed = snapshot(
        items,
        [issue(candidate), issue(prerequisite)],
        dependencies=[edge],
        graph=graph,
    )

    run = evaluate_admission(
        graph=graph,
        tickets=reversed(items),
        policy=policy(),
        snapshot=observed,
        continuously_eligible_since={candidate.key: NOW},
        clock=FrozenClock(),
    )

    assert run.selected_ticket_key == candidate.key
    assert [decision.ticket_key for decision in run.decisions] == [candidate.key]


def test_continuous_eligibility_is_required_and_must_not_be_guessed() -> None:
    candidate = ticket("ATLAS-1", TicketStatus.PLANNED)

    with pytest.raises(ValueError, match="missing continuously eligible time"):
        evaluate([candidate], eligible_since={})

    with pytest.raises(ValueError, match="cannot be in the future"):
        evaluate(
            [candidate],
            eligible_since={candidate.key: NOW + timedelta(days=1)},
            clock=FrozenClock(NOW),
        )


def test_admission_time_must_be_aware() -> None:
    candidate = ticket("ATLAS-1", TicketStatus.PLANNED)

    with pytest.raises(ValueError, match="evaluation time must be timezone-aware"):
        evaluate(
            [candidate],
            eligible_since={candidate.key: NOW},
            clock=FrozenClock(datetime(2026, 8, 3)),
        )
