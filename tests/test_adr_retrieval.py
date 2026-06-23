"""ATLAS-51: ADR retrieval over ATLAS-31's projection + an accepted-ADR
listing, per docs/atlas/context-renderer.md rule 2.

Deterministic and pure — ZERO model calls, ZERO storage queries. Each lever
has a falsifiable fixture: dependency targets rank first by number; accepted
ADRs match on tag/component title-token overlap; pure-digit and stop-word
tokens never create a match; a dependency target is never duplicated by a
tag match; the cap holds and favours dependency targets; the tag tie-break
is a total order independent of input order; and a bad ticket key raises.
"""

from uuid import uuid4

import pytest
from test_models_validation import adr_kwargs, dependency_kwargs, ticket_kwargs

from atlas.context import ADRMatch, ADRMatchSource, select_adrs
from atlas.context.adr_retrieval import DEFAULT_ADR_CAP
from atlas.core.models import ArchitectureDecisionRecord, Ticket, TicketDependency
from atlas.dependencies import adr_key, project_graph


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
    target_type: str = "adr",
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


def test_dependency_targets_rank_first_by_number() -> None:
    ticket = make_ticket()
    high = make_adr(8, "Evidence trust tiers")
    low = make_adr(2, "PostgreSQL for state")
    graph = project_graph(
        [ticket],
        [],
        [high, low],
        [depends_on(ticket, high.id), depends_on(ticket, low.id)],
    )

    result = select_adrs(graph, ticket, [])

    assert result == [
        ADRMatch("ADR-0002", 2, ADRMatchSource.DEPENDENCY),
        ADRMatch("ADR-0008", 8, ADRMatchSource.DEPENDENCY),
    ]


def test_any_dependency_type_to_an_adr_is_a_target() -> None:
    # A ticket that "implements" a decision is owed its context as much as one
    # that "depends_on" it; the source is the dependency edge, not its type.
    ticket = make_ticket()
    adr = make_adr(7, "Generative planning")
    graph = project_graph(
        [ticket], [], [adr], [depends_on(ticket, adr.id, dependency_type="implements")]
    )

    result = select_adrs(graph, ticket, [])

    assert result == [ADRMatch("ADR-0007", 7, ADRMatchSource.DEPENDENCY)]


def test_tag_match_on_accepted_adr_title() -> None:
    ticket = make_ticket(tags=["postgresql", "storage"])
    adr = make_adr(2, "PostgreSQL for operational state")
    graph = project_graph([ticket], [], [adr], [])

    result = select_adrs(graph, ticket, [adr])

    assert result == [ADRMatch("ADR-0002", 2, ADRMatchSource.TAG, ("postgresql",))]


def test_component_contributes_match_tokens() -> None:
    ticket = make_ticket(tags=[], component="evidence")
    adr = make_adr(8, "CI-sourced evidence with trust tiers")
    graph = project_graph([ticket], [], [adr], [])

    result = select_adrs(graph, ticket, [adr])

    assert result == [ADRMatch("ADR-0008", 8, ADRMatchSource.TAG, ("evidence",))]


def test_only_accepted_adrs_match_on_tags() -> None:
    ticket = make_ticket(tags=["planning"])
    proposed = make_adr(7, "Generative planning", status="proposed")
    graph = project_graph([ticket], [], [], [])

    assert select_adrs(graph, ticket, [proposed]) == []


def test_pure_digit_tag_never_matches_a_number() -> None:
    # A bare number must not match an ADR: ADR identity is the number field,
    # not free text. "0007" and "7" are dropped at tokenisation.
    ticket = make_ticket(tags=["0007", "7"])
    adr = make_adr(7, "Generative planning")
    graph = project_graph([ticket], [], [adr], [])

    assert select_adrs(graph, ticket, [adr]) == []


def test_stopword_overlap_does_not_match() -> None:
    ticket = make_ticket(tags=["the", "for"])
    adr = make_adr(2, "PostgreSQL for the state")
    graph = project_graph([ticket], [], [adr], [])

    assert select_adrs(graph, ticket, [adr]) == []


def test_dependency_target_not_duplicated_by_tag_match() -> None:
    # An ADR that is both a dependency target AND a tag match appears once,
    # as the stronger dependency source.
    ticket = make_ticket(tags=["planning"])
    adr = make_adr(7, "Generative planning")
    graph = project_graph([ticket], [], [adr], [depends_on(ticket, adr.id)])

    result = select_adrs(graph, ticket, [adr])

    assert result == [ADRMatch("ADR-0007", 7, ADRMatchSource.DEPENDENCY)]


