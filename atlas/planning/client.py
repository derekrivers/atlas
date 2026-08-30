"""Provider-neutral planner model-call seam (ATLAS-26/283, D1/D3).

Planning calls pass prompt text separately from a bounded
``PlannerCallRequest`` telemetry record.  The record contains the durable
execution/logical-call identity and deterministic prompt facts, never prompt
or document content.  Production wires ``AnthropicPlannerClient`` (the SDK is
imported lazily, inside the call); tests wire a fake.  ``ModelIdentity`` still
supplies the PlanRun provenance fields and now also derives the closed
provider-neutral planner/parameter identities required by the call contract.

No Agent-SDK / subscription path here — that is a documented follow-up.
The API key is read from the environment, never logged and never
persisted.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, overload, runtime_checkable

from atlas.core.models.planner_call_telemetry import (
    PlannerExecutionParameters,
    PlannerIdentity,
    PlannerLogicalCall,
)

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


class PlannerCallContractError(PlannerClientError):
    """A structured call was incomplete or changed across the provider seam.

    This is a pre-provider failure when the request/prompt facts disagree, and
    a provider-boundary failure when a result changes orchestration-owned
    identity.  It never falls back to incomplete telemetry.
    """


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
    streaming: bool = False


ANTHROPIC_IDENTITY = ModelIdentity(
    provider="anthropic",
    model=MODEL_NAME,
    parameters={"temperature": TEMPERATURE, "max_tokens": MAX_TOKENS},
    streaming=True,
)


_PARAMETER_NAMES = frozenset(
    {
        "temperature",
        "max_tokens",
        "top_p",
        "top_k",
        "seed",
        "request_timeout_ms",
    }
)
_PROMPT_INPUT_NAMES = frozenset(
    {
        "rendered_prompt",
        "documents",
        "anchors",
        "backlog",
        "schema",
        "dynamic_stage",
    }
)
_PROMPT_SEGMENT_NAMES = _PROMPT_INPUT_NAMES - {"rendered_prompt"}
_KNOWN_CALL_STAGE = re.compile(
    r"^(?:single|epics|dependencies|tickets\.(?:new_epic\.[0-9]+|atlas-e[0-9]+))$"
)


def planner_telemetry_identity(
    identity: ModelIdentity,
) -> tuple[PlannerIdentity, PlannerExecutionParameters]:
    """Translate recorded D3 identity into the closed telemetry vocabulary.

    Unknown settings fail before an execution identity or provider call rather
    than being silently dropped from the exact call identity.
    """

    unknown = sorted(set(identity.parameters) - _PARAMETER_NAMES)
    if unknown:
        raise PlannerCallContractError(
            f"unknown planner execution parameter(s): {unknown}"
        )
    values = identity.parameters
    try:
        return (
            PlannerIdentity(provider=identity.provider, model=identity.model),
            PlannerExecutionParameters(
                temperature=values.get("temperature"),
                max_output_tokens=values.get("max_tokens"),
                top_p=values.get("top_p"),
                top_k=values.get("top_k"),
                seed=values.get("seed"),
                request_timeout_ms=values.get("request_timeout_ms"),
                streaming=identity.streaming,
            ),
        )
    except ValueError as error:
        raise PlannerCallContractError(
            "invalid planner or execution-parameter identity"
        ) from error


@dataclass(frozen=True)
class PlannerCallRequest:
    """Content-free telemetry facts supplied beside one transient prompt."""

    logical_call: PlannerLogicalCall

    def __post_init__(self) -> None:
        if _KNOWN_CALL_STAGE.fullmatch(self.logical_call.identity.stage) is None:
            raise PlannerCallContractError(
                f"unknown planner call stage {self.logical_call.identity.stage!r}"
            )
        if self.logical_call.physical_attempts:
            raise PlannerCallContractError(
                "planner call request cannot pre-assign physical attempts"
            )
        inputs = {item.name for item in self.logical_call.input_identities}
        if inputs != _PROMPT_INPUT_NAMES:
            raise PlannerCallContractError(
                "planner call request has unknown or incomplete input identity"
            )
        segments = {item.name for item in self.logical_call.prompt_segments}
        if segments != _PROMPT_SEGMENT_NAMES:
            raise PlannerCallContractError(
                "planner call request has unknown or incomplete prompt segments"
            )

    def validate_prompt(self, prompt: str) -> None:
        """Prove the transient prompt matches the content-free request facts."""

        import hashlib

        if not isinstance(prompt, str) or not prompt:
            raise PlannerCallContractError("planner prompt must be non-empty text")
        encoded = prompt.encode("utf-8")
        size = self.logical_call.prompt_size
        if size.byte_count != len(encoded) or size.character_count != len(prompt):
            raise PlannerCallContractError(
                "planner prompt size does not match the telemetry request"
            )
        prompt_identity = next(
            item
            for item in self.logical_call.input_identities
            if item.name == "rendered_prompt"
        )
        if (
            prompt_identity.algorithm.value != "sha256"
            or prompt_identity.digest != hashlib.sha256(encoded).hexdigest()
        ):
            raise PlannerCallContractError(
                "planner prompt fingerprint does not match the telemetry request"
            )

    def canonical_bytes(self) -> bytes:
        """Return only the bounded telemetry record, never transient content."""

        return self.logical_call.canonical_bytes()


@dataclass(frozen=True)
class PlannerCallResult:
    """Transient provider text paired with its content-free telemetry record."""

    raw_output: str = field(repr=False)
    logical_call: PlannerLogicalCall

    def __post_init__(self) -> None:
        if not isinstance(self.raw_output, str):
            raise PlannerCallContractError("planner raw output must be text")

    def canonical_telemetry_bytes(self) -> bytes:
        """Serialize result telemetry without the transient provider output."""

        return self.logical_call.canonical_bytes()


@runtime_checkable
class PlannerClient(Protocol):
    """The structured planning seam: transient prompt plus bounded facts."""

    def generate(
        self, prompt: str, request: PlannerCallRequest
    ) -> PlannerCallResult: ...


def invoke_planner_call(
    client: PlannerClient,
    *,
    prompt: str,
    request: PlannerCallRequest,
) -> PlannerCallResult:
    """Validate, invoke, and enforce orchestration-owned result identity.

    A provider may add physical-attempt evidence in its result, but it may not
    change execution, stage, logical attempt, template, exact inputs, sizes,
    planner, or parameters.  Physical numbering therefore remains exclusively
    at the provider boundary.
    """

    request.validate_prompt(prompt)
    result = client.generate(prompt, request)
    if not isinstance(result, PlannerCallResult):
        raise PlannerCallContractError(
            "planner provider returned no structured PlannerCallResult"
        )
    request_facts = result.logical_call.model_copy(update={"physical_attempts": ()})
    if request_facts != request.logical_call:
        raise PlannerCallContractError(
            "planner provider changed orchestration-owned logical-call identity"
        )
    return result


class AnthropicPlannerClient:
    """Concrete client over the Anthropic API (production).

    Reads the key from ``ANTHROPIC_API_KEY`` at construction; the SDK is
    imported lazily inside ``generate`` so importing this module pulls in
    no SDK. The key is held privately and never logged or repr'd.  The
    one-argument overload remains only for the separate learning subsystem's
    text-generation protocols; ``PlannerClient`` exposes only the structured
    two-argument planning call.
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

    @overload
    def generate(self, prompt: str) -> str: ...

    @overload
    def generate(
        self, prompt: str, request: PlannerCallRequest
    ) -> PlannerCallResult: ...

    def generate(
        self,
        prompt: str,
        request: PlannerCallRequest | None = None,
    ) -> str | PlannerCallResult:
        if request is not None:
            request.validate_prompt(prompt)
            expected_planner, expected_parameters = planner_telemetry_identity(
                self.identity
            )
            if request.logical_call.planner != expected_planner:
                raise PlannerCallContractError(
                    "planner identity does not match Anthropic provider settings"
                )
            if request.logical_call.execution_parameters != expected_parameters:
                raise PlannerCallContractError(
                    "planner execution parameters do not match Anthropic "
                    "provider settings"
                )
        raw_output = self._generate_raw(prompt)
        if request is None:
            return raw_output
        return PlannerCallResult(
            raw_output=raw_output,
            logical_call=request.logical_call,
        )

    def _generate_raw(self, prompt: str) -> str:
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
