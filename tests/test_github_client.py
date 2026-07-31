"""GitHub REST client transport tests (ATLAS-62).

Every path is exercised over a stubbed ``urllib`` -- no network, no secrets
(acceptance criteria 3, 4, 5). The ETag/304 conditional-request path, the
bounded secondary-rate-limit backoff, missing-token handling, and token
redaction are all pinned here.
"""

from __future__ import annotations

import email.message
import json
from typing import Any

import pytest

from atlas.github import normalise_workflow_runs
from atlas.github.client import (
    MAX_RATE_LIMIT_RETRIES,
    GitHubAPIError,
    GitHubCompareStatus,
    GitHubRESTClient,
    MissingGitHubTokenError,
)

HEAD_SHA = "7de6f0ec2a05242b9e87c0a16a24c68661c4dedb"
BASE_SHA = "1111111111111111111111111111111111111111"


def _headers(**pairs: str) -> email.message.Message:
    """A case-insensitive header bag, as urllib hands back on a response/error."""
    msg = email.message.Message()
    for key, value in pairs.items():
        msg[key.replace("_", "-")] = value
    return msg


class _Response:
    """Stands in for the ``urlopen`` context manager."""

    def __init__(self, body: bytes, headers: email.message.Message) -> None:
        self._body = body
        self.headers = headers

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def _http_error(code: int, headers: email.message.Message | None = None) -> Any:
    from urllib import error as urllib_error

    return urllib_error.HTTPError(
        "https://api.github.com/x", code, "err", headers or _headers(), None
    )


def _no_sleep_recorder() -> tuple[Any, list[float]]:
    waits: list[float] = []

    def _sleep(seconds: float) -> None:
        waits.append(seconds)

    return _sleep, waits


# --- token handling (criterion 5) -------------------------------------------


def test_missing_token_is_typed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(MissingGitHubTokenError, match="GITHUB_TOKEN"):
        GitHubRESTClient()


def test_reads_token_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp-env-token")
    captured: dict[str, str | None] = {}

    def _urlopen(request: Any, *a: Any, **k: Any) -> _Response:
        captured["auth"] = request.get_header("Authorization")
        return _Response(b'{"workflow_runs": []}', _headers())

    monkeypatch.setattr("atlas.github.client.urllib_request.urlopen", _urlopen)
    GitHubRESTClient().fetch_workflow_runs("o", "r", HEAD_SHA)
    assert captured["auth"] == "Bearer ghp-env-token"


def test_token_is_never_in_repr() -> None:
    client = GitHubRESTClient(token="ghp-super-secret")
    assert "ghp-super-secret" not in repr(client)
    assert repr(client) == "GitHubRESTClient(token=***)"


# --- happy path + per_page ---------------------------------------------------