def test_absent_adr_dependency_target_is_skipped() -> None:
    # A dependency to an ADR with no stored entity projects as an absent node
    # (present=False); it cannot be rendered, so retrieval skips it (ATLAS-40
    # is the gate that raises on the dangling target, not this retriever).
    ticket = make_ticket()
    missing_id = uuid4()
    graph = project_graph([ticket], [], [], [depends_on(ticket, missing_id)])

    assert select_adrs(graph, ticket, []) == []


def test_tag_tiebreak_is_total_order_independent_of_input() -> None:
    # Test 5: two tag matches with EQUAL shared-token count must order by
    # ascending ADR number — never by accepted_adrs input or set iteration.
    ticket = make_ticket(tags=["state"])
    later = make_adr(8, "Evidence state tiers")
    earlier = make_adr(2, "PostgreSQL state")
    graph = project_graph([ticket], [], [earlier, later], [])

    expected = [
        ADRMatch("ADR-0002", 2, ADRMatchSource.TAG, ("state",)),
        ADRMatch("ADR-0008", 8, ADRMatchSource.TAG, ("state",)),
    ]
    # Identical result for either input ordering — the assertion is the exact
    # ordered list, not membership.
    assert select_adrs(graph, ticket, [earlier, later]) == expected
    assert select_adrs(graph, ticket, [later, earlier]) == expected


def test_more_shared_tokens_rank_above_fewer() -> None:
    ticket = make_ticket(tags=["postgresql", "operational", "state"])
    weak = make_adr(2, "PostgreSQL elsewhere")  # shares 1
    strong = make_adr(8, "Operational state store")  # shares 2
    graph = project_graph([ticket], [], [weak, strong], [])

    result = select_adrs(graph, ticket, [weak, strong])

    assert [match.number for match in result] == [8, 2]


def test_dependency_targets_outrank_tag_matches_at_the_cap() -> None:
    ticket = make_ticket(tags=["state"])
    deps = [make_adr(n, f"Decision {n}") for n in range(1, DEFAULT_ADR_CAP + 1)]
    tagged = make_adr(9, "Shared state decision")
    graph = project_graph(
        [ticket],
        [],
        [*deps, tagged],
        [depends_on(ticket, adr.id) for adr in deps],
    )

    result = select_adrs(graph, ticket, [tagged])

    # Cap honoured and every slot taken by a dependency target; the tag match
    # is squeezed out rather than displacing an explicit link.
    assert len(result) == DEFAULT_ADR_CAP
    assert all(match.source is ADRMatchSource.DEPENDENCY for match in result)
    assert [match.number for match in result] == [1, 2, 3, 4, 5]


def test_cap_is_overridable() -> None:
    ticket = make_ticket()
    adrs = [make_adr(n, f"Decision {n}") for n in (3, 1, 2)]
    graph = project_graph(
        [ticket], [], adrs, [depends_on(ticket, adr.id) for adr in adrs]
    )

    result = select_adrs(graph, ticket, [], cap=2)

    assert [match.key for match in result] == [adr_key(1), adr_key(2)]


def test_no_facets_yields_only_dependency_targets() -> None:
    ticket = make_ticket(tags=[], component=None)
    adr = make_adr(2, "PostgreSQL for operational state")
    graph = project_graph([ticket], [], [adr], [])

    # No tags/component → no tokens → no tag matches, even with a candidate.
    assert select_adrs(graph, ticket, [adr]) == []


def test_unknown_ticket_key_raises() -> None:
    ticket = make_ticket("ATLAS-404")
    graph = project_graph([], [], [], [])

    with pytest.raises(ValueError, match="no such node in graph"):
        select_adrs(graph, ticket, [])


def test_non_ticket_node_raises() -> None:
    # A key that resolves to a non-ticket node is a precondition violation,
    # distinct from "no ADRs found".
    adr = make_adr(2, "PostgreSQL for state")
    impostor = make_ticket(key=adr_key(2))
    graph = project_graph([], [], [adr], [])

    with pytest.raises(ValueError, match="not a present ticket node"):
        select_adrs(graph, impostor, [])
