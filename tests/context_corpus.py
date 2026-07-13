"""Shared context-corpus fixtures (factored from test_context_cli.py by
ATLAS-164, mechanically — both consumers keep their assertions).

One committed-corpus shape used everywhere a test needs a ticket whose
``source_anchor`` resolves: the §2.1 root docs plus one anchor doc
(``ANCHOR``), and optionally one retired inbox stub at its durable
``processed/`` address (``STUB_ANCHOR``, the ATLAS-162 class). The context CLI
tests (ATLAS-58/-162) drive these through a real git fixture repo via
``make_repo``; the pack-embedding sync tests (ATLAS-164) drive the same files
through the real ATLAS-162 collector pair as the tick's injected documents
provider.
"""

from __future__ import annotations

# The anchor doc: a "## Target Section" heading (slug "target-section") with a
# distinctive body phrase, and a following same-level heading that ends it.
BODY_PHRASE = "The renderer assembles the minimum high-value context."
CONTEXT_SPEC = "\n".join(
    [
        "# Context Spec",
        "",
        "## Target Section",
        "",
        BODY_PHRASE,
        "",
        "## Next Section",
        "",
        "next body",
        "",
    ]
)

ANCHOR_PATH = "docs/atlas/context-spec.md"
ANCHOR = f"{ANCHOR_PATH}#target-section"

# A retired inbox stub at its durable processed/ address (ATLAS-162): the pack
# path resolves a stub-minted ticket's anchor here, exactly as gate 4 does.
STUB_PHRASE = "What gate 4 can resolve, a pack can cite."
PROCESSED_STUB_PATH = "docs/planning/inbox/processed/inbox-stub-fixture.md"
PROCESSED_STUB = "\n".join(
    [
        "# Stub Fixture",
        "",
        "## Packs See Processed",
        "",
        STUB_PHRASE,
        "",
    ]
)
STUB_ANCHOR = f"{PROCESSED_STUB_PATH}#packs-see-processed"


def corpus_files(spec: str = CONTEXT_SPEC) -> dict[str, str]:
    """The committed §2.1 input set the loader re-ingests from HEAD: the four
    root docs plus the anchor doc the ticket points into."""
    return {
        "PRODUCT.md": "# Product\n\n## Vision\n",
        "ARCHITECTURE.md": "# Architecture\n",
        "ROADMAP.md": "# Roadmap\n\n## Phase 5\n",
        "WORKFLOW.md": "# Workflow\n",
        ANCHOR_PATH: spec,
    }