def test_fetch_sends_per_page_100_and_returns_array(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    def _urlopen(request: Any, *a: Any, **k: Any) -> _Response:
        captured["url"] = request.full_url
        body = b'{"workflow_runs": [{"id": 1, "name": "Tests"}]}'
        return _Response(body, _headers(ETag='"v1"'))

    monkeypatch.setattr("atlas.github.client.urllib_request.urlopen", _urlopen)
    runs = GitHubRESTClient(token="t").fetch_workflow_runs("o", "r", HEAD_SHA)
    assert runs == [{"id": 1, "name": "Tests"}]
    assert "per_page=100" in captured["url"]
    assert f"head_sha={HEAD_SHA}" in captured["url"]


def test_check_runs_uses_commit_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    def _urlopen(request: Any, *a: Any, **k: Any) -> _Response:
        captured["url"] = request.full_url
        return _Response(b'{"check_runs": []}', _headers())

    monkeypatch.setattr("atlas.github.client.urllib_request.urlopen", _urlopen)
    GitHubRESTClient(token="t").fetch_check_runs("o", "r", HEAD_SHA)
    assert f"/repos/o/r/commits/{HEAD_SHA}/check-runs" in captured["url"]


# --- PR reviews: the bare-array (result_key=None) path (ATLAS-65) ------------


def test_fetch_pr_reviews_returns_bare_array_from_pr_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The reviews endpoint returns a JSON ARRAY, not an envelope; the
    # result_key=None path returns the parsed body directly.
    captured: dict[str, str] = {}

    def _urlopen(request: Any, *a: Any, **k: Any) -> _Response:
        captured["url"] = request.full_url
        body = b'[{"id": 1, "state": "APPROVED"}, {"id": 2, "state": "COMMENTED"}]'
        return _Response(body, _headers(ETag='"v1"'))

    monkeypatch.setattr("atlas.github.client.urllib_request.urlopen", _urlopen)
    reviews = GitHubRESTClient(token="t").fetch_pr_reviews("o", "r", 11499)
    assert reviews == [
        {"id": 1, "state": "APPROVED"},
        {"id": 2, "state": "COMMENTED"},
    ]
    # PR-scoped endpoint (takes a PR number, not a head SHA), per_page still sent.
    assert "/repos/o/r/pulls/11499/reviews" in captured["url"]
    assert "per_page=100" in captured["url"]


def test_fetch_pr_reviews_304_replays_cached_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The ETag/304 path is shared with the envelope endpoints: an unchanged
    # review list replays the cached representation; ingest dedup suppresses it.
    state = {"calls": 0}

    def _urlopen(request: Any, *a: Any, **k: Any) -> _Response:
        state["calls"] += 1
        if state["calls"] == 1:
            return _Response(b'[{"id": 1, "state": "APPROVED"}]', _headers(ETag='"e"'))
        raise _http_error(304, _headers(ETag='"e"'))

    monkeypatch.setattr("atlas.github.client.urllib_request.urlopen", _urlopen)
    client = GitHubRESTClient(token="t")
    assert client.fetch_pr_reviews("o", "r", 1) == [{"id": 1, "state": "APPROVED"}]
    assert client.fetch_pr_reviews("o", "r", 1) == [{"id": 1, "state": "APPROVED"}]


def test_fetch_pr_reviews_non_array_body_is_api_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # result_key=None expects a list; an object body (e.g. a GitHub error
    # envelope) is surfaced as a typed error, never silently treated as empty.
    def _urlopen(request: Any, *a: Any, **k: Any) -> _Response:
        return _Response(b'{"message": "Not Found"}', _headers())

    monkeypatch.setattr("atlas.github.client.urllib_request.urlopen", _urlopen)
    with pytest.raises(GitHubAPIError, match="was not a list"):
        GitHubRESTClient(token="t").fetch_pr_reviews("o", "r", 1)


# --- PR files: the bare-array (result_key=None) path (ATLAS-66) -------------


def test_fetch_pr_files_returns_bare_array_from_pr_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The files endpoint, like reviews, returns a JSON ARRAY, not an envelope;
    # the result_key=None path returns the parsed body directly.
    captured: dict[str, str] = {}

    def _urlopen(request: Any, *a: Any, **k: Any) -> _Response:
        captured["url"] = request.full_url
        body = b'[{"filename": "docs/x.md"}, {"filename": "src/y.py"}]'
        return _Response(body, _headers(ETag='"v1"'))

    monkeypatch.setattr("atlas.github.client.urllib_request.urlopen", _urlopen)
    files = GitHubRESTClient(token="t").fetch_pr_files("o", "r", 13479)
    assert files == [{"filename": "docs/x.md"}, {"filename": "src/y.py"}]
    # PR-scoped endpoint (takes a PR number, not a head SHA), per_page still sent.
    assert "/repos/o/r/pulls/13479/files" in captured["url"]
    assert "per_page=100" in captured["url"]


def test_fetch_pr_files_304_replays_cached_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The ETag/304 path is shared with the other bare-array endpoints: an
    # unchanged file list replays state so scope never mistakes 304 for no files.
    state = {"calls": 0}

    def _urlopen(request: Any, *a: Any, **k: Any) -> _Response:
        state["calls"] += 1
        if state["calls"] == 1:
            return _Response(b'[{"filename": "docs/x.md"}]', _headers(ETag='"e"'))
        raise _http_error(304, _headers(ETag='"e"'))

    monkeypatch.setattr("atlas.github.client.urllib_request.urlopen", _urlopen)
    client = GitHubRESTClient(token="t")
    assert client.fetch_pr_files("o", "r", 1) == [{"filename": "docs/x.md"}]
    assert client.fetch_pr_files("o", "r", 1) == [{"filename": "docs/x.md"}]


# --- pull request: the single-object (ATLAS-67) path ------------------------


def test_fetch_pull_request_returns_object_from_pr_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The PR endpoint returns a single JSON OBJECT (an envelope, not a bare
    # array); fetch_pull_request returns the parsed body directly so callers can
    # read ["head"]["sha"].
    captured: dict[str, str] = {}

    def _urlopen(request: Any, *a: Any, **k: Any) -> _Response:
        captured["url"] = request.full_url
        body = b'{"number": 11499, "head": {"sha": "deadbeef"}}'
        return _Response(body, _headers(ETag='"v1"'))

    monkeypatch.setattr("atlas.github.client.urllib_request.urlopen", _urlopen)
    pr = GitHubRESTClient(token="t").fetch_pull_request("o", "r", 11499)
    assert pr == {"number": 11499, "head": {"sha": "deadbeef"}}
    assert "/repos/o/r/pulls/11499" in captured["url"]


def test_fetch_pull_request_shares_conditional_request_and_replays_on_304(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The object fetch rides the SAME conditional-request core as the arrays: it
    # caches the ETag and SENDS If-None-Match on the next call (proving the path
    # is shared, not just refactored). The ONLY divergence is the 304 outcome:
    # there is no empty-object analogue of [] and no body cache to replay, so a
    # 304 replays the cached object -- never a KeyError, never a silent {}.
    seen_if_none_match: list[str | None] = []
    state = {"calls": 0}

    def _urlopen(request: Any, *a: Any, **k: Any) -> _Response:
        seen_if_none_match.append(request.get_header("If-none-match"))
        state["calls"] += 1
        if state["calls"] == 1:
            return _Response(b'{"head": {"sha": "abc"}}', _headers(ETag='"obj-etag"'))
        raise _http_error(304, _headers(ETag='"obj-etag"'))

    monkeypatch.setattr("atlas.github.client.urllib_request.urlopen", _urlopen)
    client = GitHubRESTClient(token="t")

    first = client.fetch_pull_request("o", "r", 1)
    assert first == {"head": {"sha": "abc"}}

    assert client.fetch_pull_request("o", "r", 1) == first

    # First request had no conditional header; the second sent the cached ETag —
    # exactly as the array path does (see the ETag/304 test below).
    assert seen_if_none_match[0] is None
    assert seen_if_none_match[1] == '"obj-etag"'


def test_fetch_pull_request_rate_limit_is_shared_bounded_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The object fetch shares the bounded secondary-rate-limit backoff core with
    # the array endpoints: tried once + retried MAX times honouring Retry-After,
    # then a typed error (identical to the array path's behaviour).
    state = {"calls": 0}

    def _urlopen(request: Any, *a: Any, **k: Any) -> _Response:
        state["calls"] += 1
        raise _http_error(403, _headers(Retry_After="0", x_ratelimit_remaining="0"))

    sleep, waits = _no_sleep_recorder()
    monkeypatch.setattr("atlas.github.client.urllib_request.urlopen", _urlopen)
    client = GitHubRESTClient(token="t", sleep=sleep)

    with pytest.raises(GitHubAPIError, match="rate limit"):
        client.fetch_pull_request("o", "r", 1)

    assert state["calls"] == MAX_RATE_LIMIT_RETRIES + 1
    assert len(waits) == MAX_RATE_LIMIT_RETRIES


def test_fetch_pull_request_non_object_body_is_api_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The object path expects a dict; a bare array (or any non-object) is a typed
    # error, never silently treated as an object.
    def _urlopen(request: Any, *a: Any, **k: Any) -> _Response:
        return _Response(b'[{"head": {}}]', _headers())

    monkeypatch.setattr("atlas.github.client.urllib_request.urlopen", _urlopen)
    with pytest.raises(GitHubAPIError, match="was not an object"):
        GitHubRESTClient(token="t").fetch_pull_request("o", "r", 1)


# --- branch head: current protected-base resolution ------------------------


def test_fetch_branch_head_returns_exact_sha_from_encoded_branch_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    def _urlopen(request: Any, *a: Any, **k: Any) -> _Response:
        captured["url"] = request.full_url
        body = json.dumps({"commit": {"sha": BASE_SHA}}).encode()
        return _Response(body, _headers(ETag='"branch-v1"'))

    monkeypatch.setattr("atlas.github.client.urllib_request.urlopen", _urlopen)

    assert (
        GitHubRESTClient(token="t").fetch_branch_head("o", "r", "release/live")
        == BASE_SHA
    )
    assert "/repos/o/r/branches/release%2Flive" in captured["url"]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"commit": {}},
        {"commit": {"sha": "not-a-sha"}},
    ],
)
def test_fetch_branch_head_rejects_missing_or_invalid_sha(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, Any],
) -> None:
    def _urlopen(request: Any, *a: Any, **k: Any) -> _Response:
        return _Response(json.dumps(payload).encode(), _headers())

    monkeypatch.setattr("atlas.github.client.urllib_request.urlopen", _urlopen)

    with pytest.raises(GitHubAPIError, match="branch"):
        GitHubRESTClient(token="t").fetch_branch_head("o", "r", "main")


