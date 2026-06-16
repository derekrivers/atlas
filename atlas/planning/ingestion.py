"""Document ingestion and heading-anchor index (ATLAS-21).

Implements planning-engine-specification §2.1 (the input set, read
HEAD-atomically) and §2.3 (the anchor slug algorithm). This module is
the single implementation of the slug algorithm; the doc linter's
fragment check and the renderer (ATLAS-22) reuse it rather than
reimplementing.

Content-source rule (spec §2.1): inputs are read from HEAD, so each
document's content is the blob at its recorded SHA by construction —
the only reading that honours gate 4's "resolves at the recorded SHA".
A dirty or untracked input set is a typed DirtyInputError — planning
runs only against committed state (ADR-0006); there is no
untracked-file fallback.
"""

from __future__ import annotations

import fnmatch
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

# §2.1 input set, in documented order. ADRs are filtered to accepted.
_ROOT_DOCS = ("PRODUCT.md", "ARCHITECTURE.md", "ROADMAP.md", "WORKFLOW.md")
_GLOBS = ("docs/decisions/*.md", "docs/atlas/*.md", "docs/domain/*.md")
_INPUT_PATTERNS = _ROOT_DOCS + _GLOBS

_SLUG_STRIP_RE = re.compile(r"[^a-z0-9 -]")
_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})\s+(.*?)\s*$")
_STATUS_HEADING_RE = re.compile(r"^##\s+Status\s*$")


class IngestionError(ValueError):
    """Base for ingestion failures; always typed, never a fallback."""


class DirtyInputError(IngestionError):
    """The input set has uncommitted or untracked changes (spec §2.1)."""

    def __init__(self, paths: list[str]) -> None:
        super().__init__(
            "input documents are dirty or untracked: "
            f"{sorted(paths)}; planning runs only against committed state "
            "(ADR-0006) — commit or stash first"
        )
        self.paths = sorted(paths)


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


def _git(repo_root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as error:
        raise IngestionError(
            f"git {' '.join(args)} failed: {error.stderr.strip()}"
        ) from error
    return result.stdout


def _matches_input_set(path: str) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in _INPUT_PATTERNS)


def _assert_committed_state(repo_root: Path) -> None:
    """Fail closed on dirty or untracked files within the input set."""
    offending = []
    for line in _git(repo_root, "status", "--porcelain").splitlines():
        path = line[3:]
        if " -> " in path:  # rename: the new path is authoritative
            path = path.split(" -> ", 1)[1]
        path = path.strip('"')
        if _matches_input_set(path):
            offending.append(path)
    if offending:
        raise DirtyInputError(offending)


def _is_accepted_adr(content: str) -> bool:
    lines = content.splitlines()
    for number, line in enumerate(lines):
        if _STATUS_HEADING_RE.match(line):
            for body_line in lines[number + 1 :]:
                if body_line.startswith("## "):
                    break
                if body_line.strip():
                    return body_line.strip().lower().startswith("accepted")
    return False


def collect_input_documents(repo_root: Path) -> list[SourceDocument]:
    """The §2.1 input set from HEAD, in documented, deterministic order.

    Root control docs, then accepted ADRs (sorted), then docs/atlas/
    (sorted), then docs/domain/ if present (sorted).
    """
    _assert_committed_state(repo_root)
    tracked = set(_git(repo_root, "ls-tree", "-r", "--name-only", "HEAD").splitlines())

    ordered = [path for path in _ROOT_DOCS if path in tracked]
    for pattern in _GLOBS:
        ordered.extend(
            sorted(path for path in tracked if fnmatch.fnmatch(path, pattern))
        )

    documents = []
    for path in ordered:
        sha = _git(repo_root, "rev-parse", f"HEAD:{path}").strip()
        content = _git(repo_root, "show", f"HEAD:{path}")
        if path.startswith("docs/decisions/") and not _is_accepted_adr(content):
            continue  # §2.1: accepted ADRs only
        documents.append(SourceDocument(path=path, sha=sha, content=content))
    return documents


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
