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
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# Pinned call settings (D3): a single model string, temperature 0, an
# explicit max_tokens. Recorded on every PlanRun via ANTHROPIC_IDENTITY.
MODEL_NAME = "claude-sonnet-4-6"
TEMPERATURE = 0
MAX_TOKENS = 16000

API_KEY_ENV = "ANTHROPIC_API_KEY"


class PlannerClientError(RuntimeError):
    """Base for model-call seam failures."""


class MissingAPIKeyError(PlannerClientError):
    """No API key in the environment (a clean-exit precondition)."""


class ModelCallError(PlannerClientError):
    """The model call failed (network/timeout/API). Clean exit: no raw
    output exists to record, and the failure is transient — retry."""


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

        try:
            client = anthropic.Anthropic(api_key=self._api_key)
            response = client.messages.create(
                model=MODEL_NAME,
                max_tokens=MAX_TOKENS,
                temperature=TEMPERATURE,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as error:  # SDK raises a family of errors
            raise ModelCallError(f"model call failed: {error}") from error
        return "".join(
            getattr(block, "text", "")
            for block in response.content
            if getattr(block, "type", None) == "text"
        )
