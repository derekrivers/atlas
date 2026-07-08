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

import email.message
import io
import json
import os
from pathlib import Path
from typing import Any
from urllib import error as urllib_error

import pytest
from linear_fakes import InMemoryLinearClient

from atlas.linear.client import (
    LINEAR_ERROR_BODY_MAX_LEN,
    LINEAR_HTTP_TIMEOUT_SECONDS,
    LinearAPIError,
    LinearClient,
    LinearGraphQLClient,
    LinearRateLimitError,
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
        self.comments: dict[str, list[dict[str, Any]]] = {}
        self.projects: dict[str, dict[str, Any]] = {}
        self.counter = 0
        self.comment_counter = 0
        # Flip to False to emulate a project id that resolves to `project:
        # null` for the batched pull (ATLAS-148).
        self.project_issues_available = True
        self.states = [
            {"id": "state-unstarted", "name": "Todo", "type": "unstarted"},
            {"id": "state-ready", "name": "Ready for Agent", "type": "unstarted"},
        ]

    def add_project(self, project_id: str, slug_id: str) -> None:
        """Seed a project so the real client's fetch_project (ATLAS-136) can
        resolve it, mirroring the in-memory fake's seed_project."""
        self.projects[project_id] = {"id": project_id, "slugId": slug_id}

    def add_comment(
        self, issue_id: str, body: str, *, comment_id: str | None = None
    ) -> None:
        """Seed a comment so the real client's read-only fetch_comments
        (ATLAS-45) can read it back. Mints a sequential id and a deterministic
        createdAt when not given, mirroring the in-memory fake's seed_comment."""
        if comment_id is None:
            self.comment_counter += 1
            comment_id = f"comment-{self.comment_counter}"
        self.comments.setdefault(issue_id, []).append(
            {"id": comment_id, "body": body, "createdAt": "2026-01-01T00:00:00.000Z"}
        )

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
        if "TeamWorkflowStates" in query:
            # Team-scoped (ATLAS-148): the document queries team(id:).states;
            # an unknown team yields `team: null` (mapped to an empty list).
            if variables["teamId"] != "team-1":
                return {"data": {"team": None}}
            return {"data": {"team": {"states": {"nodes": self.states}}}}
        if "ProjectIssues" in query:
            # The batched pull (ATLAS-148): every stored issue, honestly
            # paginated by $first/$after (the cursor is a stringified offset)
            # so the real client's pagination loop is exercised. Handled
            # BEFORE the bare project query below (its document contains
            # `project(` too). `project_issues_available = False` emulates an
            # unresolvable project (`project: null`).
            if not self.project_issues_available:
                return {"data": {"project": None}}
            nodes = list(self.issues.values())
            start = 0 if variables.get("after") is None else int(variables["after"])
            end = start + variables["first"]
            return {
                "data": {
                    "project": {
                        "issues": {
                            "nodes": nodes[start:end],
                            "pageInfo": {
                                "hasNextPage": end < len(nodes),
                                "endCursor": str(end),
                            },
                        }
                    }
                }
            }
        if "comments" in query:
            # The read-only comment fetch (ATLAS-45). Its document selects
            # `issue(id) { comments { nodes } }`, so it contains `issue(` too --
            # handle it BEFORE the bare issue query below. A missing issue yields
            # `issue: null` (the client maps that to an empty list).
            if variables["id"] not in self.issues:
                return {"data": {"issue": None}}
            nodes = self.comments.get(variables["id"], [])
            return {"data": {"issue": {"comments": {"nodes": nodes}}}}
        if "project(" in query:
            # The A2 preflight resolve (ATLAS-136): an unknown id yields
            # `project: null`, which the client maps to None.
            return {"data": {"project": self.projects.get(variables["id"])}}
        if "issue(" in query:
            return {"data": {"issue": self.issues.get(variables["id"])}}
        raise AssertionError(f"unhandled query: {query}")


def _stub_urlopen(emulator: _Emulator) -> Any:
    def _urlopen(request: Any, *args: Any, **kwargs: Any) -> _Response:
        return _Response(emulator.handle(request))

    return _urlopen


# --- the shared behavioural contract ---------------------------------------


def _run_contract(client: LinearClient, *, team_id: str, project_id: str) -> None:
    # priority is owned but not yet syncable (deferred like labels, ATLAS-42):
    # it is absent from the allow-list, so the payload carries title +
    # description only. ``project_id`` is a required creation scope (ATLAS-135),
    # mirroring ``team_id``: both clients place every created issue in the project.
    created = client.create_issue(
        {"title": "Alpha", "description": "d"}, team_id=team_id, project_id=project_id
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
        client.create_issue(
            {"title": "x", "stateId": "s"}, team_id=team_id, project_id=project_id
        )
    with pytest.raises(UnownedFieldError):
        client.update_issue(created.id, {"title": "x", "stateId": "s"})

    # Team-scoped since ATLAS-148: the states query takes the team id the
    # tick already requires.
    states = client.fetch_workflow_states(team_id)
    assert states
    assert all(isinstance(state, WorkflowState) for state in states)

    # The batched pull (ATLAS-148): one project-scoped read returns the
    # created issue without a per-issue fetch.
    project_issues = client.fetch_project_issues(project_id)
    assert created.id in {issue.id for issue in project_issues}

    # set_state is the ONE sanctioned Atlas -> Linear state write (ATLAS-43):
    # a bare state id moves the issue, distinct from the definition path above.
    moved = client.set_state(created.id, "state-ready")
    assert moved.state_id == "state-ready"
    after_move = client.fetch_issue(created.id)
    assert after_move is not None
    assert after_move.state_id == "state-ready"

    # fetch_comments is read-only (ATLAS-45): an issue with no comments and a
    # nonexistent issue both yield an empty list, never a raise.
    assert client.fetch_comments(created.id) == []
    assert client.fetch_comments("nonexistent") == []

    # fetch_project is read-only (ATLAS-136): an unknown id resolves to None
    # across both clients (the A2 preflight reports that, never a raise).
    assert client.fetch_project("nonexistent-project") is None


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
    _run_contract(contract_client, team_id="team-1", project_id="project-1")


# A (client, seed) pair so the read-only fetch_comments data path is held to the
# same contract across the fake and the stubbed real client -- each backs its own
# comment store, and the seeder writes into it (the real client has no comment
# write, so the test seeds Linear-side, mirroring an agent's comment).
@pytest.fixture(params=["fake", "stub-real"])
def commentable_client(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> tuple[LinearClient, Any]:
    if request.param == "fake":
        fake = InMemoryLinearClient()
        return fake, fake.seed_comment
    emulator = _Emulator()
    monkeypatch.setattr(
        "atlas.linear.client.urllib_request.urlopen", _stub_urlopen(emulator)
    )
    real = LinearGraphQLClient(api_key="sk-test", team_id="team-1")
    return real, emulator.add_comment


def test_fetch_comments_reads_tagged_and_untagged(
    commentable_client: tuple[LinearClient, Any],
) -> None:
    client, seed = commentable_client
    created = client.create_issue(
        {"title": "C", "description": "d"}, team_id="team-1", project_id="project-1"
    )
    assert client.fetch_comments(created.id) == []  # none yet

    seed(created.id, "please atlas:proposed-follow-up split this out", comment_id="c-1")
    seed(created.id, "just an ordinary review note")

    comments = client.fetch_comments(created.id)
    assert len(comments) == 2
    assert comments[0].id == "c-1"
    assert "atlas:proposed-follow-up" in comments[0].body
    assert "atlas:proposed-follow-up" not in comments[1].body
    assert all(comment.created_at for comment in comments)  # provenance carried


# A (client, seed) pair so the fetch_project resolve path (ATLAS-136) is held to
# the same contract across the fake and the stubbed real client -- each backs its
# own project store, and the seeder registers a project in it.
@pytest.fixture(params=["fake", "stub-real"])
def projectable_client(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> tuple[LinearClient, Any]:
    if request.param == "fake":
        fake = InMemoryLinearClient()
        return fake, fake.seed_project
    emulator = _Emulator()
    monkeypatch.setattr(
        "atlas.linear.client.urllib_request.urlopen", _stub_urlopen(emulator)
    )
    real = LinearGraphQLClient(api_key="sk-test", team_id="team-1")
    return real, emulator.add_project


def test_fetch_project_resolves_id_to_slug(
    projectable_client: tuple[LinearClient, Any],
) -> None:
    client, seed = projectable_client
    assert client.fetch_project("proj-uuid") is None  # unknown -> None
    seed("proj-uuid", "atlas-team")
    project = client.fetch_project("proj-uuid")
    assert project is not None
    assert project.id == "proj-uuid"
    assert project.slug_id == "atlas-team"  # the slug Symphony polls by


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
    client.create_issue({"title": "x"}, team_id="team-1", project_id="project-1")
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
        {"title": "x"}, team_id="team-9", project_id="project-9"
    )
    assert captured["body"]["variables"]["input"]["teamId"] == "team-9"


def test_create_sends_project_id(monkeypatch: pytest.MonkeyPatch) -> None:
    # AC-1 (ATLAS-135): create scopes the issue to the configured project, so the
    # issueCreate input carries projectId alongside teamId. Asserted against the
    # captured GraphQL variables, exactly as test_create_sends_team_id does.
    captured: dict[str, Any] = {}
    emulator = _Emulator()

    def _urlopen(request: Any, *args: Any, **kwargs: Any) -> _Response:
        captured["body"] = json.loads(request.data.decode())
        return _Response(emulator.handle(request))

    monkeypatch.setattr("atlas.linear.client.urllib_request.urlopen", _urlopen)
    LinearGraphQLClient(api_key="sk", team_id="team-9").create_issue(
        {"title": "x"}, team_id="team-9", project_id="project-9"
    )
    input_vars = captured["body"]["variables"]["input"]
    assert input_vars["projectId"] == "project-9"  # the project scope crossed
    assert input_vars["teamId"] == "team-9"  # still alongside the team scope


# --- ATLAS-147: error-body capture, timeout, rate-limit detection ------------

# The VERBATIM transport-400 body of the 2026-07-07 rate-limit incident (the
# crash-loop that pinned the request budget at zero): the RATELIMITED detail
# lives ONLY here, never in the status line, and the reset is
# extensions.meta.rateLimitResult.duration in MILLISECONDS. Kept byte-for-byte
# so the parse is proven against what Linear actually sent.
_RATE_LIMIT_INCIDENT_BODY = b'{"errors":[{"message":"Rate limit exceeded. Only 2500 requests are allowed per 1 hour. For more information see our developer docs at: https://linear.app/developers/rate-limiting","extensions":{"type":"ratelimited","code":"RATELIMITED","statusCode":429,"userError":true,"userPresentableMessage":"Rate limit exceeded. Only 2500 requests are allowed per 1 hour. For more information see our developer docs at: https://linear.app/developers/rate-limiting.","meta":{"rateLimitResult":{"allowed":false,"requested":1,"remaining":0,"duration":3600000,"limit":2500}},"http":{"headers":{},"status":400}}}]}'  # noqa: E501


def _http_error(code: int, body: bytes) -> urllib_error.HTTPError:
    """A fake ``urllib`` HTTPError whose ``read()`` yields ``body``, mirroring
    what a real non-2xx response hands the client."""

    return urllib_error.HTTPError(
        "https://api.linear.app/graphql",
        code,
        "Bad Request",
        email.message.Message(),
        io.BytesIO(body),
    )


def _raising_urlopen(monkeypatch: pytest.MonkeyPatch, code: int, body: bytes) -> None:
    def _urlopen(request: Any, *args: Any, **kwargs: Any) -> _Response:
        raise _http_error(code, body)

    monkeypatch.setattr("atlas.linear.client.urllib_request.urlopen", _urlopen)


def test_http_error_message_carries_status_and_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Pre-147 wrong answer: HTTPError fell into the URLError catch and the body
    # was discarded, leaving only an opaque "HTTP Error 400" — undiagnosable.
    _raising_urlopen(monkeypatch, 500, b'{"errors":[{"message":"upstream broke"}]}')
    client = LinearGraphQLClient(api_key="sk", team_id="t")
    with pytest.raises(LinearAPIError, match="HTTP 500") as excinfo:
        client.fetch_issue("i")
    assert "upstream broke" in str(excinfo.value)  # the body crossed into the message
    assert not isinstance(excinfo.value, LinearRateLimitError)


def test_http_error_body_truncated_to_pinned_max(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A pathological body is carried only up to the pinned max, so an error
    # message (and the TickFailure detail derived from it) stays bounded.
    _raising_urlopen(monkeypatch, 502, b"x" * (LINEAR_ERROR_BODY_MAX_LEN + 500))
    client = LinearGraphQLClient(api_key="sk", team_id="t")
    with pytest.raises(LinearAPIError, match="HTTP 502") as excinfo:
        client.fetch_issue("i")
    message = str(excinfo.value)
    assert "x" * LINEAR_ERROR_BODY_MAX_LEN in message
    assert "x" * (LINEAR_ERROR_BODY_MAX_LEN + 1) not in message


def test_execute_passes_module_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    emulator = _Emulator()

    def _urlopen(request: Any, *args: Any, **kwargs: Any) -> _Response:
        captured["timeout"] = kwargs.get("timeout")
        return _Response(emulator.handle(request))

    monkeypatch.setattr("atlas.linear.client.urllib_request.urlopen", _urlopen)
    LinearGraphQLClient(api_key="sk", team_id="t").fetch_workflow_states("team-1")
    assert captured["timeout"] == LINEAR_HTTP_TIMEOUT_SECONDS

    # Source scan: no bare urlopen(request) remains anywhere in atlas/linear/ —
    # every call site passes the module timeout (whitespace-squeezed so a
    # reformatted multi-line call cannot dodge the scan).
    for path in sorted((REPO_ROOT / "atlas" / "linear").glob("*.py")):
        squeezed = "".join(path.read_text(encoding="utf-8").split())
        assert "urlopen(request)" not in squeezed, f"bare urlopen in {path.name}"


# --- ATLAS-148: team-scoped states + batched, paginated project pull ---------


def _capturing_urlopen(emulator: _Emulator, sent: list[dict[str, Any]]) -> Any:
    """A stub ``urlopen`` that records each request's decoded GraphQL payload
    (query + variables) before handing it to the emulator, so query-SHAPE
    assertions read what actually crossed the wire."""

    def _urlopen(request: Any, *args: Any, **kwargs: Any) -> _Response:
        sent.append(json.loads(request.data.decode()))
        return _Response(emulator.handle(request))

    return _urlopen


def test_fetch_workflow_states_query_is_team_scoped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ATLAS-148 AC-3, the query shape: the document queries team(id:).states —
    # NOT the workspace-wide workflowStates the pre-148 client sent, which
    # returned foreign teams' same-named states — and the team id crosses as
    # the $teamId variable.
    emulator = _Emulator()
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "atlas.linear.client.urllib_request.urlopen",
        _capturing_urlopen(emulator, sent),
    )
    client = LinearGraphQLClient(api_key="sk", team_id="team-1")

    states = client.fetch_workflow_states("team-1")

    assert len(sent) == 1
    assert "team(id: $teamId)" in sent[0]["query"]
    assert "workflowStates(" not in sent[0]["query"]  # the workspace-wide form
    assert sent[0]["variables"] == {"teamId": "team-1"}
    assert [s.id for s in states] == [s["id"] for s in emulator.states]

    # An unknown team resolves to `team: null` -> an empty list, never a crash.
    assert client.fetch_workflow_states("team-elsewhere") == []


def test_fetch_project_issues_query_shape_and_single_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ATLAS-148 AC-1, the query shape: one project-scoped, cursor-paginated
    # document carrying id/identifier/title/state per issue; a board within one
    # page costs exactly ONE request.
    emulator = _Emulator()
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "atlas.linear.client.urllib_request.urlopen",
        _capturing_urlopen(emulator, sent),
    )
    client = LinearGraphQLClient(api_key="sk", team_id="team-1")
    created = client.create_issue(
        {"title": "One", "description": "d"}, team_id="team-1", project_id="proj-1"
    )
    sent.clear()

    issues = client.fetch_project_issues("proj-1")

    assert len(sent) == 1  # within one page: exactly one request
    query = sent[0]["query"]
    assert "project(id: $id)" in query
    assert "issues(first: $first, after: $after)" in query
    for field in ("identifier", "id", "title", "state"):
        assert field in query
    assert "pageInfo { hasNextPage endCursor }" in query
    assert sent[0]["variables"]["id"] == "proj-1"
    assert [issue.id for issue in issues] == [created.id]


def test_fetch_project_issues_paginates_until_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Pagination honesty: 5 issues at a page size of 2 cost ceil(5/2) = 3
    # requests, chained by the endCursor, and return every issue exactly once
    # in order. (Live, the page size is 250, so a 110-ticket board is 1 page.)
    emulator = _Emulator()
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "atlas.linear.client.urllib_request.urlopen",
        _capturing_urlopen(emulator, sent),
    )
    monkeypatch.setattr("atlas.linear.client.LINEAR_ISSUES_PAGE_SIZE", 2)
    client = LinearGraphQLClient(api_key="sk", team_id="team-1")
    created_ids = [
        client.create_issue(
            {"title": f"Issue {n}", "description": "d"},
            team_id="team-1",
            project_id="proj-1",
        ).id
        for n in range(5)
    ]
    sent.clear()

    issues = client.fetch_project_issues("proj-1")

    assert [issue.id for issue in issues] == created_ids  # all, once, in order
    assert len(sent) == 3  # ceil(5 / 2)
    assert sent[0]["variables"]["after"] is None  # first page from the start
    assert sent[1]["variables"]["after"] == "2"  # then cursor-chained
    assert sent[2]["variables"]["after"] == "4"


def test_fetch_project_issues_missing_project_yields_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `project: null` (bad id, or the project was deleted) maps to an empty
    # list: the sync loop then treats every joined ticket as issue-missing and
    # leaves statuses unchanged — never a crash.
    emulator = _Emulator()
    monkeypatch.setattr(
        "atlas.linear.client.urllib_request.urlopen", _stub_urlopen(emulator)
    )
    emulator.project_issues_available = False
    client = LinearGraphQLClient(api_key="sk", team_id="team-1")
    assert client.fetch_project_issues("no-such-project") == []


def test_transport_400_ratelimited_raises_typed_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The incident shape: a transport HTTP 400 whose body carries the
    # RATELIMITED errors list. Pre-147 wrong answer: an opaque LinearAPIError
    # with no body, so the scheduler retried at the base cadence and burned the
    # budget flat.
    _raising_urlopen(monkeypatch, 400, _RATE_LIMIT_INCIDENT_BODY)
    client = LinearGraphQLClient(api_key="sk", team_id="t")
    with pytest.raises(LinearRateLimitError, match="HTTP 400") as excinfo:
        client.fetch_issue("i")
    # duration is milliseconds (3600000 observed) -> seconds.
    assert excinfo.value.reset_after_seconds == 3600.0
    assert "RATELIMITED" in str(excinfo.value)  # the body crossed into the message


def test_200_with_ratelimited_errors_raises_typed_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The same detector on the 200-with-errors envelope path.
    payload = {
        "errors": [
            {
                "message": "Rate limit exceeded",
                "extensions": {
                    "code": "RATELIMITED",
                    "meta": {"rateLimitResult": {"duration": 120000}},
                },
            }
        ]
    }

    def _urlopen(request: Any, *args: Any, **kwargs: Any) -> _Response:
        return _Response(payload)

    monkeypatch.setattr("atlas.linear.client.urllib_request.urlopen", _urlopen)
    client = LinearGraphQLClient(api_key="sk", team_id="t")
    with pytest.raises(LinearRateLimitError) as excinfo:
        client.fetch_issue("i")
    assert excinfo.value.reset_after_seconds == 120.0

    # An absent/unparsable reset yields None (the scheduler then backs off at
    # its full cap) — never a parse crash.
    payload = {"errors": [{"message": "x", "extensions": {"code": "RATELIMITED"}}]}
    with pytest.raises(LinearRateLimitError) as excinfo:
        client.fetch_issue("i")
    assert excinfo.value.reset_after_seconds is None


def test_non_ratelimit_400_is_plain_api_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Negative: a 400 whose errors are NOT rate-limit stays a plain
    # LinearAPIError — the typed error never over-claims.
    body = (
        b'{"errors":[{"message":"Argument Validation Error",'
        b'"extensions":{"code":"INVALID_INPUT"}}]}'
    )
    _raising_urlopen(monkeypatch, 400, body)
    client = LinearGraphQLClient(api_key="sk", team_id="t")
    with pytest.raises(LinearAPIError, match="HTTP 400") as excinfo:
        client.fetch_issue("i")
    assert not isinstance(excinfo.value, LinearRateLimitError)


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
    LinearGraphQLClient().create_issue({"title": "x"}, team_id="t", project_id="p")
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
    and bool(os.environ.get("LINEAR_PROJECT_ID"))
)