# --- exact-SHA compare: the single-object (ATLAS-228) path ------------------


def test_compare_commits_uses_exact_sha_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    def _urlopen(request: Any, *a: Any, **k: Any) -> _Response:
        captured["url"] = request.full_url
        body = (
            b'{"status": "ahead", "ahead_by": 2, "behind_by": 0, '
            b'"merge_base_commit": {"sha": "1111111111111111111111111111111111111111"}}'
        )
        return _Response(body, _headers(ETag='"compare-v1"'))

    monkeypatch.setattr("atlas.github.client.urllib_request.urlopen", _urlopen)
    compare = GitHubRESTClient(token="t").compare_commits("o", "r", BASE_SHA, HEAD_SHA)

    assert compare.status is GitHubCompareStatus.AHEAD
    assert compare.ahead_by == 2
    assert compare.behind_by == 0
    assert compare.merge_base_sha == BASE_SHA
    assert f"/repos/o/r/compare/{BASE_SHA}...{HEAD_SHA}" in captured["url"]
    assert "main" not in captured["url"]
    assert "per_page" not in captured["url"]


def test_compare_commits_304_replays_cached_typed_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_if_none_match: list[str | None] = []
    state = {"calls": 0}

    def _urlopen(request: Any, *a: Any, **k: Any) -> _Response:
        seen_if_none_match.append(request.get_header("If-none-match"))
        state["calls"] += 1
        if state["calls"] == 1:
            body = json.dumps(
                {
                    "status": "identical",
                    "ahead_by": 0,
                    "behind_by": 0,
                    "merge_base_commit": {"sha": BASE_SHA},
                }
            ).encode()
            return _Response(body, _headers(ETag='"compare-etag"'))
        raise _http_error(304, _headers(ETag='"compare-etag"'))

    monkeypatch.setattr("atlas.github.client.urllib_request.urlopen", _urlopen)
    client = GitHubRESTClient(token="t")

    first = client.compare_commits("o", "r", BASE_SHA, HEAD_SHA)
    second = client.compare_commits("o", "r", BASE_SHA, HEAD_SHA)

    assert second == first
    assert second.status is GitHubCompareStatus.IDENTICAL
    assert seen_if_none_match[0] is None
    assert seen_if_none_match[1] == '"compare-etag"'


