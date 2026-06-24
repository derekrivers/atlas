"""Planner model-call seam (ATLAS-26, D1/D3).

The pipeline depends only on the ``PlannerClient`` Protocol — a single
``generate(prompt) -> str`` — so it never imports an SDK. Production wires
``AnthropicPlannerClient`` (the SDK is imported lazily, inside the call);
tests wire a fake. The model identity (provider, model, parameters) is a
separate ``ModelIdentity`` recorded on the PlanRun, so a run is
reproducible-by-record (D3); it is deliberately not part of the call
Protocol.

No Agent-SDK / subscription path here — that is a documented follow-up.
The API key is read from the environment, never logged and never
persisted.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# Pinned call settings (D3): a single model string, temperature 0, an
# explicit max_tokens. Recorded on every PlanRun via ANTHROPIC_IDENTITY.
# MAX_TOKENS is the model's maximum output (claude-sonnet-4-6: 64K), raised
# from 16000 after a full-corpus proposal truncated there (ATLAS-101). A
# fixed ceiling is still finite — a large enough corpus is detected and
# reported as truncation (TruncatedOutputError), not parsed as broken JSON.
MODEL_NAME = "claude-sonnet-4-6"
TEMPERATURE = 0
MAX_TOKENS = 64000

# Bounded retry on a TRANSIENT connection failure (a mid-stream socket drop —
# "peer closed connection without sending complete message body / incomplete
# chunked read"). The SDK's own retry covers request establishment, not a body
# drop raised during get_final_message(), so a long streaming generation that
# the network truncates would otherwise abort the whole call. This bites the
# staged path hardest: it makes one call per epic (plus epics + dependencies),
# so the per-call drop probability compounds and a single blip kills the run.
# Only transient transport errors retry — TruncatedOutputError is a recorded
# outcome (ATLAS-101) and a bad key / bad request must fail immediately. Named
# constants, not config (D1): 3 attempts, 1s base, exponential backoff.
MAX_CALL_ATTEMPTS = 3
RETRY_BACKOFF_BASE_SECONDS = 1.0

API_KEY_ENV = "ANTHROPIC_API_KEY"


class PlannerClientError(RuntimeError):
    """Base for model-call seam failures."""


class MissingAPIKeyError(PlannerClientError):
    """No API key in the environment (a clean-exit precondition)."""


class ModelCallError(PlannerClientError):
    """The model call failed (network/timeout/API). Clean exit: no raw
    output exists to record, and the failure is transient — retry."""


class TruncatedOutputError(PlannerClientError):
    """The model hit the output token limit (stop_reason == max_tokens): the
    response is cut off mid-content. A recorded outcome, not a clean exit —
    the partial output is carried so its hash preserves the provenance chain
    (ATLAS-101). Distinct from ModelCallError so the pipeline records a
    specific truncation reason rather than the generic JSON parse failure."""

    def __init__(self, raw_output: str, max_tokens: int) -> None:
        super().__init__(
            f"model output truncated at the token limit (max_tokens={max_tokens})"
        )
        self.raw_output = raw_output
        self.max_tokens = max_tokens


@dataclass(frozen=True)
class ModelIdentity:
    """What a run recorded as the model and its call settings (D3)."""

    provider: str
    model: str
    parameters: dict[str, Any] = field(default_factory=dict)


ANTHROPIC_IDENTITY = ModelIdentity(
    provider="anthropic",
    model=MODEL_NAME,
    parameters={"temperature": TEMPERATURE, "max_tokens": MAX_TOKENS},
)


@runtime_checkable
class PlannerClient(Protocol):
    """The model-call seam: one method, no SDK coupling (D1)."""

    def generate(self, prompt: str) -> str: ...


class AnthropicPlannerClient:
    """Concrete client over the Anthropic API (production).

    Reads the key from ``ANTHROPIC_API_KEY`` at construction; the SDK is
    imported lazily inside ``generate`` so importing this module pulls in
    no SDK. The key is held privately and never logged or repr'd.
    """

    def __init__(self, *, api_key: str | None = None) -> None:
        key = api_key if api_key is not None else os.environ.get(API_KEY_ENV)
        if not key:
            raise MissingAPIKeyError(
                f"{API_KEY_ENV} is not set; export it to run `atlas plan` "
                "against the live model"
            )
        self._api_key = key

    @property
    def identity(self) -> ModelIdentity:
        return ANTHROPIC_IDENTITY

    def __repr__(self) -> str:  # never expose the key
        return "AnthropicPlannerClient(api_key=***)"

    def generate(self, prompt: str) -> str:
        import anthropic  # lazy: keeps the SDK out of the import path
        import httpx  # anthropic's transport; its errors surface mid-stream

        # A transient transport failure is retried; everything else is
        # classified at the boundary. anthropic.APIConnectionError covers a
        # SDK-wrapped drop; httpx.TransportError covers the raw mid-stream
        # case (the reported "incomplete chunked read"), which the streaming
        # helper does not re-wrap. TruncatedOutputError is caught first and
        # re-raised unchanged (ATLAS-101) — a recorded outcome, not a transient
        # failure — so the broad handler below can never wrap it.
        retryable: tuple[type[BaseException], ...] = (httpx.TransportError,)
        conn_error = getattr(anthropic, "APIConnectionError", None)
        if conn_error is not None:
            retryable = (conn_error, httpx.TransportError)

        last_error: BaseException | None = None
        for attempt in range(MAX_CALL_ATTEMPTS):
            try:
                return self._stream_once(anthropic, prompt)
            except TruncatedOutputError:
                raise  # a recorded outcome (ATLAS-101), never a retry
            except retryable as error:
                last_error = error
                if attempt + 1 < MAX_CALL_ATTEMPTS:
                    time.sleep(RETRY_BACKOFF_BASE_SECONDS * (2**attempt))
                    continue
                raise ModelCallError(
                    f"model call failed after {MAX_CALL_ATTEMPTS} attempts: {error}"
                ) from error
            except Exception as error:  # non-transient SDK error: fail at once
                raise ModelCallError(f"model call failed: {error}") from error
        # Unreachable: the loop returns, retries, or raises every iteration.
        raise ModelCallError(f"model call failed: {last_error}")

    def _stream_once(self, anthropic: Any, prompt: str) -> str:
        # Streaming, not messages.create: the SDK refuses non-streaming
        # requests it estimates will exceed ~10 min, which a 64K max_tokens
        # call can trip — and the final message exposes stop_reason so a
        # token-limit cutoff is detected, not silently returned (ATLAS-101).
        # Transport errors propagate raw so generate() can classify/retry them.
        client = anthropic.Anthropic(api_key=self._api_key)
        with client.messages.stream(
            model=MODEL_NAME,
            max_tokens=MAX_TOKENS,
            temperature=TEMPERATURE,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            message = stream.get_final_message()
        # Assembled identically to the former non-streaming path, so the
        # hash the pipeline takes over this text is byte-identical.
        text = "".join(
            getattr(block, "text", "")
            for block in message.content
            if getattr(block, "type", None) == "text"
        )
        if message.stop_reason == "max_tokens":
            raise TruncatedOutputError(raw_output=text, max_tokens=MAX_TOKENS)
        return text
