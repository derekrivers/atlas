"""ATLAS-263 fixed Phase 15.5 comparison and authority milestone."""

from __future__ import annotations

import ast
import copy
import json
from pathlib import Path
from typing import Any, cast

import pytest

from scripts.phase_15_5_milestone import (
    MAX_FIXTURE_BYTES,
    MAX_RETAINED_REPORT_BYTES,
    _canonical_digest,
    run_fixture,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "phase_15_5" / "milestone_v1.json"
SEEDED_LIVE = (
    ROOT / "tests" / "fixtures" / "phase_15_5" / "live_authority_seeded_pass.json"
)
REQUIRED_DOCS = (
    ROOT / "WORKFLOW.md",
    ROOT / "ROADMAP.md",
    ROOT / "docs" / "atlas" / "implementation-roadmap.md",
    ROOT / "docs" / "atlas" / "multi-agent-delivery-control.md",
    ROOT / "docs" / "atlas" / "parallel-delivery-efficiency-and-integration-control.md",
    ROOT / "docs" / "runbooks" / "local-development.md",
    ROOT / "docs" / "runbooks" / "pr-acceptance.md",
    ROOT / "docs" / "closure" / "phase-15.5-closure-report.md",
)


def _report(*, live: bool = True) -> dict[str, Any]:
    report, _passed = run_fixture(
        FIXTURE, live_receipt_path=SEEDED_LIVE if live else None
    )
    return cast(dict[str, Any], report)


def _fixture_payload() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(FIXTURE.read_text(encoding="utf-8")))


def test_fixed_workloads_are_identified_and_independent_before_measurement() -> None:
    payload = _fixture_payload()
    report = _report()
    proof = report["workload_independence"]

    assert payload["ratified_at"] < payload["window_started_at"]
    assert [item["workload_id"] for item in payload["workloads"]] == [
        "IND-1",
        "IND-2",
        "IND-3",
        "IND-4",
    ]
    assert proof["passed"] is True
    assert proof["recorded_before_measurement"] is True
    assert all(len(item["workload_identity"]) == 64 for item in proof["workloads"])
    assert all(
        len(item["validation_plan_identity"]) == 64 for item in proof["workloads"]
    )
    assert len({item["touched_path_family"] for item in proof["workloads"]}) == 4


def test_controlled_comparison_passes_every_predeclared_threshold() -> None:
    report, passed = run_fixture(FIXTURE, live_receipt_path=SEEDED_LIVE)
    result = cast(dict[str, Any], report)
    thresholds = result["thresholds"]

    assert passed is True
    assert result["controlled_decision"] == "PASS"
    assert result["overall_decision"] == "PASS"
    assert thresholds["all_passed"] is True
    assert all(item["passed"] is True for item in thresholds["results"].values())
    assert thresholds["results"]["agent_active_time"]["ratio"] <= 0.85
    assert thresholds["results"]["local_validation"]["ratio"] <= 0.75
    assert thresholds["results"]["ci_queue_run"]["ratio"] <= 1.2
    assert thresholds["results"]["accepted_flow"]["ratio"] >= 1.2


def test_phase_window_publishes_once_releases_slots_and_never_polls_ci() -> None:
    report = _report()
    phase = report["phase_15_5_window"]

    assert len(phase) == 4
    assert all(item["accepted"] is True for item in phase)
    assert all(item["publication_count"] == 1 for item in phase)
    assert all(item["complete_sweeps"] == 0 for item in phase)
    assert all(item["agent_ci_poll_count"] == 0 for item in phase)
    assert max(item["slot_release_seconds"] for item in phase) == 4
    assert all(
        any(
            transition
            == {
                "at": transition["at"],
                "from": "ci_pending",
                "owner": "system-tier-ci-reconciler",
                "to": "review_required",
            }
            for transition in item["state_transitions"]
        )
        for item in phase
    )


def test_lane_pair_is_held_before_publication_and_excluded_from_flow() -> None:
    lane = _report()["protected_lane_window"]

    assert lane == {
        "contender_publications_before_release": 0,
        "decision": "hold_before_publication",
        "excluded_from_independent_metrics": True,
        "independent_work_remained_admissible": True,
        "lane": "operator-admission-hotspot",
        "passed": True,
        "window_identity": (
            "a98d714313f8bab8828b2534af22c45f0b3bdad79f3639f458f92b06509c6352"
        ),
        "workloads": ["LANE-A", "LANE-B"],
    }


def test_ci_faults_freshness_and_reactivation_routes_fail_closed() -> None:
    report = _report()
    ci = report["ci_fault_matrix"]
    freshness = report["freshness_matrix"]
    reactivation = report["reactivation_matrix"]

    assert ci["passed"] is True
    assert {
        case["classification"]: (case["decision"], case["transition_writes"])
        for case in ci["cases"]
    } == {
        "passed": ("review_required", 1),
        "implementation_failure": ("changes_requested", 1),
        "pending": ("hold", 0),
        "missing": ("hold", 0),
        "infrastructure": ("hold", 0),
        "malformed": ("hold", 0),
        "stale": ("hold", 0),
        "ambiguous": ("hold", 0),
        "partial_failure": ("hold", 0),
    }
    assert freshness["passed"] is True
    assert all(
        case["synthetic_candidate_authority"] is False for case in freshness["cases"]
    )
    assert reactivation["passed"] is True
    assert {
        (case["from"], case["to"]): case["verdict"] for case in reactivation["cases"]
    } == {
        ("ci_pending", "in_progress"): "FAIL",
        ("ci_pending", "pr_open"): "FAIL",
        ("changes_requested", "in_progress"): "ALLOW",
    }


