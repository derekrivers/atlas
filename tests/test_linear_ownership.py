"""Field-ownership allow-list and status-map behaviour (ATLAS-41).

These are the falsifiable proofs that the boundary breaks the right way:
definitions cross Atlas -> Linear only, status crosses Linear -> Atlas only,
and a field absent from the owned tables is mechanically incapable of
crossing in either direction.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from test_models_validation import ticket_kwargs

from atlas.core.models.ticket import Ticket, TicketStatus
from atlas.linear.client import LinearIssue, WorkflowState
from atlas.linear.ownership import (
    OWNED_LINEAR_INPUT_KEYS,
    LinearStatusMap,
    LinearStatusMapError,
    definition_payload,
    render_definition_description,
    status_from_issue,
)


def make_ticket(**overrides: object) -> Ticket:
    return Ticket(**ticket_kwargs() | {"id": uuid4()} | overrides)


def issue(
    *,
    state_id: str | None,
    title: str = "Linear title",
    state_type: str | None = "started",
) -> LinearIssue:
    return LinearIssue(
        id="issue-1",
        title=title,
        state_id=state_id,
        state_name="whatever",
        state_type=state_type,
    )


# --- definition direction: Atlas -> Linear ---------------------------------


def test_definition_payload_contains_only_owned_keys() -> None:
    payload = definition_payload(make_ticket(title="T", priority=3, objective="obj"))
    assert set(payload) == OWNED_LINEAR_INPUT_KEYS
    assert {"title", "description"} == OWNED_LINEAR_INPUT_KEYS
    assert payload["title"] == "T"
    # description is the full-spec render (objective + populated fields), led by
    # the objective; its detailed shape is pinned by the render tests below.
    assert isinstance(payload["description"], str)
    assert payload["description"].startswith("obj")


def test_priority_is_owned_but_not_yet_syncable() -> None:
    # priority is owned (ADR-0006) but deferred like labels: Atlas's
    # unconstrained integer has no honest mapping to Linear's inverted 0-4
    # enum yet, so it must NOT cross. Wrong answer: 'priority' in the payload
    # -> a raw, meaning-inverted value reaches Linear.
    payload = definition_payload(make_ticket(priority=10))
    assert "priority" not in payload


def test_status_cannot_cross_atlas_to_linear() -> None:
    # A ticket whose STATUS is set must not leak a state/stateId into the
    # Linear payload. Wrong answer: 'stateId' or 'state' appears -> status
    # would cross Atlas -> Linear, which the ownership rule forbids.
    payload = definition_payload(make_ticket(status=TicketStatus.DONE))
    assert "stateId" not in payload
    assert "state" not in payload
    assert "status" not in payload


def test_non_owned_ticket_field_never_appears_in_payload() -> None:
    # The Ticket carries many fields (objective beyond the summary, context,
    # acceptance_criteria, ...); none but the three owned keys may cross.
    payload = definition_payload(
        make_ticket(
            context="secret context",
            acceptance_criteria=["a", "b"],
            risk_level="high",
        )
    )
    assert "context" not in payload
    assert "acceptance_criteria" not in payload
    assert "risk_level" not in payload


# --- full-spec description render (Atlas -> Linear) -------------------------


def _fully_populated_ticket() -> Ticket:
    return make_ticket(
        objective="Ship the thing.",
        context="Because the thing matters.",
        acceptance_criteria=["AC one", "AC two"],
        non_goals=["NG one"],
        implementation_notes=["IN one", "IN two"],
        test_requirements=["TR one"],
        documentation_requirements=["DR one"],
        definition_of_done=["DoD one", "DoD two"],
        relevant_docs=["docs/a.md", "docs/b.md"],
        estimated_effort=5,
        component="planner",
        tags=["alpha", "beta"],
    )


def test_full_spec_render_has_every_section_in_order() -> None:
    # All rich fields populated -> each header appears exactly once, every item
    # renders as a bullet, and the sections follow the fixed D1 order. Wrong
    # answer: a section out of order, a dropped item, or a header for an empty
    # field.
    rendered = render_definition_description(_fully_populated_ticket())

    headers = [
        "## Context",
        "## Acceptance Criteria",
        "## Non-Goals",
        "## Implementation Notes",
        "## Test Requirements",
        "## Documentation Requirements",
        "## Definition of Done",
        "## Meta",
    ]
    positions = [rendered.index(h) for h in headers]
    assert positions == sorted(positions)  # fixed order, no reordering
    for header in headers:
        assert rendered.count(header) == 1  # each section exactly once

    assert rendered.startswith("Ship the thing.")  # objective leads
    for item in (
        "AC one",
        "AC two",
        "NG one",
        "IN one",
        "IN two",
        "TR one",
        "DR one",
        "DoD one",
        "DoD two",
    ):
        assert f"- {item}" in rendered
    assert "- Relevant Docs: docs/a.md, docs/b.md" in rendered
    assert "- Estimated Effort: 5" in rendered
    assert "- Component: planner" in rendered
    assert "- Tags: alpha, beta" in rendered


def test_empty_fields_collapse_to_just_the_objective() -> None:
    # A ticket carrying only an objective (all lists empty, no meta) renders to
    # exactly that objective -- no empty headers, no trailing newline noise.
    # Wrong answer: emitting '## Acceptance Criteria' with no items.
    ticket = make_ticket(
        objective="Just the objective.",
        context="",
        acceptance_criteria=[],
        non_goals=[],
        implementation_notes=[],
        test_requirements=[],
        documentation_requirements=[],
        definition_of_done=[],
        relevant_docs=[],
        estimated_effort=None,
        component=None,
        tags=[],
    )
    assert render_definition_description(ticket) == "Just the objective."


def test_render_is_deterministic_and_preserves_stored_order() -> None:
    # Same ticket rendered twice -> byte-identical, and list items follow stored
    # order, not sorted order. Wrong answer: any ordering that depends on set
    # iteration.
    ticket = make_ticket(
        objective="obj",
        acceptance_criteria=["zebra", "apple", "mango"],
    )
    first = render_definition_description(ticket)
    second = render_definition_description(ticket)
    assert first == second
    assert "- zebra\n- apple\n- mango" in first  # stored order, never sorted


def test_ownership_boundary_holds_for_a_fully_populated_ticket() -> None:
    # Enriching the description content must not widen the key set: the two-key
    # boundary holds even when every rich field is populated. Wrong answer: a
    # third key leaking into the payload.
    assert set(OWNED_LINEAR_INPUT_KEYS) == {"title", "description"}
    payload = definition_payload(_fully_populated_ticket())
    assert set(payload) == {"title", "description"}


def test_render_excludes_status_priority_and_pm_internals() -> None:
    # The render carries spec content only; Linear owns/derives status and
    # priority, and the PM-engine cursor/observation fields are internal. None
    # may appear. Wrong answer: PM cursor/observation fields leaking into Linear.
    ticket = make_ticket(
        objective="obj",
        status=TicketStatus.DONE,
        priority=10,
        external_linear_id="lin-abc",
        external_github_issue_id="gh-123",
        last_observed_linear_state_id="state-xyz",
        linear_synced_at=datetime(2026, 6, 25, tzinfo=UTC),
    )
    rendered = render_definition_description(ticket)
    assert "done" not in rendered  # status
    assert "10" not in rendered  # priority
    assert "lin-abc" not in rendered  # external_linear_id
    assert "gh-123" not in rendered  # external_github_issue_id
    assert "state-xyz" not in rendered  # last_observed_linear_state_id
    assert "2026" not in rendered  # linear_synced_at


# --- status direction: Linear -> Atlas -------------------------------------


def status_map() -> LinearStatusMap:
    return LinearStatusMap(
        {
            "state-progress": TicketStatus.IN_PROGRESS,
            "state-done": TicketStatus.DONE,
        }
    )


def test_mapped_state_id_crosses() -> None:
    assert (
        status_from_issue(issue(state_id="state-progress"), status_map())
        == TicketStatus.IN_PROGRESS
    )


def test_unmapped_state_id_is_dropped_not_guessed() -> None:
    # An id absent from the configured map yields None (ATLAS-42 surfaces it
    # as an anomaly); it is never guessed into some default status.
    assert status_from_issue(issue(state_id="state-unknown"), status_map()) is None


def test_missing_state_id_is_none() -> None:
    assert status_from_issue(issue(state_id=None), status_map()) is None


def test_name_change_without_state_id_change_does_not_cross() -> None:
    # The lookup key is the stable state id, never the name. An issue whose
    # NAME changed but whose state id is unchanged yields the same status;
    # a renamed-but-same-id state must not move the ticket.
    before = LinearIssue(
        id="i",
        title="t",
        state_id="state-progress",
        state_name="In Progress",
        state_type="started",
    )
    after_rename = LinearIssue(
        id="i",
        title="t",
        state_id="state-progress",
        state_name="Doing (renamed)",
        state_type="started",
    )
    smap = status_map()
    assert status_from_issue(before, smap) == status_from_issue(after_rename, smap)
    assert status_from_issue(after_rename, smap) == TicketStatus.IN_PROGRESS


def test_only_a_changed_mapped_state_id_moves_the_status() -> None:
    smap = status_map()
    assert status_from_issue(issue(state_id="state-progress"), smap) == (
        TicketStatus.IN_PROGRESS
    )
    assert status_from_issue(issue(state_id="state-done"), smap) == TicketStatus.DONE


def test_definition_field_never_crosses_linear_to_atlas() -> None:
    # status_from_issue reads ONLY the state id. An issue with a wildly
    # different title returns a status, never the title -- the title cannot
    # cross Linear -> Atlas. Wrong answer: any title text leaks into the
    # result (the function would have to return something other than a
    # TicketStatus | None).
    result = status_from_issue(
        issue(state_id="state-done", title="MALICIOUS NEW TITLE"), status_map()
    )
    assert result is TicketStatus.DONE


# --- LINEAR_STATE_MAP config + load-time validation ------------------------


def test_from_env_parses_state_map(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "LINEAR_STATE_MAP", '{"state-progress": "in_progress", "state-done": "done"}'
    )
    smap = LinearStatusMap.from_env()
    assert smap.status_for("state-progress") == TicketStatus.IN_PROGRESS
    assert smap.status_for("state-done") == TicketStatus.DONE


def test_missing_state_map_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LINEAR_STATE_MAP", raising=False)
    with pytest.raises(LinearStatusMapError, match="LINEAR_STATE_MAP is not set"):
        LinearStatusMap.from_env()


def test_empty_state_map_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LINEAR_STATE_MAP", "{}")
    with pytest.raises(LinearStatusMapError):
        LinearStatusMap.from_env()


def test_malformed_state_map_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LINEAR_STATE_MAP", "not json")
    with pytest.raises(LinearStatusMapError, match="not valid JSON"):
        LinearStatusMap.from_env()


def test_unknown_status_value_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LINEAR_STATE_MAP", '{"state-x": "not_a_status"}')
    with pytest.raises(LinearStatusMapError, match="known TicketStatus"):
        LinearStatusMap.from_env()


def test_validate_accepts_consistent_states() -> None:
    smap = LinearStatusMap(
        {"state-progress": TicketStatus.IN_PROGRESS, "state-done": TicketStatus.DONE}
    )
    states = [
        WorkflowState(id="state-progress", name="In Progress", type="started"),
        WorkflowState(id="state-done", name="Done", type="completed"),
    ]
    smap.validate_against_states(states)  # does not raise


def test_validate_is_permissive_many_statuses_one_started_type() -> None:
    # Several active Atlas statuses legitimately share Linear type 'started'.
    smap = LinearStatusMap(
        {
            "s1": TicketStatus.IN_PROGRESS,
            "s2": TicketStatus.PR_OPEN,
            "s3": TicketStatus.REVIEW_REQUIRED,
            "s4": TicketStatus.CHANGES_REQUESTED,
        }
    )
    states = [
        WorkflowState(id=f"s{i}", name=f"S{i}", type="started") for i in range(1, 5)
    ]
    smap.validate_against_states(states)  # does not raise


def test_validate_rejects_type_contradiction() -> None:
    # A 'completed'-type Linear state mapped to in_progress is a clear
    # contradiction and must be rejected at load time.
    smap = LinearStatusMap({"state-done": TicketStatus.IN_PROGRESS})
    states = [WorkflowState(id="state-done", name="Done", type="completed")]
    with pytest.raises(LinearStatusMapError, match="only accepts"):
        smap.validate_against_states(states)


def test_validate_rejects_stale_state_id() -> None:
    # A configured id that no longer exists (rotated UUIDs) fails loudly
    # rather than silently dropping.
    smap = LinearStatusMap({"state-gone": TicketStatus.DONE})
    states = [WorkflowState(id="state-present", name="Done", type="completed")]
    with pytest.raises(LinearStatusMapError, match="does not exist"):
        smap.validate_against_states(states)
