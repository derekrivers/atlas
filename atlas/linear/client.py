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
# Transport timeout for every Linear HTTP call (ATLAS-147). A module constant
# (policy, not per-run configuration): a hung request fails the tick instead of
# hanging the scheduler loop forever, and the next idempotent tick retries.
LINEAR_HTTP_TIMEOUT_SECONDS = 30.0
# Pinned truncation for an HTTP error body carried into a LinearAPIError
# message (ATLAS-147): enough to diagnose (the 2026-07-07 RATELIMITED body fits
# comfortably), bounded so a pathological response cannot bloat the TickFailure
# ``detail`` column.
LINEAR_ERROR_BODY_MAX_LEN = 1000
# Page size for the batched project-issues pull (ATLAS-148): Linear's maximum,
# matching the repo's existing ``first: 250`` convention, so a 110-ticket board
# pulls in one request and pagination only engages past 250 issues.
LINEAR_ISSUES_PAGE_SIZE = 250


class LinearClientError(RuntimeError):
    """Base for Linear client failures."""


class MissingLinearTokenError(LinearClientError):
    """No Linear API token in the environment (a clean-exit precondition)."""


class LinearAPIError(LinearClientError):
    """A Linear GraphQL request failed (transport, HTTP, or GraphQL errors)."""


class LinearRateLimitError(LinearAPIError):
    """Linear rejected the request as rate-limited (ATLAS-147).

    Raised whenever a GraphQL errors list carries ``extensions.code ==
    "RATELIMITED"`` -- Linear delivers that rejection BOTH as a transport
    HTTP 400 whose body holds the errors list (the 2026-07-07 incident shape)
    and, in principle, as a 200 whose envelope carries the same list; one
    detector covers both paths. ``reset_after_seconds`` is parsed from
    ``extensions.meta.rateLimitResult.duration`` (milliseconds; observed
    3600000) and is ``None`` when absent or unparsable -- the scheduler then
    backs off at its full cap rather than guessing."""

    def __init__(
        self, message: str, *, reset_after_seconds: float | None = None
    ) -> None:
        super().__init__(message)
        self.reset_after_seconds = reset_after_seconds


class UnownedFieldError(ValueError):
    """A definition payload carried a field outside the Atlas -> Linear
    allow-list (ATLAS-41). Enforced at the client so a non-owned field is
    mechanically incapable of crossing, not merely unmapped."""


@dataclass(frozen=True)
class LinearIssue:
    """The only issue shape that crosses the boundary.

    ``state_type`` is carried for the status-map's load-time validation
    (ownership.py ``validate_against_states``, D7); it is never a lookup key.
    ``description`` is carried as an observation field for Atlas-authored
    context-pack headers (AgentRun reconstruction); it is never written back
    through the pull path.
    ``identifier`` (the human-facing ``ATL-42`` handle) is carried for
    diagnostics by the batched pull (ATLAS-148) and is never a join key —
    tickets join to issues by ``external_linear_id``/``id`` ONLY; it defaults
    to ``None`` because the single-issue selections do not fetch it.
    """

    id: str
    title: str
    state_id: str | None
    state_name: str | None
    state_type: str | None
    description: str | None = None
    identifier: str | None = None


