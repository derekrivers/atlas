"""ATLAS-54: related-ticket retrieval over ATLAS-31's projection, per
docs/atlas/context-renderer.md rule 3.

Deterministic and pure — ZERO model calls, ZERO storage queries. Each lever
has a falsifiable fixture: a depends_on target is selected carrying the node
UUID; a depends_on dependent is selected with the direction not swapped; a
relates_to / implements edge is excluded (the rule-3 filter); a depends_on
edge to an ADR is excluded (tickets only); a 2-hop ticket is excluded (direct
only); a mutual pair dedupes to one TARGET; the cap holds with targets ahead of
dependents in an exact ordered list independent of input order; empty yields
[] and an absent ticket raises; an absent neighbour is skipped; and a
self-depends_on edge never lists the ticket itself.
"""

from uuid import uuid4

import pytest
from test_models_validation import adr_kwargs, dependency_kwargs, ticket_kwargs

from atlas.context import RelatedTicket, RelatedTicketSource, select_related_tickets
from atlas.context.related_tickets import DEFAULT_RELATED_CAP
from atlas.core.models import ArchitectureDecisionRecord, Ticket, TicketDependency
from atlas.dependencies import project_graph


def make_ticket(key: str = "ATLAS-1", **overrides: object) -> Ticket:
    base = {"id": uuid4(), "key": key, "status": "planned"}
    return Ticket(**ticket_kwargs() | base | overrides)


def make_adr(
    number: int, title: str, **overrides: object
) -> ArchitectureDecisionRecord:
    base = {"id": uuid4(), "number": number, "title": title, "status": "accepted"}
    return ArchitectureDecisionRecord(**adr_kwargs() | base | overrides)


def depends_on(
    source: Ticket,
    target_id: object,
    target_type: str = "ticket",
    dependency_type: str = "depends_on",
) -> TicketDependency:
    return TicketDependency(
        **dependency_kwargs()
        | {
            "id": uuid4(),
            "source_ticket_id": source.id,
            "target_entity_type": target_type,
            "target_entity_id": target_id,
            "dependency_type": dependency_type,
        }
    )


# 1. Target selected — a depends_on edge ticket -> T yields T, carrying the node
# entity_id. Wrong answer: missed, or entity_id != ticket id.
def test_depends_on_target_is_selected() -> None:
    ticket = make_ticket("ATLAS-1")
    target = make_ticket("ATLAS-2")
    graph = project_graph([ticket, target], [], [], [depends_on(ticket, target.id)])

    result = select_related_tickets(graph, ticket)

    assert result == [RelatedTicket(target.id, "ATLAS-2", RelatedTicketSource.TARGET)]


# 2. Dependent selected — a depends_on edge D -> ticket yields D, DEPENDENT.
# Wrong answer: direction confused (in/out swapped).
def test_depends_on_dependent_is_selected() -> None:
    ticket = make_ticket("ATLAS-1")
    dependent = make_ticket("ATLAS-2")
    graph = project_graph(
        [ticket, dependent], [], [], [depends_on(dependent, ticket.id)]
    )

    result = select_related_tickets(graph, ticket)

    assert result == [
        RelatedTicket(dependent.id, "ATLAS-2", RelatedTicketSource.DEPENDENT)
    ]


# 3. Edge-type filter — a relates_to / implements ticket edge is NOT selected.
# Wrong answer: a non-depends_on neighbour appears.
@pytest.mark.parametrize("dependency_type", ["relates_to", "implements"])
def test_non_depends_on_ticket_edge_is_excluded(dependency_type: str) -> None:
    ticket = make_ticket("ATLAS-1")
    other = make_ticket("ATLAS-2")
    graph = project_graph(
        [ticket, other],
        [],
        [],
        [depends_on(ticket, other.id, dependency_type=dependency_type)],
    )

    assert select_related_tickets(graph, ticket) == []


# 4. Type filter — a depends_on edge to an ADR is NOT selected (tickets only).
# Wrong answer: an ADR surfaces as a related ticket.
def test_depends_on_adr_target_is_excluded() -> None:
    ticket = make_ticket("ATLAS-1")
    adr = make_adr(2, "PostgreSQL for state")
    graph = project_graph(
        [ticket], [], [adr], [depends_on(ticket, adr.id, target_type="adr")]
    )

    assert select_related_tickets(graph, ticket) == []


# 5. Direct only — a 2-hop ticket (a target's target) is NOT included.
# Wrong answer: transitive inclusion.
def test_two_hop_ticket_is_excluded() -> None:
    ticket = make_ticket("ATLAS-1")
    target = make_ticket("ATLAS-2")
    grand = make_ticket("ATLAS-3")
    graph = project_graph(
        [ticket, target, grand],
        [],
        [],
        [depends_on(ticket, target.id), depends_on(target, grand.id)],
    )

    result = select_related_tickets(graph, ticket)

    assert result == [RelatedTicket(target.id, "ATLAS-2", RelatedTicketSource.TARGET)]