def test_compare_commits_rate_limit_is_shared_bounded_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {"calls": 0}

    def _urlopen(request: Any, *a: Any, **k: Any) -> _Response:
        state["calls"] += 1
        raise _http_error(403, _headers(Retry_After="0", x_ratelimit_remaining="0"))

    sleep, waits = _no_sleep_recorder()
    monkeypatch.setattr("atlas.github.client.urllib_request.urlopen", _urlopen)
    client = GitHubRESTClient(token="t", sleep=sleep)

    with pytest.raises(GitHubAPIError, match="rate limit"):
        client.compare_commits("o", "r", BASE_SHA, HEAD_SHA)

    assert state["calls"] == MAX_RATE_LIMIT_RETRIES + 1
    assert len(waits) == MAX_RATE_LIMIT_RETRIES


def test_compare_commits_missing_field_is_api_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _urlopen(request: Any, *a: Any, **k: Any) -> _Response:
        return _Response(b'{"status": "ahead", "ahead_by": 1}', _headers())

    monkeypatch.setattr("atlas.github.client.urllib_request.urlopen", _urlopen)
    with pytest.raises(GitHubAPIError, match="behind_by"):
        GitHubRESTClient(token="t").compare_commits("o", "r", BASE_SHA, HEAD_SHA)


def test_compare_commits_contradictory_counts_are_api_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _urlopen(request: Any, *a: Any, **k: Any) -> _Response:
        body = (
            b'{"status": "ahead", "ahead_by": 1, "behind_by": 1, '
            b'"merge_base_commit": {"sha": "1111111111111111111111111111111111111111"}}'
        )
        return _Response(body, _headers())

    monkeypatch.setattr("atlas.github.client.urllib_request.urlopen", _urlopen)
    with pytest.raises(GitHubAPIError, match="contradicted"):
        GitHubRESTClient(token="t").compare_commits("o", "r", BASE_SHA, HEAD_SHA)


