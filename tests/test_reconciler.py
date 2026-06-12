"""ATLAS-24: deterministic reconciler — pass precedence, AT-2/AT-4
shadows, similarity boundary at the spec rule, ambiguity policy, and
dependency-edge semantics. Fixture keys echo existing-backlog forms
only; new items are new:<n>."""

from typing import Any
from uuid import uuid4

import pytest
from proposal_fixtures import (
    dependency_payload,
    epic_payload,
    proposal_payload,
    ticket_payload,
)
from test_models_validation import epic_kwargs, ticket_kwargs

from atlas.core.models import (
    Epic,
    Ticket,
    TicketDependency,
    TicketStatus,
)
from atlas.planning import (
    DEFAULT_SIMILARITY_THRESHOLD,
    FROZEN_STATUSES,
    Backlog,
    Proposal,
    reconcile,
)
from atlas.planning.reconciler import normalise_tokens, similarity

FROZEN = sorted(status.value for status in FROZEN_STATUSES)
NON_FROZEN = sorted(
    status.value for status in TicketStatus if status not in FROZEN_STATUSES
)


def backlog_epic(**overrides: Any) -> Epic:
    return Epic(
        **epic_kwargs()
        | {
            "id": uuid4(),
            "key": "ATLAS-E1",
            "title": "Knowledge Core",
            "description": "Models are the contract.",
            "objective": "Round-trip every entity.",
            "priority": 10,
            "risk_level": "medium",
            "source_anchor": "docs/a.md#alpha",
        }
        | overrides
    )


def backlog_ticket(epic: Epic | None = None, **overrides: Any) -> Ticket:
    return Ticket(
        **ticket_kwargs()
        | {
            "id": uuid4(),
            "key": "ATLAS-1",
            "epic_id": epic.id if epic else None,
            "title": "Build the thing",
            "objective": "The thing exists.",
            "context": "Phase context.",
            "status": "backlog",
            "ticket_type": "feature",
            "risk_level": "medium",
            "priority": 10,
            "source_anchor": "docs/a.md#beta",
            "relevant_docs": [],
            "acceptance_criteria": ["It works."],
            "non_goals": ["Not the other thing."],
            "implementation_notes": [],
            "documentation_requirements": [],
            "test_requirements": ["Unit tests."],
            "definition_of_done": ["Tests pass."],
        }
        | overrides
    )


def echo_ticket(
    ticket: Ticket, epic_ref: str | None, **overrides: Any
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "key": ticket.key,
        "epic_ref": epic_ref,
        "title": ticket.title,
        "objective": ticket.objective,
        "context": ticket.context,
        "ticket_type": ticket.ticket_type.value,
        "risk_level": ticket.risk_level.value,
        "priority": ticket.priority,
        "source_anchor": ticket.source_anchor,
        "relevant_docs": list(ticket.relevant_docs),
        "acceptance_criteria": list(ticket.acceptance_criteria),
        "non_goals": list(ticket.non_goals),
        "test_requirements": list(ticket.test_requirements),
        "implementation_notes": list(ticket.implementation_notes),
        "documentation_requirements": list(ticket.documentation_requirements),
        "definition_of_done": list(ticket.definition_of_done),
    }
    return ticket_payload(**(base | overrides))


def echo_epic(epic: Epic, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "key": epic.key,
        "title": epic.title,
        "description": epic.description,
        "objective": epic.objective,
        "priority": epic.priority,
        "risk_level": epic.risk_level.value,
        "source_anchor": epic.source_anchor,
    }
    return epic_payload(**(base | overrides))


def backlog_edge(source: Ticket, target: Ticket, reason: str) -> TicketDependency:
    return TicketDependency(
        id=uuid4(),
        source_ticket_id=source.id,
        target_entity_type="ticket",
        target_entity_id=target.id,
        dependency_type="depends_on",  # type: ignore[arg-type]
        reason=reason,
        created_by_type="human",  # type: ignore[arg-type]
        created_by_id="operator",
        created_at=backlog_epic().created_at,
    )


def proposal_of(**overrides: Any) -> Proposal:
    return Proposal(**proposal_payload(**overrides))


def verbatim_world() -> tuple[Proposal, Backlog]:
    epic = backlog_epic()
    first = backlog_ticket(epic, key="ATLAS-1")
    second = backlog_ticket(
        epic,
        key="ATLAS-2",
        title="Ship the other thing",
        objective="The other thing ships.",
        source_anchor="docs/a.md#alpha",
    )
    edge = backlog_edge(second, first, "Ordering.")
    proposal = proposal_of(
        epics=[echo_epic(epic)],
        tickets=[
            echo_ticket(first, "ATLAS-E1"),
            echo_ticket(second, "ATLAS-E1"),
        ],
        dependencies=[
            dependency_payload(source="ATLAS-2", target="ATLAS-1", reason="Ordering.")
        ],
    )
    return proposal, Backlog(epics=[epic], tickets=[first, second], dependencies=[edge])


