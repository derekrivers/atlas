"""Linear client boundary (ATLAS-41).

The atlas-side ``LinearClient`` protocol and its concrete GraphQL
implementation. Atlas core never imports this module (it is a layer above
``atlas.core``; the import-linter spine enforces the separation -- ADR-0006,
D3). Transport is request/response over Linear's GraphQL API; there is no
webhook receiver (ADR-0008, D2). The polling/reconcile loop is ATLAS-42 and
is deliberately not here.

The API token and team id are read from the environment at construction,
held privately, and never logged or ``repr``'d (D13). The deterministic
test suite never touches this client's network path: tests inject the
in-memory fake (``tests/linear_fakes.py``) or stub ``urllib`` (the contract
test), so CI runs with no network and no secrets.

Linear API (confirmed against the current developer docs, June 2026):
endpoint ``https://api.linear.app/graphql``; a personal API key is sent in
the ``Authorization`` header as the raw key (no ``Bearer`` prefix -- that
form is for OAuth access tokens). ``issueCreate``/``issueUpdate`` return
``{ success, issue { id title state { id name type } } }``; a WorkflowState
carries a stable ``id``, a customizable ``name``, and a fixed ``type`` enum
(``triage|backlog|unstarted|started|completed|cancelled``).
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable
from urllib import error as urllib_error
from urllib import request as urllib_request

from atlas.linear.ownership import OWNED_LINEAR_INPUT_KEYS

API_URL = "https://api.linear.app/graphql"
API_KEY_ENV = "LINEAR_API_KEY"
TEAM_ID_ENV = "LINEAR_TEAM_ID"
# The Linear project that scopes issue creation (ATLAS-135). This is the
# project's ``id`` (a UUID), NOT its ``slugId``: Symphony polls issues by
# ``project.slugId`` (the ``project_slug`` in WORKFLOW.md) and Atlas creates
# them into ``project.id`` (here) -- two different fields of the SAME Linear
# project, so an issue created with this id is visible to Symphony's poll. Paste
# the UUID, never the slug. Sourced at the CLI boundary alongside the team id.
PROJECT_ID_ENV = "LINEAR_PROJECT_ID"


class LinearClientError(RuntimeError):
    """Base for Linear client failures."""


class MissingLinearTokenError(LinearClientError):
    """No Linear API token in the environment (a clean-exit precondition)."""


class LinearAPIError(LinearClientError):
    """A Linear GraphQL request failed (transport, HTTP, or GraphQL errors)."""


class UnownedFieldError(ValueError):
    """A definition payload carried a field outside the Atlas -> Linear
    allow-list (ATLAS-41). Enforced at the client so a non-owned field is
    mechanically incapable of crossing, not merely unmapped."""


@dataclass(frozen=True)
class LinearIssue:
    """The only issue shape that crosses the boundary.

    ``state_type`` is carried for the status-map's load-time validation
    (ownership.py ``validate_against_states``, D7); it is never a lookup key.
    """

    id: str
    title: str
    state_id: str | None
    state_name: str | None
    state_type: str | None


@dataclass(frozen=True)
class WorkflowState:
    id: str
    name: str
    type: str


@dataclass(frozen=True)
class LinearProject:
    """The minimal project shape the A2 preflight check needs (ATLAS-136).

    Carries the two identifiers of the SAME Linear project that the
    integration straddles: ``id`` (the UUID Atlas creates issues into, the
    ``LINEAR_PROJECT_ID``) and ``slug_id`` (the ``slugId`` Symphony polls by,
    the ``tracker.project_slug`` in WORKFLOW.md). One ``fetch_project`` read
    proves the UUID resolves AND lets the preflight assert the two are aligned,
    so an issue Atlas creates is visible to Symphony's project-scoped poll."""

    id: str
    slug_id: str


@dataclass(frozen=True)
class LinearComment:
    """One issue comment, the only comment shape that crosses the boundary
    (ATLAS-45). Carries the minimum the follow-up scan needs: the stable ``id``
    (the dedup key recorded in each inbox stub), the verbatim ``body`` (scanned
    for the ``atlas:proposed-follow-up`` tag and reproduced into the stub), and
    ``created_at`` (the ISO string Linear returns, carried for provenance; the
    scan logic never parses it). Reading comments is the one new capability and
    it is read-only -- no comment write crosses Atlas -> Linear."""

    id: str
    body: str
    created_at: str


