"""Linear client: fake, stub-transport real client, contract, live smoke.

Two-tier strategy (ATLAS-41 D10-D12):

* the in-memory fake and the real ``LinearGraphQLClient`` (driven over a
  stubbed ``urllib`` -- no network, no secrets) are held to the SAME
  behavioural contract, so the fake is honest confidence rather than a
  drifting stub;
* a single live smoke test runs against the provisioned workspace only when
  ``ATLAS_LIVE_TESTS=1`` and the token are present, creates and deletes a
  throwaway issue, and is cleanly skipped (and never run in CI) otherwise.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib import error as urllib_error

import pytest
from linear_fakes import InMemoryLinearClient

from atlas.linear.client import (
    LinearAPIError,
    LinearClient,
    LinearGraphQLClient,
    MissingLinearTokenError,
    UnownedFieldError,
    WorkflowState,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


# --- a minimal in-memory Linear emulating the GraphQL endpoint --------------


class _Response:
    """Stands in for the ``urlopen`` context manager."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._data = json.dumps(payload).encode()

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self._data


class _Emulator:
    """Just enough Linear to exercise the real client's GraphQL logic."""

    def __init__(self) -> None:
        self.issues: dict[str, dict[str, Any]] = {}
        self.counter = 0
        self.states = [
            {"id": "state-unstarted", "name": "Todo", "type": "unstarted"},
            {"id": "state-ready", "name": "Ready for Agent", "type": "unstarted"},
        ]

    def handle(self, request: Any) -> dict[str, Any]:
        body = json.loads(request.data.decode())
        query: str = body["query"]
        variables: dict[str, Any] = body.get("variables", {})
        if "issueCreate" in query:
            self.counter += 1
            issue_id = f"issue-{self.counter}"
            payload = variables["input"]
            issue = {
                "id": issue_id,
                "title": payload.get("title", ""),
                "state": dict(self.states[0]),
            }
            self.issues[issue_id] = issue
            return {"data": {"issueCreate": {"success": True, "issue": issue}}}
        if "issueUpdate" in query:
            issue = self.issues[variables["id"]]
            payload = variables.get("input", {})
            if "title" in payload:
                issue["title"] = payload["title"]
            if "stateId" in variables:
                # The sanctioned set_state path (ATLAS-43): the mutation pins
                # `input: { stateId: $stateId }`, so $stateId is a top-level
                # variable. Move the issue to that workflow state.
                state = next(s for s in self.states if s["id"] == variables["stateId"])
                issue["state"] = dict(state)
            return {"data": {"issueUpdate": {"success": True, "issue": issue}}}
        if "workflowStates" in query:
            return {"data": {"workflowStates": {"nodes": self.states}}}
        if "issue(" in query:
            return {"data": {"issue": self.issues.get(variables["id"])}}
        raise AssertionError(f"unhandled query: {query}")


def _stub_urlopen(emulator: _Emulator) -> Any:
    def _urlopen(request: Any, *args: Any, **kwargs: Any) -> _Response:
        return _Response(emulator.handle(request))

    return _urlopen


# --- the shared behavioural contract ---------------------------------------


def _run_contract(client: LinearClient, *, team_id: str) -> None:
    # priority is owned but not yet syncable (deferred like labels, ATLAS-42):
    # it is absent from the allow-list, so the payload carries title +
    # description only.
    created = client.create_issue(
        {"title": "Alpha", "description": "d"}, team_id=team_id
    )
    assert created.id
    assert created.title == "Alpha"

    fetched = client.fetch_issue(created.id)
    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.title == "Alpha"

    updated = client.update_issue(created.id, {"title": "Beta"})
    assert updated.title == "Beta"
    refetched = client.fetch_issue(created.id)
    assert refetched is not None
    assert refetched.title == "Beta"

    assert client.fetch_issue("nonexistent") is None

    # The allow-list is enforced at the client: a non-owned key cannot cross
    # the definition path -- not on create, not on update.
    with pytest.raises(UnownedFieldError):
        client.create_issue({"title": "x", "stateId": "s"}, team_id=team_id)
    with pytest.raises(UnownedFieldError):
        client.update_issue(created.id, {"title": "x", "stateId": "s"})

    states = client.fetch_workflow_states()
    assert states
    assert all(isinstance(state, WorkflowState) for state in states)

    # set_state is the ONE sanctioned Atlas -> Linear state write (ATLAS-43):
    # a bare state id moves the issue, distinct from the definition path above.
    moved = client.set_state(created.id, "state-ready")
    assert moved.state_id == "state-ready"
    after_move = client.fetch_issue(created.id)
    assert after_move is not None
    assert after_move.state_id == "state-ready"