# --- spec rule: normalisation and similarity ---------------------------------


def test_normalisation_matches_spec_wording() -> None:
    # casefold; every non-alphanumeric character becomes a space;
    # whitespace-split into a token set.
    assert normalise_tokens("Hello, WORLD—foo_bar 123") == {
        "hello",
        "world",
        "foo",
        "bar",
        "123",
    }
    assert normalise_tokens("repeat repeat REPEAT") == {"repeat"}


def test_similarity_is_dice_over_concatenation() -> None:
    # |A| = |B| = 4, intersection 2 -> 2*2/8 = 0.5.
    assert similarity("a b", "c d", "a x", "c y") == 0.5
    assert similarity("", "", "", "") == 1.0


# --- determinism and AT-2 shadow ----------------------------------------------


def test_identical_inputs_produce_identical_diffs() -> None:
    _, backlog = verbatim_world()
    changed = proposal_of(
        epics=[echo_epic(backlog.epics[0])],
        tickets=[
            echo_ticket(backlog.tickets[0], "ATLAS-E1", title="Renamed thing"),
        ],
    )
    assert reconcile(changed, backlog) == reconcile(changed, backlog)


def test_verbatim_reemission_yields_empty_diff() -> None:
    # AT-2 shadow: unchanged docs -> unchanged proposal -> empty diff.
    proposal, backlog = verbatim_world()
    diff = reconcile(proposal, backlog)
    assert diff.is_empty
    assert diff.counts == {
        "ADD": 0,
        "MODIFY": 0,
        "PROPOSE_ARCHIVE": 0,
        "CONFLICT": 0,
    }


def test_first_run_against_empty_backlog_is_all_add() -> None:
    proposal = proposal_of(
        dependencies=[dependency_payload(source="new:0", target="new:0")]
    )
    proposal = proposal_of()  # one epic, one ticket, no deps
    diff = reconcile(proposal, Backlog())
    assert [entry.entry_type for entry in diff.entries] == ["ADD", "ADD"]
    assert [entry.identity for entry in diff.entries] == ["new_epic:0", "new:0"]


# --- pass precedence ------------------------------------------------------------


def test_key_match_beats_anchor_match() -> None:
    epic = backlog_epic()
    ticket_a = backlog_ticket(epic, key="ATLAS-1", source_anchor="docs/a.md#a")
    ticket_b = backlog_ticket(
        epic,
        key="ATLAS-2",
        title="Different beast entirely",
        objective="Unrelated words altogether.",
        source_anchor="docs/a.md#b",
    )
    # Echoes A's key but carries B's anchor: key must win.
    proposal = proposal_of(
        epics=[echo_epic(epic)],
        tickets=[echo_ticket(ticket_a, "ATLAS-E1", source_anchor="docs/a.md#b")],
    )
    diff = reconcile(proposal, Backlog(epics=[epic], tickets=[ticket_a, ticket_b]))
    by_identity = {entry.identity: entry for entry in diff.entries}
    assert by_identity["ATLAS-1"].entry_type == "MODIFY"
    assert by_identity["ATLAS-1"].changes["source_anchor"] == (
        "docs/a.md#a",
        "docs/a.md#b",
    )
    assert by_identity["ATLAS-2"].entry_type == "PROPOSE_ARCHIVE"
    assert diff.counts["CONFLICT"] == 0


def test_anchor_match_beats_similarity_match() -> None:
    epic = backlog_epic()
    ticket_a = backlog_ticket(
        epic,
        key="ATLAS-1",
        title="alpha alpha alpha",
        objective="alpha alpha.",
        source_anchor="docs/a.md#a",
    )
    ticket_b = backlog_ticket(
        epic,
        key="ATLAS-2",
        title="Exact proposal title",
        objective="Exact proposal objective.",
        source_anchor="docs/a.md#b",
    )
    # Text identical to B, anchor equal to A's: anchor must win.
    proposal = proposal_of(
        epics=[echo_epic(epic)],
        tickets=[
            echo_ticket(ticket_b, "ATLAS-E1", key=None, source_anchor="docs/a.md#a")
        ],
    )
    diff = reconcile(proposal, Backlog(epics=[epic], tickets=[ticket_a, ticket_b]))
    by_identity = {entry.identity: entry for entry in diff.entries}
    assert by_identity["ATLAS-1"].entry_type == "MODIFY"  # matched by anchor
    assert "title" in by_identity["ATLAS-1"].changes
    assert by_identity["ATLAS-2"].entry_type == "PROPOSE_ARCHIVE"


