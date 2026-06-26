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

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "github"


def load_fixture(name: str, key: str) -> list[dict[str, Any]]:
    """Return the ``key`` array from a recorded GitHub API response fixture."""
    body = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    items: list[dict[str, Any]] = body[key]
    return items


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
    ) -> None:
        self._workflow_runs = list(workflow_runs or [])
        self._check_runs = list(check_runs or [])
        self.calls: list[tuple[str, str, str, str]] = []

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
