"""ATLAS-26 D2: the concrete client and the no-live-call guarantees.

The Anthropic client is exercised with a stubbed SDK (no real call); the
single live smoke test is skipped unless ATLAS_LIVE_TESTS=1, and a CI
config inspection proves CI sets no key and runs no live call.
"""

from __future__ import annotations

import os
import sys
import time
import types
from pathlib import Path

import httpx
import pytest

from atlas.planning.client import (
    MAX_CALL_ATTEMPTS,
    MAX_TOKENS,
    MODEL_NAME,
    TEMPERATURE,
    AnthropicPlannerClient,
    MissingAPIKeyError,
    ModelCallError,
    TruncatedOutputError,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _stub_anthropic(
    monkeypatch: pytest.MonkeyPatch,
    recorder: dict[str, object],
    *,
    text: str = '{"epics": []}',
    stop_reason: str = "end_turn",
) -> None:
    """Install a fake `anthropic` module modelling the streaming path:
    `messages.stream(...)` is a context manager whose `get_final_message()`
    returns a message with the given content and stop_reason."""

    class _Block:
        def __init__(self, text: str) -> None:
            self.type = "text"
            self.text = text

    class _Message:
        def __init__(self) -> None:
            self.content = [_Block(text)]
            self.stop_reason = stop_reason

    class _Stream:
        def __enter__(self) -> _Stream:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def get_final_message(self) -> _Message:
            return _Message()

    class _Messages:
        def stream(self, **kwargs: object) -> _Stream:
            recorder.update(kwargs)
            return _Stream()

    class _Anthropic:
        def __init__(self, **kwargs: object) -> None:
            recorder["api_key"] = kwargs.get("api_key")
            self.messages = _Messages()

    module = types.ModuleType("anthropic")
    module.Anthropic = _Anthropic  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", module)


def _stub_anthropic_failing(
    monkeypatch: pytest.MonkeyPatch,
    *,
    error: BaseException,
    fail_times: int,
    success_text: str = '{"epics": []}',
) -> dict[str, int]:
    """Install a fake `anthropic` whose `get_final_message()` raises `error`
    on its first `fail_times` calls, then returns a success message. Models a
    transient mid-stream drop. Returns a counter dict {"calls": n} so a test
    can assert exactly how many attempts ran. The fake also exposes a real
    `APIConnectionError` class so the client's retryable set includes it."""
    counter = {"calls": 0}

    class _Block:
        def __init__(self, text: str) -> None:
            self.type = "text"
            self.text = text

    class _Message:
        def __init__(self) -> None:
            self.content = [_Block(success_text)]
            self.stop_reason = "end_turn"

    class _Stream:
        def __enter__(self) -> _Stream:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def get_final_message(self) -> _Message:
            counter["calls"] += 1
            if counter["calls"] <= fail_times:
                raise error
            return _Message()

    class _Messages:
        def stream(self, **kwargs: object) -> _Stream:
            return _Stream()

    class _Anthropic:
        def __init__(self, **kwargs: object) -> None:
            self.messages = _Messages()

    module = types.ModuleType("anthropic")
    module.Anthropic = _Anthropic  # type: ignore[attr-defined]
    module.APIConnectionError = _FakeAPIConnectionError  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", module)
    return counter


class _FakeAPIConnectionError(Exception):
    """Stand-in for anthropic.APIConnectionError in the stubbed SDK."""


def test_generate_uses_pinned_model_and_params(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder: dict[str, object] = {}
    _stub_anthropic(monkeypatch, recorder)
    client = AnthropicPlannerClient(api_key="sk-test-key")

    out = client.generate("hello")

    assert out == '{"epics": []}'
    assert recorder["model"] == MODEL_NAME
    assert recorder["max_tokens"] == MAX_TOKENS
    assert recorder["temperature"] == TEMPERATURE
    assert recorder["api_key"] == "sk-test-key"


def test_max_tokens_is_the_model_ceiling() -> None:
    # ATLAS-101: raised to the model's maximum output (claude-sonnet-4-6: 64K).
    assert MAX_TOKENS == 64000


def test_truncation_raises_truncated_output_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # stop_reason == max_tokens is surfaced as a typed error carrying the
    # partial output (ATLAS-101), not returned as a cut-off string.
    recorder: dict[str, object] = {}
    partial = '{"epics": [], "tickets": [{"title": "cut'
    _stub_anthropic(monkeypatch, recorder, text=partial, stop_reason="max_tokens")
    with pytest.raises(TruncatedOutputError) as caught:
        AnthropicPlannerClient(api_key="sk-test").generate("hello")
    assert caught.value.raw_output == partial
    assert caught.value.max_tokens == MAX_TOKENS


def test_missing_api_key_is_typed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(MissingAPIKeyError, match="ANTHROPIC_API_KEY"):
        AnthropicPlannerClient()


def test_reads_key_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env-key")
    recorder: dict[str, object] = {}
    _stub_anthropic(monkeypatch, recorder)
    AnthropicPlannerClient().generate("x")
    assert recorder["api_key"] == "sk-env-key"


def test_key_is_never_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    client = AnthropicPlannerClient(api_key="sk-super-secret")
    assert "sk-super-secret" not in repr(client)


def test_sdk_failure_becomes_model_call_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = types.ModuleType("anthropic")

    class _Boom:
        def __init__(self, **kwargs: object) -> None:
            raise RuntimeError("network down")

    module.Anthropic = _Boom  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", module)
    with pytest.raises(ModelCallError, match="model call failed"):
        AnthropicPlannerClient(api_key="sk-test").generate("x")


# --- transient connection retry (staged-path resilience) --------------------


def test_transient_transport_drop_is_retried_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The reported failure: a mid-stream "incomplete chunked read". One blip
    # then a clean call returns the output rather than aborting.
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    drop = httpx.RemoteProtocolError(
        "peer closed connection without sending complete message body "
        "(incomplete chunked read)"
    )
    counter = _stub_anthropic_failing(monkeypatch, error=drop, fail_times=1)

    out = AnthropicPlannerClient(api_key="sk-test").generate("x")

    assert out == '{"epics": []}'
    assert counter["calls"] == 2  # one failed attempt, one success


def test_anthropic_connection_error_is_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An SDK-wrapped connection error (anthropic.APIConnectionError) retries too.
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    counter = _stub_anthropic_failing(
        monkeypatch, error=_FakeAPIConnectionError("conn reset"), fail_times=1
    )

    AnthropicPlannerClient(api_key="sk-test").generate("x")

    assert counter["calls"] == 2


def test_persistent_transport_drop_fails_after_max_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Every attempt drops: fail honestly as ModelCallError naming the attempt
    # count, after exactly MAX_CALL_ATTEMPTS tries.
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda seconds: sleeps.append(seconds))
    drop = httpx.ReadError("connection reset by peer")
    counter = _stub_anthropic_failing(
        monkeypatch, error=drop, fail_times=MAX_CALL_ATTEMPTS
    )

    with pytest.raises(ModelCallError, match="after 3 attempts"):
        AnthropicPlannerClient(api_key="sk-test").generate("x")

    assert counter["calls"] == MAX_CALL_ATTEMPTS
    # Backoff between attempts but not after the last failure.
    assert sleeps == [1.0, 2.0]


def test_truncation_is_never_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    # A token-limit cutoff is a recorded outcome (ATLAS-101), not a transient
    # failure: it surfaces immediately, on the first attempt, with no retry.
    slept = False

    def _record_sleep(_seconds: float) -> None:
        nonlocal slept
        slept = True

    monkeypatch.setattr(time, "sleep", _record_sleep)
    recorder: dict[str, object] = {}
    _stub_anthropic(monkeypatch, recorder, text='{"cut', stop_reason="max_tokens")

    with pytest.raises(TruncatedOutputError):
        AnthropicPlannerClient(api_key="sk-test").generate("x")
    assert slept is False


# --- no-live-call guarantees ------------------------------------------------


def test_ci_runs_no_live_call() -> None:
    # Proof CI never calls out: the workflow sets neither the API key nor
    # the live-test flag, so the live smoke test below is always skipped.
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "ANTHROPIC_API_KEY" not in ci
    assert "ATLAS_LIVE_TESTS" not in ci


@pytest.mark.skipif(
    os.environ.get("ATLAS_LIVE_TESTS") != "1",
    reason="live API test; set ATLAS_LIVE_TESTS=1 to run by hand",
)
def test_live_smoke() -> None:  # pragma: no cover - operator-run only
    client = AnthropicPlannerClient()
    output = client.generate(
        'Reply with the JSON object {"ok": true} and nothing else.'
    )
    assert output.strip()