# --- similarity boundary ---------------------------------------------------------


def tokens_text(prefix: str, count: int) -> str:
    return " ".join(f"{prefix}{i}" for i in range(count))


def test_similarity_at_threshold_matches() -> None:
    # |A| = |B| = 20, intersection 17 -> 34/40 = 0.85 exactly.
    shared = tokens_text("t", 17)
    epic = backlog_epic()
    existing = backlog_ticket(
        epic,
        key="ATLAS-1",
        title=shared,
        objective=tokens_text("old", 3),
        source_anchor="docs/a.md#a",
    )
    proposal = proposal_of(
        epics=[echo_epic(epic)],
        tickets=[
            echo_ticket(
                existing,
                "ATLAS-E1",
                key=None,
                title=shared,
                objective=tokens_text("new", 3),
                source_anchor="docs/a.md#elsewhere",
            )
        ],
    )
    assert (
        similarity(shared, tokens_text("new", 3), shared, tokens_text("old", 3))
        == DEFAULT_SIMILARITY_THRESHOLD
    )
    diff = reconcile(proposal, Backlog(epics=[epic], tickets=[existing]))
    by_identity = {entry.identity: entry for entry in diff.entries}
    assert by_identity["ATLAS-1"].entry_type == "MODIFY"
    assert diff.counts["ADD"] == 0


def test_similarity_below_threshold_produces_add_archive_pair() -> None:
    # Intersection 16 -> 32/40 = 0.80 < 0.85.
    epic = backlog_epic()
    existing = backlog_ticket(
        epic,
        key="ATLAS-1",
        title=tokens_text("t", 16),
        objective=tokens_text("old", 4),
        source_anchor="docs/a.md#a",
    )
    proposal = proposal_of(
        epics=[echo_epic(epic)],
        tickets=[
            echo_ticket(
                existing,
                "ATLAS-E1",
                key=None,
                title=tokens_text("t", 16),
                objective=tokens_text("new", 4),
                source_anchor="docs/a.md#elsewhere",
            )
        ],
    )
    diff = reconcile(proposal, Backlog(epics=[epic], tickets=[existing]))
    assert diff.counts["ADD"] == 1
    assert diff.counts["PROPOSE_ARCHIVE"] == 1


# --- MODIFY shape -----------------------------------------------------------------


def test_modify_carries_exact_per_field_before_after() -> None:
    _, backlog = verbatim_world()
    modified = proposal_of(
        epics=[echo_epic(backlog.epics[0])],
        tickets=[
            echo_ticket(
                backlog.tickets[0],
                "ATLAS-E1",
                priority=20,
                acceptance_criteria=["It works.", "It is fast."],
            ),
            echo_ticket(backlog.tickets[1], "ATLAS-E1"),
        ],
        dependencies=[
            dependency_payload(source="ATLAS-2", target="ATLAS-1", reason="Ordering.")
        ],
    )
    diff = reconcile(modified, backlog)
    assert len(diff.entries) == 1
    entry = diff.entries[0]
    assert entry.entry_type == "MODIFY"
    assert entry.changes == {
        "priority": (10, 20),
        "acceptance_criteria": (["It works."], ["It works.", "It is fast."]),
    }


# --- AT-4 shadow: immutability ------------------------------------------------------


@pytest.mark.parametrize("status", FROZEN)
def test_frozen_modify_becomes_conflict(status: str) -> None:
    epic = backlog_epic()
    frozen = backlog_ticket(epic, key="ATLAS-1", status=status)
    proposal = proposal_of(
        epics=[echo_epic(epic)],
        tickets=[echo_ticket(frozen, "ATLAS-E1", title="Reworded title")],
    )
    diff = reconcile(proposal, Backlog(epics=[epic], tickets=[frozen]))
    assert len(diff.entries) == 1
    entry = diff.entries[0]
    assert entry.entry_type == "CONFLICT"
    assert entry.would_have_been == "MODIFY"
    assert "title" in entry.changes
    assert status in (entry.reason or "")


