"""Exact-head PR integration assessment.

This module is the shared, read-only classifier for answering whether one
GitHub PR head contains the exact current ``main`` commit named by the same PR
snapshot. Transport parsing stays in ``atlas.github``; this service extracts
the PR snapshot fields, calls compare with the two exact SHAs, and derives the
Atlas statuses that presentation surfaces consume.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, cast

from atlas.github import (
    GitHubAPIError,
    GitHubClient,
    GitHubCompare,
    GitHubCompareStatus,
)


class PRAncestryStatus(StrEnum):
    CURRENT = "current"
    BEHIND = "behind"
    DIVERGED = "diverged"
    INDETERMINATE = "indeterminate"


class PRMergeabilityStatus(StrEnum):
    MERGEABLE = "mergeable"
    CONFLICTED = "conflicted"
    INDETERMINATE = "indeterminate"


class PRIntegrationEligibility(StrEnum):
    ELIGIBLE = "eligible"
    MERGED = "merged"
    CLOSED = "closed"
    DRAFT = "draft"
    FORK = "fork_head"
    NON_MAIN = "non_main"


class PRIntegrationStatus(StrEnum):
    CURRENT = "current"
    BEHIND = "behind"
    DIVERGED = "diverged"
    CONFLICTED = "conflicted"
    INDETERMINATE = "indeterminate"
    INELIGIBLE = "ineligible"


@dataclass(frozen=True)
class PRIntegrationAssessment:
    """Immutable proof record for one exact-head assessment."""

    owner: str
    repo: str
    pr_number: int
    pr_state: str
    pr_draft: bool
    pr_merged: bool
    head_ref: str
    head_sha: str
    head_repository: str
    base_ref: str
    base_sha: str
    base_repository: str
    merge_base_sha: str | None
    ahead_by: int | None
    behind_by: int | None
    compare_status: GitHubCompareStatus | None
    mergeability: PRMergeabilityStatus
    ancestry: PRAncestryStatus
    eligibility: PRIntegrationEligibility
    integration_status: PRIntegrationStatus

    @property
    def is_current(self) -> bool:
        return self.integration_status is PRIntegrationStatus.CURRENT


@dataclass(frozen=True)
class _PRSnapshot:
    number: int
    state: str
    draft: bool
    merged: bool
    mergeability: PRMergeabilityStatus
    head_ref: str
    head_sha: str
    head_repository: str
    base_ref: str
    base_sha: str
    base_repository: str


def assess_pr_integration(
    github_client: GitHubClient,
    owner: str,
    repo: str,
    pr_number: int,
) -> PRIntegrationAssessment:
    """Assess one PR without mutating GitHub, Git, Linear, or Atlas storage."""
    pull_request = github_client.fetch_pull_request(owner, repo, pr_number)
    snapshot = _snapshot_from_pull_request(pull_request, requested_number=pr_number)
    eligibility = _eligibility(snapshot)

    if eligibility is not PRIntegrationEligibility.ELIGIBLE:
        return PRIntegrationAssessment(
            owner=owner,
            repo=repo,
            pr_number=snapshot.number,
            pr_state=snapshot.state,
            pr_draft=snapshot.draft,
            pr_merged=snapshot.merged,
            head_ref=snapshot.head_ref,
            head_sha=snapshot.head_sha,
            head_repository=snapshot.head_repository,
            base_ref=snapshot.base_ref,
            base_sha=snapshot.base_sha,
            base_repository=snapshot.base_repository,
            merge_base_sha=None,
            ahead_by=None,
            behind_by=None,
            compare_status=None,
            mergeability=snapshot.mergeability,
            ancestry=PRAncestryStatus.INDETERMINATE,
            eligibility=eligibility,
            integration_status=PRIntegrationStatus.INELIGIBLE,
        )

    compare = github_client.compare_commits(
        owner,
        repo,
        snapshot.base_sha,
        snapshot.head_sha,
    )
    ancestry = _derive_ancestry(compare, base_sha=snapshot.base_sha)
    return PRIntegrationAssessment(
        owner=owner,
        repo=repo,
        pr_number=snapshot.number,
        pr_state=snapshot.state,
        pr_draft=snapshot.draft,
        pr_merged=snapshot.merged,
        head_ref=snapshot.head_ref,
        head_sha=snapshot.head_sha,
        head_repository=snapshot.head_repository,
        base_ref=snapshot.base_ref,
        base_sha=snapshot.base_sha,
        base_repository=snapshot.base_repository,
        merge_base_sha=compare.merge_base_sha,
        ahead_by=compare.ahead_by,
        behind_by=compare.behind_by,
        compare_status=compare.status,
        mergeability=snapshot.mergeability,
        ancestry=ancestry,
        eligibility=eligibility,
        integration_status=_integration_status(
            ancestry=ancestry,
            mergeability=snapshot.mergeability,
        ),
    )


def pr_integration_assessment_json(
    assessment: PRIntegrationAssessment,
) -> dict[str, object]:
    """Stable JSON shape for CLI and future gates."""
    return {
        "repository": {"owner": assessment.owner, "repo": assessment.repo},
        "pr": {
            "number": assessment.pr_number,
            "state": assessment.pr_state,
            "draft": assessment.pr_draft,
            "merged": assessment.pr_merged,
        },
        "head": {
            "ref": assessment.head_ref,
            "sha": assessment.head_sha,
            "repository": assessment.head_repository,
        },
        "base": {
            "ref": assessment.base_ref,
            "sha": assessment.base_sha,
            "repository": assessment.base_repository,
        },
        "compare": {
            "status": (
                assessment.compare_status.value
                if assessment.compare_status is not None
                else None
            ),
            "ahead_by": assessment.ahead_by,
            "behind_by": assessment.behind_by,
            "merge_base_sha": assessment.merge_base_sha,
        },
        "mergeability": assessment.mergeability.value,
        "ancestry": assessment.ancestry.value,
        "eligibility": assessment.eligibility.value,
        "integration_status": assessment.integration_status.value,
        "current": assessment.is_current,
    }


def _snapshot_from_pull_request(
    pull_request: Mapping[str, Any], *, requested_number: int
) -> _PRSnapshot:
    number = _optional_int(pull_request, "number", label="pull-request response")
    if number is None:
        number = requested_number
    if number != requested_number:
        raise GitHubAPIError("GitHub API pull-request response number mismatched")

    state = _required_str(pull_request, "state", label="pull-request response")
    draft = _required_bool(pull_request, "draft", label="pull-request response")
    merged = _required_bool(pull_request, "merged", label="pull-request response")
    mergeability = _mergeability(pull_request)
    head = _required_object(pull_request, "head", label="pull-request response")
    base = _required_object(pull_request, "base", label="pull-request response")

    return _PRSnapshot(
        number=number,
        state=state,
        draft=draft,
        merged=merged,
        mergeability=mergeability,
        head_ref=_required_str(head, "ref", label="pull-request head"),
        head_sha=_required_sha(head, "sha", label="pull-request head"),
        head_repository=_repo_full_name(head, label="pull-request head"),
        base_ref=_required_str(base, "ref", label="pull-request base"),
        base_sha=_required_sha(base, "sha", label="pull-request base"),
        base_repository=_repo_full_name(base, label="pull-request base"),
    )


def _eligibility(snapshot: _PRSnapshot) -> PRIntegrationEligibility:
    if snapshot.merged:
        return PRIntegrationEligibility.MERGED
    if snapshot.state != "open":
        return PRIntegrationEligibility.CLOSED
    if snapshot.draft:
        return PRIntegrationEligibility.DRAFT
    if snapshot.head_repository != snapshot.base_repository:
        return PRIntegrationEligibility.FORK
    if snapshot.base_ref != "main":
        return PRIntegrationEligibility.NON_MAIN
    return PRIntegrationEligibility.ELIGIBLE


def _mergeability(payload: Mapping[str, Any]) -> PRMergeabilityStatus:
    value = payload.get("mergeable")
    if value is True:
        return PRMergeabilityStatus.MERGEABLE
    if value is False:
        return PRMergeabilityStatus.CONFLICTED
    if value is None:
        return PRMergeabilityStatus.INDETERMINATE
    raise GitHubAPIError("GitHub API pull-request response mergeable was invalid")


def _derive_ancestry(compare: GitHubCompare, *, base_sha: str) -> PRAncestryStatus:
    merge_base_is_base = compare.merge_base_sha == base_sha
    if compare.behind_by == 0:
        if not merge_base_is_base:
            raise GitHubAPIError(
                "GitHub compare response contradicted merge-base ancestry"
            )
        return PRAncestryStatus.CURRENT
    if merge_base_is_base:
        raise GitHubAPIError("GitHub compare response contradicted behind count")
    if compare.ahead_by == 0:
        return PRAncestryStatus.BEHIND
    return PRAncestryStatus.DIVERGED


def _integration_status(
    *,
    ancestry: PRAncestryStatus,
    mergeability: PRMergeabilityStatus,
) -> PRIntegrationStatus:
    if mergeability is PRMergeabilityStatus.INDETERMINATE:
        return PRIntegrationStatus.INDETERMINATE
    if ancestry is PRAncestryStatus.INDETERMINATE:
        return PRIntegrationStatus.INDETERMINATE
    if mergeability is PRMergeabilityStatus.CONFLICTED:
        return PRIntegrationStatus.CONFLICTED
    if ancestry is PRAncestryStatus.BEHIND:
        return PRIntegrationStatus.BEHIND
    if ancestry is PRAncestryStatus.DIVERGED:
        return PRIntegrationStatus.DIVERGED
    return PRIntegrationStatus.CURRENT


def _required_str(payload: Mapping[str, Any], key: str, *, label: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise GitHubAPIError(f"GitHub API {label} missing string field {key!r}")
    return value


def _optional_int(payload: Mapping[str, Any], key: str, *, label: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise GitHubAPIError(f"GitHub API {label} field {key!r} was not an integer")
    return value


def _required_bool(payload: Mapping[str, Any], key: str, *, label: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise GitHubAPIError(f"GitHub API {label} missing boolean field {key!r}")
    return value


def _required_object(
    payload: Mapping[str, Any], key: str, *, label: str
) -> Mapping[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise GitHubAPIError(f"GitHub API {label} missing object field {key!r}")
    return cast(Mapping[str, Any], value)


def _required_sha(payload: Mapping[str, Any], key: str, *, label: str) -> str:
    value = _required_str(payload, key, label=label)
    if not _is_40_hex_sha(value):
        raise GitHubAPIError(f"GitHub API {label} field {key!r} was not a SHA")
    return value


def _repo_full_name(payload: Mapping[str, Any], *, label: str) -> str:
    repo = _required_object(payload, "repo", label=label)
    return _required_str(repo, "full_name", label=f"{label} repo")


def _is_40_hex_sha(value: str) -> bool:
    return len(value) == 40 and all(char in "0123456789abcdefABCDEF" for char in value)
