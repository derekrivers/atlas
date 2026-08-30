"""Fake planner clients for pipeline/CLI tests (ATLAS-26 D2).

Every pipeline and CLI test injects one of these; no test ever makes a
real API call. The fakes satisfy the structured PlannerClient Protocol and
carry their own ModelIdentity for the CLI to record.
"""

from __future__ import annotations

from atlas.planning.client import (
    ModelCallError,
    ModelIdentity,
    PlannerCallRequest,
    PlannerCallResult,
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
        self.requests: list[PlannerCallRequest] = []
        self.results: list[PlannerCallResult] = []

    def generate(self, prompt: str, request: PlannerCallRequest) -> PlannerCallResult:
        self.last_prompt = prompt
        self.requests.append(request)
        result = PlannerCallResult(
            raw_output=self._canned,
            logical_call=request.logical_call,
        )
        self.results.append(result)
        return result


class RaisingPlannerClient:
    """Simulates a model-call failure (network/timeout/API)."""

    def generate(self, prompt: str, request: PlannerCallRequest) -> PlannerCallResult:
        raise ModelCallError("simulated model call failure")


class MustNotBeCalledClient:
    """Proves a path performs zero generation calls (ATLAS-153): any
    generate() call is an immediate, attributable test failure."""

    def generate(self, prompt: str, request: PlannerCallRequest) -> PlannerCallResult:
        raise AssertionError(
            "PlannerClient.generate was called on a path that must not "
            "call the model (ATLAS-153)"
        )


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

    def generate(self, prompt: str, request: PlannerCallRequest) -> PlannerCallResult:
        raise TruncatedOutputError(
            raw_output=self._partial, max_tokens=self._max_tokens
        )
