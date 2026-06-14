"""ATLAS-25: the pure key-assignment function (gap 2).

Determinism, ascending-index ordering, reference rewriting via resolve(),
the out-of-range typed error, real-key pass-through, and prefix
separation — all over PlanDiffs built from the reconciler's DiffEntry,
the contract this function consumes. One integration case runs the real
reconciler so the placeholder identities are not hand-asserted shapes.
"""

from __future__ import annotations

import pytest
from proposal_fixtures import dependency_payload, epic_payload, ticket_payload

from atlas.planning.key_authority import (
    EPIC_PREFIX,
    TICKET_PREFIX,
    KeyAssignment,
    KeyReferenceError,
    assign_keys,
)
from atlas.planning.proposal import Proposal
from atlas.planning.reconciler import (
    ADD,
    MODIFY,
    Backlog,
    DiffEntry,
    PlanDiff,
    reconcile,
)


def add_epic(index: int) -> DiffEntry:
    return DiffEntry(
        entry_type=ADD, kind="epic", identity=f"new_epic:{index}", title=f"E{index}"
    )


def add_ticket(index: int) -> DiffEntry:
    return DiffEntry(
        entry_type=ADD, kind="ticket", identity=f"new:{index}", title=f"T{index}"
    )


def add_dependency(source: str, target: str) -> DiffEntry:
    return DiffEntry(
        entry_type=ADD, kind="dependency", identity=f"{source} -> {target}", title="dep"
    )


# --- determinism ------------------------------------------------------------


def test_identical_inputs_yield_identical_map() -> None:
    diff = PlanDiff(entries=(add_epic(0), add_ticket(0), add_ticket(1)))
    first = assign_keys(diff, ticket_high_water=0, epic_high_water=0)
    second = assign_keys(diff, ticket_high_water=0, epic_high_water=0)
    assert dict(first.key_map) == dict(second.key_map)
    assert (first.ticket_high_water, first.epic_high_water) == (
        second.ticket_high_water,
        second.epic_high_water,
    )


def test_entry_order_does_not_change_assignment() -> None:
    # The same ADD set in two entry orders numbers identically: ordering is
    # by placeholder index, not by position in the entries tuple.
    forward = PlanDiff(entries=(add_ticket(0), add_ticket(1), add_ticket(2)))
    shuffled = PlanDiff(entries=(add_ticket(2), add_ticket(0), add_ticket(1)))
    forward_map = assign_keys(forward, ticket_high_water=0, epic_high_water=0).key_map
    shuffled_map = assign_keys(shuffled, ticket_high_water=0, epic_high_water=0).key_map
    assert dict(forward_map) == dict(shuffled_map)


# --- ordering ---------------------------------------------------------------


def test_lower_index_gets_lower_key() -> None:
    diff = PlanDiff(entries=(add_ticket(0), add_ticket(1), add_ticket(2)))
    assignment = assign_keys(diff, ticket_high_water=0, epic_high_water=0)
    assert assignment.key_map["new:0"] == f"{TICKET_PREFIX}-1"
    assert assignment.key_map["new:1"] == f"{TICKET_PREFIX}-2"
    assert assignment.key_map["new:2"] == f"{TICKET_PREFIX}-3"
    assert assignment.ticket_high_water == 3


def test_assignment_continues_from_high_water() -> None:
    diff = PlanDiff(entries=(add_ticket(0), add_ticket(1)))
    assignment = assign_keys(diff, ticket_high_water=7, epic_high_water=0)
    assert assignment.key_map["new:0"] == f"{TICKET_PREFIX}-8"
    assert assignment.key_map["new:1"] == f"{TICKET_PREFIX}-9"
    assert assignment.ticket_high_water == 9


def test_epic_key_form() -> None:
    diff = PlanDiff(entries=(add_epic(0), add_epic(1)))
    assignment = assign_keys(diff, ticket_high_water=0, epic_high_water=4)
    assert assignment.key_map["new_epic:0"] == f"{EPIC_PREFIX}5"
    assert assignment.key_map["new_epic:1"] == f"{EPIC_PREFIX}6"
    assert assignment.epic_high_water == 6


# --- reference rewriting via resolve ----------------------------------------


def test_resolve_dependency_placeholder() -> None:
    diff = PlanDiff(
        entries=(add_ticket(0), add_ticket(1), add_dependency("new:0", "new:1"))
    )
    assignment = assign_keys(diff, ticket_high_water=0, epic_high_water=0)
    assert assignment.resolve("new:0") == f"{TICKET_PREFIX}-1"
    assert assignment.resolve("new:1") == f"{TICKET_PREFIX}-2"


