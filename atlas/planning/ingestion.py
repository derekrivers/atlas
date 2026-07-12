"""Document ingestion: the §2.1 input set, read HEAD-atomically (ATLAS-21).

Implements planning-engine-specification §2.1 — the input set is read from
HEAD against committed state. The slug/anchor primitive (§2.3) now lives in
``atlas.core.anchors`` (relocated by ATLAS-129) so layers below
``atlas.planning`` can reuse it; this module imports ``IngestionError`` and
``SourceDocument`` from there.

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
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from atlas.core.anchors import IngestionError, SourceDocument

# §2.1 input set, in documented order. ADRs are filtered to accepted.
_ROOT_DOCS = ("PRODUCT.md", "ARCHITECTURE.md", "ROADMAP.md", "WORKFLOW.md")
_GLOBS = ("docs/decisions/*.md", "docs/atlas/*.md", "docs/domain/*.md")
_INPUT_PATTERNS = _ROOT_DOCS + _GLOBS

# The retired-stub subdir under the inbox (ATLAS-122 lifecycle). Apply's
# retirement move targets it; ATLAS-159 anchors promotion to it from birth.
PROCESSED_SUBDIR = "processed"

_STATUS_HEADING_RE = re.compile(r"^##\s+Status\s*$")


class DirtyInputError(IngestionError):
    """The input set has uncommitted or untracked changes (spec §2.1)."""

    def __init__(self, paths: list[str]) -> None:
        super().__init__(
            "input documents are dirty or untracked: "
            f"{sorted(paths)}; planning runs only against committed state "
            "(ADR-0006) — commit or stash first"
        )
        self.paths = sorted(paths)


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


def _matches_inbox(path: str, inbox_dir: str) -> bool:
    """A top-level follow-up inbox stub (ATLAS-122): ``<inbox_dir>/<name>.md``.

    Matches on the immediate parent directory, NOT a glob: ``fnmatch``'s ``*``
    crosses ``/``, so ``<inbox_dir>/*.md`` would also match
    ``<inbox_dir>/processed/x.md`` and re-read retired stubs forever. Comparing
    ``PurePosixPath.parent`` excludes the ``processed/`` subdir by construction.
    """
    candidate = PurePosixPath(path)
    return candidate.suffix == ".md" and candidate.parent == PurePosixPath(inbox_dir)


def _matches_processed(path: str, inbox_dir: str) -> bool:
    """A retired inbox stub (ATLAS-159): ``<inbox_dir>/processed/<name>.md``."""
    candidate = PurePosixPath(path)
    return (
        candidate.suffix == ".md"
        and candidate.parent == PurePosixPath(inbox_dir) / PROCESSED_SUBDIR
    )


def processed_path_for(path: str) -> str:
    """The durable ``processed/`` home of an active inbox stub path (ATLAS-159).

    Retirement is a pure move (``atlas apply``, §2.2), so the file's content —
    and therefore its heading slugs — is byte-identical at both addresses; the
    durable path is known at promotion time by construction.
    """
    candidate = PurePosixPath(path)
    return str(candidate.parent / PROCESSED_SUBDIR / candidate.name)


class InboxCollisionError(IngestionError):
    """An active inbox stub shares its basename with a retired ``processed/``
    file (ATLAS-159). Fail-closed: the durable-anchor alias would collide with
    the real retired document, and retirement's target-exists skip would leave
    the active stub re-read by every plan — an operator-repair state, never a
    silent pick."""

    def __init__(self, stub_path: str, processed_path: str) -> None:
        super().__init__(
            f"active inbox stub {stub_path!r} collides with retired stub "
            f"{processed_path!r}; rename or remove one before planning"
        )
        self.stub_path = stub_path
        self.processed_path = processed_path


def durable_alias_documents(
    inbox_documents: list[SourceDocument],
    processed_documents: list[SourceDocument],
) -> list[SourceDocument]:
    """Each active inbox stub re-keyed at its durable ``processed/`` path
    (ATLAS-159): the same blob (same SHA, same content, same heading slugs) at
    the address apply's retirement will give it, so a promotion anchor minted
    against the durable path resolves at gate 4 in the run that mints it.
    Aliases feed the anchor index only — never ``input_doc_shas`` (the blob is
    already pinned at its real path). A basename collision with a real retired
    stub is a typed :class:`InboxCollisionError`, fail-closed.
    """
    processed_paths = {document.path for document in processed_documents}
    aliases = []
    for document in inbox_documents:
        alias_path = processed_path_for(document.path)
        if alias_path in processed_paths:
            raise InboxCollisionError(document.path, alias_path)
        aliases.append(
            SourceDocument(path=alias_path, sha=document.sha, content=document.content)
        )
    return aliases


def _assert_committed_state(
    repo_root: Path, matches: Callable[[str], bool] = _matches_input_set
) -> None:
    """Fail closed on dirty or untracked files within the matched set.

    ``matches`` selects which paths the gate covers: the §2.1 corpus by default,
    or the follow-up inbox glob when ``collect_inbox_documents`` passes its own
    matcher (ATLAS-122) — the same fail-closed contract, never a silent read.
    """
    offending = []
    # --untracked-files=all lists each untracked file rather than collapsing a
    # wholly-untracked directory to its dir entry — so a brand-new inbox/ whose
    # every stub is uncommitted still fails closed per stub (ATLAS-122).
    for line in _git(
        repo_root, "status", "--porcelain", "--untracked-files=all"
    ).splitlines():
        path = line[3:]
        if " -> " in path:  # rename: the new path is authoritative
            path = path.split(" -> ", 1)[1]
        path = path.strip('"')
        if matches(path):
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


def collect_inbox_documents(repo_root: Path, inbox_dir: Path) -> list[SourceDocument]:
    """The committed follow-up inbox stubs from HEAD (ATLAS-122).

    A SEPARATE plan input source, NOT folded into the §2.1 corpus: the
    committed top-level ``<inbox_dir>/*.md`` stubs, the ``processed/`` subdir
    excluded (those are retired by a prior ``atlas apply`` and must never be
    re-read). Reuses ``collect_input_documents``'s committed-only contract — an
    uncommitted or untracked inbox stub raises ``DirtyInputError`` (the
    committed-inbox gate: the operator commits the inbox, and only then does
    plan see it). Sorted for determinism; an empty or missing inbox yields ``[]``
    (an empty inbox is a no-op — the planner sees exactly the corpus).

    ``inbox_dir`` is repo-relative; it is compared against the HEAD tree's
    POSIX paths, so the producer's ``docs/planning/inbox`` default matches.
    """
    inbox = inbox_dir.as_posix()
    _assert_committed_state(repo_root, lambda path: _matches_inbox(path, inbox))
    tracked = _git(repo_root, "ls-tree", "-r", "--name-only", "HEAD").splitlines()
    paths = sorted(path for path in tracked if _matches_inbox(path, inbox))

    documents = []
    for path in paths:
        sha = _git(repo_root, "rev-parse", f"HEAD:{path}").strip()
        content = _git(repo_root, "show", f"HEAD:{path}")
        documents.append(SourceDocument(path=path, sha=sha, content=content))
    return documents


def collect_processed_documents(
    repo_root: Path, inbox_dir: Path
) -> list[SourceDocument]:
    """The committed retired stubs from HEAD (ATLAS-159):
    ``<inbox_dir>/processed/*.md``.

    Anchor-resolution and provenance input ONLY — retired stubs are consumed
    follow-ups, so they never join the planner's document payload; they are
    read so a stub-minted ticket's durable ``processed/`` anchor keeps
    resolving at gate 4 after retirement, and pinned into ``input_doc_shas``
    so gate 4's "resolves at the recorded SHA" and the AT-5 staleness re-check
    stay airtight. Same committed-only, fail-closed contract as the active
    inbox; sorted for determinism; an empty or missing subdir yields ``[]``.
    """
    inbox = inbox_dir.as_posix()
    _assert_committed_state(repo_root, lambda path: _matches_processed(path, inbox))
    tracked = _git(repo_root, "ls-tree", "-r", "--name-only", "HEAD").splitlines()
    paths = sorted(path for path in tracked if _matches_processed(path, inbox))

    documents = []
    for path in paths:
        sha = _git(repo_root, "rev-parse", f"HEAD:{path}").strip()
        content = _git(repo_root, "show", f"HEAD:{path}")
        documents.append(SourceDocument(path=path, sha=sha, content=content))
    return documents
