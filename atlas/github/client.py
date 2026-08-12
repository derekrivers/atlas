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
304 Not Modified replays the cached representation so state-oriented callers
never mistake "unchanged" for "empty" (the ingest boundary deduplicates it). A
secondary-rate-limit response (403/429 with ``Retry-After`` or
``x-ratelimit-remaining: 0``) is retried a bounded number of times honouring
``Retry-After``, then raises ``GitHubAPIError`` -- never an unbounded loop.

The deterministic test suite never touches this client's network path: tests
inject the in-memory fake (``tests/github_fakes.py``) or stub ``urllib``
(the transport tests), so CI runs with no network and no secrets.
"""

from __future__ import annotations

import json
import math
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, TypeVar, cast, runtime_checkable
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

API_ROOT = "https://api.github.com"
TOKEN_ENV = "GITHUB_TOKEN"
API_VERSION = "2022-11-28"
# GitHub's max page size. Every Link rel="next" page is followed so evidence,
# review, documentation, and scope decisions always see the complete result.
PER_PAGE = 100
# Bounded retries on a secondary-rate-limit response before raising, so the
# backoff can never become an unbounded loop (ADR-0008).
MAX_RATE_LIMIT_RETRIES = 3
DEFAULT_REQUEST_TIMEOUT_SECONDS = 15.0
# Fallback backoff (seconds) when a rate-limited response carries no usable
# Retry-After; bounded so a hostile/garbled header cannot stall a tick.
_DEFAULT_BACKOFF_SECONDS = 1.0
_MAX_BACKOFF_SECONDS = 60.0

# The result type of one conditional GET, parametrised over the parse strategy
# (array endpoints -> list, the pull-request endpoint -> dict) so the shared
# transport core (`_send`) returns the right shape to each caller.
_T = TypeVar("_T")


class GitHubCompareStatus(StrEnum):
    """GitHub compare status values Atlas accepts at the transport boundary."""

    AHEAD = "ahead"
    BEHIND = "behind"
    DIVERGED = "diverged"
    IDENTICAL = "identical"


@dataclass(frozen=True)
class GitHubCompare:
    """The required, typed subset of a GitHub compare response."""

    status: GitHubCompareStatus
    ahead_by: int
    behind_by: int
    merge_base_sha: str


def _is_40_hex_sha(value: str) -> bool:
    return len(value) == 40 and all(char in "0123456789abcdefABCDEF" for char in value)


def _required_str(payload: Mapping[str, Any], key: str, *, label: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise GitHubMalformedResponseError(
            f"GitHub API {label} missing string field {key!r}"
        )
    return value


def _required_int(payload: Mapping[str, Any], key: str, *, label: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise GitHubMalformedResponseError(
            f"GitHub API {label} missing integer field {key!r}"
        )
    if value < 0:
        raise GitHubMalformedResponseError(
            f"GitHub API {label} field {key!r} was negative"
        )
    return value


def _required_object(
    payload: Mapping[str, Any], key: str, *, label: str
) -> Mapping[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise GitHubMalformedResponseError(
            f"GitHub API {label} missing object field {key!r}"
        )
    return cast(Mapping[str, Any], value)


def _parse_compare_payload(payload: Mapping[str, Any]) -> GitHubCompare:
    label = "compare response"
    raw_status = _required_str(payload, "status", label=label)
    try:
        status = GitHubCompareStatus(raw_status)
    except ValueError as error:
        raise GitHubMalformedResponseError(
            f"GitHub API compare response had unsupported status {raw_status!r}"
        ) from error

    ahead_by = _required_int(payload, "ahead_by", label=label)
    behind_by = _required_int(payload, "behind_by", label=label)
    merge_base = _required_object(payload, "merge_base_commit", label=label)
    merge_base_sha = _required_str(merge_base, "sha", label="merge_base_commit")
    if not _is_40_hex_sha(merge_base_sha):
        raise GitHubMalformedResponseError(
            "GitHub API compare response merge-base was not a SHA"
        )

    expected = {
        GitHubCompareStatus.AHEAD: ahead_by > 0 and behind_by == 0,
        GitHubCompareStatus.BEHIND: ahead_by == 0 and behind_by > 0,
        GitHubCompareStatus.DIVERGED: ahead_by > 0 and behind_by > 0,
        GitHubCompareStatus.IDENTICAL: ahead_by == 0 and behind_by == 0,
    }[status]
    if not expected:
        raise GitHubMalformedResponseError(
            "GitHub API compare response status contradicted ahead/behind counts"
        )

    return GitHubCompare(
        status=status,
        ahead_by=ahead_by,
        behind_by=behind_by,
        merge_base_sha=merge_base_sha,
    )


class GitHubClientError(RuntimeError):
    """Base for GitHub client failures."""


class MissingGitHubTokenError(GitHubClientError):
    """No GitHub token in the environment (a clean-exit precondition)."""


class GitHubAPIError(GitHubClientError):
    """A GitHub REST request failed (transport, HTTP, rate-limit, or JSON)."""


class GitHubAuthenticationError(GitHubAPIError):
    """GitHub rejected the configured server-side credentials."""


class GitHubRateLimitError(GitHubAPIError):
    """GitHub's bounded rate-limit retries were exhausted."""


