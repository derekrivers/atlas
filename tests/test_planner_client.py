"""ATLAS-26 D2: the concrete client and the no-live-call guarantees.

The Anthropic client is exercised with a stubbed SDK (no real call); the
single live smoke test is skipped unless ATLAS_LIVE_TESTS=1, and a CI
config inspection proves CI sets no key and runs no live call.
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest

from atlas.planning.client import (
    MAX_TOKENS,
    MODEL_NAME,
    TEMPERATURE,
    AnthropicPlannerClient,
    MissingAPIKeyError,
    ModelCallError,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _stub_anthropic(
    monkeypatch: pytest.MonkeyPatch, recorder: dict[str, object]
) -> None:
    """Install a fake `anthropic` module capturing the call arguments."""

    class _Block:
        def __init__(self, text: str) -> None:
            self.type = "text"
            self.text = text

    class _Response:
        def __init__(self) -> None:
            self.content = [_Block('{"epics": []}')]

    class _Messages:
        def create(self, **kwargs: object) -> _Response:
            recorder.update(kwargs)
            return _Response()

    class _Anthropic:
        def __init__(self, **kwargs: object) -> None:
            recorder["api_key"] = kwargs.get("api_key")
            self.messages = _Messages()

    module = types.ModuleType("anthropic")
    module.Anthropic = _Anthropic  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", module)


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
