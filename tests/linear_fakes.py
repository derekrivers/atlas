"""In-memory fake Linear client for deterministic tests (ATLAS-41 D10).

Mirrors the ``tests/planner_fakes.py`` precedent: a test double that does
NOT ship in the package and makes no network call. It satisfies the
``LinearClient`` protocol and is held to the same behavioural contract as
the real ``LinearGraphQLClient`` (the parametrized contract test in
``tests/test_linear_client.py``), so it is honest confidence, not a stub
that drifts from reality. ATLAS-42's sync-loop tests inject it too.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from atlas.linear.client import (
    LinearComment,
    LinearIssue,
    WorkflowState,
    reject_unowned_keys,
)

# A default state assigned on creation, shared with the contract test's
# stub-real emulator so both clients return the same shape. ``READY_STATE`` is
# a second default so the contract can exercise ``set_state`` moving an issue.
DEFAULT_STATE = WorkflowState(id="state-unstarted", name="Todo", type="unstarted")
READY_STATE = WorkflowState(id="state-ready", name="Ready for Agent", type="unstarted")


class InMemoryLinearClient:
    """A dict-backed ``LinearClient``.

    ``create_issue`` mints a sequential id and assigns ``DEFAULT_STATE``;
    ``update_issue``/``fetch_issue`` operate on the stored issue. The owned
    key allow-list is enforced exactly as the real client enforces it.
    ``set_state`` is the protocol's sanctioned state write (ATLAS-43), taking a
    bare state id; ``simulate_linear_state`` is a distinct test-only helper that
    simulates a Linear-side status change from a full ``WorkflowState``.
    """

    def __init__(self, workflow_states: list[WorkflowState] | None = None) -> None:
        self._issues: dict[str, LinearIssue] = {}
        self._comments: dict[str, list[LinearComment]] = {}
        self._counter = 0
        self._comment_counter = 0
        self._states = (
            list(workflow_states)
            if workflow_states is not None
            else [DEFAULT_STATE, READY_STATE]
        )

    def create_issue(
        self, definition: Mapping[str, Any], *, team_id: str
    ) -> LinearIssue:
        reject_unowned_keys(definition)
        self._counter += 1
        issue_id = f"issue-{self._counter}"
        issue = LinearIssue(
            id=issue_id,
            title=str(definition.get("title", "")),
            state_id=DEFAULT_STATE.id,
            state_name=DEFAULT_STATE.name,
            state_type=DEFAULT_STATE.type,
        )
        self._issues[issue_id] = issue
        return issue

    def update_issue(self, issue_id: str, definition: Mapping[str, Any]) -> LinearIssue:
        reject_unowned_keys(definition)
        current = self._issues[issue_id]
        updated = LinearIssue(
            id=current.id,
            title=str(definition.get("title", current.title)),
            state_id=current.state_id,
            state_name=current.state_name,
            state_type=current.state_type,
        )
        self._issues[issue_id] = updated
        return updated

    def fetch_issue(self, issue_id: str) -> LinearIssue | None:
        return self._issues.get(issue_id)

    def fetch_workflow_states(self) -> list[WorkflowState]:
        return list(self._states)

    def fetch_comments(self, issue_id: str) -> list[LinearComment]:
        """Read-only comment fetch (ATLAS-45), mirroring the real client: an
        unknown issue (or one with no comments) yields an empty list, never a
        raise."""
        return list(self._comments.get(issue_id, []))

    def set_state(self, issue_id: str, state_id: str) -> LinearIssue:
        """The sanctioned state write (Atlas -> Linear; ATLAS-43), mirroring the
        real client. Takes a bare state id; resolves name/type from the known
        workflow states when present (else ``None``, as the real API would for
        an id this fake has not been told about)."""
        current = self._issues[issue_id]
        match = next((state for state in self._states if state.id == state_id), None)
        updated = LinearIssue(
            id=current.id,
            title=current.title,
            state_id=state_id,
            state_name=match.name if match else None,
            state_type=match.type if match else None,
        )
        self._issues[issue_id] = updated
        return updated

    # --- test-only helpers (not part of LinearClient) -----------------------
    def seed_comment(
        self, issue_id: str, body: str, *, comment_id: str | None = None
    ) -> LinearComment:
        """Attach a comment to an issue so the follow-up scan (ATLAS-45) can read
        it back via ``fetch_comments``. Mints a sequential id when none is given
        and a deterministic ``created_at`` (no wall clock), so tests stay
        reproducible. Returns the stored comment."""
        if comment_id is None:
            self._comment_counter += 1
            comment_id = f"comment-{self._comment_counter}"
        comment = LinearComment(
            id=comment_id,
            body=body,
            created_at="2026-01-01T00:00:00.000Z",
        )
        self._comments.setdefault(issue_id, []).append(comment)
        return comment

    def simulate_linear_state(self, issue_id: str, state: WorkflowState) -> None:
        """Simulate a Linear-side status change to ``state`` (distinct from the
        protocol ``set_state``, which takes only a state id)."""
        current = self._issues[issue_id]
        self._issues[issue_id] = LinearIssue(
            id=current.id,
            title=current.title,
            state_id=state.id,
            state_name=state.name,
            state_type=state.type,
        )
