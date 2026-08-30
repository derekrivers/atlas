"""ATLAS-26 D2: the concrete client and the no-live-call guarantees.

The Anthropic client is exercised with a stubbed SDK (no real call); the
single live smoke test is skipped unless ATLAS_LIVE_TESTS=1, and a CI
config inspection proves CI sets no key and runs no live call.
"""

from __future__ import annotations

import hashlib
import os
import sys
import time
import types
from pathlib import Path
from uuid import UUID

import httpx
import pytest

from atlas.core.models.planner_call_telemetry import (
    PlannerDigestAlgorithm,
    PlannerExecutionParameters,
    PlannerIdentity,
    PlannerInputIdentity,
    PlannerLogicalCall,
    PlannerLogicalCallIdentity,
    PlannerPayloadSize,
    PlannerPromptSegmentSize,
    PlannerPromptTemplateIdentity,
    PlanningExecutionIdentity,
)
from atlas.planning.client import (
    MAX_CALL_ATTEMPTS,
    MAX_TOKENS,
    MODEL_NAME,
    TEMPERATURE,
    AnthropicPlannerClient,
    MissingAPIKeyError,
    ModelCallError,
    PlannerCallContractError,
    PlannerCallRequest,
    PlannerCallResult,
    TruncatedOutputError,
    invoke_planner_call,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _structured_request(prompt: str) -> PlannerCallRequest:
    empty_digest = hashlib.sha256(b"").hexdigest()
    prompt_digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    sizes = {
        "documents": PlannerPromptSegmentSize(
            name="documents", byte_count=0, character_count=0
        ),
        "anchors": PlannerPromptSegmentSize(
            name="anchors", byte_count=0, character_count=0
        ),
        "backlog": PlannerPromptSegmentSize(
            name="backlog", byte_count=0, character_count=0
        ),
        "schema": PlannerPromptSegmentSize(
            name="schema", byte_count=0, character_count=0
        ),
        "dynamic_stage": PlannerPromptSegmentSize(
            name="dynamic_stage",
            byte_count=len(prompt.encode("utf-8")),
            character_count=len(prompt),
        ),
    }
    identities = tuple(
        PlannerInputIdentity(
            name=name,
            algorithm=PlannerDigestAlgorithm.SHA256,
            digest=(
                prompt_digest
                if name in {"rendered_prompt", "dynamic_stage"}
                else empty_digest
            ),
        )
        for name in (
            "rendered_prompt",
            "documents",
            "anchors",
            "backlog",
            "schema",
            "dynamic_stage",
        )
    )
    return PlannerCallRequest(
        logical_call=PlannerLogicalCall(
            identity=PlannerLogicalCallIdentity(
                execution=PlanningExecutionIdentity(
                    execution_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
                ),
                stage="single",
                logical_attempt_no=0,
            ),
            planner=PlannerIdentity(provider="anthropic", model=MODEL_NAME),
            template=PlannerPromptTemplateIdentity(
                stage="single",
                template_name="planner-v1.2.0.md.j2",
                prompt_version="planner-v1.2.0",
                template_sha256="a" * 64,
            ),
            execution_parameters=PlannerExecutionParameters(
                temperature=TEMPERATURE,
                max_output_tokens=MAX_TOKENS,
                streaming=True,
            ),
            input_identities=identities,
            prompt_size=PlannerPayloadSize(
                byte_count=len(prompt.encode("utf-8")),
                character_count=len(prompt),
            ),
            prompt_segments=tuple(sizes.values()),
        )
    )


def _generate(client: AnthropicPlannerClient, prompt: str) -> PlannerCallResult:
    return client.generate(prompt, _structured_request(prompt))


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
    request = _structured_request("hello")

    out = client.generate("hello", request)

    assert out.raw_output == '{"epics": []}'
    assert out.logical_call == request.logical_call
    assert recorder["model"] == MODEL_NAME
    assert recorder["max_tokens"] == MAX_TOKENS
    assert recorder["temperature"] == TEMPERATURE
    assert recorder["api_key"] == "sk-test-key"


@pytest.mark.parametrize("mismatch", ["provider", "model", "parameters"])
def test_generate_rejects_identity_not_used_by_anthropic_before_provider(
    monkeypatch: pytest.MonkeyPatch,
    mismatch: str,
) -> None:
    recorder: dict[str, object] = {}
    _stub_anthropic(monkeypatch, recorder)
    client = AnthropicPlannerClient(api_key="sk-test-key")
    request = _structured_request("hello")

    if mismatch == "provider":
        logical_call = request.logical_call.model_copy(
            update={
                "planner": request.logical_call.planner.model_copy(
                    update={"provider": "not-anthropic"}
                )
            }
        )
        expected_error = "planner identity"
    elif mismatch == "model":
        logical_call = request.logical_call.model_copy(
            update={
                "planner": request.logical_call.planner.model_copy(
                    update={"model": "different-model"}
                )
            }
        )
        expected_error = "planner identity"
    else:
        logical_call = request.logical_call.model_copy(
            update={
                "execution_parameters": (
                    request.logical_call.execution_parameters.model_copy(
                        update={"max_output_tokens": MAX_TOKENS - 1}
                    )
                )
            }
        )
        expected_error = "execution parameters"

    mismatched_request = PlannerCallRequest(logical_call=logical_call)
    with pytest.raises(PlannerCallContractError, match=expected_error):
        client.generate("hello", mismatched_request)

    assert recorder == {}


def test_unknown_or_mismatched_call_identity_fails_before_provider() -> None:
    request = _structured_request("expected prompt")
    calls = 0

    class _Provider:
        def generate(
            self, prompt: str, request: PlannerCallRequest
        ) -> PlannerCallResult:
            nonlocal calls
            calls += 1
            return PlannerCallResult(
                raw_output="unused", logical_call=request.logical_call
            )

    with pytest.raises(PlannerCallContractError, match="fingerprint"):
        invoke_planner_call(_Provider(), prompt="altered! prompt", request=request)
    assert calls == 0

    incomplete = request.logical_call.model_copy(
        update={
            "input_identities": tuple(
                item
                for item in request.logical_call.input_identities
                if item.name != "documents"
            )
        }
    )
    with pytest.raises(PlannerCallContractError, match="input identity"):
        PlannerCallRequest(logical_call=incomplete)
    assert calls == 0

    unknown_stage = request.logical_call.model_copy(
        update={
            "identity": request.logical_call.identity.model_copy(
                update={"stage": "mystery"}
            ),
            "template": request.logical_call.template.model_copy(
                update={"stage": "mystery"}
            ),
        }
    )
    with pytest.raises(PlannerCallContractError, match="unknown planner call stage"):
        PlannerCallRequest(logical_call=unknown_stage)
    assert calls == 0


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
        _generate(AnthropicPlannerClient(api_key="sk-test"), "hello")
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
    _generate(AnthropicPlannerClient(), "x")
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
        _generate(AnthropicPlannerClient(api_key="sk-test"), "x")


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

    out = _generate(AnthropicPlannerClient(api_key="sk-test"), "x")

    assert out.raw_output == '{"epics": []}'
    assert counter["calls"] == 2  # one failed attempt, one success


def test_anthropic_connection_error_is_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An SDK-wrapped connection error (anthropic.APIConnectionError) retries too.
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    counter = _stub_anthropic_failing(
        monkeypatch, error=_FakeAPIConnectionError("conn reset"), fail_times=1
    )

    _generate(AnthropicPlannerClient(api_key="sk-test"), "x")

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
        _generate(AnthropicPlannerClient(api_key="sk-test"), "x")

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
        _generate(AnthropicPlannerClient(api_key="sk-test"), "x")
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
    prompt = 'Reply with the JSON object {"ok": true} and nothing else.'
    output = client.generate(
        prompt,
        _structured_request(prompt),
    )
    assert output.raw_output.strip()