def test_actual_live_window_is_required_and_seeded_receipt_cannot_replace_it() -> None:
    pending, pending_passed = run_fixture(FIXTURE)
    seeded, seeded_passed = run_fixture(FIXTURE, live_receipt_path=SEEDED_LIVE)
    pending_report = cast(dict[str, Any], pending)
    seeded_report = cast(dict[str, Any], seeded)

    assert pending_passed is False
    assert pending_report["controlled_decision"] == "PASS"
    assert pending_report["overall_decision"] == "PENDING_LIVE_AUTHORITY"
    assert pending_report["live_authority"]["ticket_identifier"] == "ATL-437"
    assert seeded_passed is True
    assert seeded_report["live_authority"]["source"] == (
        "seeded-live-delivery-fault-exercise"
    )


def test_any_live_ci_pending_reactivation_is_an_immediate_fail(
    tmp_path: Path,
) -> None:
    live = cast(dict[str, Any], json.loads(SEEDED_LIVE.read_text(encoding="utf-8")))
    live["transitions"].append(
        {
            "at": "2026-08-17T10:07:31Z",
            "from": "ci_pending",
            "to": "in_progress",
            "owner": "linear-github-workflow",
        }
    )
    receipt = tmp_path / "reactivated.json"
    receipt.write_text(json.dumps(live), encoding="utf-8")

    report, passed = run_fixture(FIXTURE, live_receipt_path=receipt)
    result = cast(dict[str, Any], report)

    assert passed is False
    assert result["overall_decision"] == "FAIL"
    assert result["live_authority"]["decision"] == "FAIL"
    assert result["live_authority"]["unexpected_reactivations"] == [
        {
            "from": "ci_pending",
            "owner": "linear-github-workflow",
            "to": "in_progress",
        }
    ]


def test_report_is_deterministic_bounded_secret_free_and_complete(
    tmp_path: Path,
) -> None:
    payload = copy.deepcopy(_fixture_payload())
    canary = "credential-canary-must-not-be-retained"
    payload["workloads"][0]["raw_provider_response"] = {
        "authorization": canary,
        "workspace": "/private/operator/workspace",
    }
    payload["fixture_identity"] = _canonical_digest(
        {key: value for key, value in payload.items() if key != "fixture_identity"}
    )
    injected = tmp_path / "injected.json"
    injected.write_text(json.dumps(payload), encoding="utf-8")

    first, first_passed = run_fixture(injected, live_receipt_path=SEEDED_LIVE)
    second, second_passed = run_fixture(injected, live_receipt_path=SEEDED_LIVE)
    retained = json.dumps(first, separators=(",", ":"), sort_keys=True)

    assert first_passed is second_passed is True
    assert first == second
    assert len(retained.encode()) <= MAX_RETAINED_REPORT_BYTES
    assert canary not in retained
    assert "/private/operator/workspace" not in retained
    assert first["evidence_completeness"] == {
        "measured_item_count": 8,
        "passed": True,
        "retained_item_count": 8,
    }


def test_harness_has_read_only_spies_and_no_command_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("milestone attempted a filesystem mutation")

    monkeypatch.setattr(Path, "write_text", forbidden)
    monkeypatch.setattr(Path, "write_bytes", forbidden)
    monkeypatch.setattr(Path, "touch", forbidden)
    monkeypatch.setattr(Path, "unlink", forbidden)

    report, passed = run_fixture(FIXTURE, live_receipt_path=SEEDED_LIVE)
    result = cast(dict[str, Any], report)
    spies = result["authority_spies"]

    assert passed is True
    assert spies["passed"] is True
    assert spies["repository_mutation_count"] == 0
    assert spies["external_write_count"] == 0
    assert spies["prohibited_action_count"] == 0

    source = (ROOT / "scripts" / "phase_15_5_milestone.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert imported.isdisjoint(
        {"subprocess", "socket", "urllib", "httpx", "requests", "sqlalchemy"}
    )


def test_fixture_and_required_documentation_are_bounded_and_present() -> None:
    assert FIXTURE.stat().st_size <= MAX_FIXTURE_BYTES
    assert all(path.is_file() for path in REQUIRED_DOCS)
    for path in REQUIRED_DOCS:
        content = path.read_text(encoding="utf-8")
        assert "ATLAS-263" in content or "Phase 15.5" in content
        assert "CI Pending" in content


def test_fixture_identity_and_workload_identity_drift_fail_before_results(
    tmp_path: Path,
) -> None:
    payload = _fixture_payload()
    payload["workloads"][0]["touched_path_family"] = "tampered"
    tampered = tmp_path / "tampered.json"
    tampered.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="milestone fixture identity mismatched"):
        run_fixture(tampered)
