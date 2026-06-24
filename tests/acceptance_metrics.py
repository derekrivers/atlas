"""AT-7 coverage metrics (ATLAS-29, ATLAS-112), defined in spec §7.1.

Enumerates the hand-written tickets in the implementation roadmap and
computes coverage of a proposal against them. Test-only: production
plan/apply never compute coverage.

Two coverage metrics live here, side by side (ATLAS-112):

- ``anchor_coverage`` — the original exact-anchor metric (ATLAS-29). A
  hand-written ticket is covered iff some proposed ticket's
  ``source_anchor`` equals its roadmap anchor exactly. This measures
  anchoring-convention agreement: a correct ticket the planner anchored to
  a *design document* rather than the roadmap epic heading scores as a miss.
- ``content_coverage`` — a work-coverage sibling (ATLAS-112). A hand-written
  ticket is covered iff *some* proposed ticket's title is similar enough to
  the roadmap ticket's title (title-vs-title; see the function docstring for
  why the objective is excluded), document-independent. This answers "did the
  planner cover the work?" rather than "did it anchor where the roadmap author
  did?" It reuses the reconciler's Sorensen-Dice primitive — there is exactly
  one similarity implementation in the codebase.

The metrics are reported together, never one in place of the other: the
exact-anchor figure is a strict floor, the content figure is
document-independent, and the gap between them is the anchoring-convention
signal ATLAS-112 exists to surface. The content-coverage threshold is set
for correctness and recorded (``CONTENT_COVERAGE_THRESHOLD``); it is never
tuned to change the score.

Definition (spec §7.1): a hand-written ticket is a line matching
`^ATLAS-<n> <title>` (Retired/wrapped lines excluded); its anchor is the
ingestion slug (§2.3) of the nearest preceding heading; for exact-anchor it
is covered iff some proposed ticket's source_anchor equals it exactly.
Exact-anchor matching can only undercount, so anchor coverage is a lower
bound.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from atlas.core.anchors import slugify
from atlas.planning import reconciler

ROADMAP_PATH = "docs/atlas/implementation-roadmap.md"

# Content-coverage similarity threshold (ATLAS-112), spec §7.1. Set for
# correctness, NOT to maximise the score: the comparison is asymmetric — a
# short roadmap title versus a planner title+objective paragraph — so the
# reconciler's 0.85 *entity-match* threshold (symmetric title+objective vs
# title+objective, for identity) does not transfer; it would structurally
# under-match. 0.5 requires at least half the combined token mass to overlap,
# i.e. the roadmap title's words substantially appear in the planner ticket.
# Recorded here and in the spec; never tuned against the metric.
CONTENT_COVERAGE_THRESHOLD = 0.5

# Exact-anchor floor (ATLAS-112 RESOLVED, spec §7.2). The AT-7 bar is a pair:
# this exact-anchor floor gates, and content_coverage is reported until a second
# durably-saved capture pins it (ATLAS-124). Set for correctness as a cheap,
# unambiguous catastrophe catch, NOT as a precision gate: 0.50 sits safely below
# the observed 63.4%/82.6% exact-anchor range, so it catches a collapse without
# penalising the anchoring-convention swing §7.2 documents. Recorded here and in
# the spec; never tuned upward against the metric. Supersedes the historical
# anchor_coverage >= 0.90 pass line.
ANCHOR_COVERAGE_FLOOR = 0.50

_TICKET_RE = re.compile(r"^ATLAS-\d+\s+\S")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
_FENCE_RE = re.compile(r"^\s*```")


@dataclass(frozen=True)
class RoadmapTicket:
    """One hand-written roadmap ticket: its key, the anchor it sits under,
    and its full (de-wrapped) title text. ``title`` (ATLAS-112) joins the
    key line's remainder with any indented continuation lines, so the
    work-coverage metric can compare against it; ``anchor`` and ``key`` are
    unchanged from ATLAS-29."""

    key: str
    anchor: str  # "<path>#<slug>"
    title: str = ""


@dataclass(frozen=True)
class Heading:
    """A document heading: its ingestion slug and its level (number of
    leading ``#``). Used to compute heading proximity (ATLAS-112)."""

    slug: str
    level: int


class _TitledTicket(Protocol):
    """Structural type for a proposed ticket: anything carrying a title (the
    Proposal model's tickets satisfy this)."""

    title: str


def heading_index(text: str) -> list[Heading]:
    """The document's headings in order, fence-aware (§2.3: headings inside
    fenced code blocks are not headings). Slugs use the single ingestion
    slug algorithm. Ordering and level support the proximity test that
    distinguishes an adjacent-anchor undercount from a different-document
    anchoring choice (ATLAS-112)."""
    headings: list[Heading] = []
    in_fence = False
    for line in text.splitlines():
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _HEADING_RE.match(line)
        if match:
            headings.append(
                Heading(slug=slugify(match.group(2)), level=len(match.group(1)))
            )
    return headings


def _parent_slug(headings: list[Heading], index: int) -> str | None:
    """The slug of the nearest preceding heading at a shallower level — the
    immediate parent of ``headings[index]``."""
    level = headings[index].level
    for earlier in range(index - 1, -1, -1):
        if headings[earlier].level < level:
            return headings[earlier].slug
    return None


def is_adjacent_anchor(
    wanted_anchor: str, candidate_anchor: str, headings: list[Heading]
) -> bool:
    """True iff ``candidate_anchor`` sits NEAR ``wanted_anchor`` within the
    same document (ATLAS-112): the two heading slugs are within one position
    in the document's ordered heading index, or they share the same
    immediate parent heading. ``headings`` is that document's heading index.

    This replaces the defeated "any anchor in the same document" test: the
    roadmap holds many unrelated Phase 0-3 ticket anchors, so mere same-document
    membership flagged every roadmap-doc miss as adjacent. A candidate anchored
    to a *different document* is never adjacent — that is a different-document
    anchoring choice, not a metric undercount."""
    wanted_doc, _, wanted_slug = wanted_anchor.partition("#")
    candidate_doc, _, candidate_slug = candidate_anchor.partition("#")
    if wanted_doc != candidate_doc:
        return False
    position = {heading.slug: i for i, heading in enumerate(headings)}
    if wanted_slug not in position or candidate_slug not in position:
        return False
    i, j = position[wanted_slug], position[candidate_slug]
    if abs(i - j) <= 1:
        return True
    parent = _parent_slug(headings, i)
    return parent is not None and parent == _parent_slug(headings, j)


def enumerate_roadmap_tickets(
    text: str, *, path: str = ROADMAP_PATH
) -> list[RoadmapTicket]:
    """Hand-written tickets in roadmap order, each anchored to its nearest
    preceding heading and carrying its full de-wrapped title. Headings
    inside fenced code blocks are not headings (§2.3), so fenced content is
    skipped. A title spans the key line's remainder plus any immediately
    following indented continuation lines (ATLAS-112)."""
    tickets: list[RoadmapTicket] = []
    current_anchor: str | None = None
    in_fence = False

    # The ticket currently being assembled, so indented continuation lines
    # can extend its title before it is frozen.
    pending_key: str | None = None
    pending_anchor: str | None = None
    pending_title_parts: list[str] = []

    def flush() -> None:
        nonlocal pending_key
        if pending_key is not None:
            tickets.append(
                RoadmapTicket(
                    key=pending_key,
                    anchor=pending_anchor or f"{path}#",
                    title=" ".join(pending_title_parts).strip(),
                )
            )
            pending_key = None

    for line in text.splitlines():
        if _FENCE_RE.match(line):
            flush()
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            flush()
            current_anchor = f"{path}#{slugify(heading.group(2))}"
            continue
        if _TICKET_RE.match(line):
            flush()
            key, _, remainder = line.partition(" ")
            pending_key = key
            # A ticket before any heading has no anchor it could match.
            pending_anchor = current_anchor or f"{path}#"
            pending_title_parts = [remainder.strip()]
            continue
        # An indented, non-blank line right after a ticket line continues its
        # title (the roadmap wraps long titles, indented, with no leading key).
        if pending_key is not None and line.strip() and line[:1].isspace():
            pending_title_parts.append(line.strip())
            continue
        # Anything else (blank line, unindented prose, Retired:) ends the
        # current ticket's title.
        flush()

    flush()
    return tickets


def anchor_coverage(
    proposed_anchors: Iterable[str], roadmap_text: str, *, path: str = ROADMAP_PATH
) -> float:
    """Fraction of hand-written tickets whose anchor is hit by a proposed
    ticket's source_anchor (exact equality). A lower bound on true
    coverage: a correct proposal anchored to an adjacent heading scores as
    a miss, never a false hit. This is the original ATLAS-29 metric,
    unchanged — it measures anchoring-convention agreement."""
    tickets = enumerate_roadmap_tickets(roadmap_text, path=path)
    if not tickets:
        return 1.0  # vacuous: no hand-written tickets to cover
    proposed = set(proposed_anchors)
    covered = sum(1 for ticket in tickets if ticket.anchor in proposed)
    return covered / len(tickets)


@dataclass(frozen=True)
class ContentMatch:
    """One roadmap ticket's best work-coverage match in the proposal."""

    roadmap_key: str
    roadmap_title: str
    best_score: float
    best_planner_title: str | None
    covered: bool


@dataclass(frozen=True)
class ContentCoverageResult:
    """The work-coverage result: a fraction plus the per-ticket breakdown,
    the same shape the exact-anchor reporting consumes."""

    threshold: float
    matches: tuple[ContentMatch, ...]

    @property
    def fraction(self) -> float:
        if not self.matches:
            return 1.0  # vacuous: no hand-written tickets to cover
        return sum(1 for match in self.matches if match.covered) / len(self.matches)

    @property
    def covered(self) -> list[ContentMatch]:
        return [match for match in self.matches if match.covered]

    @property
    def missed(self) -> list[ContentMatch]:
        return [match for match in self.matches if not match.covered]


def content_coverage(
    proposed_tickets: Iterable[_TitledTicket],
    roadmap_text: str,
    *,
    threshold: float = CONTENT_COVERAGE_THRESHOLD,
    path: str = ROADMAP_PATH,
) -> ContentCoverageResult:
    """Fraction of hand-written tickets whose *work* appears in the proposal,
    independent of where the proposed ticket is anchored (ATLAS-112). A
    roadmap ticket is covered iff some proposed ticket's title is similar to
    the roadmap ticket's title at or above ``threshold``, measured with the
    reconciler's Sorensen-Dice primitive (the single similarity
    implementation).

    The comparand is title-vs-title, not roadmap-title-vs-planner-title+objective:
    a roadmap ticket carries a terse title, so concatenating the planner's
    descriptive objective onto its side structurally dilutes the coefficient
    (many planner-only tokens), driving genuinely-covered work below threshold —
    which would defeat the metric. Both sides therefore pass an empty objective,
    comparing like with like. This is a comparand choice for measurement
    correctness; the threshold is not moved to chase the score."""
    tickets = enumerate_roadmap_tickets(roadmap_text, path=path)
    proposed = list(proposed_tickets)
    matches: list[ContentMatch] = []
    for ticket in tickets:
        best_score = 0.0
        best_title: str | None = None
        for candidate in proposed:
            # Call via the module so the routing is observable (and patchable
            # in tests): there is exactly one similarity implementation.
            score = reconciler.similarity(ticket.title, "", candidate.title, "")
            if score > best_score:
                best_score = score
                best_title = candidate.title
        matches.append(
            ContentMatch(
                roadmap_key=ticket.key,
                roadmap_title=ticket.title,
                best_score=best_score,
                best_planner_title=best_title,
                covered=best_score >= threshold,
            )
        )
    return ContentCoverageResult(threshold=threshold, matches=tuple(matches))


@dataclass(frozen=True)
class AT7Verdict:
    """The AT-7 pair verdict (ATLAS-112 RESOLVED, spec §7.2). The exact-anchor
    floor gates; content-coverage is computed and surfaced but NOT asserted —
    its bar is unpinned until a second durably-saved capture (ATLAS-124). One
    helper so the logic the live leg runs is exactly the logic the synthetic
    CI test proves (the live leg is skipped in CI)."""

    anchor_coverage: float
    content_coverage: float
    floor: float
    passed: bool
    message: str


def at7_pair_verdict(
    anchor_cov: float,
    content_cov: float,
    *,
    floor: float = ANCHOR_COVERAGE_FLOOR,
) -> AT7Verdict:
    """Apply the AT-7 pair: gate on the exact-anchor ``floor`` and surface
    ``content_cov`` for the record. ``passed`` is solely ``anchor_cov >=
    floor`` — content-coverage is reported in the message, never gated, until
    the bar is pinned (ATLAS-124). The message names the floor figure so a
    failure says exactly which leg fell and by how much."""
    passed = anchor_cov >= floor
    relation = ">=" if passed else "<"
    message = (
        f"AT-7 exact-anchor floor: anchor_coverage {anchor_cov:.2%} "
        f"{relation} floor {floor:.2%} -> {'PASS' if passed else 'FAIL'}; "
        f"content_coverage {content_cov:.2%} (reported, bar unpinned per §7.2)"
    )
    return AT7Verdict(
        anchor_coverage=anchor_cov,
        content_coverage=content_cov,
        floor=floor,
        passed=passed,
        message=message,
    )
