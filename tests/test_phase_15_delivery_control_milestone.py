"""ATLAS-253 deterministic validator for the operator-owned live ramp."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from atlas.pm.protected_lanes import (
    DEFAULT_PROTECTED_LANE_REGISTRY,
    ProtectedLaneClassifierInput,
    classify_protected_lane_inputs,
)
from scripts.phase_15_delivery_control_milestone import (
    COMMON_INVARIANTS,
    FIXTURE_RUNTIME_PROCEDURE,
    GATE_EXERCISES,
    GATE_LEVELS,
    LIVE_RUNTIME_PROCEDURE,
    LIVE_RUNTIME_SERVICE_UNIT,
    LIVE_SYMPHONY_COMMIT_SHA,
    MAX_MANIFEST_BYTES,
    MAX_REPORT_BYTES,
    calculate_v3_manifest_fingerprint,
    evaluate_ramp,
    main,
    validate_manifest,
)

ROOT = Path(__file__).resolve().parents[1]
V2_MANIFEST = ROOT / "tests" / "fixtures" / "phase_15" / "ramp_workload_seed_v2.json"
HISTORICAL_MANIFEST = (
    ROOT / "tests" / "fixtures" / "phase_15" / "ramp_workload_seed_v1.json"
)
REQUIRED_DOCS = (
    ROOT / "WORKFLOW.md",
    ROOT / "ROADMAP.md",
    ROOT / "docs" / "atlas" / "implementation-roadmap.md",
    ROOT / "docs" / "atlas" / "multi-agent-delivery-control.md",
    ROOT / "docs" / "atlas" / "agentic-engineering-programme-design.md",
    ROOT / "docs" / "atlas" / "phase-16-agent-runtime-and-integration-safety.md",
    ROOT / "docs" / "atlas" / "parallel-delivery-efficiency-and-integration-control.md",
    ROOT / "docs" / "runbooks" / "local-development.md",
    ROOT / "docs" / "runbooks" / "operator-environment.md",
    ROOT / "docs" / "runbooks" / "pr-acceptance.md",
    ROOT / "docs" / "closure" / "phase-15.5-closure-report.md",
)


def _manifest() -> dict[str, Any]:
    v2 = _v2_manifest()
    exercise_roles = {
        (1, "protected_lane_ci_pending_hold", "owner"): (
            "gate_1_protected_lane_ci_pending_hold_owner",
            "workflow-configuration",
        ),
        (3, "protected_lane_contention", "blocked_candidate"): (
            "gate_3_protected_lane_contention_blocked_candidate",
            "operator-admission-hotspot",
        ),
        (3, "protected_lane_contention", "owner"): (
            "gate_3_protected_lane_contention_owner",
            "operator-admission-hotspot",
        ),
        (5, "protected_lane_with_unrelated_parallelism", "owner"): (
            "gate_5_protected_lane_with_unrelated_parallelism_owner",
            "database-migrations",
        ),
        (7, "ci_pending_lane_ownership", "owner"): (
            "gate_7_ci_pending_lane_ownership_owner",
            "planning-state",
        ),
        (7, "risk_component_protected_lanes_under_load", "owner"): (
            "gate_7_risk_component_protected_lanes_under_load_owner",
            "generated-contracts",
        ),
    }
    exercise_identity = {
        "ATLAS-8": ("ATL-8", "b3200846-8d4f-400e-9ba5-dc4ac323131b"),
        "ATLAS-14": ("ATL-14", "963b7803-a4e2-4e2a-97b3-2e06db2daa87"),
        "ATLAS-234": ("ATL-411", "0b327c05-8033-4d26-b9ed-23300807ec12"),
        "ATLAS-250": ("ATL-423", "52576e67-0181-473f-983d-3d7d41341c82"),
        "ATLAS-251": ("ATL-420", "76bee4d4-adc6-441c-8832-2279307c5d60"),
        "ATLAS-252": ("ATL-409", "54c55367-392e-4832-a568-ca2650063573"),
    }
    binding_by_workload = {
        binding["exercise_workload_id"]: binding for binding in v2["exercise_bindings"]
    }
    workloads: list[dict[str, Any]] = []
    for index, workload in enumerate(v2["workloads"]):
        material = {
            "workload_id": workload["workload_id"],
            "atlas_key": f"ATLAS-{264 + index}",
            "linear_identifier": f"ATL-{500 + index}",
            "linear_uuid": f"00000000-0000-4000-8000-{index + 1:012d}",
            "risk": ("low", "medium", "high", "critical")[index % 4],
            "production_paths": workload["touched_paths"],
            "test_paths": [f"tests/ramp/family-{index + 1:02d}/test_contract.py"],
            "path_family": workload["touched_path_family"],
            "independent": True,
            "dependency_identities": [],
            "protected_lane_classification": workload["protected_lanes"],
            "workload_role": "ordinary",
            "earliest_permitted_gate": 1,
            "excluded_from_throughput": False,
        }
        material["native_workload_fingerprint"] = hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        workloads.append(material)
    exercise_workloads: list[dict[str, Any]] = []
    for index, workload in enumerate(v2["exercise_workloads"]):
        binding = binding_by_workload[workload["exercise_workload_id"]]
        role, expected_lane = exercise_roles[
            (binding["gate_level"], binding["exercise_id"], binding["role"])
        ]
        assert workload["classification"]["matches"][0]["lane"] == expected_lane
        linear_identifier, linear_uuid = exercise_identity[workload["ticket_key"]]
        exercise_workloads.append(
            {
                "exercise_workload_id": workload["exercise_workload_id"],
                "atlas_key": workload["ticket_key"],
                "linear_identifier": linear_identifier,
                "linear_uuid": linear_uuid,
                "objective": f"Seeded objective for {role}",
                "production_paths": workload["touched_paths"],
                "test_paths": [f"tests/ramp/exercise-{index + 1}/test_contract.py"],
                "path_family": workload["touched_path_family"],
                "classifier_inputs": {
                    "atlas_key": workload["ticket_key"],
                    "component": workload["component"],
                    "tags": workload["tags"],
                    "relevant_docs": workload["relevant_docs"],
                    "documentation_requirements": workload[
                        "documentation_requirements"
                    ],
                },
                "protected_lane_classification": workload["classification"],
                "reconstructed_protected_lanes": [expected_lane],
                "classifier_fingerprint": workload["classification_fingerprint"],
                "dependency_identities": [],
                "exercise_role": role,
                "earliest_permitted_gate": binding["gate_level"],
                "excluded_from_throughput": True,
            }
        )
    manifest = {
        **{
            key: value
            for key, value in v2.items()
            if key
            not in {
                "schema_version",
                "milestone_branch",
                "workloads",
                "exercise_workloads",
            }
        },
        "schema_version": "phase-15-ramp-workload-v3",
        "governing_origin_main_sha": "1" * 40,
        "milestone_branch": "phase-15-atlas-253-ceiling-ramp",
        "milestone_head_sha": "1" * 40,
        "milestone_base_sha": "1" * 40,
        "workloads": workloads,
        "exercise_workloads": exercise_workloads,
        "throughput_numerator": [workload["atlas_key"] for workload in workloads],
        "explicit_exclusions": [
            {
                "atlas_key": workload["atlas_key"],
                "reason": "protected-lane-exercise",
            }
            for workload in exercise_workloads
        ],
        "manifest_fingerprint": "0" * 64,
    }
    manifest["manifest_fingerprint"] = calculate_v3_manifest_fingerprint(manifest)
    return manifest


def _v2_manifest() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(V2_MANIFEST.read_text(encoding="utf-8")))


def _historical_manifest() -> dict[str, Any]:
    return cast(
        dict[str, Any], json.loads(HISTORICAL_MANIFEST.read_text(encoding="utf-8"))
    )


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _identity(seed: int) -> str:
    return f"{seed:064x}"[-64:]


def _recompute_exercise_workload(workload: dict[str, Any]) -> None:
    classification = classify_protected_lane_inputs(
        ProtectedLaneClassifierInput(
            ticket_key=workload["ticket_key"],
            component=workload["component"],
            tags=tuple(workload["tags"]),
            relevant_docs=tuple(workload["relevant_docs"]),
            documentation_requirements=tuple(workload["documentation_requirements"]),
        ),
        DEFAULT_PROTECTED_LANE_REGISTRY,
    )
    workload["classification"] = json.loads(classification.canonical_bytes())
    workload["classification_fingerprint"] = classification.fingerprint


def _recompute_v3_exercise_workload(workload: dict[str, Any]) -> None:
    inputs = workload["classifier_inputs"]
    classification = classify_protected_lane_inputs(
        ProtectedLaneClassifierInput(
            ticket_key=inputs["atlas_key"],
            component=inputs["component"],
            tags=tuple(inputs["tags"]),
            relevant_docs=tuple(inputs["relevant_docs"]),
            documentation_requirements=tuple(inputs["documentation_requirements"]),
        ),
        DEFAULT_PROTECTED_LANE_REGISTRY,
    )
    workload["protected_lane_classification"] = json.loads(
        classification.canonical_bytes()
    )
    workload["reconstructed_protected_lanes"] = list(classification.lanes)
    workload["classifier_fingerprint"] = classification.fingerprint


def _use_live_runtime(receipt: dict[str, Any]) -> dict[str, Any]:
    runtime = cast(dict[str, Any], receipt["runtime_configuration"])
    runtime.update(
        {
            "service_unit": LIVE_RUNTIME_SERVICE_UNIT,
            "supported_procedure_id": LIVE_RUNTIME_PROCEDURE,
            "symphony_commit_sha": LIVE_SYMPHONY_COMMIT_SHA,
            "workflow_content_sha256": _identity(9000 + receipt["gate_level"]),
        }
    )
    identity = {key: value for key, value in runtime.items() if key != "proof_identity"}
    runtime["proof_identity"] = hashlib.sha256(
        json.dumps(
            identity, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        ).encode()
    ).hexdigest()
    return runtime


def _receipt(
    gate: int,
    index: int,
    *,
    manifest: dict[str, Any],
    outcome: str = "PASS",
    failed_invariant: str | None = None,
) -> dict[str, Any]:
    selected_manifest = validate_manifest(manifest)
    manifest_fingerprint = cast(str, selected_manifest["manifest_fingerprint"])
    uses_protected_exercises = selected_manifest["schema_version"] != (
        "phase-15-ramp-workload-v1"
    )
    started = datetime(2026, 8, 23, 1 + (index * 2), tzinfo=UTC)
    finished = started + timedelta(hours=1)
    publication_count = max(2, gate)
    working_occupancy = 8 if gate == 10 else gate
    acceptance_count = 3 if gate == 7 else 1
    observation = {
        "started_at": _timestamp(started),
        "finished_at": _timestamp(finished),
        "max_symphony_working_occupancy": working_occupancy,
        "max_atlas_working_occupancy": working_occupancy,
        "max_integration_occupancy": min(gate, 2),
        "max_review_occupancy": min(gate, 2),
        "max_changes_requested_occupancy": 1,
        "max_slot_release_seconds": 5,
        "max_reconciliation_latency_seconds": 240,
        "max_ci_queue_run_seconds": 720,
        "max_review_dwell_seconds": 1200,
        "publication_count": publication_count,
        "ci_pending_entries": publication_count,
        "ci_pending_exits": publication_count,
        "determinate_ci_pending_exits": publication_count,
        "system_tier_ci_exit_writes": publication_count,
        "max_ci_handoff_candidates_per_tick": 1,
        "max_reconciliation_ticks_per_determinate_exit": 1,
        "admission_count": publication_count,
        "max_external_admissions_per_pm_window": 1,
        "hold_count": 3,
        "typed_hold_reason_count": 3,
        "untyped_hold_reason_count": 0,
        "rank_reproduction_count": publication_count,
        "unselected_admission_count": 0,
        "changes_requested_dispatch_count": 1,
        "changes_requested_starved_count": 0,
        "protected_lane_hold_count": 1,
        "protected_lane_collision_count": 0,
        "independent_parallel_count": 1 if gate == 1 else 2,
        "agent_ci_poll_count": 0,
        "repeated_unchanged_head_publication_count": 0,
        "repeated_full_validation_wait_count": 0,
        "indeterminate_ci_hold_count": 1,
        "poll_compressed_edge_count": 1,
        "invented_transition_count": 0,
        "unexpected_ci_pending_reactivation_count": 0,
        "review_saturation_hold_count": 1,
        "integration_saturation_hold_count": 1 if gate >= 5 else 0,
        "acceptance_arrival_count": acceptance_count,
        "exact_head_acceptance_completion_count": acceptance_count,
        "one_pr_freeze_breach_count": 0,
        "stale_review_head_count": 1,
        "mechanical_rebase_count": 1,
        "semantic_conflict_count": 0,
        "ambiguous_write_incident_count": 1,
        "unresolved_write_fence_count": 0,
        "conflicting_external_write_count": 0,
        "prohibited_authority_call_count": 0,
        "repository_mutation_count": 0,
        "external_mutation_count": 0,
        "secret_retention_count": 0,
    }
    invariant_evidence = {
        name: {
            "passed": name != failed_invariant,
            "evidence_identity": _identity(1000 + index * 100 + offset),
        }
        for offset, name in enumerate(COMMON_INVARIANTS)
    }
    exercise_evidence = {
        name: {
            "passed": True,
            "evidence_identity": _identity(2000 + index * 100 + offset),
        }
        for offset, name in enumerate(GATE_EXERCISES[gate])
    }
    protected_lane_exercise_evidence: list[dict[str, Any]] = []
    if uses_protected_exercises:
        workloads = {
            workload["exercise_workload_id"]: workload
            for workload in cast(
                list[dict[str, Any]], selected_manifest["exercise_workloads"]
            )
        }
        bindings = [
            binding
            for binding in cast(
                list[dict[str, Any]], selected_manifest["exercise_bindings"]
            )
            if binding["gate_level"] == gate
        ]
        for binding_offset, binding in enumerate(bindings):
            workload = workloads[binding["exercise_workload_id"]]
            classification = cast(
                dict[str, Any],
                workload.get(
                    "protected_lane_classification", workload.get("classification")
                ),
            )
            lane = classification["matches"][0]["lane"]
            protected_lane_exercise_evidence.append(
                {
                    "gate_level": gate,
                    "exercise_id": binding["exercise_id"],
                    "exercise_workload_id": binding["exercise_workload_id"],
                    "ticket_key": workload.get("atlas_key", workload.get("ticket_key")),
                    "role": binding["role"],
                    "protected_lane": lane,
                    "observed_status": (
                        "CI Pending"
                        if binding["role"] == "owner"
                        else "Ready for Agent"
                    ),
                    "evidence_identity": _identity(8000 + index * 100 + binding_offset),
                    "workload_manifest_fingerprint": manifest_fingerprint,
                }
            )
        protected_lane_exercise_evidence.sort(
            key=lambda evidence: (
                evidence["exercise_id"],
                evidence["role"],
                evidence["exercise_workload_id"],
            )
        )
        for exercise_id in sorted(
            {evidence["exercise_id"] for evidence in protected_lane_exercise_evidence}
        ):
            bound_evidence = [
                evidence
                for evidence in protected_lane_exercise_evidence
                if evidence["exercise_id"] == exercise_id
            ]
            exercise_evidence[exercise_id]["evidence_identity"] = hashlib.sha256(
                json.dumps(
                    bound_evidence,
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode()
            ).hexdigest()
    if uses_protected_exercises and protected_lane_exercise_evidence:
        lane_occupancy = [
            {
                "lane": evidence["protected_lane"],
                "count": 1,
                "limit": 1,
                "ticket_keys": [evidence["ticket_key"]],
                "operator_declared": (
                    evidence["protected_lane"] == "operator-admission-hotspot"
                ),
            }
            for evidence in protected_lane_exercise_evidence
            if evidence["role"] == "owner"
        ]
    elif uses_protected_exercises:
        lane_occupancy = [
            {
                "lane": "operator-admission-hotspot",
                "count": 1,
                "limit": 1,
                "ticket_keys": ["ATLAS-250"],
                "operator_declared": True,
            }
        ]
    else:
        lane_occupancy = [
            {
                "lane": "workflow-configuration",
                "count": 1,
                "limit": 1,
                "ticket_keys": [f"META-GATE-{gate}"],
                "operator_declared": False,
            }
        ]
    level_token = f"{gate:x}"
    origin_main = (
        cast(str, selected_manifest["governing_origin_main_sha"])
        if index == 0
        and selected_manifest["schema_version"] == "phase-15-ramp-workload-v3"
        else f"{index + 1:x}" * 40
    )
    milestone_commit = (
        cast(str, selected_manifest["milestone_head_sha"])
        if index == 0
        and selected_manifest["schema_version"] == "phase-15-ramp-workload-v3"
        else level_token * 40
    )
    receipt: dict[str, Any] = {
        "schema_version": (
            "phase-15-ramp-gate-receipt-v2"
            if uses_protected_exercises
            else "phase-15-ramp-gate-receipt-v1"
        ),
        "receipt_id": f"seed-gate-{gate}",
        "gate_level": gate,
        "outcome": outcome,
        "workload_manifest_fingerprint": manifest_fingerprint,
        "previous_gate_receipt_id": None
        if index == 0
        else f"seed-gate-{GATE_LEVELS[index - 1]}",
        "previous_proven_level": 1 if index == 0 else GATE_LEVELS[index - 1],
        "retained_or_restored_level": (
            gate if outcome == "PASS" else (1 if index == 0 else GATE_LEVELS[index - 1])
        ),
        "branch": "phase-15-atlas-253-ceiling-ramp",
        "milestone_commit_sha": milestone_commit,
        "origin_main_sha": origin_main,
        "merge_base_sha": origin_main,
        "workflow_blob_sha": f"{index + 10:x}" * 40,
        "main_configuration": {
            "commit_sha": origin_main,
            "max_concurrent_agents": 1,
            "max_turns": 10,
        },
        "runtime_configuration": {
            "instance_id": "seeded-symphony-vps",
            "supported_procedure_id": FIXTURE_RUNTIME_PROCEDURE,
            "loaded_commit_sha": milestone_commit,
            "workflow_blob_sha": f"{index + 10:x}" * 40,
            "configured_ceiling": gate,
            "max_turns": 10,
            "loaded_at": _timestamp(started - timedelta(minutes=2)),
            "proof_observed_at": _timestamp(started - timedelta(minutes=1)),
            "proof_identity": _identity(3000 + index),
        },
        "policy": {
            "policy_id": f"seed-policy-{gate}",
            "revision": index + 10,
            "fingerprint": _identity(4000 + index),
            "approved_symphony_ceiling": gate,
            "working_budget": gate,
            "integration_budget": max(gate, 2),
            "review_budget": max(gate, 2),
            "changes_requested_reserve": 1,
            "risk_lane_limits": [{"risk_level": "critical", "limit": 1}],
            "component_lane_limits": [{"component": "orchestration", "limit": gate}],
            "mode": "running",
        },
        "snapshot": {
            "snapshot_id": f"seed-snapshot-{gate}",
            "snapshot_fingerprint": _identity(6000 + index),
            "board_fingerprint": _identity(7000 + index),
            "complete": True,
            "fresh": True,
            "continuous": True,
            "contradictory": False,
            "unresolved_write_fence": False,
            "critical_fault": False,
            "protected_lane_registry_version": "protected-integration-lanes/v1",
            "protected_lane_registry_fingerprint": (
                selected_manifest["protected_lane_registry_fingerprint"]
                if uses_protected_exercises
                else _identity(5000 + index)
            ),
            "protected_lane_state_fingerprint": _identity(5100 + index),
            "protected_lane_occupancy": lane_occupancy,
            "pm_sync_receipt_ids": [f"pm-sync-{gate}"],
            "admission_run_ids": [f"admission-{gate}"],
            "ci_handoff_reconciliation_ids": [f"ci-handoff-{gate}"],
        },
        "observation": observation,
        "common_invariants": invariant_evidence,
        "gate_exercises": exercise_evidence,
        "stop_reasons": [] if outcome == "PASS" else ["invariant_failed"],
        "operator_identity": "human/operator:seeded-validator",
        "recorded_at": _timestamp(finished + timedelta(minutes=1)),
    }
    if uses_protected_exercises:
        receipt["protected_lane_exercise_evidence"] = protected_lane_exercise_evidence
    return receipt


def _receipts(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        _receipt(gate, index, manifest=manifest)
        for index, gate in enumerate(GATE_LEVELS)
    ]


def test_predeclared_manifest_has_more_than_ten_independent_workloads() -> None:
    selected = validate_manifest(_manifest())
    workloads = cast(list[dict[str, Any]], selected["workloads"])
    exercise_workloads = cast(list[dict[str, Any]], selected["exercise_workloads"])

    assert selected["validation_scope"] == "offline-read-only"
    assert len(workloads) == 11
    assert all(workload["independent"] is True for workload in workloads)
    assert all(workload["dependency_identities"] == [] for workload in workloads)
    assert (
        len({workload["native_workload_fingerprint"] for workload in workloads}) == 11
    )
    assert (
        len(
            {
                path
                for workload in workloads
                for name in ("production_paths", "test_paths")
                for path in cast(list[str], workload[name])
            }
        )
        == 22
    )
    ordinary_lanes = [
        lane
        for workload in workloads
        for lane in cast(list[str], workload["protected_lane_classification"])
    ]
    assert len(ordinary_lanes) == len(set(ordinary_lanes))
    assert selected["protected_lane_registry_version"] == (
        DEFAULT_PROTECTED_LANE_REGISTRY.version
    )
    assert selected["protected_lane_registry_fingerprint"] == (
        DEFAULT_PROTECTED_LANE_REGISTRY.fingerprint
    )
    assert len(exercise_workloads) == 6
    assert all(
        workload["excluded_from_throughput"] is True for workload in exercise_workloads
    )
    assert {workload["atlas_key"] for workload in exercise_workloads} == {
        "ATLAS-250",
        "ATLAS-251",
        "ATLAS-252",
        "ATLAS-8",
        "ATLAS-14",
        "ATLAS-234",
    }
    assert selected["phase_15_5_release"] == {
        "issue": "ATL-437",
        "state": "Done",
        "pr_number": 335,
        "merged": True,
        "contributor_head": "a598798c1a6c5cabe4c80c0f04020c271f438de1",
        "controlled_comparison": "PASS",
        "production_reconciliation_retained": True,
        "synthetic_no_rewrite_route": "retired",
        "linear_pr_opened_automation_disabled": True,
    }


def test_no_receipt_stops_at_gate_one_without_claiming_failure() -> None:
    report, passed = evaluate_ramp(_manifest(), [])

    assert passed is False
    assert report["decision"] == "PENDING_GATE_1"
    assert report["closure_authorized"] is False
    assert report["last_validated_pass_receipt_level"] is None


def test_complete_sequence_is_validation_only_and_never_live_authority() -> None:
    manifest = _manifest()
    report, passed = evaluate_ramp(manifest, _receipts(manifest))
    retained = json.dumps(report, separators=(",", ":"), sort_keys=True)

    assert passed is True
    assert report["decision"] == "RECEIPT_SEQUENCE_VALIDATED"
    assert report["closure_authorized"] is False
    assert report["transition_authorized"] is False
    assert report["manifest_authority"] == "live-freeze-authoritative-v3"
    assert report["live_freeze_authoritative"] is True
    assert report["gate_sequence"] == [1, 3, 5, 7, 10]
    assert report["last_validated_pass_receipt_level"] == 10
    assert len(retained.encode()) <= MAX_REPORT_BYTES


def test_v2_manifest_and_receipt_fingerprints_ignore_order() -> None:
    manifest = _v2_manifest()
    baseline = validate_manifest(manifest)
    reordered = copy.deepcopy(manifest)
    reordered["workloads"].reverse()
    reordered["exercise_catalog"].reverse()
    reordered["exercise_workloads"].reverse()
    reordered["exercise_bindings"].reverse()
    for workload in reordered["exercise_workloads"]:
        workload["tags"].reverse()
        workload["relevant_docs"].reverse()
        workload["documentation_requirements"].reverse()
        workload["touched_paths"].reverse()

    selected = validate_manifest(reordered)

    assert selected["manifest_fingerprint"] == baseline["manifest_fingerprint"]
    assert [
        workload["classification_fingerprint"]
        for workload in cast(list[dict[str, Any]], selected["exercise_workloads"])
    ] == [
        workload["classification_fingerprint"]
        for workload in cast(list[dict[str, Any]], baseline["exercise_workloads"])
    ]

    receipts = _receipts(manifest)[:2]
    receipts[1]["policy"]["risk_lane_limits"] = [
        {"risk_level": "critical", "limit": 1},
        {"risk_level": "high", "limit": 0},
    ]
    receipts[1]["policy"]["component_lane_limits"] = [
        {"component": "orchestration", "limit": 3},
        {"component": "atlas.api", "limit": 0},
    ]
    first_report, first_passed = evaluate_ramp(manifest, receipts)
    reordered_receipts = copy.deepcopy(receipts)
    reordered_receipts[1]["protected_lane_exercise_evidence"].reverse()
    reordered_receipts[1]["gate_exercises"] = dict(
        reversed(list(reordered_receipts[1]["gate_exercises"].items()))
    )
    reordered_receipts[1]["common_invariants"] = dict(
        reversed(list(reordered_receipts[1]["common_invariants"].items()))
    )
    reordered_receipts[1]["policy"]["risk_lane_limits"].reverse()
    reordered_receipts[1]["policy"]["component_lane_limits"].reverse()
    second_report, second_passed = evaluate_ramp(manifest, reordered_receipts)

    assert first_passed is second_passed is False
    first_summaries = cast(list[dict[str, Any]], first_report["receipt_summaries"])
    second_summaries = cast(list[dict[str, Any]], second_report["receipt_summaries"])
    assert (
        first_summaries[-1]["receipt_fingerprint"]
        == second_summaries[-1]["receipt_fingerprint"]
    )


def test_v1_attempt_records_replay_deterministically_as_historical_only() -> None:
    manifest = _historical_manifest()
    receipts = _receipts(manifest)

    for prefix, expected in ((1, "PENDING_GATE_3"), (2, "PENDING_GATE_5")):
        first, first_passed = evaluate_ramp(manifest, receipts[:prefix])
        second, second_passed = evaluate_ramp(manifest, receipts[:prefix])

        assert first == second
        assert first_passed is second_passed is False
        assert first["decision"] == "HISTORICAL_RECEIPT_REPLAY"
        assert first["historical_only"] is True
        assert first["historical_result"] == expected
        assert first["transition_authorized"] is False
        assert first["closure_authorized"] is False

    complete, passed = evaluate_ramp(manifest, receipts)
    assert passed is False
    assert complete["decision"] == "HISTORICAL_RECEIPT_REPLAY"
    assert complete["historical_result"] == "RECEIPT_SEQUENCE_VALIDATED"


def test_v2_remains_deterministic_schema_valid_history_not_live_freeze_authority() -> (
    None
):
    manifest = _v2_manifest()
    first, first_passed = evaluate_ramp(manifest, _receipts(manifest))
    second, second_passed = evaluate_ramp(manifest, _receipts(manifest))

    assert first == second
    assert first_passed is second_passed is False
    assert first["decision"] == "HISTORICAL_SCHEMA_VALIDATION"
    assert first["historical_result"] == "RECEIPT_SEQUENCE_VALIDATED"
    assert first["manifest_authority"] == "historical-schema-valid-v2"
    assert first["live_freeze_authoritative"] is False
    assert first["transition_authorized"] is False
    assert first["closure_authorized"] is False


def test_v3_canonical_fingerprints_ignore_semantically_unordered_collections() -> None:
    manifest = _manifest()
    baseline = validate_manifest(manifest)
    reordered = copy.deepcopy(manifest)
    reordered["workloads"].reverse()
    reordered["exercise_catalog"].reverse()
    reordered["exercise_workloads"].reverse()
    reordered["exercise_bindings"].reverse()
    reordered["throughput_numerator"].reverse()
    reordered["explicit_exclusions"].reverse()
    for workload in reordered["workloads"]:
        workload["production_paths"].reverse()
        workload["test_paths"].reverse()
        workload["protected_lane_classification"].reverse()
    for workload in reordered["exercise_workloads"]:
        workload["production_paths"].reverse()
        workload["test_paths"].reverse()
        workload["classifier_inputs"]["tags"].reverse()
        workload["classifier_inputs"]["relevant_docs"].reverse()
        workload["classifier_inputs"]["documentation_requirements"].reverse()
        workload["reconstructed_protected_lanes"].reverse()
    reordered["manifest_fingerprint"] = calculate_v3_manifest_fingerprint(reordered)

    selected = validate_manifest(reordered)

    assert selected["manifest_fingerprint"] == baseline["manifest_fingerprint"]
    assert [
        workload["native_workload_fingerprint"]
        for workload in cast(list[dict[str, Any]], selected["workloads"])
    ] == [
        workload["native_workload_fingerprint"]
        for workload in cast(list[dict[str, Any]], baseline["workloads"])
    ]


@pytest.mark.parametrize(
    ("case", "message"),
    (
        ("wrong_schema", "unsupported"),
        ("malformed_sha", "40-character digest"),
        ("branch", "dedicated branch"),
        ("base_contradiction", "contradicts governing"),
        ("duplicate_atlas", "duplicate Atlas or Linear"),
        ("duplicate_linear_identifier", "duplicate Atlas or Linear"),
        ("duplicate_linear_uuid", "duplicate Atlas or Linear"),
        ("missing_linear_identifier", "exact required fields"),
        ("missing_linear_uuid", "exact required fields"),
        ("native_fingerprint", "native workload fingerprint drifted"),
        ("dependency_contradiction", "dependency-independent declaration"),
        ("dependency_identity_mismatch", "contradict workload authority"),
        ("independence_contradiction", "required workload independence"),
        ("classifier_reconstruction", "classifier reconstruction mismatch"),
        ("protected_lane_fingerprint", "registry fingerprint drifted"),
        ("ordinary_role", "ordinary workload role"),
        ("ordinary_earliest_gate", "ordinary role requires"),
        ("exercise_role", "six required roles"),
        ("earliest_gate", "earliest gate contradicts"),
        ("throughput_numerator", "excluded exercise or omission"),
        ("wrong_role_lane", "wrong protected lane"),
        ("missing_role", "six required roles"),
        ("same_ticket_roles", "duplicate Atlas or Linear"),
        ("manifest_fingerprint", "canonical fingerprint drifted"),
        ("unknown_field", "exact required fields"),
    ),
)
def test_v3_authority_manifest_fail_closed(case: str, message: str) -> None:
    manifest = _manifest()
    workloads = manifest["workloads"]
    exercises = manifest["exercise_workloads"]
    if case == "wrong_schema":
        manifest["schema_version"] = "phase-15-ramp-workload-v4"
    elif case == "malformed_sha":
        manifest["milestone_head_sha"] = "f" * 39
    elif case == "branch":
        manifest["milestone_branch"] = "phase-15-wrong-branch"
    elif case == "base_contradiction":
        manifest["milestone_base_sha"] = "2" * 40
    elif case == "duplicate_atlas":
        workloads[1]["atlas_key"] = workloads[0]["atlas_key"]
    elif case == "duplicate_linear_identifier":
        workloads[1]["linear_identifier"] = workloads[0]["linear_identifier"]
    elif case == "duplicate_linear_uuid":
        workloads[1]["linear_uuid"] = workloads[0]["linear_uuid"]
    elif case == "missing_linear_identifier":
        workloads[0].pop("linear_identifier")
    elif case == "missing_linear_uuid":
        workloads[0].pop("linear_uuid")
    elif case == "native_fingerprint":
        workloads[0]["native_workload_fingerprint"] = "f" * 64
    elif case == "dependency_contradiction":
        workloads[0]["dependency_identities"] = [
            {
                "atlas_key": "ATLAS-999",
                "linear_identifier": "ATL-999",
                "linear_uuid": "00000000-0000-4000-8000-000000000999",
            }
        ]
    elif case == "dependency_identity_mismatch":
        exercises[0]["dependency_identities"] = [
            {
                "atlas_key": workloads[0]["atlas_key"],
                "linear_identifier": "ATL-999",
                "linear_uuid": "00000000-0000-4000-8000-000000000999",
            }
        ]
    elif case == "independence_contradiction":
        workloads[0]["independent"] = False
    elif case == "classifier_reconstruction":
        exercises[0]["protected_lane_classification"]["matches"][0]["lane"] = (
            "database-migrations"
        )
    elif case == "protected_lane_fingerprint":
        manifest["protected_lane_registry_fingerprint"] = "f" * 64
    elif case == "ordinary_role":
        workloads[0]["workload_role"] = "exercise"
    elif case == "ordinary_earliest_gate":
        workloads[0]["earliest_permitted_gate"] = 3
    elif case == "exercise_role":
        exercises[0]["exercise_role"] = "ordinary"
    elif case == "earliest_gate":
        exercises[0]["earliest_permitted_gate"] = 10
    elif case == "throughput_numerator":
        manifest["throughput_numerator"][0] = exercises[0]["atlas_key"]
    elif case == "wrong_role_lane":
        exercises[0]["classifier_inputs"].update(
            {
                "component": "atlas.storage",
                "tags": ["migration"],
                "relevant_docs": [],
                "documentation_requirements": [],
            }
        )
        _recompute_v3_exercise_workload(exercises[0])
    elif case == "missing_role":
        exercises.pop()
    elif case == "same_ticket_roles":
        exercises[1]["atlas_key"] = exercises[0]["atlas_key"]
    elif case == "manifest_fingerprint":
        manifest["manifest_fingerprint"] = "f" * 64
    elif case == "unknown_field":
        manifest["repository_lookup"] = "forbidden"
    else:  # pragma: no cover - parametrisation is closed above
        raise AssertionError(case)

    with pytest.raises(ValueError, match=message):
        validate_manifest(manifest)


def test_v3_gate_one_receipt_must_match_frozen_repository_identity() -> None:
    manifest = _manifest()
    receipt = _receipts(manifest)[0]
    receipt["milestone_commit_sha"] = "f" * 40
    receipt["runtime_configuration"]["loaded_commit_sha"] = "f" * 40

    with pytest.raises(ValueError, match="repository-authoritative v3 freeze"):
        evaluate_ramp(manifest, [receipt])


def test_self_declared_operator_authority_is_rejected() -> None:
    manifest = _manifest()
    manifest["authority"] = "live-operator"
    with pytest.raises(ValueError, match="exact required fields"):
        evaluate_ramp(manifest, [])

    manifest = _manifest()
    manifest["validation_scope"] = "live-operator"
    with pytest.raises(ValueError, match="offline read-only validation"):
        evaluate_ramp(manifest, [])


@pytest.mark.parametrize(
    ("case", "message"),
    (
        ("unknown_lane", "unknown protected lane"),
        ("registry_version", "registry version drifted"),
        ("registry_fingerprint", "registry fingerprint drifted"),
        ("classifier_drift", "disagrees with recomputation"),
        ("declared_disagreement", "disagrees with recomputation"),
        ("classification_fingerprint", "classification fingerprint drifted"),
        ("duplicate_workload", "duplicate identities"),
        ("duplicate_ticket", "duplicate ticket identities"),
        ("missing_identity", "exact required fields"),
        ("orphan_workload", "orphaned identities"),
        ("unbound_same_lane", "explicit Gate 3 co-binding"),
        ("gate3_different_lane", "distinct same-lane owner"),
    ),
)
def test_v2_manifest_registry_classification_and_identity_drift_fail_closed(
    case: str, message: str
) -> None:
    manifest = _v2_manifest()
    exercise_workloads = manifest["exercise_workloads"]
    if case == "unknown_lane":
        manifest["workloads"][0]["protected_lanes"] = ["invented-lane"]
    elif case == "registry_version":
        manifest["protected_lane_registry_version"] = "protected-lanes/future"
    elif case == "registry_fingerprint":
        manifest["protected_lane_registry_fingerprint"] = "f" * 64
    elif case == "classifier_drift":
        exercise_workloads[0]["tags"].append("workflow")
    elif case == "declared_disagreement":
        exercise_workloads[0]["classification"]["matches"][0]["lane"] = (
            "database-migrations"
        )
    elif case == "classification_fingerprint":
        exercise_workloads[0]["classification_fingerprint"] = "f" * 64
    elif case == "duplicate_workload":
        exercise_workloads[1]["exercise_workload_id"] = exercise_workloads[0][
            "exercise_workload_id"
        ]
    elif case == "duplicate_ticket":
        exercise_workloads[1]["ticket_key"] = exercise_workloads[0]["ticket_key"]
    elif case == "missing_identity":
        exercise_workloads[0].pop("ticket_key")
    elif case == "orphan_workload":
        orphan = copy.deepcopy(exercise_workloads[0])
        orphan.update(
            {
                "exercise_workload_id": "RAMP-EX-ORPHAN",
                "ticket_key": "ATLAS-258",
                "touched_path_family": "protected-lane-classifier",
                "touched_paths": ["atlas/pm/protected_lanes.py"],
            }
        )
        _recompute_exercise_workload(orphan)
        exercise_workloads.append(orphan)
    elif case == "unbound_same_lane":
        exercise_workloads[0].update(
            {
                "component": "orchestration",
                "tags": ["admission"],
                "relevant_docs": ["docs/atlas/symphony-integration.md"],
                "documentation_requirements": ["docs/atlas/symphony-integration.md"],
            }
        )
        _recompute_exercise_workload(exercise_workloads[0])
    elif case == "gate3_different_lane":
        exercise_workloads[2].update(
            {
                "component": "operator-ui",
                "tags": ["workflow"],
                "relevant_docs": ["docs/atlas/operator-ui.md"],
                "documentation_requirements": ["docs/atlas/operator-ui.md"],
            }
        )
        _recompute_exercise_workload(exercise_workloads[2])
    else:  # pragma: no cover - parametrisation is closed above
        raise AssertionError(case)

    with pytest.raises(ValueError, match=message):
        validate_manifest(manifest)


def test_gate_order_parent_link_and_per_gate_main_identity_are_fail_closed() -> None:
    manifest = _manifest()
    receipts = _receipts(manifest)
    receipts[1]["gate_level"] = 5
    with pytest.raises(ValueError, match="exact"):
        evaluate_ramp(manifest, receipts)

    receipts = _receipts(manifest)
    receipts[1]["previous_gate_receipt_id"] = "wrong"
    with pytest.raises(ValueError, match="prior receipt"):
        evaluate_ramp(manifest, receipts)

    receipts = _receipts(manifest)
    assert len({receipt["origin_main_sha"] for receipt in receipts}) == 5
    report, passed = evaluate_ramp(manifest, receipts)
    assert passed is True
    assert report["decision"] == "RECEIPT_SEQUENCE_VALIDATED"

    receipts = _receipts(manifest)
    receipts[2]["merge_base_sha"] = "f" * 40
    with pytest.raises(ValueError, match="not fresh against origin/main"):
        evaluate_ramp(manifest, receipts)


def test_policy_validation_matches_the_delivered_domain() -> None:
    manifest = _manifest()
    receipts = _receipts(manifest)
    receipts[0]["policy"]["changes_requested_reserve"] = 0
    receipts[0]["policy"]["risk_lane_limits"] = []
    receipts[0]["policy"]["component_lane_limits"] = []
    report, passed = evaluate_ramp(manifest, receipts)
    assert passed is True
    first = cast(list[dict[str, Any]], report["receipt_summaries"])[0]
    assert first["gate_level"] == 1

    receipts = _receipts(manifest)
    receipts[1]["policy"]["risk_lane_limits"] = [{"risk_level": "critical", "limit": 0}]
    receipts[1]["policy"]["component_lane_limits"] = [
        {"component": "orchestration", "limit": 0}
    ]
    assert evaluate_ramp(manifest, receipts)[1] is True

    receipts = _receipts(manifest)
    receipts[1]["policy"]["component_lane_limits"] = [
        {"component": "orchestration", "limit": 4}
    ]
    with pytest.raises(ValueError, match="lane limit exceeds working_budget"):
        evaluate_ramp(manifest, receipts)


def test_protected_lane_limits_and_state_are_snapshot_owned() -> None:
    manifest = _manifest()
    receipts = _receipts(manifest)
    receipts[0]["policy"]["protected_lane_budgets"] = {"workflow-configuration": 1}
    with pytest.raises(ValueError, match="exact required fields"):
        evaluate_ramp(manifest, receipts)

    receipts = _receipts(manifest)
    receipts[0]["snapshot"]["protected_lane_occupancy"][0]["count"] = 2
    with pytest.raises(ValueError, match="occupancy count is incoherent"):
        evaluate_ramp(manifest, receipts)

    receipts = _receipts(manifest)
    receipts[0]["snapshot"]["protected_lane_registry_version"] = "unversioned"
    with pytest.raises(ValueError, match="registry version is unsupported"):
        evaluate_ramp(manifest, receipts)

    receipts = _receipts(manifest)
    receipts[0]["snapshot"]["protected_lane_registry_fingerprint"] = "f" * 64
    with pytest.raises(ValueError, match="fingerprint drifted from manifest"):
        evaluate_ramp(manifest, receipts)


@pytest.mark.parametrize(
    ("case", "message"),
    (
        ("wrong_gate", "wrong gate, exercise or role"),
        ("wrong_exercise", "wrong gate, exercise or role"),
        ("wrong_role", "wrong gate, exercise or role"),
        ("wrong_status", "incoherent occupancy status"),
        ("wrong_lane", "disagrees with classification"),
        ("stale_manifest", "stale manifest fingerprint"),
        ("undeclared_workload", "undeclared workload"),
        ("substituted_ticket", "substitutes a ticket identity"),
        ("meta_gate_ticket", "substitutes a ticket identity"),
        ("duplicate_workload", "duplicates a workload"),
        ("duplicate_ticket", "duplicates a ticket identity"),
        ("duplicate_evidence", "duplicates an evidence identity"),
        ("aggregate_evidence", "evidence identity is unbound"),
        ("unknown_occupancy_lane", "uses an unknown lane"),
        ("occupancy_mismatch", "owner mismatches lane occupancy"),
        ("hold_without_evidence", "lacks exact workload-bound"),
    ),
)
def test_v2_receipt_protected_exercise_bindings_fail_closed(
    case: str, message: str
) -> None:
    manifest = _v2_manifest()
    receipts = _receipts(manifest)
    receipt = receipts[1] if case.startswith("duplicate_") else receipts[0]
    evidence = receipt["protected_lane_exercise_evidence"]
    if case == "wrong_gate":
        evidence[0]["gate_level"] = 3
    elif case == "wrong_exercise":
        evidence[0]["exercise_id"] = "review_saturation_hold"
    elif case == "wrong_role":
        evidence[0]["role"] = "blocked_candidate"
    elif case == "wrong_status":
        evidence[0]["observed_status"] = "In Progress"
    elif case == "wrong_lane":
        evidence[0]["protected_lane"] = "database-migrations"
    elif case == "stale_manifest":
        evidence[0]["workload_manifest_fingerprint"] = "f" * 64
    elif case == "undeclared_workload":
        evidence[0]["exercise_workload_id"] = "RAMP-EX-UNDECLARED"
    elif case == "substituted_ticket":
        evidence[0]["ticket_key"] = "ATLAS-258"
    elif case == "meta_gate_ticket":
        evidence[0]["ticket_key"] = "META-GATE-1"
    elif case == "duplicate_workload":
        evidence[1]["exercise_workload_id"] = evidence[0]["exercise_workload_id"]
    elif case == "duplicate_ticket":
        evidence[1]["ticket_key"] = evidence[0]["ticket_key"]
    elif case == "duplicate_evidence":
        evidence[1]["evidence_identity"] = evidence[0]["evidence_identity"]
    elif case == "aggregate_evidence":
        receipt["gate_exercises"]["protected_lane_ci_pending_hold"][
            "evidence_identity"
        ] = "f" * 64
    elif case == "unknown_occupancy_lane":
        receipt["snapshot"]["protected_lane_occupancy"][0]["lane"] = "invented-lane"
    elif case == "occupancy_mismatch":
        receipt["snapshot"]["protected_lane_occupancy"][0]["ticket_keys"] = [
            "ATLAS-250"
        ]
    elif case == "hold_without_evidence":
        assert receipt["observation"]["protected_lane_hold_count"] > 0
        receipt["protected_lane_exercise_evidence"] = []
    else:  # pragma: no cover - parametrisation is closed above
        raise AssertionError(case)

    with pytest.raises(ValueError, match=message):
        evaluate_ramp(manifest, receipts)


@pytest.mark.parametrize(
    ("gate", "exercise_id"),
    (
        (5, "protected_lane_with_unrelated_parallelism"),
        (7, "risk_component_protected_lanes_under_load"),
        (7, "ci_pending_lane_ownership"),
    ),
)
def test_later_protected_exercises_reject_generic_evidence_without_bound_workload(
    gate: int, exercise_id: str
) -> None:
    manifest = _manifest()
    receipts = _receipts(manifest)
    receipt = receipts[GATE_LEVELS.index(gate)]
    assert receipt["gate_exercises"][exercise_id]["passed"] is True
    receipt["protected_lane_exercise_evidence"] = [
        evidence
        for evidence in receipt["protected_lane_exercise_evidence"]
        if evidence["exercise_id"] != exercise_id
    ]

    with pytest.raises(ValueError, match="lacks exact workload-bound"):
        evaluate_ramp(manifest, receipts)


def test_post_ratification_workload_substitution_changes_and_invalidates_identity() -> (
    None
):
    manifest = _v2_manifest()
    receipts = _receipts(manifest)
    original = validate_manifest(manifest)["manifest_fingerprint"]
    substituted = copy.deepcopy(manifest)
    substituted["exercise_workloads"][0]["touched_paths"] = [
        "tests/test_workflow_contract.py"
    ]

    changed = validate_manifest(substituted)["manifest_fingerprint"]

    assert changed != original
    with pytest.raises(ValueError, match="workload manifest identity mismatched"):
        evaluate_ramp(substituted, receipts)


@pytest.mark.parametrize(
    "field",
    (
        "exercise_workload_id",
        "ticket_key",
        "touched_path_family",
        "touched_paths",
        "component",
        "tags",
        "relevant_docs",
        "documentation_requirements",
    ),
)
def test_every_exercise_workload_identity_input_participates_in_manifest_fingerprint(
    field: str,
) -> None:
    baseline_manifest = _v2_manifest()
    baseline = validate_manifest(baseline_manifest)["manifest_fingerprint"]
    changed_manifest = copy.deepcopy(baseline_manifest)
    workload = changed_manifest["exercise_workloads"][0]
    if field == "exercise_workload_id":
        workload[field] = "RAMP-EX-G1-OWNER-VARIANT"
        changed_manifest["exercise_bindings"][0][field] = workload[field]
    elif field == "ticket_key":
        workload[field] = "ATLAS-258"
        _recompute_exercise_workload(workload)
    elif field == "touched_path_family":
        workload[field] = "workflow-contract-variant"
    elif field == "touched_paths":
        workload[field] = ["tests/test_workflow_contract.py"]
    elif field == "component":
        workload[field] = "orchestration-variant"
        _recompute_exercise_workload(workload)
    elif field == "tags":
        workload[field].append("fixture-evidence")
        _recompute_exercise_workload(workload)
    elif field == "relevant_docs":
        workload[field].append("docs/atlas/review-acceptance-console.md")
        _recompute_exercise_workload(workload)
    elif field == "documentation_requirements":
        workload[field].append("docs/runbooks/pr-acceptance.md")
        _recompute_exercise_workload(workload)
    else:  # pragma: no cover - parametrisation is closed above
        raise AssertionError(field)

    changed = validate_manifest(changed_manifest)["manifest_fingerprint"]

    assert changed != baseline


def test_capacity_ci_owner_and_reactivation_breaches_cannot_pass() -> None:
    manifest = _manifest()
    for field, value in (
        ("max_symphony_working_occupancy", 4),
        ("system_tier_ci_exit_writes", 0),
        ("unexpected_ci_pending_reactivation_count", 1),
        ("max_external_admissions_per_pm_window", 2),
        ("max_ci_handoff_candidates_per_tick", 2),
        ("max_reconciliation_ticks_per_determinate_exit", 2),
    ):
        receipts = _receipts(manifest)
        receipts[1]["observation"][field] = value
        with pytest.raises(ValueError, match="cannot PASS"):
            evaluate_ramp(manifest, receipts)


def test_failed_gate_is_retained_honestly_and_stops_progression() -> None:
    manifest = _manifest()
    gate_one = _receipt(1, 0, manifest=manifest)
    gate_three = _receipt(
        3,
        1,
        manifest=manifest,
        outcome="FAIL",
        failed_invariant="review_budget_bound",
    )
    report, passed = evaluate_ramp(manifest, [gate_one, gate_three])

    assert passed is False
    assert report["decision"] == "FAIL_GATE_3"
    assert report["last_validated_pass_receipt_level"] == 1
    summaries = cast(list[dict[str, object]], report["receipt_summaries"])
    assert summaries[-1]["retained_or_restored_level"] == 1

    gate_five = _receipt(5, 2, manifest=manifest)
    with pytest.raises(ValueError, match="after a failed gate"):
        evaluate_ramp(manifest, [gate_one, gate_three, gate_five])


def test_runtime_must_prove_exact_branch_configuration_before_admission() -> None:
    manifest = _manifest()
    receipts = _receipts(manifest)
    receipts[0]["runtime_configuration"]["loaded_commit_sha"] = "f" * 40

    with pytest.raises(ValueError, match="running Symphony identity"):
        evaluate_ramp(manifest, receipts)

    receipts = _receipts(manifest)
    receipts[0]["runtime_configuration"]["proof_observed_at"] = receipts[0][
        "observation"
    ]["finished_at"]
    with pytest.raises(ValueError, match="followed workload admission"):
        evaluate_ramp(manifest, receipts)

    receipts = _receipts(manifest)
    receipts[0]["runtime_configuration"]["supported_procedure_id"] = (
        "operator-claimed-supported-v1"
    )
    with pytest.raises(ValueError, match="runtime procedure is unsupported"):
        evaluate_ramp(manifest, receipts)


def test_live_runtime_procedure_retains_only_its_bounded_coherent_identity() -> None:
    manifest = _manifest()
    receipts = _receipts(manifest)
    expected = _use_live_runtime(receipts[0]).copy()

    report, passed = evaluate_ramp(manifest, receipts)

    assert passed is True
    first = cast(list[dict[str, Any]], report["receipt_summaries"])[0]
    assert first["runtime_identity"] == expected
    assert first["runtime_proof_identity"] == expected["proof_identity"]
    assert expected["service_unit"] == "atlas-symphony.service"
    assert expected["symphony_commit_sha"] == (
        "e5c5e48917e9e91ffb6709ab5a2a02c5af16bf02"
    )
    assert set(expected) == {
        "configured_ceiling",
        "instance_id",
        "loaded_at",
        "loaded_commit_sha",
        "max_turns",
        "proof_identity",
        "proof_observed_at",
        "service_unit",
        "supported_procedure_id",
        "symphony_commit_sha",
        "workflow_blob_sha",
        "workflow_content_sha256",
    }


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("service_unit", "wrong.service", "service unit is unsupported"),
        ("symphony_commit_sha", "f" * 40, "release identity is unsupported"),
        ("workflow_content_sha256", "f" * 63, "64-character digest"),
        ("loaded_commit_sha", "f" * 40, "identity mismatches the branch"),
        ("workflow_blob_sha", "f" * 40, "identity mismatches the branch"),
        ("configured_ceiling", 3, "values are incoherent"),
        ("max_turns", 11, "values are incoherent"),
        ("proof_identity", "f" * 63, "64-character digest"),
        ("proof_identity", "f" * 64, "proof identity is incoherent"),
    ),
)
def test_live_runtime_identity_mismatches_fail_closed(
    field: str, value: object, message: str
) -> None:
    manifest = _manifest()
    receipt = _receipts(manifest)[0]
    runtime = _use_live_runtime(receipt)
    runtime[field] = value

    with pytest.raises(ValueError, match=message):
        evaluate_ramp(manifest, [receipt])


def test_live_runtime_proof_rejects_stale_inverted_and_secret_bearing_input() -> None:
    manifest = _manifest()
    receipt = _receipts(manifest)[0]
    runtime = _use_live_runtime(receipt)
    runtime["proof_observed_at"] = "2026-08-17T12:57:00Z"
    runtime["loaded_at"] = "2026-08-17T12:58:00Z"
    _use_live_runtime(receipt)
    with pytest.raises(ValueError, match="predates the runtime load"):
        evaluate_ramp(manifest, [receipt])

    receipt = _receipts(manifest)[0]
    runtime = _use_live_runtime(receipt)
    runtime["loaded_at"] = "2026-08-17T11:58:00Z"
    runtime["proof_observed_at"] = "2026-08-17T11:59:00Z"
    _use_live_runtime(receipt)
    with pytest.raises(ValueError, match="runtime proof is stale"):
        evaluate_ramp(manifest, [receipt])

    receipt = _receipts(manifest)[0]
    runtime = _use_live_runtime(receipt)
    runtime["instance_id"] = "/root/atlas-runtime/secret-bearing-path"
    with pytest.raises(ValueError, match="secret-bearing material"):
        evaluate_ramp(manifest, [receipt])


def test_secret_bearing_input_is_rejected_without_echoing_the_value(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = _manifest()
    canary = "github_pat_do_not_retain_this_canary"
    manifest["provider_response"] = {"authorization": canary}
    injected = tmp_path / "manifest.json"
    injected.write_text(json.dumps(manifest), encoding="utf-8")

    assert main([str(injected)]) == 2
    output = capsys.readouterr().out
    assert main([str(injected)]) == 2
    repeated = capsys.readouterr().out
    assert repeated == output
    assert len(output.encode()) <= MAX_REPORT_BYTES
    assert "INVALID_INPUT" in output
    invalid = json.loads(output)
    assert invalid["transition_authorized"] is False
    assert invalid["closure_authorized"] is False
    assert canary not in output
    assert "provider_response" not in output


def test_unknown_manifest_or_receipt_fields_cannot_enter_retained_evidence() -> None:
    manifest = _manifest()
    manifest["operator_notes"] = "not part of the bounded receipt schema"
    with pytest.raises(ValueError, match="exact required fields"):
        evaluate_ramp(manifest, [])

    manifest = _manifest()
    receipts = _receipts(manifest)
    receipts[0]["operator_notes"] = "not part of the bounded receipt schema"
    with pytest.raises(ValueError, match="exact required fields"):
        evaluate_ramp(manifest, receipts)


def test_harness_has_no_mutation_or_network_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("ramp validator attempted a filesystem mutation")

    monkeypatch.setattr(Path, "write_text", forbidden)
    monkeypatch.setattr(Path, "write_bytes", forbidden)
    monkeypatch.setattr(Path, "touch", forbidden)
    monkeypatch.setattr(Path, "unlink", forbidden)

    manifest = _manifest()
    report, passed = evaluate_ramp(manifest, _receipts(manifest))
    assert passed is True
    assert report["authority_spies"] == {
        "external_write_count": 0,
        "network_call_count": 0,
        "prohibited_action_count": 0,
        "repository_mutation_count": 0,
    }

    historical = _historical_manifest()
    historical_report, historical_passed = evaluate_ramp(
        historical, _receipts(historical)[:2]
    )
    assert historical_passed is False
    assert historical_report["authority_spies"] == report["authority_spies"]

    invalid = _manifest()
    invalid["exercise_workloads"][0]["atlas_key"] = "META-GATE-1"
    with pytest.raises(ValueError, match="real Atlas ticket key"):
        evaluate_ramp(invalid, [])

    source = (ROOT / "scripts" / "phase_15_delivery_control_milestone.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert imported.isdisjoint(
        {"httpx", "requests", "socket", "sqlalchemy", "subprocess", "urllib"}
    )


def test_required_docs_and_seed_fixture_are_present_and_bounded() -> None:
    assert V2_MANIFEST.stat().st_size < MAX_MANIFEST_BYTES
    assert HISTORICAL_MANIFEST.stat().st_size < MAX_MANIFEST_BYTES
    assert all(path.is_file() for path in REQUIRED_DOCS)
    workflow = (ROOT / "WORKFLOW.md").read_text(encoding="utf-8")
    runbook = (ROOT / "docs" / "runbooks" / "operator-environment.md").read_text(
        encoding="utf-8"
    )
    delivery_control = (
        ROOT / "docs" / "atlas" / "multi-agent-delivery-control.md"
    ).read_text(encoding="utf-8")
    phase_15_5 = (ROOT / "docs" / "closure" / "phase-15.5-closure-report.md").read_text(
        encoding="utf-8"
    )
    workload_contract_docs = [
        (ROOT / relative).read_text(encoding="utf-8")
        for relative in (
            "docs/runbooks/operator-environment.md",
            "docs/runbooks/local-development.md",
            "docs/atlas/multi-agent-delivery-control.md",
            "docs/atlas/agentic-engineering-programme-design.md",
            "docs/atlas/phase-16-agent-runtime-and-integration-safety.md",
        )
    ]

    assert "max_concurrent_agents: 1" in workflow
    assert "max_turns: 10" in workflow
    assert "running VPS" in runbook
    assert "exact commit" in runbook
    assert "integration/review" in runbook
    assert LIVE_RUNTIME_PROCEDURE in runbook
    assert LIVE_RUNTIME_SERVICE_UNIT in runbook
    assert "process-owned `/api/v1/runtime`" in runbook
    assert "fixture/schema regression only" in runbook
    assert "Gate 1 is BLOCKED" not in runbook
    assert "RECEIPT_SEQUENCE_VALIDATED" in runbook
    assert all("Attempt-3" in document for document in workload_contract_docs)
    assert all("v2" in document for document in workload_contract_docs)
    assert all("v3" in document for document in workload_contract_docs)
    assert "scripts/phase_15_delivery_control_milestone.py" in delivery_control
    assert "Phase 15.5 is CLOSED" in phase_15_5
    assert not (ROOT / "docs" / "closure" / "phase-15-closure-report.md").exists()


def test_manifest_fingerprint_cli_is_deterministic(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main([str(V2_MANIFEST), "--fingerprint-only"]) == 0
    first = capsys.readouterr().out.strip()
    assert main([str(V2_MANIFEST), "--fingerprint-only"]) == 0
    second = capsys.readouterr().out.strip()

    assert first == second
    assert len(first) == 64