class LinearProjectIssues(list[LinearIssue]):
    """Materialised project pull with bounded pagination completeness metadata.

    It remains a ``list`` for source compatibility with the ATLAS-148 boundary,
    while allowing the PM admission path to fail closed when a cursor chain is
    malformed instead of treating a partial prefix as a complete board.
    """

    def __init__(
        self,
        issues: list[LinearIssue] | None = None,
        *,
        complete: bool = True,
        pagination_gaps: tuple[str, ...] = (),
    ) -> None:
        super().__init__(issues or [])
        self.complete = complete
        self.pagination_gaps = pagination_gaps


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

    def fetch_project_issues(self, project_id: str) -> list[LinearIssue]:
        """Fetch every issue in the configured Linear project, paginated
        internally (ATLAS-148) — the batched pull that replaces step 1's
        per-ticket ``fetch_issue`` loop, costing ``ceil(n / 250)`` requests
        instead of one per ticket. Project-scoped (``LINEAR_PROJECT_ID``) to
        match Symphony's poll scope. Read-only; callers join the returned
        issues to tickets by ``external_linear_id`` ONLY (never title or
        identifier)."""
        ...

    def fetch_workflow_states(self, team_id: str) -> list[WorkflowState]:
        """The given team's workflow states, for status-map validation (D7).

        Team-scoped (ATLAS-148): the workspace-wide form returned foreign
        teams' states with colliding names (two ``Canceled``, two ``Done``,
        two ``Duplicate`` observed live), so the query takes the team id the
        tick already requires and returns only that team's board."""
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
_ISSUE_FIELDS = "id title description state { id name type }"
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
# The batched pull (ATLAS-148): every issue in the configured project, one
# page of `LINEAR_ISSUES_PAGE_SIZE` per request, cursor-paginated until
# `hasNextPage` is false. Selects `identifier` (diagnostics only) alongside
# the shared issue fragment; the join key is always the issue `id`.
_PROJECT_ISSUES_QUERY = (
    "query ProjectIssues($id: String!, $first: Int!, $after: String) { "
    "project(id: $id) { issues(first: $first, after: $after) { "
    f"nodes {{ identifier {_ISSUE_FIELDS} }} "
    "pageInfo { hasNextPage endCursor } } } }"
)
# Team-scoped (ATLAS-148): the workspace-wide `workflowStates(first: 250)`
# form returned foreign teams' states with colliding names. `first: 250`
# covers any realistic team board.
_STATES_QUERY = (
    "query TeamWorkflowStates($teamId: String!) { "
    "team(id: $teamId) { states(first: 250) { nodes { id name type } } } }"
)
# The A2 preflight read (ATLAS-136): resolve a project by its UUID to its
# slugId. A nonexistent id returns `project: null` (mapped to None).
_PROJECT_QUERY = "query Project($id: String!) { project(id: $id) { id slugId } }"
_DELETE_MUTATION = (
    "mutation IssueDelete($id: String!) { issueDelete(id: $id) { success } }"
)


def _rate_limit_reset_seconds(extensions: Mapping[str, Any]) -> float | None:
    """The reset window from a RATELIMITED error's extensions, in seconds.

    The field is ``extensions.meta.rateLimitResult.duration``, in MILLISECONDS
    (observed value 3600000 in the 2026-07-07 capture); there is no ``resetAt``
    field, so none is looked for. Absent or unparsable yields ``None`` (the
    scheduler backs off at its full cap)."""

    meta = extensions.get("meta")
    if not isinstance(meta, Mapping):
        return None
    result = meta.get("rateLimitResult")
    if not isinstance(result, Mapping):
        return None
    duration_ms = result.get("duration")
    if isinstance(duration_ms, bool) or not isinstance(duration_ms, int | float):
        return None
    return float(duration_ms) / 1000.0


def _raise_if_rate_limited(errors: Any, message: str) -> None:
    """The ONE rate-limit detector (ATLAS-147), applied to every GraphQL errors
    list regardless of the transport that carried it: a transport-400 whose
    body parses as JSON errors, and a 200-with-errors envelope. Raises the
    typed :class:`LinearRateLimitError` when any error carries
    ``extensions.code == "RATELIMITED"``; otherwise returns and the caller
    raises its plain :class:`LinearAPIError`."""

    if not isinstance(errors, list):
        return
    for error in errors:
        if not isinstance(error, Mapping):
            continue
        extensions = error.get("extensions")
        if not isinstance(extensions, Mapping):
            continue
        if extensions.get("code") != "RATELIMITED":
            continue
        raise LinearRateLimitError(
            message, reset_after_seconds=_rate_limit_reset_seconds(extensions)
        )


