"""ATLAS-260 system-tier candidate-attestation assessment evidence."""

from __future__ import annotations

import ast
import copy
import json
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from scripts.candidate_attestation_assessment import (
    MAX_FIXTURE_BYTES,
    Decision,
    ReasonCode,
    UnboundedEvidenceError,
    assess_observations,
    exact_commit_authority,
    refresh_fixture_attestation,
    required_check_set_fingerprint,
    run_fixture,
)
from scripts.exact_base_candidate_spike import _run_git

FIXTURE = (
    Path(__file__).parent / "fixtures" / "github" / "candidate_attestation_cases.json"
)
SCRIPT = Path(__file__).parents[1] / "scripts" / "candidate_attestation_assessment.py"
FORBIDDEN_GIT_ACTIONS = {"fetch", "merge", "push", "rebase", "update-ref"}
REQUIRED_ADVERSARIAL_CASES = {
    "stable-clean-candidate",
    "contributor-head-movement",
    "sibling-live-base-movement",
    "synthetic-candidate-replacement",
    "missing-candidate",
    "conflicted-candidate",
    "indeterminate-mergeability",
    "malformed-provider-observation",
    "missing-required-result",
    "failed-required-result",
    "duplicate-ambiguous-required-result",
    "required-check-set-movement",
    "workflow-configuration-movement",
    "workflow-rerun-attempt-replacement",
    "contributor-modifiable-producer",
    "stale-attestation-from-previous-base",
    "candidate-mismatched-results",
    "superseded-result-lifecycle",
    "cancelled-required-result",
    "skipped-required-result",
    "unverified-attestation-provenance",
    "oversized-provider-evidence",
}


def fixture_payload() -> dict[str, object]:
    payload: dict[str, object] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return payload


def stable_inputs() -> tuple[dict[str, object], dict[str, object]]:
    payload = fixture_payload()
    observation = copy.deepcopy(payload["base_observation"])
    assert isinstance(observation, dict)
    required = observation["required_check_set"]
    assert isinstance(required, dict)
    required["fingerprint"] = required_check_set_fingerprint(required)
    refresh_fixture_attestation(observation)
    trust = payload["trust_policy"]
    assert isinstance(trust, dict)
    return observation, trust


def test_governed_assessment_passes_complete_adversarial_matrix(
    tmp_path: Path,
) -> None:
    report, matched = run_fixture(FIXTURE, tmp_path / "repository")

    assert matched is True
    assert report["fixture_contract_passed"] is True
    assert report["governed_case"] == "stable-clean-candidate"
    assert report["governed_decision"] == "PASS"
    assert report["case_count"] == 25
    cases = report["cases"]
    assert isinstance(cases, list)
    assert all(case["expected_matched"] is True for case in cases)
    assert {case["name"] for case in cases} >= REQUIRED_ADVERSARIAL_CASES


def test_stable_identity_records_every_required_binding(tmp_path: Path) -> None:
    report, _matched = run_fixture(FIXTURE, tmp_path / "repository")
    identity = report["governed_identity"]
    assert isinstance(identity, dict)

    assert identity["repository"] == "fixture/atlas"
    assert identity["pr_number"] == 260
    assert identity["contributor_head_sha"] == "2" * 40
    assert identity["live_base"] == {"ref": "main", "sha": "1" * 40}
    assert identity["candidate"] == {
        "parents": ["1" * 40, "2" * 40],
        "sha": "3" * 40,
        "tree_sha": "4" * 40,
    }
    assert identity["execution"] == {
        "run_attempt": 1,
        "run_id": 7001,
        "workflow_blob_sha": "b" * 40,
        "workflow_path": ".github/workflows/candidate-ci.yml",
        "workflow_repository": "atlas/ci-trust",
        "workflow_sha": "a" * 40,
    }
    assert identity["required_check_set_fingerprint"] == (
        "4b032a97fc5736e612b1b24e1032c6f16b7b8a26577b4e3433518152c193a589"
    )
    assert [result["name"] for result in identity["required_results"]] == [
        "lint",
        "test",
    ]
    assert {result["candidate_sha"] for result in identity["required_results"]} == {
        "3" * 40
    }
    assert {
        (result["run_id"], result["run_attempt"])
        for result in identity["required_results"]
    } == {(7001, 1)}


def test_repeated_unchanged_reads_reproduce_the_same_authoritative_identity() -> None:
    observation, trust = stable_inputs()

    first = assess_observations(observation, copy.deepcopy(observation), trust)
    second = assess_observations(
        copy.deepcopy(observation), copy.deepcopy(observation), trust
    )

    assert first.decision is Decision.PASS
    assert second.decision is Decision.PASS
    assert first.authoritative_identity == second.authoritative_identity
    assert first.payload() == second.payload()