# --- ETag / 304 (criterion 3) -----------------------------------------------


def test_etag_304_replays_state_for_ingest_dedup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_if_none_match: list[str | None] = []
    state = {"calls": 0}

    def _urlopen(request: Any, *a: Any, **k: Any) -> _Response:
        seen_if_none_match.append(request.get_header("If-none-match"))
        state["calls"] += 1
        if state["calls"] == 1:
            body = (
                b'{"workflow_runs": [{"id": 1, "name": "Tests", '
                b'"status": "completed", "conclusion": "success"}]}'
            )
            return _Response(body, _headers(ETag='"abc123"'))
        raise _http_error(304, _headers(ETag='"abc123"'))

    monkeypatch.setattr("atlas.github.client.urllib_request.urlopen", _urlopen)
    client = GitHubRESTClient(token="t")

    first = client.fetch_workflow_runs("o", "r", HEAD_SHA)
    assert normalise_workflow_runs(first, head_sha=HEAD_SHA)  # one event

    second = client.fetch_workflow_runs("o", "r", HEAD_SHA)
    assert second == first
    assert normalise_workflow_runs(second, head_sha=HEAD_SHA)

    # First request had no conditional header; the second sent the cached ETag.
    assert seen_if_none_match[0] is None
    assert seen_if_none_match[1] == '"abc123"'


# --- secondary rate limit (criterion 4) -------------------------------------


