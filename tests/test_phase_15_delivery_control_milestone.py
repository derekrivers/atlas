"""ATLAS-253 deterministic validator for the operator-owned live ramp."""

from __future__ import annotations

import ast
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from scripts.phase_15_delivery_control_milestone import (
    COMMON_INVARIANTS,
    FIXTURE_RUNTIME_PROCEDURE,
    GATE_EXERCISES,
    GATE_LEVELS,
    MAX_MANIFEST_BYTES,
    MAX_REPORT_BYTES,
    evaluate_ramp,
    main,
    validate_manifest,
)

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests" / "fixtures" / "phase_15" / "ramp_workload_seed_v1.json"
REQUIRED_DOCS = (
    ROOT / "WORKFLOW.md",
    ROOT / "ROADMAP.md",
    ROOT / "docs" / "atlas" / "implementation-roadmap.md",
    ROOT / "docs" / "atlas" / "multi-agent-delivery-control.md",
    ROOT / "docs" / "atlas" / "parallel-delivery-efficiency-and-integration-control.md",
    ROOT / "docs" / "runbooks" / "local-development.md",
    ROOT / "docs" / "runbooks" / "operator-environment.md",
    ROOT / "docs" / "runbooks" / "pr-acceptance.md",
    ROOT / "docs" / "closure" / "phase-15.5-closure-report.md",
)


def _manifest() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(MANIFEST.read_text(encoding="utf-8")))


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _identity(seed: int) -> str:
    return f"{seed:064x}"[-64:]


def _receipt(
    gate: int,
    index: int,
    *,
    manifest_fingerprint: str,
    outcome: str = "PASS",
    failed_invariant: str | None = None,
) -> dict[str, Any]:
    started = datetime(2026, 8, 17, 13 + (index * 2), tzinfo=UTC)
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
    level_token = f"{gate:x}"
    origin_main = f"{index + 1:x}" * 40
    return {
        "schema_version": "phase-15-ramp-gate-receipt-v1",
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
        "milestone_commit_sha": level_token * 40,
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
            "loaded_commit_sha": level_token * 40,
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
            "protected_lane_registry_fingerprint": _identity(5000 + index),
            "protected_lane_state_fingerprint": _identity(5100 + index),
            "protected_lane_occupancy": [
                {
                    "lane": "workflow-configuration",
                    "count": 1,
                    "limit": 1,
                    "ticket_keys": [f"META-GATE-{gate}"],
                    "operator_declared": False,
                }
            ],
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


def _receipts(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    fingerprint = cast(str, validate_manifest(manifest)["manifest_fingerprint"])
    return [
        _receipt(gate, index, manifest_fingerprint=fingerprint)
        for index, gate in enumerate(GATE_LEVELS)
    ]


def test_predeclared_manifest_has_more_than_ten_independent_workloads() -> None:
    selected = validate_manifest(_manifest())
    workloads = cast(list[dict[str, Any]], selected["workloads"])

    assert selected["validation_scope"] == "offline-read-only"
    assert len(workloads) == 11
    assert all(workload["independent"] is True for workload in workloads)
    assert all(workload["dependency_ids"] == [] for workload in workloads)
    assert len({workload["workload_identity"] for workload in workloads}) == 11
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
    assert report["gate_sequence"] == [1, 3, 5, 7, 10]
    assert report["last_validated_pass_receipt_level"] == 10
    assert len(retained.encode()) <= MAX_REPORT_BYTES


def test_self_declared_operator_authority_is_rejected() -> None:
    manifest = _manifest()
    manifest["authority"] = "live-operator"
    with pytest.raises(ValueError, match="exact required fields"):
        evaluate_ramp(manifest, [])

    manifest = _manifest()
    manifest["validation_scope"] = "live-operator"
    with pytest.raises(ValueError, match="offline read-only validation"):
        evaluate_ramp(manifest, [])


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
    with pytest.raises(ValueError, match="cannot PASS"):
        evaluate_ramp(manifest, receipts)

    receipts = _receipts(manifest)
    receipts[0]["snapshot"]["protected_lane_registry_version"] = "unversioned"
    with pytest.raises(ValueError, match="registry version is unsupported"):
        evaluate_ramp(manifest, receipts)


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
    fingerprint = cast(str, validate_manifest(manifest)["manifest_fingerprint"])
    gate_one = _receipt(1, 0, manifest_fingerprint=fingerprint)
    gate_three = _receipt(
        3,
        1,
        manifest_fingerprint=fingerprint,
        outcome="FAIL",
        failed_invariant="review_budget_bound",
    )
    report, passed = evaluate_ramp(manifest, [gate_one, gate_three])

    assert passed is False
    assert report["decision"] == "FAIL_GATE_3"
    assert report["last_validated_pass_receipt_level"] == 1
    summaries = cast(list[dict[str, object]], report["receipt_summaries"])
    assert summaries[-1]["retained_or_restored_level"] == 1

    gate_five = _receipt(5, 2, manifest_fingerprint=fingerprint)
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
    with pytest.raises(ValueError, match="Gate 1 is blocked"):
        evaluate_ramp(manifest, receipts)


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
    assert "INVALID_INPUT" in output
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
    assert MANIFEST.stat().st_size < MAX_MANIFEST_BYTES
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

    assert "max_concurrent_agents: 1" in workflow
    assert "max_turns: 10" in workflow
    assert "running VPS" in runbook
    assert "exact commit" in runbook
    assert "integration/review" in runbook
    assert "Gate 1 is BLOCKED" in runbook
    assert "RECEIPT_SEQUENCE_VALIDATED" in runbook
    assert "scripts/phase_15_delivery_control_milestone.py" in delivery_control
    assert "Phase 15.5 is CLOSED" in phase_15_5
    assert not (ROOT / "docs" / "closure" / "phase-15-closure-report.md").exists()


def test_manifest_fingerprint_cli_is_deterministic(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main([str(MANIFEST), "--fingerprint-only"]) == 0
    first = capsys.readouterr().out.strip()
    assert main([str(MANIFEST), "--fingerprint-only"]) == 0
    second = capsys.readouterr().out.strip()

    assert first == second
    assert len(first) == 64