# 6. Dedup — a mutual depends_on pair yields the other ticket once, as a target.
def test_mutual_depends_on_dedupes_to_target() -> None:
    ticket = make_ticket("ATLAS-1")
    other = make_ticket("ATLAS-2")
    graph = project_graph(
        [ticket, other],
        [],
        [],
        [depends_on(ticket, other.id), depends_on(other, ticket.id)],
    )

    result = select_related_tickets(graph, ticket)

    assert result == [RelatedTicket(other.id, "ATLAS-2", RelatedTicketSource.TARGET)]


# 7. Cap and ranking — with >8 related tickets, exactly 8 are returned, targets
# ahead of dependents, in a deterministic order asserted as an exact ordered
# list for two different input orderings. Wrong answer: >8, non-determinism, or
# a dependent displacing a target.
def test_cap_and_ranking_is_exact_order_independent_of_input() -> None:
    ticket = make_ticket("ATLAS-1")
    # Five targets (ATLAS-20..24) and five dependents (ATLAS-10..14): ten
    # related, so the cap of 8 must drop the two highest-key dependents and
    # keep all five targets ahead.
    targets = [make_ticket(f"ATLAS-2{n}") for n in range(5)]
    dependents = [make_ticket(f"ATLAS-1{n}") for n in range(5)]
    deps = [depends_on(ticket, t.id) for t in targets] + [
        depends_on(d, ticket.id) for d in dependents
    ]

    expected = [
        RelatedTicket(targets[0].id, "ATLAS-20", RelatedTicketSource.TARGET),
        RelatedTicket(targets[1].id, "ATLAS-21", RelatedTicketSource.TARGET),
        RelatedTicket(targets[2].id, "ATLAS-22", RelatedTicketSource.TARGET),
        RelatedTicket(targets[3].id, "ATLAS-23", RelatedTicketSource.TARGET),
        RelatedTicket(targets[4].id, "ATLAS-24", RelatedTicketSource.TARGET),
        RelatedTicket(dependents[0].id, "ATLAS-10", RelatedTicketSource.DEPENDENT),
        RelatedTicket(dependents[1].id, "ATLAS-11", RelatedTicketSource.DEPENDENT),
        RelatedTicket(dependents[2].id, "ATLAS-12", RelatedTicketSource.DEPENDENT),
    ]

    nodes = [ticket, *targets, *dependents]
    forward = project_graph(nodes, [], [], deps)
    reversed_ = project_graph(list(reversed(nodes)), [], [], list(reversed(deps)))

    assert select_related_tickets(forward, ticket) == expected
    assert select_related_tickets(reversed_, ticket) == expected
    assert len(select_related_tickets(forward, ticket)) == DEFAULT_RELATED_CAP


# 8a. Empty — a ticket with no depends_on neighbours yields []. Wrong answer: a
# crash on empty.
def test_no_neighbours_yields_empty() -> None:
    ticket = make_ticket("ATLAS-1")
    graph = project_graph([ticket], [], [], [])

    assert select_related_tickets(graph, ticket) == []


# 8b. Absent — a ticket absent from the graph raises ValueError (mirroring 51).
# Wrong answer: a silent [] on an absent ticket.
def test_absent_ticket_raises() -> None:
    ticket = make_ticket("ATLAS-404")
    graph = project_graph([], [], [], [])

    with pytest.raises(ValueError, match="no such node in graph"):
        select_related_tickets(graph, ticket)


def test_non_ticket_node_raises() -> None:
    # A key that resolves to a non-ticket node is a precondition violation,
    # distinct from "no related tickets found".
    adr = make_adr(2, "PostgreSQL for state")
    impostor = make_ticket(key="ADR-0002")
    graph = project_graph([], [], [adr], [])

    with pytest.raises(ValueError, match="not a present ticket node"):
        select_related_tickets(graph, impostor)


# 9. Present filter — an absent (present=False) neighbour node is skipped.
def test_absent_neighbour_is_skipped() -> None:
    ticket = make_ticket("ATLAS-1")
    missing_id = uuid4()
    graph = project_graph([ticket], [], [], [depends_on(ticket, missing_id)])

    assert select_related_tickets(graph, ticket) == []


# 10. Self-reference — a ticket with a self-depends_on edge yields [] (it does
# not appear in its own related list). Wrong answer: the ticket lists itself.
def test_self_depends_on_edge_is_skipped() -> None:
    ticket = make_ticket("ATLAS-1")
    graph = project_graph([ticket], [], [], [depends_on(ticket, ticket.id)])

    assert select_related_tickets(graph, ticket) == []
