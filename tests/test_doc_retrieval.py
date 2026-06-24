"""Documentation retrieval (ATLAS-52), per docs/atlas/context-renderer.md rule 1.

Each test is falsifiable and names the wrong answer where the algorithm has a
direction or boundary that an off-by-one or reversed implementation would still
pass against: the section-boundary tests assert the following same-level heading
is ABSENT from the body; the breadcrumb tests assert the excluded sibling-subtree
heading and the reversed chain are wrong. The parse_headings/_heading_slugs
refactor's own proof is the existing anchor/ingestion suite staying green
(test_anchors.py, test_ingestion.py) — not re-asserted here.
"""

from __future__ import annotations

from typing import Any

import pytest
from test_models_validation import ticket_kwargs

from atlas.context import (
    DEFAULT_DOC_CAP,
    DocContext,
    DocReference,
    select_doc_sections,
)
from atlas.core.anchors import (
    MalformedAnchorError,
    SourceDocument,
    UnknownAnchorError,
    UnknownDocumentError,
)
from atlas.core.models import Ticket


def _ticket(**overrides: Any) -> Ticket:
    return Ticket(**ticket_kwargs() | overrides)


def _doc(path: str, content: str, *, sha: str = "sha0") -> SourceDocument:
    return SourceDocument(path=path, sha=sha, content=content)


# A document with a sibling subtree (Epic A's ### sub) between the anchor and its
# true H2 parent (Epic B), plus a deeper subheading inside the anchor section and
# a following same-level / shallower heading after it. One fixture exercises the
# boundary, the breadcrumb reset, and the parent-chain order.
_NESTED = "\n".join(
    [
        "# Phase 5",
        "## Epic A",
        "### sub",
        "sibling body",
        "## Epic B",
        "intro b",
        "### target",
        "target body line 1",
        "#### deeper",
        "target body line 2",
        "## Epic C",
        "epic c body",
        "# Phase 6",
        "phase 6 body",
    ]
)


def test_anchored_section_is_extracted_verbatim() -> None:
    """The DocSection carries the resolved path/sha/heading/anchor and a verbatim
    body of the section's raw lines."""
    docs = [_doc("d.md", _NESTED, sha="abc123")]
    ticket = _ticket(source_anchor="d.md#target", relevant_docs=[])

    ctx = select_doc_sections(docs, ticket)

    assert isinstance(ctx, DocContext)
    assert ctx.section.path == "d.md"
    assert ctx.section.sha == "abc123"
    assert ctx.section.anchor == "d.md#target"
    assert ctx.section.heading == "target"
    assert ctx.section.body.splitlines()[0] == "### target"
    assert ctx.references == ()


def test_section_boundary_keeps_subheadings_excludes_next_same_level() -> None:
    """An H3 anchor's section runs through a deeper H4 but stops before the next
    heading at the same-or-shallower level. The deeper '#### deeper' IS in body;
    the next H2 'Epic C' and the following H1 'Phase 6' are NOT (the wrong
    answer)."""
    docs = [_doc("d.md", _NESTED)]
    ticket = _ticket(source_anchor="d.md#target", relevant_docs=[])

    body = select_doc_sections(docs, ticket).section.body

    # Deeper subheading and all of the section's own lines are inside.
    assert "#### deeper" in body
    assert "target body line 1" in body
    assert "target body line 2" in body
    # The next same-or-shallower headings end the section — they must be absent.
    assert "Epic C" not in body
    assert "epic c body" not in body
    assert "Phase 6" not in body


def test_parent_chain_excludes_sibling_subtree_heading() -> None:
    """The breadcrumb of the H3 'target' under '## Epic B' under '# Phase 5' is
    exactly ('Phase 5', 'Epic B'). The earlier '### sub' lives under the sibling
    '## Epic A'; a 'nearest heading at each shallower level' walk would wrongly
    admit it — assert it is absent and the chain is not reversed."""
    docs = [_doc("d.md", _NESTED)]
    ticket = _ticket(source_anchor="d.md#target", relevant_docs=[])

    parents = select_doc_sections(docs, ticket).section.parent_headings

    assert parents == ("Phase 5", "Epic B")
    assert "sub" not in parents  # the sibling-subtree heading, never an ancestor
    assert "Epic A" not in parents  # the sibling section itself
    assert parents != ("Epic B", "Phase 5")  # shallowest-first, not reversed


def test_parent_chain_simple_nesting_is_top_down() -> None:
    """An H3 under H2 under H1 yields (H1 text, H2 text), shallowest-first."""
    content = "\n".join(["# Top", "## Mid", "### Leaf", "leaf body"])
    docs = [_doc("d.md", content)]
    ticket = _ticket(source_anchor="d.md#leaf", relevant_docs=[])

    parents = select_doc_sections(docs, ticket).section.parent_headings

    assert parents == ("Top", "Mid")


def test_parent_chain_skips_absent_intermediate_level() -> None:
    """An H1 directly above an H3 yields just the H1 — no phantom H2."""
    content = "\n".join(["# Only Top", "### Deep", "deep body"])
    docs = [_doc("d.md", content)]
    ticket = _ticket(source_anchor="d.md#deep", relevant_docs=[])

    assert select_doc_sections(docs, ticket).section.parent_headings == ("Only Top",)


