"""In-memory fake GitHub client for deterministic tests (ATLAS-62).

Mirrors ``tests/linear_fakes.py``: a test double that does NOT ship in the
package and makes no network call. It satisfies the ``GitHubClient``
protocol by replaying recorded fixture payloads, so the normaliser is
exercised against foreign CI without touching the live API. The ETag/304 and
rate-limit transport paths are held against the real client over a stubbed
``urllib`` (see ``tests/test_github_client.py``), not this fake.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from atlas.github.client import GitHubAPIError, GitHubCompare, GitHubCompareStatus

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "github"


def load_fixture(name: str, key: str) -> list[dict[str, Any]]:
    """Return the ``key`` array from a recorded GitHub API response fixture."""
    body = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    items: list[dict[str, Any]] = body[key]
    return items


def load_array_fixture(name: str) -> list[dict[str, Any]]:
    """Return a recorded GitHub bare-array fixture (e.g. PR reviews, ATLAS-65).

    The reviews endpoint returns a JSON array, not an envelope, so there is no
    key to unwrap.
    """
    items: list[dict[str, Any]] = json.loads(
        (FIXTURES / name).read_text(encoding="utf-8")
    )
    return items


def load_object_fixture(name: str) -> dict[str, Any]:
    """Return a recorded GitHub single-object fixture (e.g. a pull request).

    The pull-request endpoint returns a JSON object (an envelope, not a bare
    array), so the body IS the result -- there is no key to unwrap (ATLAS-67).
    """
    body: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return body


class FakeGitHubClient:
    """A fixture-backed ``GitHubClient``.

    ``fetch_workflow_runs`` / ``fetch_check_runs`` replay the lists they were
    seeded with and record their call arguments, so a test can assert the
    poller asked about the right ``(owner, repo, head_sha)``.
    """

    def __init__(
        self,
        *,
        workflow_runs: list[dict[str, Any]] | None = None,
        check_runs: list[dict[str, Any]] | None = None,
        pr_reviews: list[dict[str, Any]] | None = None,
        pr_files: list[dict[str, Any]] | None = None,
        pull_request: dict[str, Any] | None = None,
        compare: GitHubCompare | None = None,
        compare_error: GitHubAPIError | None = None,
    ) -> None:
        self._workflow_runs = list(workflow_runs or [])
        self._check_runs = list(check_runs or [])
        self._pr_reviews = list(pr_reviews or [])
        self._pr_files = list(pr_files or [])
        # ``None`` (the default) models an unknown PR: fetch_pull_request raises
        # GitHubAPIError (the 404 path), so the CLI's clean-precondition exit is
        # exercised. A seeded dict replays as the recorded PR object.
        self._pull_request = pull_request
        self._compare = compare or GitHubCompare(
            status=GitHubCompareStatus.AHEAD,
            ahead_by=1,
            behind_by=0,
            merge_base_sha="0" * 40,
        )
        self._compare_error = compare_error
        self.calls: list[tuple[str, str, str, str | int]] = []

    def fetch_workflow_runs(
        self, owner: str, repo: str, head_sha: str
    ) -> list[dict[str, Any]]:
        self.calls.append(("workflow_runs", owner, repo, head_sha))
        return list(self._workflow_runs)

    def fetch_check_runs(
        self, owner: str, repo: str, head_sha: str
    ) -> list[dict[str, Any]]:
        self.calls.append(("check_runs", owner, repo, head_sha))
        return list(self._check_runs)

    def fetch_pr_reviews(
        self, owner: str, repo: str, pr_number: int
    ) -> list[dict[str, Any]]:
        # Reviews are PR-scoped, so the recorded arg is the PR number, not a SHA.
        self.calls.append(("pr_reviews", owner, repo, pr_number))
        return list(self._pr_reviews)

    def fetch_pr_files(
        self, owner: str, repo: str, pr_number: int
    ) -> list[dict[str, Any]]:
        # PR files are PR-scoped too: the recorded arg is the PR number (ATLAS-66).
        self.calls.append(("pr_files", owner, repo, pr_number))
        return list(self._pr_files)

    def fetch_pull_request(
        self, owner: str, repo: str, pr_number: int
    ) -> dict[str, Any]:
        # PR-scoped single-object fetch (ATLAS-67): records the PR number, and
        # replays the recorded object. An unseeded (None) PR models a 404 ->
        # GitHubAPIError, the unknown-PR clean-precondition path.
        self.calls.append(("pull_request", owner, repo, pr_number))
        if self._pull_request is None:
            raise GitHubAPIError(
                f"GitHub API HTTP 404: pull request {pr_number} not found"
            )
        return dict(self._pull_request)

    def compare_commits(
        self, owner: str, repo: str, base_sha: str, head_sha: str
    ) -> GitHubCompare:
        self.calls.append(("compare", owner, repo, f"{base_sha}...{head_sha}"))
        if self._compare_error is not None:
            raise self._compare_error
        return self._compare