@pytest.mark.skipif(
    not _LIVE_READY,
    reason=(
        "live Linear test; set ATLAS_LIVE_TESTS=1, LINEAR_API_KEY, "
        "LINEAR_TEAM_ID and LINEAR_PROJECT_ID to run by hand"
    ),
)
def test_live_smoke() -> None:  # pragma: no cover - operator-run only
    client = LinearGraphQLClient()
    team_id = os.environ["LINEAR_TEAM_ID"]
    project_id = os.environ["LINEAR_PROJECT_ID"]
    # priority is owned but not yet syncable (ATLAS-42 deferred it like
    # labels, pending an honest Atlas-int -> inverted Linear 0-4 mapping), so
    # the live payload carries title + description only.
    created = client.create_issue(
        {
            "title": "atlas-41 live smoke (throwaway)",
            "description": "throwaway issue created by the ATLAS-41 live smoke test",
        },
        team_id=team_id,
        project_id=project_id,
    )
    try:
        fetched = client.fetch_issue(created.id)
        assert fetched is not None
        assert fetched.id == created.id
        states = client.fetch_workflow_states(team_id)
        assert states
    finally:
        client.delete_issue(created.id)


# --- ATLAS-45 follow-up read live gate (operator-run) ------------------------
#
# 45 reads real Linear (fetch_comments), so it is NOT a CI-only completion. This
# operator-run smoke (ADR-0008) pins the load-bearing primitive: a real comment
# tagged atlas:proposed-follow-up on a workspace issue is read back through the
# read-only fetch_comments. The operator places the tagged comment on an issue
# and exports its id in LINEAR_FOLLOW_UP_ISSUE_ID; the runbook step that drives
# sync_tick to turn that comment into one inbox stub is the PR evidence. Skipped
# (and never run in CI) otherwise.
_LIVE_FOLLOW_UP_READY = _LIVE_READY and bool(
    os.environ.get("LINEAR_FOLLOW_UP_ISSUE_ID")
)