class GitHubTransportError(GitHubAPIError):
    """GitHub could not be reached or returned a non-auth HTTP failure."""


class GitHubTimeoutError(GitHubTransportError):
    """A configured finite GitHub request deadline expired."""


class GitHubMalformedResponseError(GitHubAPIError):
    """GitHub returned a response that violated the typed source contract."""


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

    def fetch_pull_request(
        self, owner: str, repo: str, pr_number: int
    ) -> dict[str, Any]:
        """Raw pull-request object for a PR number (GitHub -> Atlas, ATLAS-67).

        Unlike the list endpoints this returns a single JSON OBJECT (an
        envelope, not a bare array), so callers read ``["head"]["sha"]`` to
        resolve the head SHA the CI/docs normalisers pin to. The evidence
        ``pull`` command needs this because it has a PR number but the
        workflow-run / check-run / docs normalisers need a head SHA.
        """
        ...

    def fetch_branch_head(self, owner: str, repo: str, branch: str) -> str:
        """Resolve one branch's current exact head SHA (GitHub -> Atlas)."""
        ...

    def compare_commits(
        self, owner: str, repo: str, base_sha: str, head_sha: str
    ) -> GitHubCompare:
        """Typed exact-SHA compare for ``base_sha...head_sha`` (GitHub -> Atlas)."""
        ...


