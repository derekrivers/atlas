"""Cross-layer resolution of the GitHub context shared by PR commands."""

from __future__ import annotations

from typing import Any, NamedTuple

from atlas.github import GitHubClient, GitHubRESTClient


class PRContext(NamedTuple):
    """The already-fetched GitHub context for one pull request."""

    owner: str
    repo: str
    pull_request: dict[str, Any]
    head_commit: str
    pr_files: list[str]


def pr_file_paths(files: list[dict[str, object]]) -> list[str]:
    """Extract the changed-file paths from a raw `fetch_pr_files` response (D3).

    Reads each entry's ``filename`` defensively (the D5 "never traceback" spirit
    applies to input extraction, not just the evaluators): an entry without a
    non-blank ``str`` filename is skipped rather than raising KeyError on odd
    GitHub data. The order is preserved; the scope evaluator distincts internally.
    """
    paths: list[str] = []
    for entry in files:
        filename = entry.get("filename")
        if isinstance(filename, str) and filename.strip():
            paths.append(filename)
    return paths


def parse_tickets_flag(raw: str) -> tuple[str, ...]:
    """Normalise a `--tickets` override to canonical uppercase keys (D1).

    Splits on commas, strips and uppercases each entry, drops blanks, and dedupes
    order-preserving — so `atlas-72, ATLAS-72 ,ATLAS-73` -> ``("ATLAS-72",
    "ATLAS-73")``. The same canonical form `parse_close_set` emits, so both feed
    `get_by_key` identically."""
    keys: list[str] = []
    seen: set[str] = set()
    for part in raw.split(","):
        key = part.strip().upper()
        if key and key not in seen:
            seen.add(key)
            keys.append(key)
    return tuple(keys)


def resolve_github_client(github_client: GitHubClient | None) -> GitHubClient:
    """Return an injected client or construct the live GitHub boundary.

    ``MissingGitHubTokenError`` propagates unchanged so callers can keep client
    construction under its own presentation-layer guard.
    """
    return github_client if github_client is not None else GitHubRESTClient()


def resolve_pr_context(
    repo_slug: str,
    pr_number: int,
    github_client: GitHubClient,
) -> PRContext:
    """Fetch the raw PR, its head commit, and changed paths for ``repo_slug``.

    The caller validates the ``OWNER/REPO`` shape before entry. Client
    Either fetch may raise ``GitHubAPIError``; it propagates unchanged for the
    presentation surface to map. Client construction and close-set parsing
    deliberately remain outside this function so the command preserves its
    original exception-guard topology exactly.
    """
    owner, _, repo = repo_slug.partition("/")
    pull_request = github_client.fetch_pull_request(owner, repo, pr_number)
    head_commit = str(pull_request["head"]["sha"])
    pr_files = pr_file_paths(github_client.fetch_pr_files(owner, repo, pr_number))
    return PRContext(owner, repo, pull_request, head_commit, pr_files)