@pytest.fixture(params=["fake", "stub-real"])
def contract_client(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> LinearClient:
    if request.param == "fake":
        return InMemoryLinearClient()
    emulator = _Emulator()
    monkeypatch.setattr(
        "atlas.linear.client.urllib_request.urlopen", _stub_urlopen(emulator)
    )
    return LinearGraphQLClient(api_key="sk-test", team_id="team-1")


def test_clients_satisfy_the_same_contract(contract_client: LinearClient) -> None:
    _run_contract(contract_client, team_id="team-1")


# --- real-client specifics --------------------------------------------------


def test_auth_header_is_raw_key_without_bearer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str | None] = {}
    emulator = _Emulator()

    def _urlopen(request: Any, *args: Any, **kwargs: Any) -> _Response:
        captured["auth"] = request.get_header("Authorization")
        return _Response(emulator.handle(request))

    monkeypatch.setattr("atlas.linear.client.urllib_request.urlopen", _urlopen)
    client = LinearGraphQLClient(api_key="sk-raw-key", team_id="team-1")
    client.create_issue({"title": "x"}, team_id="team-1")
    assert captured["auth"] == "sk-raw-key"  # personal key, raw
    assert "Bearer" not in (captured["auth"] or "")


def test_create_sends_team_id(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    emulator = _Emulator()

    def _urlopen(request: Any, *args: Any, **kwargs: Any) -> _Response:
        captured["body"] = json.loads(request.data.decode())
        return _Response(emulator.handle(request))

    monkeypatch.setattr("atlas.linear.client.urllib_request.urlopen", _urlopen)
    LinearGraphQLClient(api_key="sk", team_id="team-9").create_issue(
        {"title": "x"}, team_id="team-9"
    )
    assert captured["body"]["variables"]["input"]["teamId"] == "team-9"


def test_graphql_errors_become_linear_api_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _urlopen(request: Any, *args: Any, **kwargs: Any) -> _Response:
        return _Response({"errors": [{"message": "boom"}]})

    monkeypatch.setattr("atlas.linear.client.urllib_request.urlopen", _urlopen)
    client = LinearGraphQLClient(api_key="sk", team_id="t")
    with pytest.raises(LinearAPIError, match="GraphQL errors"):
        client.fetch_issue("i")


def test_transport_error_becomes_linear_api_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _urlopen(request: Any, *args: Any, **kwargs: Any) -> _Response:
        raise urllib_error.URLError("network down")

    monkeypatch.setattr("atlas.linear.client.urllib_request.urlopen", _urlopen)
    client = LinearGraphQLClient(api_key="sk", team_id="t")
    with pytest.raises(LinearAPIError, match="request failed"):
        client.fetch_issue("i")


def test_missing_token_is_typed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    with pytest.raises(MissingLinearTokenError, match="LINEAR_API_KEY"):
        LinearGraphQLClient()


def test_reads_key_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LINEAR_API_KEY", "sk-env-key")
    captured: dict[str, str | None] = {}
    emulator = _Emulator()

    def _urlopen(request: Any, *args: Any, **kwargs: Any) -> _Response:
        captured["auth"] = request.get_header("Authorization")
        return _Response(emulator.handle(request))

    monkeypatch.setattr("atlas.linear.client.urllib_request.urlopen", _urlopen)
    LinearGraphQLClient().create_issue({"title": "x"}, team_id="t")
    assert captured["auth"] == "sk-env-key"


def test_key_is_never_in_repr() -> None:
    client = LinearGraphQLClient(api_key="sk-super-secret", team_id="t")
    assert "sk-super-secret" not in repr(client)


# --- no-live-call guarantees ------------------------------------------------


def test_ci_runs_no_live_call() -> None:
    # CI sets neither the token nor the live flag, so the smoke test below is
    # always skipped on the default path.
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "LINEAR_API_KEY" not in ci
    assert "ATLAS_LIVE_TESTS" not in ci


_LIVE_READY = (
    os.environ.get("ATLAS_LIVE_TESTS") == "1"
    and bool(os.environ.get("LINEAR_API_KEY"))
    and bool(os.environ.get("LINEAR_TEAM_ID"))
)


@pytest.mark.skipif(
    not _LIVE_READY,
    reason=(
        "live Linear test; set ATLAS_LIVE_TESTS=1, LINEAR_API_KEY and "
        "LINEAR_TEAM_ID to run by hand"
    ),
)
def test_live_smoke() -> None:  # pragma: no cover - operator-run only
    client = LinearGraphQLClient()
    team_id = os.environ["LINEAR_TEAM_ID"]
    # priority is owned but not yet syncable (ATLAS-42 deferred it like
    # labels, pending an honest Atlas-int -> inverted Linear 0-4 mapping), so
    # the live payload carries title + description only.
    created = client.create_issue(
        {
            "title": "atlas-41 live smoke (throwaway)",
            "description": "throwaway issue created by the ATLAS-41 live smoke test",
        },
        team_id=team_id,
    )
    try:
        fetched = client.fetch_issue(created.id)
        assert fetched is not None
        assert fetched.id == created.id
        states = client.fetch_workflow_states()
        assert states
    finally:
        client.delete_issue(created.id)


# --- ATLAS-120 review-cycling live route gate (operator-run) -----------------
#
# 120 is NOT a CI-only completion: it writes a real Linear state. This is the
# operator-run live evidence (ADR-0008) that the sanctioned set_state route
# reaches a real Needs-Human state while the general status-write path stays
# shut. Needs the operator's unique Needs-Human state id in
# LINEAR_NEEDS_HUMAN_STATE_ID; skipped (and never run in CI) otherwise. Driving a
# ticket past 3 changes_requested -> pr_open round trips through sync_tick in the
# real workspace is the operator runbook step the PR evidence records; this test
# pins the load-bearing primitive that step relies on.
_LIVE_NEEDS_HUMAN_READY = _LIVE_READY and bool(
    os.environ.get("LINEAR_NEEDS_HUMAN_STATE_ID")
)


@pytest.mark.skipif(
    not _LIVE_NEEDS_HUMAN_READY,
    reason=(
        "live review-cycling route test; set ATLAS_LIVE_TESTS=1, LINEAR_API_KEY, "
        "LINEAR_TEAM_ID and LINEAR_NEEDS_HUMAN_STATE_ID to run by hand"
    ),
)
def test_live_review_cycle_route_to_needs_human() -> None:  # pragma: no cover
    client = LinearGraphQLClient()
    team_id = os.environ["LINEAR_TEAM_ID"]
    needs_human_state_id = os.environ["LINEAR_NEEDS_HUMAN_STATE_ID"]
    created = client.create_issue(
        {
            "title": "atlas-120 review-cycle route (throwaway)",
            "description": "throwaway issue for the ATLAS-120 live route gate",
        },
        team_id=team_id,
    )
    try:
        # The sanctioned move: set_state lands the real Needs-Human state.
        moved = client.set_state(created.id, needs_human_state_id)
        assert moved.state_id == needs_human_state_id
        # The general status-write path stays blocked: a definition update cannot
        # carry a state, so even a buggy caller cannot route through it.
        with pytest.raises(UnownedFieldError):
            client.update_issue(created.id, {"stateId": needs_human_state_id})
    finally:
        client.delete_issue(created.id)