def test_rate_limit_backs_off_bounded_then_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {"calls": 0}

    def _urlopen(request: Any, *a: Any, **k: Any) -> _Response:
        state["calls"] += 1
        raise _http_error(403, _headers(Retry_After="0", x_ratelimit_remaining="0"))

    sleep, waits = _no_sleep_recorder()
    monkeypatch.setattr("atlas.github.client.urllib_request.urlopen", _urlopen)
    client = GitHubRESTClient(token="t", sleep=sleep)

    with pytest.raises(GitHubAPIError, match="rate limit"):
        client.fetch_workflow_runs("o", "r", HEAD_SHA)

    # Bounded: it tried once + retried MAX times, and slept exactly MAX times.
    assert state["calls"] == MAX_RATE_LIMIT_RETRIES + 1
    assert len(waits) == MAX_RATE_LIMIT_RETRIES


def test_rate_limit_clears_on_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    state = {"calls": 0}

    def _urlopen(request: Any, *a: Any, **k: Any) -> _Response:
        state["calls"] += 1
        if state["calls"] == 1:
            raise _http_error(429, _headers(Retry_After="0"))
        return _Response(b'{"workflow_runs": []}', _headers())

    sleep, waits = _no_sleep_recorder()
    monkeypatch.setattr("atlas.github.client.urllib_request.urlopen", _urlopen)
    client = GitHubRESTClient(token="t", sleep=sleep)
    assert client.fetch_workflow_runs("o", "r", HEAD_SHA) == []
    assert state["calls"] == 2  # retried once, then succeeded
    assert len(waits) == 1


# --- other transport failures -----------------------------------------------


def test_non_rate_limit_http_error_becomes_api_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _urlopen(request: Any, *a: Any, **k: Any) -> _Response:
        raise _http_error(500)

    monkeypatch.setattr("atlas.github.client.urllib_request.urlopen", _urlopen)
    with pytest.raises(GitHubAPIError, match="HTTP 500"):
        GitHubRESTClient(token="t").fetch_workflow_runs("o", "r", HEAD_SHA)


def test_transport_error_becomes_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from urllib import error as urllib_error

    def _urlopen(request: Any, *a: Any, **k: Any) -> _Response:
        raise urllib_error.URLError("network down")

    monkeypatch.setattr("atlas.github.client.urllib_request.urlopen", _urlopen)
    with pytest.raises(GitHubAPIError, match="request failed"):
        GitHubRESTClient(token="t").fetch_workflow_runs("o", "r", HEAD_SHA)


def test_non_json_body_becomes_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _urlopen(request: Any, *a: Any, **k: Any) -> _Response:
        return _Response(b"<html>not json</html>", _headers())

    monkeypatch.setattr("atlas.github.client.urllib_request.urlopen", _urlopen)
    with pytest.raises(GitHubAPIError, match="non-JSON"):
        GitHubRESTClient(token="t").fetch_workflow_runs("o", "r", HEAD_SHA)


# --- complete, trusted pagination -------------------------------------------


def test_all_pages_are_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    urls: list[str] = []

    def _urlopen(request: Any, *a: Any, **k: Any) -> _Response:
        urls.append(request.full_url)
        if len(urls) == 1:
            link = '<https://api.github.com/x?page=2>; rel="next"'
            return _Response(b'{"workflow_runs": [{"id": 1}]}', _headers(Link=link))
        return _Response(b'{"workflow_runs": [{"id": 2}]}', _headers())

    monkeypatch.setattr("atlas.github.client.urllib_request.urlopen", _urlopen)
    result = GitHubRESTClient(token="t").fetch_workflow_runs("o", "r", HEAD_SHA)

    assert result == [{"id": 1}, {"id": 2}]
    assert urls[1] == "https://api.github.com/x?page=2"


def test_pagination_rejects_non_github_next_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _urlopen(request: Any, *a: Any, **k: Any) -> _Response:
        link = '<https://example.com/steal-token?page=2>; rel="next"'
        return _Response(b'{"workflow_runs": []}', _headers(Link=link))

    monkeypatch.setattr("atlas.github.client.urllib_request.urlopen", _urlopen)
    with pytest.raises(GitHubAPIError, match=r"outside api\.github\.com"):
        GitHubRESTClient(token="t").fetch_workflow_runs("o", "r", HEAD_SHA)