@pytest.mark.parametrize("status", FROZEN)
def test_frozen_archive_becomes_conflict(status: str) -> None:
    epic = backlog_epic()
    frozen = backlog_ticket(epic, key="ATLAS-1", status=status)
    fresh = ticket_payload(
        epic_ref="ATLAS-E1",
        title="Entirely unrelated replacement",
        objective="Different words throughout this objective.",
        source_anchor="docs/a.md#new",
    )
    proposal = proposal_of(epics=[echo_epic(epic)], tickets=[fresh])
    diff = reconcile(proposal, Backlog(epics=[epic], tickets=[frozen]))
    by_identity = {entry.identity: entry for entry in diff.entries}
    assert by_identity["ATLAS-1"].entry_type == "CONFLICT"
    assert by_identity["ATLAS-1"].would_have_been == "PROPOSE_ARCHIVE"


@pytest.mark.parametrize("status", NON_FROZEN)
def test_non_frozen_statuses_diff_normally(status: str) -> None:
    epic = backlog_epic()
    ticket = backlog_ticket(epic, key="ATLAS-1", status=status)
    proposal = proposal_of(
        epics=[echo_epic(epic)],
        tickets=[echo_ticket(ticket, "ATLAS-E1", title="Reworded title")],
    )
    diff = reconcile(proposal, Backlog(epics=[epic], tickets=[ticket]))
    assert [entry.entry_type for entry in diff.entries] == ["MODIFY"]


# --- ambiguity policy ------------------------------------------------------------


def test_duplicate_echoed_keys_conflict() -> None:
    epic = backlog_epic()
    existing = backlog_ticket(epic, key="ATLAS-1")
    proposal = proposal_of(
        epics=[echo_epic(epic)],
        tickets=[
            echo_ticket(existing, "ATLAS-E1", title="Claimant one"),
            echo_ticket(existing, "ATLAS-E1", title="Claimant two"),
        ],
    )
    diff = reconcile(proposal, Backlog(epics=[epic], tickets=[existing]))
    conflicts = [e for e in diff.entries if e.entry_type == "CONFLICT"]
    assert len(conflicts) == 1
    assert conflicts[0].identity == "ATLAS-1"
    assert "duplicate echoed key" in (conflicts[0].reason or "")


def test_ambiguous_anchor_group_falls_through_to_similarity() -> None:
    # Two existing tickets share an anchor; two key-less proposal items
    # share it too, content-distinct: similarity pairs them correctly
    # and the verbatim re-emission yields no ticket entries.
    epic = backlog_epic()
    first = backlog_ticket(
        epic,
        key="ATLAS-1",
        title="Storage layer work",
        objective="Repositories and migrations.",
        source_anchor="docs/a.md#shared",
    )
    second = backlog_ticket(
        epic,
        key="ATLAS-2",
        title="Renderer template work",
        objective="Prompt rendering and hashing.",
        source_anchor="docs/a.md#shared",
    )
    proposal = proposal_of(
        epics=[echo_epic(epic)],
        tickets=[
            echo_ticket(first, "ATLAS-E1", key=None),
            echo_ticket(second, "ATLAS-E1", key=None),
        ],
    )
    diff = reconcile(proposal, Backlog(epics=[epic], tickets=[first, second]))
    assert diff.is_empty


def test_exact_similarity_tie_is_conflict() -> None:
    epic = backlog_epic()
    twin_a = backlog_ticket(
        epic,
        key="ATLAS-1",
        title="Identical twin ticket",
        objective="Same words exactly.",
        source_anchor="docs/a.md#a",
    )
    twin_b = backlog_ticket(
        epic,
        key="ATLAS-2",
        title="Identical twin ticket",
        objective="Same words exactly.",
        source_anchor="docs/a.md#b",
    )
    proposal = proposal_of(
        epics=[echo_epic(epic)],
        tickets=[
            echo_ticket(twin_a, "ATLAS-E1", key=None, source_anchor="docs/a.md#c")
        ],
    )
    diff = reconcile(proposal, Backlog(epics=[epic], tickets=[twin_a, twin_b]))
    conflicts = [e for e in diff.entries if e.entry_type == "CONFLICT"]
    assert len(conflicts) == 1
    assert "similarity tie" in (conflicts[0].reason or "")
    assert "ATLAS-1" in (conflicts[0].reason or "")
    assert "ATLAS-2" in (conflicts[0].reason or "")


# --- dependency edges -------------------------------------------------------------


def test_edge_added() -> None:
    _, backlog = verbatim_world()
    with_edge = proposal_of(
        epics=[echo_epic(backlog.epics[0])],
        tickets=[
            echo_ticket(backlog.tickets[0], "ATLAS-E1"),
            echo_ticket(backlog.tickets[1], "ATLAS-E1"),
        ],
        dependencies=[
            dependency_payload(source="ATLAS-2", target="ATLAS-1", reason="Ordering."),
            dependency_payload(
                source="ATLAS-1", target="ATLAS-2", reason="New linkage."
            ),
        ],
    )
    diff = reconcile(with_edge, backlog)
    assert [entry.entry_type for entry in diff.entries] == ["ADD"]
    assert diff.entries[0].kind == "dependency"
    assert diff.entries[0].identity == "ATLAS-1 -> ATLAS-2"


