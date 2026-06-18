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


@runtime_checkable
class LinearClient(Protocol):
    """The atlas-side Linear boundary: the minimal request/response
    operations ATLAS-41 needs. The reconcile loop that calls them on a
    cadence is ATLAS-42."""

    def create_issue(
        self, definition: Mapping[str, Any], *, team_id: str
    ) -> LinearIssue:
        """Create an issue from an owned definition payload (Atlas -> Linear)."""
        ...

    def update_issue(self, issue_id: str, definition: Mapping[str, Any]) -> LinearIssue:
        """Update an issue's owned definition fields (Atlas -> Linear)."""
        ...

    def fetch_issue(self, issue_id: str) -> LinearIssue | None:
        """Fetch one issue for the status direction; ``None`` if absent."""
        ...

    def fetch_workflow_states(self) -> list[WorkflowState]:
        """The workspace's workflow states, for status-map validation (D7)."""
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
_ISSUE_QUERY = f"query Issue($id: String!) {{ issue(id: $id) {{ {_ISSUE_FIELDS} }} }}"
# `first: 250` covers any realistic workspace; paginating beyond it is an
# ATLAS-42 concern, not the boundary's.
_STATES_QUERY = (
    "query WorkflowStates { workflowStates(first: 250) { nodes { id name type } } }"
)
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
        self, definition: Mapping[str, Any], *, team_id: str
    ) -> LinearIssue:
        reject_unowned_keys(definition)
        data = self._execute(
            _CREATE_MUTATION, {"input": {**definition, "teamId": team_id}}
        )
        return _issue_from_node(data["issueCreate"]["issue"])

    def update_issue(self, issue_id: str, definition: Mapping[str, Any]) -> LinearIssue:
        reject_unowned_keys(definition)
        data = self._execute(
            _UPDATE_MUTATION, {"id": issue_id, "input": dict(definition)}
        )
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

    def delete_issue(self, issue_id: str) -> bool:
        """Delete (trash) an issue. Not part of the ``LinearClient`` boundary
        protocol -- it exists for the live smoke test's cleanup, never the
        sync loop."""

        data = self._execute(_DELETE_MUTATION, {"id": issue_id})
        return bool(data["issueDelete"]["success"])
