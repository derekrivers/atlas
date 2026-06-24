"""The §2.3 slug algorithm and heading-anchor index (ATLAS-21).

This module is the single implementation of the planning-engine-specification
§2.3 slug algorithm and the heading-anchor indexing built on it; the doc
linter's fragment check and the renderer (ATLAS-22) reuse it rather than
reimplementing.

Relocated from ``atlas.planning.ingestion`` (ATLAS-129) so that layers below
``atlas.planning`` in the import-linter spine — the Phase 5 ``atlas.context``
retrievers in particular — can reuse the one slug implementation without a
forbidden low→high import edge. The §2.1 HEAD reading and committed-state gate
stay in ``atlas.planning.ingestion``; this module is the pure primitive and
imports only the standard library (``re``) and ``dataclasses`` — nothing above
``atlas.core`` and no ``subprocess``/``Path``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_SLUG_STRIP_RE = re.compile(r"[^a-z0-9 -]")
_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})\s+(.*?)\s*$")


class IngestionError(ValueError):
    """Base for ingestion failures; always typed, never a fallback."""


class MalformedAnchorError(IngestionError):
    """An anchor is not of the form <path>#<slug>."""


class UnknownDocumentError(IngestionError):
    """An anchor names a document outside the indexed input set."""


class UnknownAnchorError(IngestionError):
    """An anchor's slug matches no heading in its document."""


def slugify(heading: str) -> str:
    """The §2.3 slug algorithm — the single implementation.

    Lowercase; strip characters outside [a-z0-9 -]; spaces to hyphens
    (each space, not collapsed — an em-dash between spaces yields a
    double hyphen, matching GitHub's real slugs).
    """
    return _SLUG_STRIP_RE.sub("", heading.lower()).replace(" ", "-")


@dataclass(frozen=True)
class SourceDocument:
    """One planner input: content is the blob at ``sha``, always."""

    path: str
    sha: str
    content: str


@dataclass(frozen=True)
class ResolvedAnchor:
    """A successfully resolved ``path#slug`` anchor."""

    path: str
    slug: str
    sha: str
    heading: str


def _blank_fenced_blocks(lines: list[str]) -> list[str]:
    # §2.3: headings inside fenced code blocks are not headings.
    out = []
    in_fence = False
    for line in lines:
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            out.append("")
            continue
        out.append("" if in_fence else line)
    return out


def _heading_slugs(content: str) -> dict[str, str]:
    """slug -> heading text, duplicates suffixed -1, -2 (§2.3)."""
    slugs: dict[str, str] = {}
    seen: dict[str, int] = {}
    for line in _blank_fenced_blocks(content.splitlines()):
        match = _HEADING_RE.match(line)
        if not match:
            continue
        heading = match.group(2)
        base = slugify(heading)
        count = seen.get(base, 0)
        seen[base] = count + 1
        slug = base if count == 0 else f"{base}-{count}"
        slugs[slug] = heading
    return slugs


class AnchorIndex:
    """Heading-anchor index over an ingested document set.

    Immutable over the documents it was built from; staleness detection
    is comparing ``input_doc_shas`` against a fresh
    ``collect_input_documents`` run (spec §2.2 step 2).
    """

    def __init__(self, documents: list[SourceDocument]) -> None:
        self._shas = {doc.path: doc.sha for doc in documents}
        self._slugs = {doc.path: _heading_slugs(doc.content) for doc in documents}

    @classmethod
    def build(cls, documents: list[SourceDocument]) -> AnchorIndex:
        return cls(documents)

    @property
    def input_doc_shas(self) -> dict[str, str]:
        """path -> blob SHA, the PlanRun.input_doc_shas shape."""
        return dict(self._shas)

    def slugs_for(self, path: str) -> list[str]:
        if path not in self._slugs:
            raise UnknownDocumentError(f"{path!r} is not in the indexed input set")
        return list(self._slugs[path])

    def anchor_choices(self) -> list[dict[str, str]]:
        """Every resolvable ``path#slug`` anchor paired with its heading, for the
        prompt's valid-anchor list (ATLAS-111).

        DERIVED from the indexed ``_slugs`` map — the single slug implementation
        (``slugify``) — never recomputed: the list the model selects from is the
        exact list gate 4 validates against, so the two cannot drift. Document
        and heading order are preserved (dict insertion order), so the rendered
        list is deterministic and the ``prompt_hash`` stable.
        """
        return [
            {"anchor": f"{path}#{slug}", "heading": heading}
            for path, slugs in self._slugs.items()
            for slug, heading in slugs.items()
        ]

    def resolve(self, anchor: str) -> ResolvedAnchor:
        """Resolve ``<path>#<slug>``; every failure is a typed error."""
        if "#" not in anchor:
            raise MalformedAnchorError(
                f"{anchor!r} is not of the form <doc path>#<heading-slug>"
            )
        path, _, slug = anchor.partition("#")
        if path not in self._slugs:
            raise UnknownDocumentError(
                f"{path!r} (from {anchor!r}) is not in the indexed input set"
            )
        headings = self._slugs[path]
        if slug not in headings:
            raise UnknownAnchorError(
                f"{anchor!r} matches no heading in {path!r} at {self._shas[path]}"
            )
        return ResolvedAnchor(
            path=path, slug=slug, sha=self._shas[path], heading=headings[slug]
        )
