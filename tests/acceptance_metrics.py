"""AT-7 coverage metric (ATLAS-29), defined in spec §7.1.

Enumerates the hand-written tickets in the implementation roadmap and
computes the anchor-match coverage of a proposal against them. Test-only:
production plan/apply never compute coverage. The metric is exercised two
ways — a synthetic pair pins the percentage math, the real roadmap pins
the enumeration denominator (a roadmap reformat the parser misses changes
the count and fires the test).

Definition (spec §7.1): a hand-written ticket is a line matching
`^ATLAS-<n> <title>` (Retired/wrapped lines excluded); its anchor is the
ingestion slug (§2.3) of the nearest preceding heading; a ticket is
covered iff some proposed ticket's source_anchor equals it exactly.
Exact-anchor matching can only undercount, so coverage is a lower bound.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from atlas.planning.ingestion import slugify

ROADMAP_PATH = "docs/atlas/implementation-roadmap.md"

_TICKET_RE = re.compile(r"^ATLAS-\d+\s+\S")
_HEADING_RE = re.compile(r"^#{1,6}\s+(.*?)\s*$")
_FENCE_RE = re.compile(r"^\s*```")


@dataclass(frozen=True)
class RoadmapTicket:
    """One hand-written roadmap ticket and the anchor it sits under."""

    key: str
    anchor: str  # "<path>#<slug>"


def enumerate_roadmap_tickets(
    text: str, *, path: str = ROADMAP_PATH
) -> list[RoadmapTicket]:
    """Hand-written tickets in roadmap order, each anchored to its nearest
    preceding heading. Headings inside fenced code blocks are not headings
    (§2.3), so fenced content is skipped."""
    tickets: list[RoadmapTicket] = []
    current_anchor: str | None = None
    in_fence = False
    for line in text.splitlines():
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            current_anchor = f"{path}#{slugify(heading.group(1))}"
            continue
        if _TICKET_RE.match(line):
            key = line.split(None, 1)[0]
            # A ticket before any heading has no anchor it could match.
            tickets.append(RoadmapTicket(key=key, anchor=current_anchor or f"{path}#"))
    return tickets


def anchor_coverage(
    proposed_anchors: Iterable[str], roadmap_text: str, *, path: str = ROADMAP_PATH
) -> float:
    """Fraction of hand-written tickets whose anchor is hit by a proposed
    ticket's source_anchor (exact equality). A lower bound on true
    coverage: a correct proposal anchored to an adjacent heading scores as
    a miss, never a false hit."""
    tickets = enumerate_roadmap_tickets(roadmap_text, path=path)
    if not tickets:
        return 1.0  # vacuous: no hand-written tickets to cover
    proposed = set(proposed_anchors)
    covered = sum(1 for ticket in tickets if ticket.anchor in proposed)
    return covered / len(tickets)
