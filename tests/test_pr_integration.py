"""Exact-head PR integration assessment.

These tests pin the shared read-only classifier behind ``atlas pr status``:
it fetches one PR snapshot, compares the exact base/head SHAs from that
snapshot, and derives separate eligibility, ancestry, mergeability, and overall
statuses without touching Git, GitHub write APIs, Linear, or Atlas storage.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import Any

import pytest
from github_fakes import FakeGitHubClient

from atlas.github import GitHubAPIError, GitHubCompare, GitHubCompareStatus
from atlas.orchestration import (
    PRAncestryStatus,
    PRIntegrationAssessment,
    PRIntegrationEligibility,
    PRIntegrationStatus,
    PRMergeabilityStatus,
    assess_pr_integration,
)

OWNER = "atlas"
REPO = "atlas"
PR_NUMBER = 228
BASE_SHA = "1111111111111111111111111111111111111111"
HEAD_SHA = "2222222222222222222222222222222222222222"
MERGE_BASE_SHA = "3333333333333333333333333333333333333333"


def pr_payload(
    *,
    state: str = "open",
    draft: bool = False,
    merged: bool = False,
    mergeable: bool | None = True,
    base_ref: str = "main",
    head_repo: str = "atlas/atlas",
    base_repo: str = "atlas/atlas",
) -> dict[str, Any]:
    return {
        "number": PR_NUMBER,
        "state": state,
        "draft": draft,
        "merged": merged,
        "mergeable": mergeable,
        "head": {
            "ref": "feature/exact-head-status",
            "sha": HEAD_SHA,
            "repo": {"full_name": head_repo},
        },
        "base": {
            "ref": base_ref,
            "sha": BASE_SHA,
            "repo": {"full_name": base_repo},
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


def assess(
    *,
    pull_request: dict[str, Any] | None = None,
    compare_result: GitHubCompare | None = None,
) -> tuple[PRIntegrationAssessment, FakeGitHubClient]:
    fake = FakeGitHubClient(
        pull_request=pull_request or pr_payload(),
        compare=compare_result
        or compare(
            GitHubCompareStatus.AHEAD,
            ahead_by=1,
            behind_by=0,
            merge_base_sha=BASE_SHA,
        ),
    )
    return assess_pr_integration(fake, OWNER, REPO, PR_NUMBER), fake


def test_current_ahead_pr_uses_exact_shas_from_snapshot() -> None:
    assessment, fake = assess()

    assert assessment.eligibility is PRIntegrationEligibility.ELIGIBLE
    assert assessment.ancestry is PRAncestryStatus.CURRENT
    assert assessment.mergeability is PRMergeabilityStatus.MERGEABLE
    assert assessment.integration_status is PRIntegrationStatus.CURRENT
    assert assessment.ahead_by == 1
    assert assessment.behind_by == 0
    assert assessment.merge_base_sha == BASE_SHA
    assert fake.calls == [
        ("pull_request", OWNER, REPO, PR_NUMBER),
        ("compare", OWNER, REPO, f"{BASE_SHA}...{HEAD_SHA}"),
    ]


def test_assessment_is_immutable() -> None:
    assessment, _fake = assess()

    with pytest.raises(FrozenInstanceError):
        assessment.integration_status = PRIntegrationStatus.BEHIND  # type: ignore[misc]


def test_behind_pr_remains_distinct_from_diverged() -> None:
    assessment, _fake = assess(
        compare_result=compare(
            GitHubCompareStatus.BEHIND,
            ahead_by=0,
            behind_by=2,
            merge_base_sha=HEAD_SHA,
        )
    )

    assert assessment.ancestry is PRAncestryStatus.BEHIND
    assert assessment.integration_status is PRIntegrationStatus.BEHIND
    assert assessment.ahead_by == 0
    assert assessment.behind_by == 2


def test_diverged_pr_reports_diverged_with_raw_counts() -> None:
    assessment, _fake = assess(
        compare_result=compare(
            GitHubCompareStatus.DIVERGED,
            ahead_by=3,
            behind_by=4,
            merge_base_sha=MERGE_BASE_SHA,
        )
    )

    assert assessment.ancestry is PRAncestryStatus.DIVERGED
    assert assessment.integration_status is PRIntegrationStatus.DIVERGED
    assert assessment.ahead_by == 3
    assert assessment.behind_by == 4


def test_conflicted_pr_preserves_current_ancestry() -> None:
    assessment, _fake = assess(pull_request=pr_payload(mergeable=False))

    assert assessment.ancestry is PRAncestryStatus.CURRENT
    assert assessment.mergeability is PRMergeabilityStatus.CONFLICTED
    assert assessment.integration_status is PRIntegrationStatus.CONFLICTED


def test_mergeability_null_fails_closed_as_indeterminate() -> None:
    assessment, _fake = assess(pull_request=pr_payload(mergeable=None))

    assert assessment.ancestry is PRAncestryStatus.CURRENT
    assert assessment.mergeability is PRMergeabilityStatus.INDETERMINATE
    assert assessment.integration_status is PRIntegrationStatus.INDETERMINATE


@pytest.mark.parametrize(
    ("payload", "eligibility"),
    [
        (pr_payload(state="closed"), PRIntegrationEligibility.CLOSED),
        (pr_payload(state="closed", merged=True), PRIntegrationEligibility.MERGED),
        (pr_payload(draft=True), PRIntegrationEligibility.DRAFT),
        (pr_payload(head_repo="contributor/atlas"), PRIntegrationEligibility.FORK),
        (pr_payload(base_ref="develop"), PRIntegrationEligibility.NON_MAIN),
    ],
)
def test_ineligible_prs_are_named_and_do_not_compare(
    payload: dict[str, Any], eligibility: PRIntegrationEligibility
) -> None:
    assessment, fake = assess(pull_request=payload)

    assert assessment.eligibility is eligibility
    assert assessment.integration_status is PRIntegrationStatus.INELIGIBLE
    assert assessment.ancestry is PRAncestryStatus.INDETERMINATE
    assert assessment.compare_status is None
    assert fake.calls == [("pull_request", OWNER, REPO, PR_NUMBER)]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda payload: payload.pop("number"), "number"),
        (lambda payload: payload.update({"number": "228"}), "number"),
        (lambda payload: payload.update({"number": True}), "number"),
        (lambda payload: payload.update({"number": 0}), "number"),
        (lambda payload: payload.update({"number": -1}), "number"),
        (lambda payload: payload.update({"number": PR_NUMBER + 1}), "mismatched"),
    ],
)
def test_pr_number_is_required_positive_and_matches_requested_number(
    mutation: Any, message: str
) -> None:
    payload = pr_payload()
    mutation(payload)
    fake = FakeGitHubClient(pull_request=payload)

    with pytest.raises(GitHubAPIError, match=message):
        assess_pr_integration(fake, OWNER, REPO, PR_NUMBER)


def test_missing_pr_field_is_typed_api_error_not_key_error() -> None:
    payload = pr_payload()
    del payload["head"]["sha"]
    fake = FakeGitHubClient(pull_request=payload)

    with pytest.raises(GitHubAPIError, match="sha"):
        assess_pr_integration(fake, OWNER, REPO, PR_NUMBER)


def test_compare_api_error_propagates_without_current_assessment() -> None:
    fake = FakeGitHubClient(
        pull_request=pr_payload(),
        compare_error=GitHubAPIError("GitHub API request failed: timeout"),
    )

    with pytest.raises(GitHubAPIError, match="timeout"):
        assess_pr_integration(fake, OWNER, REPO, PR_NUMBER)


def test_compare_merge_base_contradiction_is_boundary_error() -> None:
    fake = FakeGitHubClient(
        pull_request=pr_payload(),
        compare=compare(
            GitHubCompareStatus.AHEAD,
            ahead_by=1,
            behind_by=0,
            merge_base_sha=MERGE_BASE_SHA,
        ),
    )

    with pytest.raises(GitHubAPIError, match="contradicted"):
        assess_pr_integration(fake, OWNER, REPO, PR_NUMBER)
