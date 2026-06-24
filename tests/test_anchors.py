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
