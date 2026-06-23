"""ADR retrieval (ATLAS-51), per docs/atlas/context-renderer.md
"Retrieval rules (v1)" rule 2.

Selects the ADRs worth putting in front of an execution agent for one
ticket. Two sources, in priority order:

1. **Dependency targets** — every ADR the ticket has a dependency edge to,
   read straight off ATLAS-31's projected graph. These are explicit links
   the operator/planner drew, so they are always relevant and rank first.
2. **Tag matches** — any *accepted* ADR whose title tokens intersect the
   ticket's free-form ``tags``/``component`` facets (ATLAS-127). A weaker,
   heuristic signal, so it only fills the slots dependency targets leave.

Pure computation: ZERO model/API calls and ZERO storage queries. The graph
is the input for source 1 (it already carries each ADR node's ``number``,
``key``, ``status`` and ``present`` — no body text), and the caller passes
the accepted-ADR listing for source 2 (building the graph and listing is
the caller's job — ATLAS-56/-58 — so this stays a pure function exercised
directly in tests). It selects *which* ADRs; rendering them as
decision + consequences is the context renderer's job.

Ordering is a total order so callers and tests can assert an exact list:
dependency targets first by ascending ADR number, then tag matches by
descending shared-token count and ascending ADR number as the tie-break.
The whole selection is capped (default 5, per the design); dependency
targets are never dropped in favour of a weaker tag match.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

import networkx as nx

from atlas.core.models.adr import ADRStatus, ArchitectureDecisionRecord
from atlas.core.models.ticket import Ticket
from atlas.dependencies.graph import adr_key

# The design's cap on rendered ADRs (context-renderer.md rule 2: "Cap: 5").
DEFAULT_ADR_CAP = 5

# A deliberately small stop-word set: generic English glue that would
# otherwise let an ADR match a ticket on a meaningless shared token. Kept
# minimal on purpose — a controlled component/tag vocabulary is deferred
# (context-renderer.md Open items), so over-pruning here would silently lose
# real matches.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "and",
        "as",
        "at",
        "be",
        "by",
        "for",
        "in",
        "is",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
    }
)

# Tokens are alphanumeric runs of the lower-cased text; punctuation and
# whitespace split. Pure-digit tokens are dropped at tokenisation time (a
# ticket tag must never match an ADR by a bare number — ADR identity is the
# ``number`` field, not free text), as are stop-words.
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenise(*texts: str) -> frozenset[str]:
    """The matchable token set of ``texts`` — lower-cased alphanumeric runs
    with pure-digit tokens and stop-words removed."""
    tokens: set[str] = set()
    for text in texts:
        for token in _TOKEN_RE.findall(text.lower()):
            if token.isdigit() or token in _STOPWORDS:
                continue
            tokens.add(token)
    return frozenset(tokens)


class ADRMatchSource(StrEnum):
    """Why an ADR was selected, so the caller can explain provenance and
    rank — a dependency edge the planner drew vs. a heuristic tag overlap."""

    DEPENDENCY = "dependency"
    TAG = "tag"


@dataclass(frozen=True)
class ADRMatch:
    """One selected ADR. ``adr_id`` is the stored ADR's UUID — the identity the
    ContextPack records in ``relevant_adrs``; ``key`` is the synthesised
    repo-wide form (``ADR-0006``); ``shared_tokens`` are the ticket/title tokens
    that drove a :attr:`ADRMatchSource.TAG` match (empty for a dependency
    target), in sorted order so the value is deterministic."""

    adr_id: UUID
    key: str
    number: int
    source: ADRMatchSource
    shared_tokens: tuple[str, ...] = ()


def select_adrs(
    graph: nx.DiGraph[str],
    ticket: Ticket,
    accepted_adrs: list[ArchitectureDecisionRecord],
    *,
    cap: int = DEFAULT_ADR_CAP,
) -> list[ADRMatch]:
    """Select up to ``cap`` ADRs for ``ticket`` from its dependency targets
    in ``graph`` and the ``accepted_adrs`` listing.

    ``ticket.key`` must name a present ticket node in ``graph`` — that is a
    precondition, not a retrieval condition, so a missing key or a non-ticket
    node raises :class:`ValueError` (mirroring ATLAS-34's readiness contract).

    Reads only ATLAS-31's node attributes for source 1; never touches storage.
    Dependency targets that are absent (``present=False``) ADR nodes cannot be
    rendered and are skipped here — ATLAS-40's ``validate_graph`` is the gate
    that raises on a dangling target, not this retriever.
    """
    if ticket.key not in graph:
        raise ValueError(f"no such node in graph: {ticket.key!r}")
    ticket_data = graph.nodes[ticket.key]
    if ticket_data.get("node_type") != "ticket" or not ticket_data.get("present", True):
        raise ValueError(f"not a present ticket node: {ticket.key!r}")

    # Source 1: ADR dependency targets. Edge A -> B means A depends_on B, so
    # the ticket's ADR targets are its out-edges to ADR nodes. Any dependency
    # type counts — a ticket linked to a decision (depends_on, implements,
    # relates_to) is owed that decision's context. Keyed by number to dedupe
    # both repeated edges and, below, a tag match for the same ADR.
    dependency_numbers: set[int] = set()
    dependency_matches: list[ADRMatch] = []
    for _source, target in graph.out_edges(ticket.key):
        target_data = graph.nodes[target]
        if target_data.get("node_type") != "adr":
            continue
        if not target_data.get("present", True):
            continue
        number = target_data["number"]
        if number in dependency_numbers:
            continue
        dependency_numbers.add(number)
        dependency_matches.append(
            ADRMatch(
                target_data["entity_id"],
                target_data["key"],
                number,
                ADRMatchSource.DEPENDENCY,
            )
        )
    dependency_matches.sort(key=lambda match: match.number)

    # Source 2: accepted ADRs whose title tokens intersect the ticket's
    # tags/component. Accepted only (a proposed/rejected/superseded decision
    # is not live context); a dependency target already selected is never
    # re-added as a weaker tag match.
    ticket_tokens = _tokenise(*ticket.tags, ticket.component or "")
    tag_matches: list[ADRMatch] = []
    if ticket_tokens:
        for adr in accepted_adrs:
            if adr.status != ADRStatus.ACCEPTED:
                continue
            if adr.number in dependency_numbers:
                continue
            shared = ticket_tokens & _tokenise(adr.title)
            if not shared:
                continue
            tag_matches.append(
                ADRMatch(
                    adr.id,
                    adr_key(adr.number),
                    adr.number,
                    ADRMatchSource.TAG,
                    tuple(sorted(shared)),
                )
            )
    # Descending shared-token count, ascending number as the total-order
    # tie-break (never input or set-iteration order).
    tag_matches.sort(key=lambda match: (-len(match.shared_tokens), match.number))

    return (dependency_matches + tag_matches)[:cap]