class GitHubRESTClient:
    """Concrete ``GitHubClient`` over the GitHub REST API (production).

    Uses the stdlib ``urllib`` transport (no third-party HTTP dependency).
    The token is read from ``GITHUB_TOKEN`` at construction and never
    exposed. ETags and parsed representations are cached per request URL so the
    next poll can send a conditional request; a 304 replays cached state.
    """

    def __init__(
        self,
        *,
        token: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
        timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        resolved = token if token is not None else os.environ.get(TOKEN_ENV)
        if not resolved:
            raise MissingGitHubTokenError(
                f"{TOKEN_ENV} is not set; export it to use the live GitHub client"
            )
        self._token = resolved
        self._sleep = sleep
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("GitHub request timeout must be finite and positive")
        self._timeout_seconds = timeout_seconds
        # Per-URL ETag cache for conditional requests (ADR-0008 rate limits).
        self._etags: dict[str, str] = {}
        self._page_cache: dict[str, tuple[list[dict[str, Any]], str | None]] = {}
        self._object_cache: dict[str, dict[str, Any]] = {}
        self._compare_cache: dict[str, GitHubCompare] = {}

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

    def fetch_pull_request(
        self, owner: str, repo: str, pr_number: int
    ) -> dict[str, Any]:
        # The pull-request endpoint returns a single OBJECT (an envelope, not a
        # bare array): the body IS the result, so there is no result_key/list to
        # unwrap. It rides the SAME conditional-request + rate-limit core as the
        # list endpoints (it sends If-None-Match like they do). A 304 replays
        # the cached object rather than returning a footgun empty object.
        path = f"/repos/{owner}/{repo}/pulls/{pr_number}"
        url = f"{API_ROOT}{path}"

        def _on_not_modified() -> dict[str, Any]:
            cached = self._object_cache.get(url)
            if cached is None:
                raise GitHubMalformedResponseError(
                    "PR object returned 304 with no cached body"
                )
            return cached

        return self._send(
            url,
            parse=lambda response: self._read_object(
                response, url, label="pull-request response"
            ),
            on_not_modified=_on_not_modified,
        )

    def fetch_branch_head(self, owner: str, repo: str, branch: str) -> str:
        """Resolve a branch's current exact head SHA from the branches endpoint."""
        if not branch:
            raise GitHubAPIError("GitHub branch name must not be empty")
        encoded_branch = urllib_parse.quote(branch, safe="")
        path = f"/repos/{owner}/{repo}/branches/{encoded_branch}"
        url = f"{API_ROOT}{path}"

        def _on_not_modified() -> dict[str, Any]:
            cached = self._object_cache.get(url)
            if cached is None:
                raise GitHubMalformedResponseError(
                    "branch object returned 304 with no cached body"
                )
            return cached

        body = self._send(
            url,
            parse=lambda response: self._read_object(
                response, url, label="branch response"
            ),
            on_not_modified=_on_not_modified,
        )
        commit = _required_object(body, "commit", label="branch response")
        sha = _required_str(commit, "sha", label="branch commit")
        if not _is_40_hex_sha(sha):
            raise GitHubMalformedResponseError("GitHub API branch commit was not a SHA")
        return sha

    def compare_commits(
        self, owner: str, repo: str, base_sha: str, head_sha: str
    ) -> GitHubCompare:
        """Compare two exact commit SHAs with GitHub's compare endpoint.

        The URL is built from the two 40-hex SHAs supplied by the caller, not
        from branch names. The response is narrowed to the fields Atlas needs
        and validated before leaving ``atlas.github``.
        """
        if not _is_40_hex_sha(base_sha):
            raise GitHubAPIError("GitHub compare base must be an exact 40-hex SHA")
        if not _is_40_hex_sha(head_sha):
            raise GitHubAPIError("GitHub compare head must be an exact 40-hex SHA")
        path = f"/repos/{owner}/{repo}/compare/{base_sha}...{head_sha}"
        url = f"{API_ROOT}{path}"

        def _on_not_modified() -> GitHubCompare:
            cached = self._compare_cache.get(url)
            if cached is None:
                raise GitHubMalformedResponseError(
                    "GitHub compare returned 304 with no cached body"
                )
            return cached

        def _parse_compare(response: Any) -> GitHubCompare:
            body = self._read_object(response, url, label="compare response")
            compare = _parse_compare_payload(body)
            self._compare_cache[url] = compare
            return compare

        return self._send(
            url,
            parse=_parse_compare,
            on_not_modified=_on_not_modified,
        )

    # --- transport ----------------------------------------------------------

    def _get(
        self, path: str, params: dict[str, str], *, result_key: str | None
    ) -> list[dict[str, Any]]:
        """Conditional GET returning a list of items.

        With ``result_key`` set, the parsed body is an envelope and the
        ``result_key`` array is returned; with ``result_key=None`` the parsed
        body IS the list (the reviews endpoint, ATLAS-65). A 304 (ETag hit)
        replays cached state. A secondary-rate-limit response is retried a
        bounded number of
        times honouring ``Retry-After``, then raises. The conditional-request,
        backoff, and error handling live in the shared :meth:`_send` core; this
        wrapper supplies the initial URL and walks every trusted ``next`` link.
        """
        query = urllib_parse.urlencode({**params, "per_page": str(PER_PAGE)})
        next_url: str | None = f"{API_ROOT}{path}?{query}"
        visited: set[str] = set()
        items: list[dict[str, Any]] = []
        while next_url is not None:
            if next_url in visited:
                raise GitHubMalformedResponseError(
                    "GitHub pagination returned a Link cycle"
                )
            visited.add(next_url)
            page_url = next_url

            def _on_not_modified(
                current_url: str = page_url,
            ) -> tuple[list[dict[str, Any]], str | None]:
                cached = self._page_cache.get(current_url)
                if cached is None:
                    raise GitHubMalformedResponseError(
                        "GitHub page returned 304 with no cached body"
                    )
                return cached

            def _parse_page(
                response: Any, current_url: str = page_url
            ) -> tuple[list[dict[str, Any]], str | None]:
                return self._read_page(response, result_key, current_url)

            page, next_url = self._send(
                page_url,
                parse=_parse_page,
                on_not_modified=_on_not_modified,
            )
            items.extend(page)
        return items

    def _send(
        self,
        url: str,
        *,
        parse: Callable[[Any], _T],
        on_not_modified: Callable[[], _T],
    ) -> _T:
        """The shared conditional-GET transport core (ATLAS-67 extraction).

        A pure lift of the loop ``_get`` used to inline: it sends the
        conditional request (``If-None-Match`` via :meth:`_headers`), parses a
        200 with ``parse``, returns ``on_not_modified()`` on a 304 (ETag hit),
        retries a secondary-rate-limit response a bounded number of times
        honouring ``Retry-After``, and maps every HTTP/transport failure to
        ``GitHubAPIError``. The only per-caller variation is ``parse`` (array vs
        object body) and ``on_not_modified`` (both replay cached state); the
        conditional-request and rate-limit behaviour is identical for every
        endpoint, which the shared-core tests pin.
        """
        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            request = urllib_request.Request(
                url, method="GET", headers=self._headers(url)
            )
            try:
                with urllib_request.urlopen(
                    request,
                    timeout=self._timeout_seconds,
                ) as response:
                    return parse(response)
            except urllib_error.HTTPError as error:
                if error.code == 304:
                    return on_not_modified()  # ETag hit: replay cached state
                if self._is_rate_limited(error) and attempt < MAX_RATE_LIMIT_RETRIES:
                    self._sleep(self._retry_after_seconds(error))
                    continue
                if self._is_rate_limited(error):
                    raise GitHubRateLimitError(
                        f"GitHub rate limit not cleared after "
                        f"{MAX_RATE_LIMIT_RETRIES} retries: {error}"
                    ) from error
                if error.code in (401, 403):
                    raise GitHubAuthenticationError(
                        f"GitHub API authentication failed with HTTP {error.code}"
                    ) from error
                raise GitHubTransportError(
                    f"GitHub API HTTP {error.code}: {error}"
                ) from error
            except TimeoutError as error:
                raise GitHubTimeoutError(
                    "GitHub API request exceeded its configured deadline"
                ) from error
            except urllib_error.URLError as error:
                if isinstance(error.reason, TimeoutError):
                    raise GitHubTimeoutError(
                        "GitHub API request exceeded its configured deadline"
                    ) from error
                raise GitHubTransportError(
                    f"GitHub API request failed: {error}"
                ) from error
            except OSError as error:
                raise GitHubTransportError(
                    f"GitHub API request failed: {error}"
                ) from error

        # Unreachable: the loop returns or raises on every path.
        raise GitHubTransportError("GitHub API request exhausted retries unexpectedly")

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
    ) -> tuple[list[dict[str, Any]], str | None]:
        etag = response.headers.get("ETag")
        next_url = self._next_link(response.headers.get("Link"))
        try:
            body = json.loads(response.read().decode())
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise GitHubMalformedResponseError(
                f"GitHub API returned non-JSON: {error}"
            ) from error
        # result_key=None: the body IS the list (bare-array endpoints, e.g. PR
        # reviews); otherwise unwrap the envelope's result_key array (ATLAS-65).
        if result_key is None:
            items = body
        else:
            if not isinstance(body, dict):
                raise GitHubMalformedResponseError(
                    "GitHub API response envelope was not an object"
                )
            if result_key not in body:
                raise GitHubMalformedResponseError(
                    f"GitHub API response envelope missing field {result_key!r}"
                )
            items = body[result_key]
        if not isinstance(items, list):
            label = result_key or "response body"
            raise GitHubMalformedResponseError(f"GitHub API {label} was not a list")
        page = (items, next_url)
        if etag is not None:
            self._etags[url] = etag
        self._page_cache[url] = page
        return page

    def _read_object(
        self, response: Any, url: str, *, label: str = "response"
    ) -> dict[str, Any]:
        """Read one 200 response whose body is a single JSON OBJECT (ATLAS-67).

        The object analogue of :meth:`_read_page`: it caches the ETag the same
        way (so the next poll can send a conditional request) but the parsed
        body IS the result -- there is no envelope to unwrap and no list. A body
        that is not an object (e.g. a bare array or a GitHub error envelope that
        slipped a non-object through) is surfaced as a typed error, never
        silently returned. No Link/pagination applies to a single object.
        """
        etag = response.headers.get("ETag")
        try:
            body = json.loads(response.read().decode())
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise GitHubMalformedResponseError(
                f"GitHub API returned non-JSON: {error}"
            ) from error
        if not isinstance(body, dict):
            raise GitHubMalformedResponseError(f"GitHub API {label} was not an object")
        if etag is not None:
            self._etags[url] = etag
        self._object_cache[url] = body
        return body

    @staticmethod
    def _next_link(link_header: str | None) -> str | None:
        """Return a trusted GitHub ``rel=next`` URL from a Link header."""

        if not link_header:
            return None
        if not isinstance(link_header, str):
            raise GitHubMalformedResponseError(
                "GitHub pagination Link header was not a string"
            )
        for part in link_header.split(","):
            target, *parameters = part.split(";")
            if not any(parameter.strip() == 'rel="next"' for parameter in parameters):
                continue
            target = target.strip()
            if not (target.startswith("<") and target.endswith(">")):
                raise GitHubMalformedResponseError(
                    "GitHub pagination Link target was malformed"
                )
            url = target[1:-1]
            parsed = urllib_parse.urlsplit(url)
            root = urllib_parse.urlsplit(API_ROOT)
            if (parsed.scheme, parsed.netloc) != (root.scheme, root.netloc):
                raise GitHubMalformedResponseError(
                    "GitHub pagination Link target was outside api.github.com"
                )
            return url
        return None

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