def test_top_level_anchor_has_empty_parent_chain() -> None:
    """An H1 anchor has no parents."""
    docs = [_doc("d.md", _NESTED)]
    ticket = _ticket(source_anchor="d.md#phase-5", relevant_docs=[])

    assert select_doc_sections(docs, ticket).section.parent_headings == ()


def test_present_relevant_doc_records_its_sha() -> None:
    """A relevant_doc that exact-matches a corpus doc yields a DocReference with
    that doc's sha."""
    docs = [
        _doc("anchor.md", "# A\nbody", sha="anchorsha"),
        _doc("ref.md", "# R\nbody", sha="refsha"),
    ]
    ticket = _ticket(source_anchor="anchor.md#a", relevant_docs=["ref.md"])

    refs = select_doc_sections(docs, ticket).references

    assert refs == (DocReference(path="ref.md", sha="refsha"),)


def test_absent_relevant_doc_is_skipped_not_raised() -> None:
    """A relevant_doc absent from the corpus is a soft skip (D3)."""
    docs = [_doc("anchor.md", "# A\nbody", sha="anchorsha")]
    ticket = _ticket(
        source_anchor="anchor.md#a", relevant_docs=["missing.md", "also-missing.md"]
    )

    assert select_doc_sections(docs, ticket).references == ()


def test_relevant_doc_equal_to_anchor_doc_is_excluded() -> None:
    """The source_anchor's own doc is never a reference, even if listed."""
    docs = [
        _doc("anchor.md", "# A\nbody", sha="anchorsha"),
        _doc("ref.md", "# R\nbody", sha="refsha"),
    ]
    ticket = _ticket(source_anchor="anchor.md#a", relevant_docs=["anchor.md", "ref.md"])

    refs = select_doc_sections(docs, ticket).references

    assert [r.path for r in refs] == ["ref.md"]


def test_references_preserve_listed_order_and_dedupe() -> None:
    """References follow the ticket's relevant_docs order; a repeat is deduped."""
    docs = [
        _doc("anchor.md", "# A\nbody"),
        _doc("b.md", "# B\nbody", sha="bsha"),
        _doc("a.md", "# A\nbody", sha="asha"),
        _doc("c.md", "# C\nbody", sha="csha"),
    ]
    ticket = _ticket(
        source_anchor="anchor.md#a",
        relevant_docs=["c.md", "a.md", "b.md", "a.md"],
    )

    refs = select_doc_sections(docs, ticket).references

    assert [r.path for r in refs] == ["c.md", "a.md", "b.md"]


def test_references_truncate_to_cap() -> None:
    """The cap bounds the references; the anchored section is not counted."""
    refs_docs = [_doc(f"r{i}.md", "# R\nbody", sha=f"s{i}") for i in range(7)]
    docs = [_doc("anchor.md", "# A\nbody"), *refs_docs]
    ticket = _ticket(
        source_anchor="anchor.md#a",
        relevant_docs=[f"r{i}.md" for i in range(7)],
    )

    refs = select_doc_sections(docs, ticket, cap=DEFAULT_DOC_CAP).references

    assert len(refs) == DEFAULT_DOC_CAP == 5
    assert [r.path for r in refs] == [f"r{i}.md" for i in range(5)]


def test_doc_edit_changes_the_recorded_sha() -> None:
    """Milestone half: the same path with different content/sha across two
    document sets yields a different recorded sha — a doc edit is recorded (the
    staleness COMPARISON itself is ATLAS-56)."""
    ticket = _ticket(source_anchor="d.md#a", relevant_docs=["ref.md"])

    before = select_doc_sections(
        [
            _doc("d.md", "# A\nold body", sha="old-anchor"),
            _doc("ref.md", "# R\nold", sha="old-ref"),
        ],
        ticket,
    )
    after = select_doc_sections(
        [
            _doc("d.md", "# A\nnew body", sha="new-anchor"),
            _doc("ref.md", "# R\nnew", sha="new-ref"),
        ],
        ticket,
    )

    assert before.section.sha == "old-anchor"
    assert after.section.sha == "new-anchor"
    assert before.section.sha != after.section.sha
    assert before.references[0].sha == "old-ref"
    assert after.references[0].sha == "new-ref"
    assert before.references[0].sha != after.references[0].sha


def test_malformed_anchor_raises() -> None:
    """A source_anchor with no '#' is a precondition breach (D9)."""
    docs = [_doc("d.md", "# A\nbody")]
    ticket = _ticket(source_anchor="d.md-no-hash", relevant_docs=[])

    with pytest.raises(MalformedAnchorError):
        select_doc_sections(docs, ticket)


def test_unknown_document_anchor_raises() -> None:
    """A source_anchor naming a doc outside the corpus raises (D9)."""
    docs = [_doc("d.md", "# A\nbody")]
    ticket = _ticket(source_anchor="other.md#a", relevant_docs=[])

    with pytest.raises(UnknownDocumentError):
        select_doc_sections(docs, ticket)


def test_unknown_slug_anchor_raises() -> None:
    """A source_anchor whose slug matches no heading raises (D9)."""
    docs = [_doc("d.md", "# A\nbody")]
    ticket = _ticket(source_anchor="d.md#nope", relevant_docs=[])

    with pytest.raises(UnknownAnchorError):
        select_doc_sections(docs, ticket)