def test_edge_reason_change_is_modify() -> None:
    _, backlog = verbatim_world()
    reworded = proposal_of(
        epics=[echo_epic(backlog.epics[0])],
        tickets=[
            echo_ticket(backlog.tickets[0], "ATLAS-E1"),
            echo_ticket(backlog.tickets[1], "ATLAS-E1"),
        ],
        dependencies=[
            dependency_payload(
                source="ATLAS-2", target="ATLAS-1", reason="Sharper reason."
            )
        ],
    )
    diff = reconcile(reworded, backlog)
    assert len(diff.entries) == 1
    entry = diff.entries[0]
    assert entry.entry_type == "MODIFY"
    assert entry.changes == {"reason": ("Ordering.", "Sharper reason.")}


def test_archiving_ticket_archives_its_edges_explicitly() -> None:
    _, backlog = verbatim_world()
    without_second = proposal_of(
        epics=[echo_epic(backlog.epics[0])],
        tickets=[echo_ticket(backlog.tickets[0], "ATLAS-E1")],
    )
    diff = reconcile(without_second, backlog)
    archives = [e for e in diff.entries if e.entry_type == "PROPOSE_ARCHIVE"]
    assert {(e.kind, e.identity) for e in archives} == {
        ("ticket", "ATLAS-2"),
        ("dependency", "ATLAS-2 -> ATLAS-1"),
    }


def test_frozen_source_edge_change_conflicts() -> None:
    epic = backlog_epic()
    frozen = backlog_ticket(epic, key="ATLAS-1", status="done")
    other = backlog_ticket(
        epic, key="ATLAS-2", title="Other work", source_anchor="docs/a.md#x"
    )
    edge = backlog_edge(frozen, other, "Old reason.")
    proposal = proposal_of(
        epics=[echo_epic(epic)],
        tickets=[
            echo_ticket(frozen, "ATLAS-E1"),
            echo_ticket(other, "ATLAS-E1"),
        ],
        dependencies=[
            dependency_payload(
                source="ATLAS-1", target="ATLAS-2", reason="Reworded reason."
            )
        ],
    )
    diff = reconcile(
        proposal, Backlog(epics=[epic], tickets=[frozen, other], dependencies=[edge])
    )
    assert len(diff.entries) == 1
    entry = diff.entries[0]
    assert entry.entry_type == "CONFLICT"
    assert entry.would_have_been == "MODIFY"
    assert entry.kind == "dependency"


def test_frozen_target_edge_add_is_permitted() -> None:
    # New work may depend on completed work (spec §4).
    epic = backlog_epic()
    done = backlog_ticket(epic, key="ATLAS-1", status="done")
    proposal = proposal_of(
        epics=[echo_epic(epic)],
        tickets=[
            echo_ticket(done, "ATLAS-E1"),
            ticket_payload(epic_ref="ATLAS-E1", source_anchor="docs/a.md#new"),
        ],
        dependencies=[
            dependency_payload(source="new:1", target="ATLAS-1", reason="Builds on.")
        ],
    )
    diff = reconcile(proposal, Backlog(epics=[epic], tickets=[done]))
    by_kind = {(e.kind, e.entry_type) for e in diff.entries}
    assert ("dependency", "ADD") in by_kind
    assert ("dependency", "CONFLICT") not in by_kind


# --- ordering and summary shape -----------------------------------------------------


def test_entry_ordering_is_deterministic_and_documented() -> None:
    _, backlog = verbatim_world()
    mixed = proposal_of(
        epics=[echo_epic(backlog.epics[0]), epic_payload(title="New epic")],
        tickets=[
            echo_ticket(backlog.tickets[0], "ATLAS-E1", title="Renamed"),
            ticket_payload(epic_ref="new_epic:1", source_anchor="docs/a.md#n"),
        ],
        dependencies=[
            dependency_payload(source="new:1", target="ATLAS-1", reason="Link.")
        ],
    )
    diff = reconcile(mixed, backlog)
    kinds = [entry.kind for entry in diff.entries]
    assert kinds == sorted(kinds, key=["epic", "ticket", "dependency"].index)
    summary = diff.as_summary()
    assert set(summary) == {"counts", "entries"}
    assert summary["counts"]["MODIFY"] >= 1
    assert all(
        set(entry) >= {"type", "kind", "identity", "title"}
        for entry in summary["entries"]
    )
