"""`atlas pr status`: read-only exact-head integration presentation."""

from __future__ import annotations

import json
from typing import Any

import pytest
from github_fakes import FakeGitHubClient

from atlas.cli import EXIT_OK, EXIT_PRECONDITION, EXIT_RECORDED_FAILURE, main
from atlas.github import GitHubAPIError, GitHubCompare, GitHubCompareStatus

OWNER = "atlas"
REPO = "atlas"
REPO_SLUG = f"{OWNER}/{REPO}"
PR_NUMBER = 228
BASE_SHA = "1111111111111111111111111111111111111111"
HEAD_SHA = "2222222222222222222222222222222222222222"
MERGE_BASE_SHA = "3333333333333333333333333333333333333333"


def pr_payload(
    *,
    draft: bool = False,
    mergeable: bool | None = True,
    base_ref: str = "main",
    head_repo: str = REPO_SLUG,
) -> dict[str, Any]:
    return {
        "number": PR_NUMBER,
        "state": "open",
        "draft": draft,
        "merged": False,
        "mergeable": mergeable,
        "head": {
            "ref": "feature/exact-head-status",
            "sha": HEAD_SHA,
            "repo": {"full_name": head_repo},
        },
        "base": {
            "ref": base_ref,
            "sha": BASE_SHA,
            "repo": {"full_name": REPO_SLUG},
        },
    }


def compare(
    status: GitHubCompareStatus,
    *,
    ahead_by: int,
    behind_by: int,
    merge_base_sha: str,
) -> GitHubCompare:
    return GitHubCompare(
        status=status,
        ahead_by=ahead_by,
        behind_by=behind_by,
        merge_base_sha=merge_base_sha,
    )


def fake(
    *,
    pull_request: dict[str, Any] | None = None,
    compare_result: GitHubCompare | None = None,
    compare_error: GitHubAPIError | None = None,
    with_pr: bool = True,
) -> FakeGitHubClient:
    return FakeGitHubClient(
        pull_request=pr_payload() if with_pr and pull_request is None else pull_request,
        compare=compare_result
        or compare(
            GitHubCompareStatus.AHEAD,
            ahead_by=1,
            behind_by=0,
            merge_base_sha=BASE_SHA,
        ),
        compare_error=compare_error,
    )


def run_status(client: FakeGitHubClient, *extra: str) -> int:
    return main(
        ["pr", "status", "--pr", str(PR_NUMBER), "--repo", REPO_SLUG, *extra],
        github_client=client,
    )