def _issue_from_node(node: Mapping[str, Any]) -> LinearIssue:
    state = node.get("state")
    return LinearIssue(
        id=node["id"],
        title=node["title"],
        state_id=state["id"] if state else None,
        state_name=state["name"] if state else None,
        state_type=state["type"] if state else None,
        description=node.get("description"),
        # Only the batched project-issues selection fetches `identifier`
        # (ATLAS-148, diagnostics only); the single-issue selections leave it
        # None via the DTO default.
        identifier=node.get("identifier"),
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
            with urllib_request.urlopen(
                request, timeout=LINEAR_HTTP_TIMEOUT_SECONDS
            ) as response:
                body = json.loads(response.read().decode())
        except urllib_error.HTTPError as error:
            # HTTPError IS-A URLError, so it must be caught first. Read the
            # body once: Linear returns rate-limit rejections as transport
            # HTTP 400 with the RATELIMITED detail ONLY in the body (the
            # 2026-07-07 incident crash-looped for an hour on the opaque
            # status alone). Detection parses the full body; the message
            # carries it truncated to the pinned max.
            detail = error.read().decode(errors="replace")
            message = (
                f"Linear API request failed: HTTP {error.code}: "
                f"{detail[:LINEAR_ERROR_BODY_MAX_LEN]}"
            )
            try:
                parsed = json.loads(detail)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict):
                _raise_if_rate_limited(parsed.get("errors"), message)
            raise LinearAPIError(message) from error
        except (urllib_error.URLError, OSError) as error:
            raise LinearAPIError(f"Linear API request failed: {error}") from error
        except json.JSONDecodeError as error:
            raise LinearAPIError(f"Linear API returned non-JSON: {error}") from error
        if body.get("errors"):
            message = f"Linear GraphQL errors: {body['errors']}"
            _raise_if_rate_limited(body["errors"], message)
            raise LinearAPIError(message)
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

    def fetch_project_issues(self, project_id: str) -> list[LinearIssue]:
        # The batched pull (ATLAS-148): cursor-paginate the project's issue
        # connection until hasNextPage is false — ceil(n / page size) requests
        # for an n-issue project. A missing project yields `project: null`,
        # mapped to an empty list (the sync loop then sees every joined ticket
        # as issue-missing and leaves statuses unchanged, never a crash).
        issues: list[LinearIssue] = []
        after: str | None = None
        seen_cursors: set[str] = set()
        while True:
            data = self._execute(
                _PROJECT_ISSUES_QUERY,
                {"id": project_id, "first": LINEAR_ISSUES_PAGE_SIZE, "after": after},
            )
            project = data.get("project")
            if not project:
                return LinearProjectIssues()
            try:
                connection = project["issues"]
                nodes = connection["nodes"]
                page_info = connection["pageInfo"]
                has_next_page = page_info["hasNextPage"]
                end_cursor = page_info["endCursor"]
                if not isinstance(nodes, list) or not isinstance(has_next_page, bool):
                    raise TypeError
                issues.extend(_issue_from_node(node) for node in nodes)
            except (KeyError, TypeError):
                return LinearProjectIssues(
                    issues,
                    complete=False,
                    pagination_gaps=("malformed-page",),
                )
            if not has_next_page:
                return LinearProjectIssues(issues)
            if (
                not isinstance(end_cursor, str)
                or not end_cursor
                or end_cursor == after
                or end_cursor in seen_cursors
            ):
                return LinearProjectIssues(
                    issues,
                    complete=False,
                    pagination_gaps=(
                        end_cursor if isinstance(end_cursor, str) else "invalid-cursor",
                    ),
                )
            seen_cursors.add(end_cursor)
            after = end_cursor

    def fetch_workflow_states(self, team_id: str) -> list[WorkflowState]:
        # Team-scoped (ATLAS-148): only the given team's states, so same-named
        # states on foreign teams can no longer collide with the map's targets.
        data = self._execute(_STATES_QUERY, {"teamId": team_id})
        team = data.get("team")
        if not team:
            return []
        return [
            WorkflowState(id=node["id"], name=node["name"], type=node["type"])
            for node in team["states"]["nodes"]
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
