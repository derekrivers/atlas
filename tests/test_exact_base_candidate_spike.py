"""ATLAS-259 read-only exact-base synthetic-merge feasibility evidence."""

from __future__ import annotations

import copy
import json
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

from scripts.exact_base_candidate_spike import (
    Decision,
    ReasonCode,
    _run_git,
    assess_observations,
    exercise_disposable_repository,
    run_fixture,
)

FIXTURE = (
    Path(__file__).parent / "fixtures" / "github" / "exact_base_candidate_cases.json"
)
FORBIDDEN_GIT_ACTIONS = {"fetch", "merge", "push", "rebase", "update-ref"}


def fixture_payload() -> dict[str, object]:
    payload: dict[str, object] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return payload


def fixture_case(name: str) -> dict[str, object]:
    payload = fixture_payload()
    cases = payload["cases"]
    assert isinstance(cases, list)
    return next(item for item in cases if item["name"] == name)


def test_governed_fixture_reaches_fail_with_all_expected_cases(tmp_path: Path) -> None:
    report, matched = run_fixture(FIXTURE, tmp_path / "repository")

    assert matched is True
    assert report["fixture_contract_passed"] is True
    assert report["governed_case"] == "github-pr-329-recorded"
    assert report["governed_decision"] == "FAIL"
    assert report["case_count"] == 10
    cases = report["cases"]
    assert isinstance(cases, list)
    assert all(case["expected_matched"] is True for case in cases)


def test_recorded_github_candidate_cannot_claim_head_pinned_checks() -> None:
    case = fixture_case("github-pr-329-recorded")
    observations = case["observations"]
    assert isinstance(observations, list)

    assessment = assess_observations(observations[0], observations[1])

    assert assessment.decision is Decision.FAIL
    assert assessment.reasons == (ReasonCode.REQUIRED_CHECK_NOT_CANDIDATE_PINNED,)
    assert assessment.observation is not None
    assert assessment.observation.candidate_sha == (
        "9c756d071289691dd56f769450b1d623d2d3e2ff"
    )
    assert assessment.observation.candidate_tree_sha == (
        "44a3ba815f75d8163a0af1ef009a33d4242c6200"
    )
    assert len(assessment.observation.required_checks) == 8
    assert {check.app_id for check in assessment.observation.required_checks} == {15368}
    assert len(assessment.observation.check_results) == 8
    assert {result.commit_sha for result in assessment.observation.check_results} == {
        "499e3687ac66279ae0ea09c571dbe797db8c13f2"
    }


def test_disposable_repository_proves_git_relationships(tmp_path: Path) -> None:
    evidence = exercise_disposable_repository(tmp_path / "repository")

    clean = evidence["clean_candidate"]
    moved = evidence["base_move"]
    merge_commit = evidence["merge_commit"]
    squash = evidence["squash_merge"]
    conflict = evidence["conflict"]
    assert isinstance(clean, dict)
    assert isinstance(moved, dict)
    assert isinstance(merge_commit, dict)
    assert isinstance(squash, dict)
    assert isinstance(conflict, dict)

    assert clean["stable"] is True
    assert clean["sha"] == clean["repeated_sha"]
    assert clean["parents"] == [clean["base_sha"], clean["head_sha"]]
    assert moved["head_unchanged"] == clean["head_sha"]
    assert moved["old_candidate_sha"] == clean["sha"]
    assert moved["candidate_changed"] is True
    assert merge_commit["same_tree_as_candidate"] is True
    assert merge_commit["tree_sha"] == clean["tree_sha"]
    assert merge_commit["parents"] == clean["parents"]
    assert squash["same_tree_as_candidate"] is True
    assert squash["tree_sha"] == clean["tree_sha"]
    assert squash["parents"] == [clean["base_sha"]]
    assert squash["different_commit_from_candidate"] is True
    assert conflict["candidate_available"] is False


def test_git_mutation_spy_limits_harness_to_disposable_plumbing(
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

    exercise_disposable_repository(root, git_runner=spy)

    assert calls
    assert all(cwd == root for cwd, _args in calls)
    assert not any(args[0] in FORBIDDEN_GIT_ACTIONS for _cwd, args in calls)
    assert any(args[0] == "merge-tree" for _cwd, args in calls)


def test_retained_projection_excludes_credentials_and_raw_payloads() -> None:
    case = fixture_case("stable-candidate")
    observations = case["observations"]
    assert isinstance(observations, list)
    first = copy.deepcopy(observations[0])
    second = copy.deepcopy(observations[1])
    assert isinstance(first, dict)
    assert isinstance(second, dict)
    first["authorization"] = "Bearer retained-secret-must-not-appear"
    first["raw_payload"] = {"body": "x" * 50_000}
    second["token"] = "retained-secret-must-not-appear"

    assessment = assess_observations(first, second)
    retained = json.dumps(assessment.payload(), sort_keys=True)

    assert assessment.decision is Decision.PASS
    assert len(retained) < 4_096
    assert "retained-secret-must-not-appear" not in retained
    assert "raw_payload" not in retained
    assert '"external_id": "test-1"' in retained


def test_unbounded_provider_check_response_fails_closed() -> None:
    case = fixture_case("stable-candidate")
    observations = case["observations"]
    assert isinstance(observations, list)
    first = copy.deepcopy(observations[0])
    second = copy.deepcopy(observations[1])
    assert isinstance(second, dict)
    result = {
        "name": "extra",
        "app_id": 15368,
        "external_id": "extra",
        "commit_sha": "3333333333333333333333333333333333333333",
        "status": "completed",
        "conclusion": "success",
    }
    second["check_results"] = [
        dict(result, external_id=str(index)) for index in range(65)
    ]

    assessment = assess_observations(first, second)

    assert assessment.decision is Decision.FAIL
    assert assessment.reasons == (ReasonCode.MALFORMED_OBSERVATION,)
    assert assessment.payload()["identity"] is None


def test_fixture_file_size_is_bounded_before_json_parse(tmp_path: Path) -> None:
    oversized = tmp_path / "oversized.json"
    oversized.write_text(" " * (64 * 1024 + 1), encoding="utf-8")

    try:
        run_fixture(oversized, tmp_path / "repository")
    except ValueError as error:
        assert str(error) == "fixture exceeds the bounded input limit"
    else:
        raise AssertionError("oversized fixture was accepted")