def test_current_status_json_exits_zero_and_carries_exact_proof(
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = fake()

    code = run_status(client, "--json")
    payload = json.loads(capsys.readouterr().out)

    assert code == EXIT_OK
    assert payload["integration_status"] == "current"
    assert payload["eligibility"] == "eligible"
    assert payload["ancestry"] == "current"
    assert payload["mergeability"] == "mergeable"
    assert payload["base"]["sha"] == BASE_SHA
    assert payload["base"]["sha_source"] == "live_branch"
    assert payload["head"]["sha"] == HEAD_SHA
    assert payload["compare"] == {
        "status": "ahead",
        "ahead_by": 1,
        "behind_by": 0,
        "merge_base_sha": BASE_SHA,
    }
    assert client.calls == [
        ("pull_request", OWNER, REPO, PR_NUMBER),
        ("branch_head", OWNER, REPO, "main"),
        ("compare", OWNER, REPO, f"{BASE_SHA}...{HEAD_SHA}"),
    ]


@pytest.mark.parametrize(
    ("compare_result", "status"),
    [
        (
            compare(
                GitHubCompareStatus.BEHIND,
                ahead_by=0,
                behind_by=2,
                merge_base_sha=HEAD_SHA,
            ),
            "behind",
        ),
        (
            compare(
                GitHubCompareStatus.DIVERGED,
                ahead_by=2,
                behind_by=3,
                merge_base_sha=MERGE_BASE_SHA,
            ),
            "diverged",
        ),
    ],
)
def test_eligible_not_current_status_exits_nonzero(
    compare_result: GitHubCompare,
    status: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = run_status(fake(compare_result=compare_result), "--json")
    payload = json.loads(capsys.readouterr().out)

    assert code == EXIT_RECORDED_FAILURE
    assert payload["integration_status"] == status
    assert payload["ancestry"] == status


def test_conflicted_status_is_distinct_in_human_output(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = run_status(fake(pull_request=pr_payload(mergeable=False)))
    out = capsys.readouterr().out

    assert code == EXIT_RECORDED_FAILURE
    assert "integration_status: conflicted" in out
    assert "ancestry: current" in out
    assert "mergeability: conflicted" in out


def test_mergeability_null_is_indeterminate_and_nonzero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = run_status(fake(pull_request=pr_payload(mergeable=None)), "--json")
    payload = json.loads(capsys.readouterr().out)

    assert code == EXIT_RECORDED_FAILURE
    assert payload["integration_status"] == "indeterminate"
    assert payload["mergeability"] == "indeterminate"
    assert payload["current"] is False


@pytest.mark.parametrize(
    ("pull_request", "eligibility"),
    [
        (pr_payload(draft=True), "draft"),
        (pr_payload(head_repo="contributor/atlas"), "fork_head"),
        (pr_payload(base_ref="develop"), "non_main"),
    ],
)
def test_ineligible_status_renders_named_state_without_compare(
    pull_request: dict[str, Any],
    eligibility: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = fake(pull_request=pull_request)

    code = run_status(client, "--json")
    payload = json.loads(capsys.readouterr().out)

    assert code == EXIT_RECORDED_FAILURE
    assert payload["integration_status"] == "ineligible"
    assert payload["eligibility"] == eligibility
    assert payload["base"]["sha_source"] == "historical_pr_snapshot"
    assert payload["compare"]["status"] is None
    assert client.calls == [("pull_request", OWNER, REPO, PR_NUMBER)]


def test_malformed_repo_is_clean_precondition(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = main(
        ["pr", "status", "--pr", str(PR_NUMBER), "--repo", "not-a-slug"],
        github_client=fake(),
    )

    captured = capsys.readouterr()
    assert code == EXIT_PRECONDITION
    assert captured.out == ""
    assert "OWNER/REPO" in captured.err
    assert "Traceback" not in captured.err


def test_missing_token_is_clean_precondition(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    code = main(["pr", "status", "--pr", str(PR_NUMBER), "--repo", REPO_SLUG])

    captured = capsys.readouterr()
    assert code == EXIT_PRECONDITION
    assert captured.out == ""
    assert "GITHUB_TOKEN" in captured.err
    assert "Traceback" not in captured.err


def test_unknown_pr_is_clean_precondition(capsys: pytest.CaptureFixture[str]) -> None:
    code = run_status(fake(with_pr=False))

    captured = capsys.readouterr()
    assert code == EXIT_PRECONDITION
    assert captured.out == ""
    assert "404" in captured.err
    assert "Traceback" not in captured.err


def test_missing_field_is_clean_precondition_not_traceback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = pr_payload()
    del payload["base"]["sha"]

    code = run_status(fake(pull_request=payload))

    captured = capsys.readouterr()
    assert code == EXIT_PRECONDITION
    assert captured.out == ""
    assert "sha" in captured.err
    assert "Traceback" not in captured.err


def test_missing_pr_number_is_clean_precondition_not_traceback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = pr_payload()
    del payload["number"]

    code = run_status(fake(pull_request=payload))

    captured = capsys.readouterr()
    assert code == EXIT_PRECONDITION
    assert captured.out == ""
    assert "number" in captured.err
    assert "Traceback" not in captured.err


def test_compare_transport_error_is_clean_precondition(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = run_status(
        fake(compare_error=GitHubAPIError("GitHub API request failed: timeout"))
    )

    captured = capsys.readouterr()
    assert code == EXIT_PRECONDITION
    assert captured.out == ""
    assert "timeout" in captured.err
    assert "Traceback" not in captured.err