@runtime_checkable
class LinearClient(Protocol):
    """The atlas-side Linear boundary: the minimal request/response
    operations ATLAS-41 needs. The reconcile loop that calls them on a
    cadence is ATLAS-42."""

    def create_issue(
        self, definition: Mapping[str, Any], *, team_id: str, project_id: str
    ) -> LinearIssue:
        """Create an issue from an owned definition payload (Atlas -> Linear).

        ``project_id`` is a required creation scope, mirroring ``team_id``: every
        issue lands in the configured Linear project (its ``id``/UUID) so it is
        visible to Symphony's project-scoped poll (ATLAS-135). Like ``teamId`` it
        is a creation-scope value, NOT a definition field -- it is added outside
        ``definition`` and stays out of ``OWNED_LINEAR_INPUT_KEYS``."""
        ...

    def update_issue(self, issue_id: str, definition: Mapping[str, Any]) -> LinearIssue:
        """Update an issue's owned definition fields (Atlas -> Linear)."""
        ...

    def set_state(self, issue_id: str, state_id: str) -> LinearIssue:
        """Write an issue's workflow state (Atlas -> Linear; ATLAS-43).

        The ONE sanctioned outbound status write: the PM Engine's readiness
        promotion to ``Ready for Agent``. It takes a bare ``state_id`` and
        builds the ``{stateId: ...}`` input itself, so it can carry no
        definition field -- ``update_issue`` / ``definition_payload`` remain
        mechanically incapable of crossing a state, and ``stateId`` stays out
        of ``OWNED_LINEAR_INPUT_KEYS``. Used solely for this one promotion."""
        ...

    def fetch_issue(self, issue_id: str) -> LinearIssue | None:
        """Fetch one issue for the status direction; ``None`` if absent."""
        ...

    def fetch_workflow_states(self) -> list[WorkflowState]:
        """The workspace's workflow states, for status-map validation (D7)."""
        ...

    def fetch_project(self, project_id: str) -> LinearProject | None:
        """Resolve a Linear project by its ``id`` (UUID) for the A2 preflight
        check (ATLAS-136). Returns the project's ``{id, slug_id}`` or ``None``
        when the id resolves to no project. Read-only: adds no mutation and
        leaves ``OWNED_LINEAR_INPUT_KEYS`` untouched."""
        ...

    def fetch_comments(self, issue_id: str) -> list[LinearComment]:
        """Read an issue's comments for the follow-up scan (Linear -> Atlas;
        ATLAS-45). Read-only: the PM Engine reads agent-written comments and
        never writes one back, so this adds no mutation and leaves
        ``OWNED_LINEAR_INPUT_KEYS`` untouched. Empty list if the issue is
        absent or has no comments."""
        ...


def reject_unowned_keys(definition: Mapping[str, Any]) -> None:
    """Defence in depth: refuse any definition key outside the allow-list, so
    even a buggy caller cannot push a non-owned field (e.g. ``stateId``)
    Atlas -> Linear. The owned set is the single source of truth in
    ownership.py."""

    unowned = set(definition) - OWNED_LINEAR_INPUT_KEYS
    if unowned:
        raise UnownedFieldError(
            f"definition payload carries non-owned field(s) {sorted(unowned)}; "
            f"only {sorted(OWNED_LINEAR_INPUT_KEYS)} may cross Atlas -> Linear"
        )


# GraphQL documents. Every issue selection returns the same fragment so the
# DTO is assembled identically on create, update, and fetch.
_ISSUE_FIELDS = "id title state { id name type }"
_CREATE_MUTATION = (
    "mutation IssueCreate($input: IssueCreateInput!) { "
    f"issueCreate(input: $input) {{ success issue {{ {_ISSUE_FIELDS} }} }} }}"
)
_UPDATE_MUTATION = (
    "mutation IssueUpdate($id: String!, $input: IssueUpdateInput!) { "
    f"issueUpdate(id: $id, input: $input) {{ success issue {{ {_ISSUE_FIELDS} }} }} }}"
)
# The sanctioned state write (ATLAS-43). A dedicated mutation that builds the
# `stateId` input itself: it never accepts a definition dict, so it cannot
# carry title/description/etc. This is the only document that sends `stateId`.
_SET_STATE_MUTATION = (
    "mutation IssueSetState($id: String!, $stateId: String!) { "
    f"issueUpdate(id: $id, input: {{ stateId: $stateId }}) "
    f"{{ success issue {{ {_ISSUE_FIELDS} }} }} }}"
)
_ISSUE_QUERY = f"query Issue($id: String!) {{ issue(id: $id) {{ {_ISSUE_FIELDS} }} }}"
# Read-only comment fetch (ATLAS-45). Every comment selection returns the same
# fragment so the DTO is assembled identically. `first: 250` covers any
# realistic comment thread, mirroring the workflow-states query's bound.
_COMMENT_FIELDS = "id body createdAt"
_COMMENTS_QUERY = (
    "query IssueComments($id: String!) { "
    f"issue(id: $id) {{ comments(first: 250) {{ nodes {{ {_COMMENT_FIELDS} }} }} }} }}"
)
# `first: 250` covers any realistic workspace; paginating beyond it is an
# ATLAS-42 concern, not the boundary's.
_STATES_QUERY = (
    "query WorkflowStates { workflowStates(first: 250) { nodes { id name type } } }"
)
# The A2 preflight read (ATLAS-136): resolve a project by its UUID to its
# slugId. A nonexistent id returns `project: null` (mapped to None).
_PROJECT_QUERY = "query Project($id: String!) { project(id: $id) { id slugId } }"
_DELETE_MUTATION = (
    "mutation IssueDelete($id: String!) { issueDelete(id: $id) { success } }"
)