def test_unverified_or_contributor_controlled_provenance_fails_closed() -> None:
    observation, trust = stable_inputs()
    unverified = copy.deepcopy(observation)
    attestation = unverified["attestation"]
    assert isinstance(attestation, dict)
    provenance = attestation["provenance"]
    assert isinstance(provenance, dict)
    provenance["cryptographically_verified"] = False

    unverified_result = assess_observations(unverified, unverified, trust)
    assert unverified_result.decision is Decision.FAIL
    assert ReasonCode.ATTESTATION_UNVERIFIED in unverified_result.reasons

    untrusted = copy.deepcopy(observation)
    execution = untrusted["execution"]
    assert isinstance(execution, dict)
    execution["producer_control"] = "candidate"
    execution["workflow_ref"] = "refs/pull/260/head"
    execution["signer_isolated"] = False
    refresh_fixture_attestation(untrusted)
    untrusted_result = assess_observations(untrusted, untrusted, trust)
    assert untrusted_result.decision is Decision.FAIL
    assert ReasonCode.UNTRUSTED_PRODUCER in untrusted_result.reasons


@pytest.mark.parametrize(
    ("status", "conclusion"),
    [
        ("completed", "failure"),
        ("completed", "cancelled"),
        ("completed", "skipped"),
        ("in_progress", "success"),
    ],
)
def test_non_successful_required_results_fail_closed(
    status: str, conclusion: str
) -> None:
    observation, trust = stable_inputs()
    jobs = observation["provider_jobs"]
    assert isinstance(jobs, list)
    jobs[1]["status"] = status
    jobs[1]["conclusion"] = conclusion
    refresh_fixture_attestation(observation)

    assessment = assess_observations(observation, observation, trust)

    assert assessment.decision is Decision.FAIL
    assert ReasonCode.REQUIRED_RESULT_NOT_PASSED in assessment.reasons


def test_disposable_git_evidence_refuses_tree_based_authority_transfer(
    tmp_path: Path,
) -> None:
    report, _matched = run_fixture(FIXTURE, tmp_path / "repository")
    evidence = report["repository_evidence"]
    assert isinstance(evidence, dict)
    candidate = evidence["clean_candidate"]
    final_merge = evidence["merge_commit"]
    squash = evidence["squash_merge"]
    assert isinstance(candidate, dict)
    assert isinstance(final_merge, dict)
    assert isinstance(squash, dict)

    assert final_merge["tree_sha"] == candidate["tree_sha"]
    assert final_merge["sha"] != candidate["sha"]
    assert exact_commit_authority(candidate["sha"], final_merge["sha"]) is False
    assert squash["tree_sha"] == candidate["tree_sha"]
    assert squash["sha"] != candidate["sha"]
    assert exact_commit_authority(candidate["sha"], squash["sha"]) is False


def test_git_mutation_spy_confines_changes_to_disposable_repository(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    calls: list[tuple[Path, tuple[str, ...]]] = []

    def spy(
        cwd: Path,
        args: Sequence[str],
        *,
        input_text: str | None = None,
        check: bool = True,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((cwd, tuple(args)))
        return _run_git(
            cwd,
            args,
            input_text=input_text,
            check=check,
            env=env,
        )

    report, matched = run_fixture(FIXTURE, root, git_runner=spy)

    assert matched is True
    assert calls
    assert all(cwd == root for cwd, _args in calls)
    assert not any(args[0] in FORBIDDEN_GIT_ACTIONS for _cwd, args in calls)
    assert report["mutation_inventory"] == {
        "automatic_acceptance": False,
        "github_merge_or_update": False,
        "git_rebase_or_push": False,
        "linear_transition": False,
        "symphony_control": False,
    }


def test_assessment_module_has_no_provider_or_control_plane_client() -> None:
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_roots.add(node.module.split(".", 1)[0])

    assert imported_roots.isdisjoint(
        {"atlas", "httpx", "requests", "subprocess", "urllib"}
    )


def test_retained_projection_excludes_secrets_logs_and_raw_payloads() -> None:
    observation, trust = stable_inputs()
    observation["authorization"] = "Bearer retained-secret-must-not-appear"
    observation["raw_payload"] = {"body": "x" * 50_000}
    observation["workflow_logs"] = "retained-secret-must-not-appear"

    assessment = assess_observations(observation, observation, trust)
    retained = json.dumps(assessment.payload(), sort_keys=True)

    assert assessment.decision is Decision.PASS
    assert len(retained.encode()) < 16 * 1024
    assert "retained-secret-must-not-appear" not in retained
    assert "raw_payload" not in retained
    assert "workflow_logs" not in retained


def test_oversized_fixture_is_rejected_before_json_parse(tmp_path: Path) -> None:
    oversized = tmp_path / "oversized.json"
    oversized.write_text(" " * (MAX_FIXTURE_BYTES + 1), encoding="utf-8")

    with pytest.raises(
        UnboundedEvidenceError, match="fixture exceeds the bounded input limit"
    ):
        run_fixture(oversized, tmp_path / "repository")
