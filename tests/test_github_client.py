"""GitHub REST client transport tests (ATLAS-62).

Every path is exercised over a stubbed ``urllib`` -- no network, no secrets
(acceptance criteria 3, 4, 5). The ETag/304 conditional-request path, the
bounded secondary-rate-limit backoff, missing-token handling, and token
redaction are all pinned here.
"""

from __future__ import annotations

import email.message
import logging
from typing import Any

import pytest

from atlas.github import normalise_workflow_runs
from atlas.github.client import (
    MAX_RATE_LIMIT_RETRIES,
    GitHubAPIError,
    GitHubRESTClient,
    MissingGitHubTokenError,
)

HEAD_SHA = "7de6f0ec2a05242b9e87c0a16a24c68661c4dedb"


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


# --- ETag / 304 (criterion 3) -----------------------------------------------


def test_etag_304_produces_no_new_normalised_event(
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
    assert second == []  # 304: nothing new
    assert normalise_workflow_runs(second, head_sha=HEAD_SHA) == []  # no new event

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


# --- pagination is surfaced, never silently dropped -------------------------


def test_second_page_is_warned_not_silently_dropped(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def _urlopen(request: Any, *a: Any, **k: Any) -> _Response:
        link = '<https://api.github.com/x?page=2>; rel="next"'
        return _Response(b'{"workflow_runs": []}', _headers(Link=link))

    monkeypatch.setattr("atlas.github.client.urllib_request.urlopen", _urlopen)
    with caplog.at_level(logging.WARNING, logger="atlas.github.client"):
        GitHubRESTClient(token="t").fetch_workflow_runs("o", "r", HEAD_SHA)
    assert any("more than one page" in r.message for r in caplog.records)