def _issue_from_node(node: Mapping[str, Any]) -> LinearIssue:
    state = node.get("state")
    return LinearIssue(
        id=node["id"],
        title=node["title"],
        state_id=state["id"] if state else None,
        state_name=state["name"] if state else None,
        state_type=state["type"] if state else None,
    )


def _comment_from_node(node: Mapping[str, Any]) -> LinearComment:
    return LinearComment(
        id=node["id"],
        body=node["body"],
        created_at=node["createdAt"],
    )


class LinearGraphQLClient:
    """Concrete ``LinearClient`` over Linear's GraphQL API (production).

    Uses the stdlib ``urllib`` transport (no third-party HTTP dependency).
    The key is read from ``LINEAR_API_KEY`` at construction and never
    exposed; the team id (``LINEAR_TEAM_ID``) scopes issue creation.
    """

    def __init__(
        self, *, api_key: str | None = None, team_id: str | None = None
    ) -> None:
        key = api_key if api_key is not None else os.environ.get(API_KEY_ENV)
        if not key:
            raise MissingLinearTokenError(
                f"{API_KEY_ENV} is not set; export it to use the live Linear client"
            )
        self._api_key = key
        self._team_id = team_id if team_id is not None else os.environ.get(TEAM_ID_ENV)

    def __repr__(self) -> str:  # never expose the key
        return "LinearGraphQLClient(api_key=***)"

    def _execute(self, query: str, variables: Mapping[str, Any]) -> dict[str, Any]:
        payload = json.dumps({"query": query, "variables": dict(variables)}).encode()
        request = urllib_request.Request(
            API_URL,
            data=payload,
            method="POST",
            headers={
                "Authorization": self._api_key,  # personal key: raw, no Bearer
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib_request.urlopen(request) as response:
                body = json.loads(response.read().decode())
        except (urllib_error.URLError, OSError) as error:
            raise LinearAPIError(f"Linear API request failed: {error}") from error
        except json.JSONDecodeError as error:
            raise LinearAPIError(f"Linear API returned non-JSON: {error}") from error
        if body.get("errors"):
            raise LinearAPIError(f"Linear GraphQL errors: {body['errors']}")
        data = body.get("data")
        if not isinstance(data, dict):
            raise LinearAPIError("Linear GraphQL response had no data")
        return data

    def create_issue(
        self, definition: Mapping[str, Any], *, team_id: str, project_id: str
    ) -> LinearIssue:
        reject_unowned_keys(definition)
        data = self._execute(
            _CREATE_MUTATION,
            {"input": {**definition, "teamId": team_id, "projectId": project_id}},
        )
        return _issue_from_node(data["issueCreate"]["issue"])

    def update_issue(self, issue_id: str, definition: Mapping[str, Any]) -> LinearIssue:
        reject_unowned_keys(definition)
        data = self._execute(
            _UPDATE_MUTATION, {"id": issue_id, "input": dict(definition)}
        )
        return _issue_from_node(data["issueUpdate"]["issue"])

    def set_state(self, issue_id: str, state_id: str) -> LinearIssue:
        # The sanctioned promotion write (ATLAS-43): the input is built here
        # from the bare state id, so no definition field can ride along.
        data = self._execute(_SET_STATE_MUTATION, {"id": issue_id, "stateId": state_id})
        return _issue_from_node(data["issueUpdate"]["issue"])

    def fetch_issue(self, issue_id: str) -> LinearIssue | None:
        data = self._execute(_ISSUE_QUERY, {"id": issue_id})
        node = data.get("issue")
        return _issue_from_node(node) if node else None

    def fetch_workflow_states(self) -> list[WorkflowState]:
        data = self._execute(_STATES_QUERY, {})
        return [
            WorkflowState(id=node["id"], name=node["name"], type=node["type"])
            for node in data["workflowStates"]["nodes"]
        ]

    def fetch_project(self, project_id: str) -> LinearProject | None:
        # Read-only A2 preflight resolve (ATLAS-136): a missing project yields
        # `project: null`, which maps to None (the preflight reports that as a
        # failing finding, never a crash).
        data = self._execute(_PROJECT_QUERY, {"id": project_id})
        node = data.get("project")
        if not node:
            return None
        return LinearProject(id=node["id"], slug_id=node["slugId"])

    def fetch_comments(self, issue_id: str) -> list[LinearComment]:
        # Read-only (ATLAS-45): selects the issue's comment connection and
        # assembles a DTO per node. A missing issue (or one with no comments)
        # yields an empty list, never a raise -- the scan simply finds nothing.
        data = self._execute(_COMMENTS_QUERY, {"id": issue_id})
        issue = data.get("issue")
        if not issue:
            return []
        return [_comment_from_node(node) for node in issue["comments"]["nodes"]]

    def delete_issue(self, issue_id: str) -> bool:
        """Delete (trash) an issue. Not part of the ``LinearClient`` boundary
        protocol -- it exists for the live smoke test's cleanup, never the
        sync loop."""

        data = self._execute(_DELETE_MUTATION, {"id": issue_id})
        return bool(data["issueDelete"]["success"])
