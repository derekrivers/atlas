"""Guard the ATLAS-129 relocation of the anchor/slug primitive to atlas.core.

A pure move: the primitive's behaviour is exercised by test_ingestion.py through
the preserved ``atlas.planning`` package surface. These two guards pin the
move's load-bearing invariants — the package re-export surface stays whole, and
the anchor error hierarchy survives crossing the module boundary (the core base
class still parents the error raised in atlas.planning.ingestion).
"""

from __future__ import annotations

from atlas.core.anchors import IngestionError


def test_planning_reexport_surface_is_preserved() -> None:
    """`from atlas.planning import …` still resolves the whole moved surface."""
    from atlas.planning import (  # noqa: F401
        AnchorIndex,
        DirtyInputError,
        IngestionError,
        MalformedAnchorError,
        ResolvedAnchor,
        SourceDocument,
        UnknownAnchorError,
        UnknownDocumentError,
        collect_inbox_documents,
        collect_input_documents,
        slugify,
    )


def test_anchor_error_hierarchy_survives_the_move() -> None:
    """The cross-module hierarchy holds: every typed error subclasses the now-core
    IngestionError, including DirtyInputError which stays in atlas.planning."""
    from atlas.core.anchors import (
        MalformedAnchorError,
        UnknownAnchorError,
        UnknownDocumentError,
    )
    from atlas.planning.ingestion import DirtyInputError

    assert issubclass(DirtyInputError, IngestionError)
    assert issubclass(MalformedAnchorError, IngestionError)
    assert issubclass(UnknownDocumentError, IngestionError)
    assert issubclass(UnknownAnchorError, IngestionError)


def test_parse_headings_levels_and_line_indices_map_to_raw_content() -> None:
    """Every level H1..H6 is parsed with the right ``#`` count, and ``line``
    indexes the raw ``content.splitlines()`` (not the blanked copy)."""
    from atlas.core.anchors import parse_headings

    content = "\n".join(
        [
            "intro",
            "# h1",
            "## h2",
            "### h3",
            "body",
            "#### h4",
            "##### h5",
            "###### h6",
        ]
    )
    headings = parse_headings(content)
    raw = content.splitlines()

    assert [h.level for h in headings] == [1, 2, 3, 4, 5, 6]
    assert [h.text for h in headings] == ["h1", "h2", "h3", "h4", "h5", "h6"]
    # line maps back to the exact raw line — the load-bearing slice invariant.
    for h in headings:
        assert raw[h.line] == "#" * h.level + " " + h.text


def test_parse_headings_ignores_a_heading_inside_a_fence() -> None:
    """A ``## heading`` inside a ``` fence is code, not a heading (§2.3)."""
    from atlas.core.anchors import parse_headings

    content = "\n".join(
        [
            "# real",
            "```",
            "## not a heading",
            "```",
            "## also real",
        ]
    )
    headings = parse_headings(content)

    assert [h.text for h in headings] == ["real", "also real"]
    assert "not a heading" not in {h.text for h in headings}


def test_parse_headings_dedupes_duplicate_slugs_with_numeric_suffixes() -> None:
    """Duplicate headings get -1/-2 slugs while keeping their text (§2.3)."""
    from atlas.core.anchors import parse_headings

    content = "\n".join(["## Setup", "## Setup", "## Setup"])
    headings = parse_headings(content)

    assert [h.slug for h in headings] == ["setup", "setup-1", "setup-2"]
    assert [h.text for h in headings] == ["Setup", "Setup", "Setup"]