def test_resolve_epic_ref_placeholder() -> None:
    diff = PlanDiff(entries=(add_epic(0), add_ticket(0)))
    assignment = assign_keys(diff, ticket_high_water=0, epic_high_water=0)
    # A ticket's epic_ref of new_epic:0 resolves to the assigned epic key.
    assert assignment.resolve("new_epic:0") == f"{EPIC_PREFIX}1"


def test_resolve_real_key_passes_through() -> None:
    # Existing items echo their keys; resolve leaves a real key untouched.
    diff = PlanDiff(entries=(add_ticket(0),))
    assignment = assign_keys(diff, ticket_high_water=0, epic_high_water=0)
    assert assignment.resolve("ATLAS-42") == "ATLAS-42"
    assert assignment.resolve("ATLAS-E7") == "ATLAS-E7"


# --- negative: out-of-range placeholder -------------------------------------


def test_out_of_range_ticket_placeholder_raises() -> None:
    # new:1 has no ADD entry (e.g. its proposal item was dropped to
    # CONFLICT) — resolving it is a typed error, not a silent miss.
    diff = PlanDiff(entries=(add_ticket(0),))
    assignment = assign_keys(diff, ticket_high_water=0, epic_high_water=0)
    with pytest.raises(KeyReferenceError, match="new:1"):
        assignment.resolve("new:1")


def test_out_of_range_epic_placeholder_raises() -> None:
    diff = PlanDiff(entries=(add_epic(0),))
    assignment = assign_keys(diff, ticket_high_water=0, epic_high_water=0)
    with pytest.raises(KeyReferenceError, match="new_epic:9"):
        assignment.resolve("new_epic:9")


# --- prefix separation ------------------------------------------------------


def test_tickets_and_epics_draw_from_distinct_counters() -> None:
    # Interleaved ADD epics and tickets keep independent sequences; neither
    # counter perturbs the other.
    diff = PlanDiff(entries=(add_epic(0), add_ticket(0), add_epic(1), add_ticket(1)))
    assignment = assign_keys(diff, ticket_high_water=2, epic_high_water=5)
    assert assignment.key_map == {
        "new_epic:0": f"{EPIC_PREFIX}6",
        "new_epic:1": f"{EPIC_PREFIX}7",
        "new:0": f"{TICKET_PREFIX}-3",
        "new:1": f"{TICKET_PREFIX}-4",
    }
    assert assignment.epic_high_water == 7
    assert assignment.ticket_high_water == 4


def test_no_adds_leaves_marks_unchanged() -> None:
    # A diff with only MODIFY entries mints nothing.
    diff = PlanDiff(
        entries=(
            DiffEntry(entry_type=MODIFY, kind="ticket", identity="ATLAS-1", title="x"),
        )
    )
    assignment = assign_keys(diff, ticket_high_water=9, epic_high_water=4)
    assert dict(assignment.key_map) == {}
    assert assignment.ticket_high_water == 9
    assert assignment.epic_high_water == 4


# --- integration with the real reconciler -----------------------------------


def test_consumes_real_reconciler_diff() -> None:
    # An empty backlog makes every proposal item an ADD with new:/new_epic:
    # identities and dependency endpoints in placeholder form — exactly the
    # shape assign_keys consumes.
    proposal = Proposal.model_validate(
        {
            "epics": [epic_payload()],
            "tickets": [
                ticket_payload(title="First", objective="One"),
                ticket_payload(title="Second", objective="Two"),
            ],
            "dependencies": [dependency_payload(source="new:1", target="new:0")],
            "planner_notes": [],
        }
    )
    diff = reconcile(proposal, Backlog())
    assignment = assign_keys(diff, ticket_high_water=0, epic_high_water=0)
    assert isinstance(assignment, KeyAssignment)
    assert assignment.key_map["new_epic:0"] == f"{EPIC_PREFIX}1"
    assert assignment.key_map["new:0"] == f"{TICKET_PREFIX}-1"
    assert assignment.key_map["new:1"] == f"{TICKET_PREFIX}-2"
    # The dependency endpoints rewrite to assigned keys.
    assert assignment.resolve("new:1") == f"{TICKET_PREFIX}-2"
    assert assignment.resolve("new:0") == f"{TICKET_PREFIX}-1"
