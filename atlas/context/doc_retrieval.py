"""Documentation retrieval (ATLAS-52), per docs/atlas/context-renderer.md
"Retrieval rules (v1)" rule 1 (Docs) and "Staleness and provenance".

Extracts the one doc section an execution agent needs for a ticket and records
the SHAs of every doc the pack depends on. Rule 1 has two parts, split here at
the type level (no ``*Source`` enum — contrast ATLAS-51/-54, whose provenance is
one enum field; here the anchor section and the references are distinct types):

1. **Anchored section** — the ticket's ``source_anchor`` (``path#slug``) resolves
   to exactly one heading; its section (the heading, its parent-heading chain for
   orientation, and the verbatim body down to the next same-or-shallower heading)
   is extracted as the single :class:`DocSection`. Never a whole file.
2. **References** — every ``relevant_docs`` entry that exact-matches a corpus
   document is recorded as a :class:`DocReference` (path + sha) for the pack's
   ``relevant_docs`` list and ``input_doc_shas`` staleness. v1 records the SHA
   only; it does NOT body-extract references (that, and any controlled section
   selection within them, is deferred).

Pure computation: ZERO model/API calls and ZERO storage/git/file access. The
sole state input is ``documents`` — the caller's ``collect_input_documents``
output (exactly as :func:`select_adrs` takes the projected graph) — and the
:class:`AnchorIndex` is built from it internally. Building that corpus is the
caller's job (ATLAS-56/-58), so this stays a pure function exercised directly in
tests. It SELECTS and extracts the section and RECORDS the reference SHAs;
assembling the ContextPack, building ``input_doc_shas``, and the staleness
COMPARISON are ATLAS-56's job — this module only records the SHA a later compare
will diff (the Phase 5 milestone's "a doc edit after rendering is detectable"
half).

Anchor resolution is a precondition, not a retrieval condition: a malformed,
unknown-document, or unknown-slug ``source_anchor`` propagates the matching typed
:class:`~atlas.core.anchors.IngestionError` (mirroring :func:`select_adrs`
raising on a missing graph node), never a soft skip. An unmatched ``relevant_docs``
entry is the opposite — a soft signal, skipped, never raised.
"""

from __future__ import annotations

from dataclasses import dataclass

from atlas.core.anchors import AnchorIndex, Heading, SourceDocument, parse_headings
from atlas.core.models.ticket import Ticket

# The design's cap on rendered doc sections (context-renderer.md rule 1:
# "Cap: 5 doc sections"). Bounds the recorded REFERENCES only; the anchored
# section is always present and never counted against the cap.
DEFAULT_DOC_CAP = 5


@dataclass(frozen=True)
class DocSection:
    """The one section extracted from the ``source_anchor`` doc.

    ``path``/``sha`` are the resolved document's identity at render time (the sha
    a staleness compare will diff); ``anchor`` is the verbatim ``path#slug``;
    ``heading`` is the resolved heading text; ``parent_headings`` is the ancestor
    breadcrumb, shallowest-first (``()`` for a top-level anchor); ``body`` is the
    section's raw lines joined verbatim — no reflow, code fences intact."""

    path: str
    sha: str
    anchor: str
    heading: str
    parent_headings: tuple[str, ...]
    body: str


@dataclass(frozen=True)
class DocReference:
    """One ``relevant_docs`` entry recorded as path + sha. v1 records the SHA for
    the pack's ``relevant_docs`` list and ``input_doc_shas`` staleness; it is not
    body-extracted (D2)."""

    path: str
    sha: str


@dataclass(frozen=True)
class DocContext:
    """The doc retrieval result for one ticket: the single anchored
    :class:`DocSection` plus the recorded :class:`DocReference` list (deduped,
    listed order, capped, anchor doc excluded)."""

    section: DocSection
    references: tuple[DocReference, ...]


def _parent_headings(headings: list[Heading], anchor_index: int) -> tuple[str, ...]:
    """The ancestor breadcrumb of the heading at ``anchor_index``, shallowest-first.

    A strictly-decreasing-threshold backward walk (NOT "nearest heading at each
    shallower level", which wrongly admits a deeper heading from a sibling
    subtree): starting at ``threshold = anchor.level``, walk backward and take a
    heading iff its level is strictly less than the current threshold; on a take,
    drop the threshold to that level. A ``## Epic B`` between the anchor and an
    earlier ``### sub`` under ``## Epic A`` resets the threshold to 2, so the
    H3 ``sub`` (level 3, not < 2) is correctly skipped. Skipped levels fall out
    for free — an H1 directly above an H3 yields just the H1, no phantom H2.
    """
    chain: list[str] = []
    threshold = headings[anchor_index].level
    for h in reversed(headings[:anchor_index]):
        if h.level < threshold:
            chain.append(h.text)
            threshold = h.level
            if threshold == 1:
                break
    chain.reverse()
    return tuple(chain)


def select_doc_sections(
    documents: list[SourceDocument],
    ticket: Ticket,
    *,
    cap: int = DEFAULT_DOC_CAP,
) -> DocContext:
    """Extract the ``source_anchor`` section and record the ``relevant_docs`` SHAs
    for ``ticket`` over the ``documents`` corpus.

    ``ticket.source_anchor`` must resolve in ``documents`` — that is a
    precondition, so :class:`~atlas.core.anchors.MalformedAnchorError`,
    :class:`~atlas.core.anchors.UnknownDocumentError`, and
    :class:`~atlas.core.anchors.UnknownAnchorError` propagate (D9). An unmatched
    ``relevant_docs`` entry is skipped, never raised (D3).
    """
    index = AnchorIndex.build(documents)
    resolved = index.resolve(ticket.source_anchor)

    by_path = {doc.path: doc for doc in documents}
    source_doc = by_path[resolved.path]
    raw_lines = source_doc.content.splitlines()
    headings = parse_headings(source_doc.content)

    # The resolved slug is unique in the doc (dedup gives every slug its own
    # entry), so exactly one heading matches.
    anchor_index = next(i for i, h in enumerate(headings) if h.slug == resolved.slug)
    anchor_heading = headings[anchor_index]

    # Section boundary (D5): from the anchor heading's line down to the line
    # before the next heading at the same or a shallower level. Deeper
    # subheadings stay inside the section; the next same-or-shallower heading
    # ends it. Body is those raw lines joined verbatim.
    boundary = len(raw_lines)
    for h in headings[anchor_index + 1 :]:
        if h.level <= anchor_heading.level:
            boundary = h.line
            break
    body = "\n".join(raw_lines[anchor_heading.line : boundary])

    section = DocSection(
        path=resolved.path,
        sha=resolved.sha,
        anchor=ticket.source_anchor,
        heading=resolved.heading,
        parent_headings=_parent_headings(headings, anchor_index),
        body=body,
    )

    # References (D8/D3): relevant_docs in the ticket's listed order, exact
    # repo-relative path match against the corpus, the anchor doc excluded,
    # deduped by path, unmatched entries skipped, then truncated to cap. The sha
    # is the matched corpus document's — the value a staleness compare diffs.
    references: list[DocReference] = []
    seen_paths: set[str] = set()
    for ref_path in ticket.relevant_docs:
        if ref_path == resolved.path or ref_path in seen_paths:
            continue
        doc = by_path.get(ref_path)
        if doc is None:
            continue
        seen_paths.add(ref_path)
        references.append(DocReference(path=ref_path, sha=doc.sha))

    return DocContext(section=section, references=tuple(references[:cap]))