@pytest.mark.skipif(
    not _LIVE_FOLLOW_UP_READY,
    reason=(
        "live follow-up read test; set ATLAS_LIVE_TESTS=1, LINEAR_API_KEY, "
        "LINEAR_TEAM_ID and LINEAR_FOLLOW_UP_ISSUE_ID (an issue carrying a real "
        "atlas:proposed-follow-up comment) to run by hand"
    ),
)
def test_live_fetch_comments_reads_tagged_comment() -> None:  # pragma: no cover
    client = LinearGraphQLClient()
    issue_id = os.environ["LINEAR_FOLLOW_UP_ISSUE_ID"]
    comments = client.fetch_comments(issue_id)
    assert comments, "expected at least one comment on the configured issue"
    assert any("atlas:proposed-follow-up" in comment.body for comment in comments)
    assert all(comment.id and comment.created_at for comment in comments)


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
    project_id = os.environ["LINEAR_PROJECT_ID"]
    needs_human_state_id = os.environ["LINEAR_NEEDS_HUMAN_STATE_ID"]
    created = client.create_issue(
        {
            "title": "atlas-120 review-cycle route (throwaway)",
            "description": "throwaway issue for the ATLAS-120 live route gate",
        },
        team_id=team_id,
        project_id=project_id,
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
