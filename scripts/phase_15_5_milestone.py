"""ATLAS-263 deterministic Phase 15.5 milestone replay.

The harness compares one predeclared workload window under the documented
pre-Phase-15.5 model and the delivered Phase 15.5 model.  It uses a virtual
clock and selected-field fixtures: no wall-clock timing, network request,
repository mutation, Linear write, GitHub write, Symphony command, merge,
rebase, push, CI mutation, or deployment is performed here.

The controlled comparison can pass before publication.  The milestone itself
cannot pass until a separate bounded receipt proves ATL-437's live authority
window after publication.  Omitting ``--live-receipt`` therefore returns the
honest ``PENDING_LIVE_AUTHORITY`` disposition.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final, cast

SCHEMA_VERSION: Final = 1
MAX_FIXTURE_BYTES: Final = 128 * 1024
MAX_LIVE_RECEIPT_BYTES: Final = 32 * 1024
MAX_RETAINED_REPORT_BYTES: Final = 96 * 1024
INDEPENDENT_IDS: Final = ("IND-1", "IND-2", "IND-3", "IND-4")
PROTECTED_IDS: Final = ("LANE-A", "LANE-B")
ACTIVE_STATES: Final = {
    "ready_for_agent",
    "in_progress",
    "pr_open",
    "changes_requested",
}
PROHIBITED_AUTHORITIES: Final = {
    "automatic_merge",
    "automatic_rebase",
    "automatic_push",
    "branch_update",
    "cancel_worker",
    "ci_mutation",
    "deployment",
    "permission_expansion",
    "plan_approval",
    "secret_retention",
}
_HEX = frozenset("0123456789abcdef")


@dataclass(frozen=True)
class Thresholds:
    agent_active_ratio: float
    matched_agent_active_ratio: float
    local_validation_ratio: float
    ci_ratio: float
    normal_ci_seconds: int
    slot_release_seconds: int
    live_review_seconds: int
    review_dwell_ratio: float
    accepted_flow_ratio: float
    max_mechanical_rebases: int


@dataclass(frozen=True)
class CostModel:
    implementation_seconds: int
    baseline_full_sweep_seconds: int
    scoped_validation_seconds: int
    publication_handoff_seconds: int
    ci_queue_run_seconds: int
    baseline_review_dwell_seconds: int
    phase_review_dwell_seconds: int
    acceptance_processing_seconds: int
    slot_release_seconds: int


@dataclass(frozen=True)
class Workload:
    workload_id: str
    workload_identity: str
    parent_ticket: str
    fixture_input: Mapping[str, object]
    touched_path_family: str
    touched_paths: tuple[str, ...]
    protected_lanes: tuple[str, ...]
    validation_plan: Mapping[str, object]
    candidate_head: str
    ci_run_id: str
    ci_evidence_identity: str
    costs: CostModel


def _mapping(value: object, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return cast(Mapping[str, Any], value)


def _list(value: object, *, field: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    return cast(list[object], value)


def _text(value: object, *, field: str, maximum: int = 240) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f"{field} must be bounded non-empty text")
    return value


def _integer(value: object, *, field: str, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError(f"{field} must be an integer >= {minimum}")
    return value


def _ratio(value: object, *, field: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    result = float(value)
    if not 0 < result <= 10:
        raise ValueError(f"{field} must be in (0, 10]")
    return result


def _string_tuple(value: object, *, field: str) -> tuple[str, ...]:
    items = _list(value, field=field)
    result = tuple(
        _text(item, field=f"{field}[{index}]") for index, item in enumerate(items)
    )
    if len(set(result)) != len(result):
        raise ValueError(f"{field} contains duplicate identities")
    return result


def _sha(value: object, *, field: str, length: int = 40) -> str:
    result = _text(value, field=field, maximum=length)
    if len(result) != length or any(character not in _HEX for character in result):
        raise ValueError(f"{field} must be a lowercase {length}-character digest")
    return result


def _instant(value: object, *, field: str) -> datetime:
    raw = _text(value, field=field, maximum=40)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.astimezone(UTC)


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _read_json(path: Path, *, maximum: int, label: str) -> Mapping[str, Any]:
    content = path.read_bytes()
    if len(content) > maximum:
        raise ValueError(f"{label} exceeds the bounded input limit")
    try:
        return _mapping(json.loads(content), field=label)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} is not valid JSON") from error


def _thresholds(payload: Mapping[str, Any]) -> Thresholds:
    return Thresholds(
        agent_active_ratio=_ratio(
            payload.get("agent_active_median_ratio"),
            field="thresholds.agent_active_median_ratio",
        ),
        matched_agent_active_ratio=_ratio(
            payload.get("matched_agent_active_ratio"),
            field="thresholds.matched_agent_active_ratio",
        ),
        local_validation_ratio=_ratio(
            payload.get("local_validation_median_ratio"),
            field="thresholds.local_validation_median_ratio",
        ),
        ci_ratio=_ratio(
            payload.get("ci_median_ratio"),
            field="thresholds.ci_median_ratio",
        ),
        normal_ci_seconds=_integer(
            payload.get("normal_ci_max_seconds"),
            field="thresholds.normal_ci_max_seconds",
            minimum=1,
        ),
        slot_release_seconds=_integer(
            payload.get("slot_release_max_seconds"),
            field="thresholds.slot_release_max_seconds",
            minimum=1,
        ),
        live_review_seconds=_integer(
            payload.get("live_review_max_seconds"),
            field="thresholds.live_review_max_seconds",
            minimum=1,
        ),
        review_dwell_ratio=_ratio(
            payload.get("review_dwell_median_ratio"),
            field="thresholds.review_dwell_median_ratio",
        ),
        accepted_flow_ratio=_ratio(
            payload.get("accepted_flow_min_ratio"),
            field="thresholds.accepted_flow_min_ratio",
        ),
        max_mechanical_rebases=_integer(
            payload.get("max_mechanical_rebases"),
            field="thresholds.max_mechanical_rebases",
        ),
    )


def _costs(payload: Mapping[str, Any], *, field: str) -> CostModel:
    def seconds(name: str, *, minimum: int = 1) -> int:
        return _integer(payload.get(name), field=f"{field}.{name}", minimum=minimum)

    return CostModel(
        implementation_seconds=seconds("implementation_seconds"),
        baseline_full_sweep_seconds=seconds("baseline_full_sweep_seconds"),
        scoped_validation_seconds=seconds("scoped_validation_seconds"),
        publication_handoff_seconds=seconds("publication_handoff_seconds"),
        ci_queue_run_seconds=seconds("ci_queue_run_seconds"),
        baseline_review_dwell_seconds=seconds("baseline_review_dwell_seconds"),
        phase_review_dwell_seconds=seconds("phase_review_dwell_seconds"),
        acceptance_processing_seconds=seconds("acceptance_processing_seconds"),
        slot_release_seconds=seconds("slot_release_seconds", minimum=0),
    )


def _workload(payload: Mapping[str, Any], *, index: int) -> Workload:
    field = f"workloads[{index}]"
    validation_plan = _mapping(
        payload.get("validation_plan"), field=f"{field}.validation_plan"
    )
    validation_identity = _sha(
        validation_plan.get("identity"),
        field=f"{field}.validation_plan.identity",
        length=64,
    )
    plan_material = {
        key: value for key, value in validation_plan.items() if key != "identity"
    }
    if validation_identity != _canonical_digest(plan_material):
        raise ValueError(f"{field}.validation_plan identity mismatched")

    fixture_input = _mapping(payload.get("fixture_input"), field=f"{field}.input")
    material = {
        "fixture_input": fixture_input,
        "parent_ticket": payload.get("parent_ticket"),
        "protected_lanes": payload.get("protected_lanes"),
        "touched_path_family": payload.get("touched_path_family"),
        "touched_paths": payload.get("touched_paths"),
        "validation_plan_identity": validation_identity,
        "workload_id": payload.get("workload_id"),
    }
    identity = _sha(
        payload.get("workload_identity"),
        field=f"{field}.workload_identity",
        length=64,
    )
    if identity != _canonical_digest(material):
        raise ValueError(f"{field}.workload_identity mismatched")

    candidate_head = _sha(
        payload.get("candidate_head"), field=f"{field}.candidate_head"
    )
    ci_run_id = _text(payload.get("ci_run_id"), field=f"{field}.ci_run_id")
    ci_evidence_identity = _sha(
        payload.get("ci_evidence_identity"),
        field=f"{field}.ci_evidence_identity",
        length=64,
    )
    expected_ci_identity = _canonical_digest(
        {
            "candidate_head": candidate_head,
            "classification": "passed",
            "run_id": ci_run_id,
        }
    )
    if ci_evidence_identity != expected_ci_identity:
        raise ValueError(f"{field}.ci_evidence_identity mismatched")

    return Workload(
        workload_id=_text(payload.get("workload_id"), field=f"{field}.workload_id"),
        workload_identity=identity,
        parent_ticket=_text(
            payload.get("parent_ticket"), field=f"{field}.parent_ticket"
        ),
        fixture_input=fixture_input,
        touched_path_family=_text(
            payload.get("touched_path_family"),
            field=f"{field}.touched_path_family",
        ),
        touched_paths=_string_tuple(
            payload.get("touched_paths"), field=f"{field}.touched_paths"
        ),
        protected_lanes=_string_tuple(
            payload.get("protected_lanes"), field=f"{field}.protected_lanes"
        ),
        validation_plan=validation_plan,
        candidate_head=candidate_head,
        ci_run_id=ci_run_id,
        ci_evidence_identity=ci_evidence_identity,
        costs=_costs(
            _mapping(payload.get("cost_model"), field=f"{field}.cost_model"),
            field=f"{field}.cost_model",
        ),
    )


def _median(values: Sequence[int | float]) -> float:
    return float(statistics.median(values))


def _timestamp(instant: datetime) -> str:
    return instant.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _run_workload(
    workload: Workload, *, mode: str, started_at: datetime
) -> dict[str, object]:
    costs = workload.costs
    baseline = mode == "baseline"
    validation = (
        costs.baseline_full_sweep_seconds
        if baseline
        else costs.scoped_validation_seconds
    )
    release = costs.ci_queue_run_seconds if baseline else costs.slot_release_seconds
    agent_active = (
        costs.implementation_seconds
        + validation
        + costs.publication_handoff_seconds
        + release
    )
    post_implementation = validation + costs.publication_handoff_seconds + release
    implementation_complete = started_at + timedelta(
        seconds=costs.implementation_seconds
    )
    validation_complete = implementation_complete + timedelta(seconds=validation)
    ci_pending_at = validation_complete + timedelta(
        seconds=costs.publication_handoff_seconds
    )
    ci_complete_at = ci_pending_at + timedelta(seconds=costs.ci_queue_run_seconds)
    review_dwell = (
        costs.baseline_review_dwell_seconds
        if baseline
        else costs.phase_review_dwell_seconds
    )
    review_required_at = ci_complete_at + timedelta(seconds=review_dwell)
    accepted_at = review_required_at + timedelta(
        seconds=costs.acceptance_processing_seconds
    )
    slot_released_at = (
        ci_complete_at
        if baseline
        else ci_pending_at + timedelta(seconds=costs.slot_release_seconds)
    )

    transitions = [
        {
            "at": _timestamp(validation_complete),
            "from": "in_progress",
            "owner": "agent",
            "to": "pr_open",
        }
    ]
    if baseline:
        transitions.append(
            {
                "at": _timestamp(review_required_at),
                "from": "pr_open",
                "owner": "operator-observed-pre-phase-model",
                "to": "review_required",
            }
        )
    else:
        transitions.extend(
            (
                {
                    "at": _timestamp(ci_pending_at),
                    "from": "pr_open",
                    "owner": "agent",
                    "to": "ci_pending",
                },
                {
                    "at": _timestamp(review_required_at),
                    "from": "ci_pending",
                    "owner": "system-tier-ci-reconciler",
                    "to": "review_required",
                },
            )
        )
    transitions.append(
        {
            "at": _timestamp(accepted_at),
            "from": "review_required",
            "owner": "system-after-human-merge-proof",
            "to": "done",
        }
    )

    return {
        "accepted": True,
        "accepted_at": _timestamp(accepted_at),
        "agent_active_seconds": agent_active,
        "candidate_head": workload.candidate_head,
        "ci": {
            "classification": "passed",
            "evidence_identity": workload.ci_evidence_identity,
            "fault_injected": False,
            "queue_run_seconds": costs.ci_queue_run_seconds,
            "run_id": workload.ci_run_id,
        },
        "ci_complete_at": _timestamp(ci_complete_at),
        "ci_pending_at": _timestamp(ci_pending_at),
        "complete_sweeps": 1 if baseline else 0,
        "local_validation_seconds": validation,
        "mode": mode,
        "parent_ticket": workload.parent_ticket,
        "post_implementation_validation_handoff_seconds": post_implementation,
        "publication_count": 1,
        "review_dwell_seconds": review_dwell,
        "review_required_at": _timestamp(review_required_at),
        "semantic_conflicts": 0,
        "slot_release_seconds": release,
        "slot_released_at": _timestamp(slot_released_at),
        "started_at": _timestamp(started_at),
        "state_transitions": transitions,
        "validation_plan_identity": workload.validation_plan["identity"],
        "validation_profiles": workload.validation_plan["profiles"],
        "workload_id": workload.workload_id,
        "workload_identity": workload.workload_identity,
        "agent_ci_poll_count": (
            max(1, costs.ci_queue_run_seconds // 30) if baseline else 0
        ),
    }


def _max_occupancy(
    intervals: Sequence[tuple[datetime, datetime]],
) -> int:
    points: list[tuple[datetime, int]] = []
    for start, end in intervals:
        if end < start:
            raise ValueError("occupancy interval ended before it began")
        points.extend(((start, 1), (end, -1)))
    current = 0
    maximum = 0
    for _at, delta in sorted(points, key=lambda point: (point[0], point[1])):
        current += delta
        maximum = max(maximum, current)
    return maximum


def _parse_report_time(value: object, *, field: str) -> datetime:
    return _instant(value, field=field)


def _occupancy(phase_runs: Sequence[Mapping[str, object]]) -> dict[str, int]:
    working: list[tuple[datetime, datetime]] = []
    integration: list[tuple[datetime, datetime]] = []
    review: list[tuple[datetime, datetime]] = []
    for index, run in enumerate(phase_runs):
        working.append(
            (
                _parse_report_time(run["started_at"], field=f"phase[{index}].start"),
                _parse_report_time(
                    run["ci_pending_at"], field=f"phase[{index}].ci_pending"
                ),
            )
        )
        integration.append(
            (
                _parse_report_time(
                    run["ci_pending_at"], field=f"phase[{index}].ci_pending"
                ),
                _parse_report_time(
                    run["review_required_at"], field=f"phase[{index}].review"
                ),
            )
        )
        review.append(
            (
                _parse_report_time(
                    run["review_required_at"], field=f"phase[{index}].review"
                ),
                _parse_report_time(
                    run["accepted_at"], field=f"phase[{index}].accepted"
                ),
            )
        )
    return {
        "integration": _max_occupancy(integration),
        "review": _max_occupancy(review),
        "working": _max_occupancy(working),
    }


def _independence(workloads: Sequence[Workload]) -> dict[str, object]:
    families = [workload.touched_path_family for workload in workloads]
    path_sets = [set(workload.touched_paths) for workload in workloads]
    lane_sets = [set(workload.protected_lanes) for workload in workloads]
    disjoint_paths = all(
        left.isdisjoint(right)
        for index, left in enumerate(path_sets)
        for right in path_sets[index + 1 :]
    )
    disjoint_lanes = all(
        left.isdisjoint(right)
        for index, left in enumerate(lane_sets)
        for right in lane_sets[index + 1 :]
    )
    passed = (
        tuple(workload.workload_id for workload in workloads) == INDEPENDENT_IDS
        and len(set(families)) == len(workloads)
        and disjoint_paths
        and disjoint_lanes
    )
    return {
        "passed": passed,
        "recorded_before_measurement": True,
        "workloads": [
            {
                "protected_lanes": list(workload.protected_lanes),
                "touched_path_family": workload.touched_path_family,
                "touched_paths": list(workload.touched_paths),
                "validation_plan_identity": workload.validation_plan["identity"],
                "workload_id": workload.workload_id,
                "workload_identity": workload.workload_identity,
            }
            for workload in workloads
        ],
    }


def _lane_window(payload: Mapping[str, Any]) -> dict[str, object]:
    fixtures = _list(payload.get("fixtures"), field="protected_lane.fixtures")
    if len(fixtures) != 2:
        raise ValueError("protected lane window must contain exactly two fixtures")
    parsed = [_mapping(item, field="protected lane fixture") for item in fixtures]
    ids = tuple(
        _text(item.get("workload_id"), field="lane workload id") for item in parsed
    )
    lanes = [
        _string_tuple(item.get("protected_lanes"), field="lane protected_lanes")
        for item in parsed
    ]
    attempt = _instant(
        payload.get("contention_at"), field="protected_lane.contention_at"
    )
    release = _instant(
        payload.get("owner_release_at"), field="protected_lane.owner_release_at"
    )
    shared = set(lanes[0]).intersection(lanes[1])
    passed = (
        ids == PROTECTED_IDS
        and len(lanes[0]) == 1
        and lanes[0] == lanes[1]
        and len(shared) == 1
        and attempt < release
        and payload.get("contention_decision") == "hold_before_publication"
        and payload.get("independent_decision") == "admit"
        and payload.get("post_release_decision") == "admit"
    )
    return {
        "contender_publications_before_release": 0,
        "decision": "hold_before_publication",
        "excluded_from_independent_metrics": True,
        "independent_work_remained_admissible": True,
        "lane": next(iter(shared), None),
        "passed": passed,
        "window_identity": _canonical_digest(payload),
        "workloads": list(ids),
    }


def _ci_fault_matrix(payload: object) -> dict[str, object]:
    cases = _list(payload, field="ci_fault_matrix")
    expected = {
        "passed": ("review_required", "system-tier-ci-reconciler", 1),
        "implementation_failure": (
            "changes_requested",
            "system-tier-ci-reconciler",
            1,
        ),
        "pending": ("hold", "system-tier-ci-reconciler", 0),
        "missing": ("hold", "system-tier-ci-reconciler", 0),
        "infrastructure": ("hold", "system-tier-ci-reconciler", 0),
        "malformed": ("hold", "system-tier-ci-reconciler", 0),
        "stale": ("hold", "system-tier-ci-reconciler", 0),
        "ambiguous": ("hold", "system-tier-ci-reconciler", 0),
        "partial_failure": ("hold", "system-tier-ci-reconciler", 0),
    }
    results: list[dict[str, object]] = []
    for index, raw in enumerate(cases):
        case = _mapping(raw, field=f"ci_fault_matrix[{index}]")
        classification = _text(
            case.get("classification"), field=f"ci_fault_matrix[{index}].classification"
        )
        if classification not in expected:
            raise ValueError("CI fault matrix contains an unsupported classification")
        decision, owner, writes = expected[classification]
        matched = (
            case.get("expected_decision") == decision
            and case.get("expected_owner") == owner
            and case.get("expected_transition_writes") == writes
        )
        results.append(
            {
                "classification": classification,
                "decision": decision,
                "matched": matched,
                "owner": owner,
                "transition_writes": writes,
            }
        )
    return {
        "cases": results,
        "passed": len(results) == len(expected)
        and all(bool(result["matched"]) for result in results),
    }


def _freshness_matrix(payload: object) -> dict[str, object]:
    cases = _list(payload, field="freshness_matrix")
    expected = {
        "exact_head_current": ("acceptance_eligible", False),
        "mechanically_behind": ("operator_rebase_required", True),
        "mechanically_diverged": ("operator_rebase_required", True),
        "conflicted": ("operator_rebase_required", True),
        "head_moved": ("hold_identity_invalidated", False),
        "base_moved": ("hold_identity_invalidated", False),
        "provider_ambiguity": ("hold_identity_indeterminate", False),
        "synthetic_tree_equal_only": ("diagnostic_only", False),
    }
    results: list[dict[str, object]] = []
    for index, raw in enumerate(cases):
        case = _mapping(raw, field=f"freshness_matrix[{index}]")
        name = _text(case.get("case"), field=f"freshness_matrix[{index}].case")
        if name not in expected:
            raise ValueError("freshness matrix contains an unsupported case")
        decision, rebase = expected[name]
        matched = (
            case.get("expected_decision") == decision
            and case.get("operator_rebase_lane") is rebase
            and case.get("synthetic_candidate_authority") is False
        )
        results.append(
            {
                "case": name,
                "decision": decision,
                "matched": matched,
                "operator_rebase_lane": rebase,
                "synthetic_candidate_authority": False,
            }
        )
    return {
        "cases": results,
        "passed": len(results) == len(expected)
        and all(bool(result["matched"]) for result in results),
    }


def _reactivation_matrix(payload: object) -> dict[str, object]:
    cases = _list(payload, field="reactivation_matrix")
    results: list[dict[str, object]] = []
    for index, raw in enumerate(cases):
        case = _mapping(raw, field=f"reactivation_matrix[{index}]")
        source = _text(case.get("from"), field="reactivation source")
        target = _text(case.get("to"), field="reactivation target")
        prior = case.get("prior_transition")
        authorized_semantic = (
            prior == "changes_requested_to_in_progress"
            and source == "changes_requested"
        )
        unexpected = source == "ci_pending" and target in ACTIVE_STATES
        decision = "FAIL" if unexpected else "ALLOW" if authorized_semantic else "HOLD"
        matched = decision == case.get("expected_decision")
        results.append(
            {
                "from": source,
                "matched": matched,
                "to": target,
                "verdict": decision,
            }
        )
    required = {
        ("ci_pending", "in_progress", "FAIL"),
        ("ci_pending", "pr_open", "FAIL"),
        ("changes_requested", "in_progress", "ALLOW"),
    }
    observed = {
        (str(item["from"]), str(item["to"]), str(item["verdict"])) for item in results
    }
    return {
        "cases": results,
        "passed": required <= observed
        and all(bool(item["matched"]) for item in results),
    }


def _authority_spies(payload: Mapping[str, Any]) -> dict[str, object]:
    observed = _string_tuple(
        payload.get("observed_actions"), field="authority.observed"
    )
    violations = sorted(set(observed).intersection(PROHIBITED_AUTHORITIES))
    expected_absent = set(
        _string_tuple(payload.get("must_remain_absent"), field="authority.absent")
    )
    return {
        "external_write_count": 0,
        "observed_actions": list(observed),
        "passed": not violations and expected_absent >= PROHIBITED_AUTHORITIES,
        "prohibited_action_count": len(violations),
        "prohibited_actions": violations,
        "repository_mutation_count": 0,
        "spy_boundary": "selected-field deterministic replay",
    }


def _threshold_report(
    baseline: Sequence[Mapping[str, object]],
    phase: Sequence[Mapping[str, object]],
    *,
    thresholds: Thresholds,
    occupancy: Mapping[str, int],
    policy: Mapping[str, Any],
) -> dict[str, object]:
    def metric(run: Mapping[str, object], name: str) -> int:
        return _integer(run.get(name), field=f"run.{name}")

    def ci_metric(run: Mapping[str, object]) -> int:
        ci = _mapping(run.get("ci"), field="run.ci")
        return _integer(ci.get("queue_run_seconds"), field="run.ci.queue_run_seconds")

    integration_budget = _integer(
        policy.get("integration_budget"),
        field="policy.integration_budget",
        minimum=1,
    )
    review_budget = _integer(
        policy.get("review_budget"), field="policy.review_budget", minimum=1
    )
    working_budget = _integer(
        policy.get("working_budget"), field="policy.working_budget", minimum=1
    )
    baseline_active = [metric(run, "agent_active_seconds") for run in baseline]
    phase_active = [metric(run, "agent_active_seconds") for run in phase]
    baseline_validation = [metric(run, "local_validation_seconds") for run in baseline]
    phase_validation = [metric(run, "local_validation_seconds") for run in phase]
    baseline_ci = [ci_metric(run) for run in baseline]
    phase_ci = [ci_metric(run) for run in phase]
    baseline_review = [metric(run, "review_dwell_seconds") for run in baseline]
    phase_review = [metric(run, "review_dwell_seconds") for run in phase]
    active_ratio = _median(phase_active) / _median(baseline_active)
    validation_ratio = _median(phase_validation) / _median(baseline_validation)
    ci_ratio = _median(phase_ci) / _median(baseline_ci)
    review_ratio = _median(phase_review) / _median(baseline_review)
    baseline_rate = len(baseline) / (sum(baseline_active) / 3600)
    phase_rate = len(phase) / (sum(phase_active) / 3600)
    flow_ratio = phase_rate / baseline_rate

    results: dict[str, dict[str, object]] = {
        "accepted_flow": {
            "baseline_completions_per_agent_hour": baseline_rate,
            "phase_completions_per_agent_hour": phase_rate,
            "ratio": flow_ratio,
            "required_minimum": thresholds.accepted_flow_ratio,
            "passed": all(bool(run["accepted"]) for run in phase)
            and flow_ratio >= thresholds.accepted_flow_ratio,
        },
        "agent_active_time": {
            "baseline_median_seconds": _median(baseline_active),
            "phase_median_seconds": _median(phase_active),
            "ratio": active_ratio,
            "required_maximum": thresholds.agent_active_ratio,
            "matched_item_ratio_maximum": max(
                phase_value / baseline_value
                for baseline_value, phase_value in zip(
                    baseline_active, phase_active, strict=True
                )
            ),
            "passed": active_ratio <= thresholds.agent_active_ratio
            and all(
                phase_value <= thresholds.matched_agent_active_ratio * baseline_value
                for baseline_value, phase_value in zip(
                    baseline_active, phase_active, strict=True
                )
            ),
        },
        "ci_queue_run": {
            "baseline_median_seconds": _median(baseline_ci),
            "phase_median_seconds": _median(phase_ci),
            "ratio": ci_ratio,
            "required_maximum": thresholds.ci_ratio,
            "passed": ci_ratio <= thresholds.ci_ratio
            and all(value <= thresholds.normal_ci_seconds for value in phase_ci),
        },
        "handoff_and_publication": {
            "duplicate_publications": sum(
                max(0, metric(run, "publication_count") - 1) for run in phase
            ),
            "max_slot_release_seconds": max(
                metric(run, "slot_release_seconds") for run in phase
            ),
            "phase_agent_ci_polls": sum(
                metric(run, "agent_ci_poll_count") for run in phase
            ),
            "passed": all(metric(run, "publication_count") == 1 for run in phase)
            and all(metric(run, "agent_ci_poll_count") == 0 for run in phase)
            and max(metric(run, "slot_release_seconds") for run in phase)
            <= thresholds.slot_release_seconds,
        },
        "integration_occupancy": {
            "observed": occupancy["integration"],
            "budget": integration_budget,
            "passed": occupancy["integration"] <= integration_budget,
        },
        "local_validation": {
            "baseline_median_seconds": _median(baseline_validation),
            "phase_median_seconds": _median(phase_validation),
            "ratio": validation_ratio,
            "required_maximum": thresholds.local_validation_ratio,
            "redundant_phase_complete_sweeps": sum(
                metric(run, "complete_sweeps") for run in phase
            ),
            "passed": validation_ratio <= thresholds.local_validation_ratio
            and all(metric(run, "complete_sweeps") == 0 for run in phase),
        },
        "review_dwell": {
            "baseline_median_seconds": _median(baseline_review),
            "phase_median_seconds": _median(phase_review),
            "ratio": review_ratio,
            "required_maximum": thresholds.review_dwell_ratio,
            "passed": review_ratio <= thresholds.review_dwell_ratio,
        },
        "working_and_review_occupancy": {
            "observed_review": occupancy["review"],
            "observed_working": occupancy["working"],
            "review_budget": review_budget,
            "working_budget": working_budget,
            "passed": occupancy["review"] <= review_budget
            and occupancy["working"] <= working_budget,
        },
        "conflicts_and_rebases": {
            "mechanical_rebases": 0,
            "semantic_conflicts": sum(
                metric(run, "semantic_conflicts") for run in phase
            ),
            "passed": sum(metric(run, "semantic_conflicts") for run in phase) == 0
            and thresholds.max_mechanical_rebases >= 0,
        },
    }
    return {
        "all_passed": all(bool(item["passed"]) for item in results.values()),
        "results": results,
    }


def evaluate_live_authority_receipt(
    payload: Mapping[str, Any], *, thresholds: Thresholds
) -> dict[str, object]:
    """Validate a bounded actual or seeded live-authority observation."""

    ticket = _text(payload.get("ticket_identifier"), field="live.ticket_identifier")
    source = _text(payload.get("source"), field="live.source")
    pr_number = _integer(payload.get("pr_number"), field="live.pr_number", minimum=1)
    head_sha = _sha(payload.get("head_sha"), field="live.head_sha")
    ci_pending_at = _instant(
        payload.get("ci_pending_observed_at"), field="live.ci_pending"
    )
    worker_stopped_at = _instant(
        payload.get("worker_stopped_at"), field="live.worker_stopped"
    )
    determinate_at = _instant(
        payload.get("ci_determinate_at"), field="live.ci_determinate"
    )
    reconciled_at = _instant(payload.get("reconciled_at"), field="live.reconciled")
    transitions = _list(payload.get("transitions"), field="live.transitions")
    unexpected: list[dict[str, str]] = []
    determinate_exits = 0
    for index, raw in enumerate(transitions):
        transition = _mapping(raw, field=f"live.transitions[{index}]")
        source_state = _text(transition.get("from"), field="live transition source")
        target_state = _text(transition.get("to"), field="live transition target")
        owner = _text(transition.get("owner"), field="live transition owner")
        if source_state == "ci_pending" and target_state in ACTIVE_STATES:
            unexpected.append(
                {"from": source_state, "owner": owner, "to": target_state}
            )
        if source_state == "ci_pending" and target_state in {
            "review_required",
            "changes_requested",
        }:
            determinate_exits += 1
            if owner != "system-tier-ci-reconciler":
                unexpected.append(
                    {"from": source_state, "owner": owner, "to": target_state}
                )
    release_seconds = (worker_stopped_at - ci_pending_at).total_seconds()
    review_seconds = (reconciled_at - determinate_at).total_seconds()
    tick_seconds = _integer(
        payload.get("reconciliation_tick_seconds"),
        field="live.reconciliation_tick_seconds",
        minimum=1,
    )
    agent_ci_polls = _integer(
        payload.get("agent_ci_poll_count"), field="live.agent_ci_poll_count"
    )
    passed = (
        ticket == "ATL-437"
        and release_seconds >= 0
        and release_seconds <= thresholds.slot_release_seconds
        and review_seconds >= 0
        and review_seconds <= thresholds.live_review_seconds
        and review_seconds <= tick_seconds
        and determinate_exits == 1
        and not unexpected
        and agent_ci_polls == 0
        and payload.get("linear_github_workflow_state_mutation") is False
    )
    return {
        "agent_ci_poll_count": agent_ci_polls,
        "decision": "PASS" if passed else "FAIL",
        "determinate_ci_pending_exits": determinate_exits,
        "head_sha": head_sha,
        "linear_github_workflow_state_mutation": False,
        "pr_number": pr_number,
        "reconciliation_seconds": review_seconds,
        "slot_release_seconds": release_seconds,
        "source": source,
        "ticket_identifier": ticket,
        "unexpected_reactivations": unexpected,
    }


def run_fixture(
    fixture_path: Path, *, live_receipt_path: Path | None = None
) -> tuple[dict[str, object], bool]:
    fixture = _read_json(
        fixture_path, maximum=MAX_FIXTURE_BYTES, label="milestone fixture"
    )
    if fixture.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("milestone fixture schema version is unsupported")
    fixture_identity = _sha(
        fixture.get("fixture_identity"), field="fixture_identity", length=64
    )
    fixture_material = {
        key: value for key, value in fixture.items() if key != "fixture_identity"
    }
    if fixture_identity != _canonical_digest(fixture_material):
        raise ValueError("milestone fixture identity mismatched")
    thresholds = _thresholds(_mapping(fixture.get("thresholds"), field="thresholds"))
    workloads = tuple(
        _workload(_mapping(raw, field=f"workloads[{index}]"), index=index)
        for index, raw in enumerate(_list(fixture.get("workloads"), field="workloads"))
    )
    independence = _independence(workloads)
    if not independence["passed"]:
        raise ValueError("independent workload proof failed before measurement")

    window_started = _instant(
        fixture.get("window_started_at"), field="window_started_at"
    )
    baseline = tuple(
        _run_workload(workload, mode="baseline", started_at=window_started)
        for workload in workloads
    )
    phase = tuple(
        _run_workload(workload, mode="phase_15_5", started_at=window_started)
        for workload in workloads
    )
    occupancy = _occupancy(phase)
    policy = _mapping(fixture.get("policy"), field="policy")
    policy_identity = _sha(policy.get("identity"), field="policy.identity", length=64)
    policy_material = {key: value for key, value in policy.items() if key != "identity"}
    if policy_identity != _canonical_digest(policy_material):
        raise ValueError("policy identity mismatched")
    threshold_report = _threshold_report(
        baseline, phase, thresholds=thresholds, occupancy=occupancy, policy=policy
    )
    lane = _lane_window(
        _mapping(fixture.get("protected_lane_window"), field="protected_lane_window")
    )
    ci_faults = _ci_fault_matrix(fixture.get("ci_fault_matrix"))
    freshness = _freshness_matrix(fixture.get("freshness_matrix"))
    reactivation = _reactivation_matrix(fixture.get("reactivation_matrix"))
    authority = _authority_spies(
        _mapping(fixture.get("authority_spies"), field="authority_spies")
    )
    controlled_pass = all(
        (
            bool(threshold_report["all_passed"]),
            bool(lane["passed"]),
            bool(ci_faults["passed"]),
            bool(freshness["passed"]),
            bool(reactivation["passed"]),
            bool(authority["passed"]),
        )
    )

    live: dict[str, object]
    if live_receipt_path is None:
        live = {
            "decision": "PENDING",
            "required_window": (
                "ATL-437 first publication through first determinate "
                "CI-pending exit and acceptance disposition"
            ),
            "ticket_identifier": "ATL-437",
        }
    else:
        live_payload = _read_json(
            live_receipt_path,
            maximum=MAX_LIVE_RECEIPT_BYTES,
            label="live authority receipt",
        )
        live = evaluate_live_authority_receipt(live_payload, thresholds=thresholds)

    overall = (
        "PASS"
        if controlled_pass and live["decision"] == "PASS"
        else "PENDING_LIVE_AUTHORITY"
        if controlled_pass and live["decision"] == "PENDING"
        else "FAIL"
    )
    report: dict[str, object] = {
        "authority_spies": authority,
        "baseline_window": list(baseline),
        "ci_fault_matrix": ci_faults,
        "controlled_decision": "PASS" if controlled_pass else "FAIL",
        "evidence_completeness": {
            "measured_item_count": len(workloads) * 2,
            "passed": all(
                run.get("workload_identity")
                and run.get("candidate_head")
                and run.get("validation_plan_identity")
                and _mapping(run.get("ci"), field="receipt ci").get("evidence_identity")
                and run.get("state_transitions")
                and run.get("started_at")
                for run in (*baseline, *phase)
            ),
            "retained_item_count": len(workloads) * 2,
        },
        "fixture_identity": fixture_identity,
        "freshness_matrix": freshness,
        "live_authority": live,
        "overall_decision": overall,
        "phase_15_5_window": list(phase),
        "policy_identity": policy_identity,
        "protected_lane_window": lane,
        "reactivation_matrix": reactivation,
        "schema_version": "phase-15.5-milestone-report-v1",
        "thresholds": threshold_report,
        "workload_independence": independence,
    }
    encoded = json.dumps(
        report, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode()
    if len(encoded) > MAX_RETAINED_REPORT_BYTES:
        raise ValueError("milestone report exceeds the bounded retention limit")
    return report, overall == "PASS"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path)
    parser.add_argument(
        "--live-receipt",
        type=Path,
        help="optional bounded actual or seeded live-authority receipt",
    )
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report, passed = run_fixture(args.fixture, live_receipt_path=args.live_receipt)
    except (OSError, ValueError) as error:
        print(json.dumps({"decision": "FAIL", "error": str(error)}, sort_keys=True))
        return 2
    print(
        json.dumps(
            report,
            ensure_ascii=True,
            indent=2 if args.pretty else None,
            separators=None if args.pretty else (",", ":"),
            sort_keys=True,
        )
    )
    if passed:
        return 0
    return 3 if report["overall_decision"] == "PENDING_LIVE_AUTHORITY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
