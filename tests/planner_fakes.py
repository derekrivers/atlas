"""Fake planner clients for pipeline/CLI tests (ATLAS-26 D2).

Every pipeline and CLI test injects one of these; no test ever makes a
real API call. The fakes satisfy the PlannerClient Protocol (generate
only) and carry their own ModelIdentity for the CLI to record.
"""

from __future__ import annotations

from atlas.planning.client import (
    ModelCallError,
    ModelIdentity,
    TruncatedOutputError,
)

FAKE_IDENTITY = ModelIdentity(
    provider="fake",
    model="fake-model-1",
    parameters={"temperature": 0, "max_tokens": 1024},
)


class FakePlannerClient:
    """Returns a canned raw proposal, recording the prompt it was given."""

    def __init__(self, canned: str) -> None:
        self._canned = canned
        self.last_prompt: str | None = None

    def generate(self, prompt: str) -> str:
        self.last_prompt = prompt
        return self._canned


class RaisingPlannerClient:
    """Simulates a model-call failure (network/timeout/API)."""

    def generate(self, prompt: str) -> str:
        raise ModelCallError("simulated model call failure")


class TruncatingPlannerClient:
    """Simulates a token-limit truncation (stop_reason == max_tokens): the
    real client raises this on a cut-off response, carrying the partial
    output. The default partial is deliberately truncated JSON."""

    def __init__(
        self,
        partial: str = '{"epics": [], "tickets": [{"title": "cut off',
        max_tokens: int = 64000,
    ) -> None:
        self._partial = partial
        self._max_tokens = max_tokens

    def generate(self, prompt: str) -> str:
        raise TruncatedOutputError(
            raw_output=self._partial, max_tokens=self._max_tokens
        )
