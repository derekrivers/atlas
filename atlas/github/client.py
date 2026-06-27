"""GitHub Checks/Workflow-Runs polling boundary (ATLAS-62).

The atlas-side ``GitHubClient`` protocol and its concrete REST
implementation, mirroring ``atlas/linear/client.py``: a stdlib ``urllib``
transport (no third-party HTTP dependency), the token read from the
environment at construction and never logged or ``repr``'d, and a
``GitHubClientError`` / ``Missing*TokenError`` / ``*APIError`` hierarchy.
``atlas.github`` is a layer above ``atlas.core`` only (the import-linter
spine forbids ``github -> linear``; ARCHITECTURE.md "Layer spine").

Scope is the CI primitives ADR-0008's pull-first transport needs: fetch the
workflow runs and check runs for an explicit ``(owner, repo, head_sha)`` and
the PR reviews for a ``(owner, repo, pr_number)`` (ATLAS-65 -- reviews are
PR-scoped, and their endpoint returns a bare array, not an envelope).
Normalisation into the webhook-swap shape lives in ``normaliser.py``; PR files
(and their fetch method) ship later beside their own normaliser (ATLAS-66),
and the ticket-driven tick loop is Phase 8.

Rate-limit discipline (ADR-0008): conditional requests with ``If-None-Match``
using the ETag from the prior response keep polling inside the rate limit; a
304 Not Modified yields the empty list (no new normalised event). A
secondary-rate-limit response (403/429 with ``Retry-After`` or
``x-ratelimit-remaining: 0``) is retried a bounded number of times honouring
``Retry-After``, then raises ``GitHubAPIError`` -- never an unbounded loop.

The deterministic test suite never touches this client's network path: tests
inject the in-memory fake (``tests/github_fakes.py``) or stub ``urllib``
(the transport tests), so CI runs with no network and no secrets.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

API_ROOT = "https://api.github.com"
TOKEN_ENV = "GITHUB_TOKEN"
API_VERSION = "2022-11-28"
# GitHub's max page size. We request it explicitly rather than rely on the
# default (30). Full Link-header pagination is a deliberate follow-up, exactly
# as atlas/linear/client.py defers paging past its `first: 250` bound to
# ATLAS-42: a second page is surfaced (a warning), never silently dropped.
PER_PAGE = 100
# Bounded retries on a secondary-rate-limit response before raising, so the
# backoff can never become an unbounded loop (ADR-0008).
MAX_RATE_LIMIT_RETRIES = 3
# Fallback backoff (seconds) when a rate-limited response carries no usable
# Retry-After; bounded so a hostile/garbled header cannot stall a tick.
_DEFAULT_BACKOFF_SECONDS = 1.0
_MAX_BACKOFF_SECONDS = 60.0

logger = logging.getLogger("atlas.github.client")


class GitHubClientError(RuntimeError):
    """Base for GitHub client failures."""


class MissingGitHubTokenError(GitHubClientError):
    """No GitHub token in the environment (a clean-exit precondition)."""


class GitHubAPIError(GitHubClientError):
    """A GitHub REST request failed (transport, HTTP, rate-limit, or JSON)."""


@runtime_checkable
class GitHubClient(Protocol):
    """The atlas-side GitHub boundary: the CI + review primitives Phase 6 needs.

    The ticket-driven loop that calls these on a cadence is Phase 8; the PR-file
    fetch for docs evidence ships later beside its own normaliser (ATLAS-66).
    """

    def fetch_workflow_runs(
        self, owner: str, repo: str, head_sha: str
    ) -> list[dict[str, Any]]:
        """Raw Actions workflow runs for a PR head SHA (GitHub -> Atlas)."""
        ...

    def fetch_check_runs(
        self, owner: str, repo: str, head_sha: str
    ) -> list[dict[str, Any]]:
        """Raw check runs for a commit SHA (GitHub -> Atlas)."""
        ...

    def fetch_pr_reviews(
        self, owner: str, repo: str, pr_number: int
    ) -> list[dict[str, Any]]:
        """Raw PR reviews for a pull request (GitHub -> Atlas).

        Reviews are PR-scoped, so this takes ``pr_number`` (not a head SHA);
        the endpoint returns a bare JSON array, not an envelope (ATLAS-65).
        """
        ...

    def fetch_pr_files(
        self, owner: str, repo: str, pr_number: int
    ) -> list[dict[str, Any]]:
        """Raw changed-file list for a pull request (GitHub -> Atlas).

        Like reviews, PR files are PR-scoped (takes ``pr_number``) and the
        endpoint returns a bare JSON array, not an envelope (ATLAS-66). Feeds
        the documentation-evidence normaliser, which records a touched-``docs/``
        change as a DOCUMENTATION_UPDATE.
        """
        ...


class GitHubRESTClient:
    """Concrete ``GitHubClient`` over the GitHub REST API (production).

    Uses the stdlib ``urllib`` transport (no third-party HTTP dependency).
    The token is read from ``GITHUB_TOKEN`` at construction and never
    exposed. ETags from each response are cached per request URL so the next
    poll can send a conditional request; a 304 returns the empty list.
    """

    def __init__(
        self,
        *,
        token: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        resolved = token if token is not None else os.environ.get(TOKEN_ENV)
        if not resolved:
            raise MissingGitHubTokenError(
                f"{TOKEN_ENV} is not set; export it to use the live GitHub client"
            )
        self._token = resolved
        self._sleep = sleep
        # Per-URL ETag cache for conditional requests (ADR-0008 rate limits).
        self._etags: dict[str, str] = {}

    def __repr__(self) -> str:  # never expose the token
        return "GitHubRESTClient(token=***)"

    def fetch_workflow_runs(
        self, owner: str, repo: str, head_sha: str
    ) -> list[dict[str, Any]]:
        path = f"/repos/{owner}/{repo}/actions/runs"
        body = self._get(path, {"head_sha": head_sha}, result_key="workflow_runs")
        return body

    def fetch_check_runs(
        self, owner: str, repo: str, head_sha: str
    ) -> list[dict[str, Any]]:
        path = f"/repos/{owner}/{repo}/commits/{head_sha}/check-runs"
        body = self._get(path, {}, result_key="check_runs")
        return body

    def fetch_pr_reviews(
        self, owner: str, repo: str, pr_number: int
    ) -> list[dict[str, Any]]:
        # The reviews endpoint returns a bare JSON array, not an envelope, so
        # there is no result_key to unwrap (result_key=None; ATLAS-65).
        path = f"/repos/{owner}/{repo}/pulls/{pr_number}/reviews"
        body = self._get(path, {}, result_key=None)
        return body

    def fetch_pr_files(
        self, owner: str, repo: str, pr_number: int
    ) -> list[dict[str, Any]]:
        # The files endpoint returns a bare JSON array, like reviews, so there
        # is no result_key to unwrap (result_key=None; ATLAS-66).
        path = f"/repos/{owner}/{repo}/pulls/{pr_number}/files"
        body = self._get(path, {}, result_key=None)
        return body

    # --- transport ----------------------------------------------------------

    def _get(
        self, path: str, params: dict[str, str], *, result_key: str | None
    ) -> list[dict[str, Any]]:
        """Conditional GET returning a list of items.

        With ``result_key`` set, the parsed body is an envelope and the
        ``result_key`` array is returned; with ``result_key=None`` the parsed
        body IS the list (the reviews endpoint, ATLAS-65). A 304 (ETag hit)
        returns ``[]`` -- the unchanged resource produces no new normalised
        event. A secondary-rate-limit response is retried a bounded number of
        times honouring ``Retry-After``, then raises.
        """
        query = urllib_parse.urlencode({**params, "per_page": str(PER_PAGE)})
        url = f"{API_ROOT}{path}?{query}"

        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            request = urllib_request.Request(
                url, method="GET", headers=self._headers(url)
            )
            try:
                with urllib_request.urlopen(request) as response:
                    return self._read_page(response, result_key, url)
            except urllib_error.HTTPError as error:
                if error.code == 304:
                    return []  # ETag hit: nothing changed, no new event
                if self._is_rate_limited(error) and attempt < MAX_RATE_LIMIT_RETRIES:
                    self._sleep(self._retry_after_seconds(error))
                    continue
                if self._is_rate_limited(error):
                    raise GitHubAPIError(
                        f"GitHub rate limit not cleared after "
                        f"{MAX_RATE_LIMIT_RETRIES} retries: {error}"
                    ) from error
                raise GitHubAPIError(
                    f"GitHub API HTTP {error.code}: {error}"
                ) from error
            except (urllib_error.URLError, OSError) as error:
                raise GitHubAPIError(f"GitHub API request failed: {error}") from error

        # Unreachable: the loop returns or raises on every path.
        raise GitHubAPIError("GitHub API request exhausted retries unexpectedly")

    def _headers(self, url: str) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
        }
        etag = self._etags.get(url)
        if etag is not None:
            headers["If-None-Match"] = etag
        return headers

    def _read_page(
        self, response: Any, result_key: str | None, url: str
    ) -> list[dict[str, Any]]:
        etag = response.headers.get("ETag")
        if etag is not None:
            self._etags[url] = etag
        # The evidence layer's contract is that nothing is silently dropped:
        # a second page (Link rel="next") is surfaced rather than discarded.
        # Full Link-header pagination stays a follow-up.
        link = response.headers.get("Link") or ""
        if 'rel="next"' in link:
            logger.warning(
                "GitHub %s has more than one page (per_page=%d); only the first "
                "page was read -- full pagination is a follow-up (ATLAS-62)",
                result_key or "reviews",
                PER_PAGE,
            )
        try:
            body = json.loads(response.read().decode())
        except json.JSONDecodeError as error:
            raise GitHubAPIError(f"GitHub API returned non-JSON: {error}") from error
        # result_key=None: the body IS the list (bare-array endpoints, e.g. PR
        # reviews); otherwise unwrap the envelope's result_key array (ATLAS-65).
        items = body if result_key is None else body.get(result_key, [])
        if not isinstance(items, list):
            label = result_key or "response body"
            raise GitHubAPIError(f"GitHub API {label} was not a list")
        return items

    @staticmethod
    def _is_rate_limited(error: urllib_error.HTTPError) -> bool:
        if error.code not in (403, 429):
            return False
        # Secondary rate limit: Retry-After present; primary: remaining == 0.
        return (
            error.headers.get("Retry-After") is not None
            or error.headers.get("x-ratelimit-remaining") == "0"
        )

    @staticmethod
    def _retry_after_seconds(error: urllib_error.HTTPError) -> float:
        raw = error.headers.get("Retry-After")
        try:
            seconds = float(raw) if raw is not None else _DEFAULT_BACKOFF_SECONDS
        except ValueError:
            # A non-numeric Retry-After (HTTP-date form) is not parsed here;
            # fall back to the bounded default rather than stall.
            seconds = _DEFAULT_BACKOFF_SECONDS
        return max(0.0, min(seconds, _MAX_BACKOFF_SECONDS))
