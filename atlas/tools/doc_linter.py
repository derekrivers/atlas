"""Doc linter (v1: ATLAS-4; v2: ATLAS-16; v3: ATLAS-198): mechanical validation of the
canonical doc set.

Checks, per the implementation roadmap and ADR-0006/0007:

- ADR: every ``docs/decisions/*.md`` matches the ADR model in
  data-model-and-schemas.md §3.2 — title heading ``# ADR-NNNN: <title>``
  with the number matching the filename, a recognised status, and
  non-empty Status / Context / Decision / Rationale / Consequences /
  Alternatives considered sections.
- MANIFEST: every path referenced in docs/MANIFEST.md exists, and every
  canonical doc at any depth under docs/atlas/, docs/architecture/,
  docs/decisions/, and docs/runbooks/ is listed. Stub directories the MANIFEST
  declares as awaiting content (docs/product/, docs/tech-debt/) are not yet
  required to be listed.
- LEGACY: retired v1/v2/v3 document names are banned outside
  docs/archive/ — ``*-v[123].md`` forms, ``ATLAS_V[123]``, ``_V[123]_``
  infixes, and ``roadmap.html``. Lines that explicitly mark retirement
  (containing "retired") are allowed: the roadmap's "Retired:" lines are
  the documented mechanism for recording retirements, not live use.
  Two carve-outs cover the operator-authored inbox-stub namespace, which
  is distinct from retired canonical-doc generations (the ATLAS_V2 era):
  filenames under ``docs/planning/inbox/processed/`` are exempt from the
  name check (same terminal-record semantics as the docs/archive/ skip),
  and legacy-name matches inside a backticked path under
  ``docs/planning/inbox/`` (any depth) are exempt from the reference
  check so run records can name inbox stubs verbatim. Both carve-outs are
  span-scoped: any legacy reference outside those inbox backticks, and any
  ``-v[123].md`` filename outside processed/, still fires.
- LINK: relative ``.md`` link targets in active docs must resolve to
  existing files. ``#fragment`` validation is deferred to the
  heading-anchor index (ATLAS-21), which owns the slug algorithm.
- PLANNING: docs/planning/ files are renders written only by
  ``atlas apply`` (ADR-0007). Per the render format in
  docs/architecture/knowledge-core.md, a render carries a generated
  header recording ``plan_run_id`` and naming ``atlas apply``; any file
  without that header is a hand-edit. Header presence cannot detect an
  edit that preserves the header — content-hash integrity arrives with
  PlanRun ingestion in later phases.

v2 (ATLAS-16) adds, per knowledge-core.md "JSON Schema generation":

- JSON: every ```json fence in an active doc declares its schema via
  ``model=<ModelName>`` in the fence info string (``partial`` suppresses
  required-field completeness only) or is exempted with ``no-schema``.
  Mapped examples validate against docs/generated/schemas/: unmapped or
  malformed fence markers (JSN001), unknown model (JSN002), invalid JSON
  (JSN003), unknown key (JSN004), type/format mismatch (JSN005), missing
  required field in a non-partial fence (JSN006). Schema constructs the
  validator does not recognise fail closed (JSN007), never skip.
- GENERATED: docs/generated/schemas/ must byte-match an in-memory
  regeneration from the canonical models (GEN001) — the hand-edit ban on
  docs/generated/ is mechanical, same rule as docs/planning/.
- PATH: backticked spans in active docs that parse as concrete repository
  paths must resolve at HEAD (PTH001). Explicit carve-outs cover module dotted
  paths, command lines, glob or templated paths, docs/archive/, docs/planning/
  inbox records, and local/runtime paths rather than committed repo paths.
  docs/closure/ files are terminal records and are fully exempt as PATH
  sources; PHASE still reads them.
- PHASE: roadmap phase closure state is mechanically consistent between
  ROADMAP.md, docs/atlas/implementation-roadmap.md, and docs/closure/. CLOSED
  phases require phase closure reports (PHS001), phase closure reports require
  CLOSED roadmap phase sections (PHS002), ROADMAP.md's current-work claim must
  name an existing phase section (PHS003), and unrecognised roadmap status
  lines fail closed (PHS004).
- SOURCE: every ``source_anchor`` recorded in planning renders, and in a store
  when a caller supplies one, resolves through the same ``AnchorIndex`` and
  planner corpus that gate 4 uses. A path outside the indexed input set is
  SRC001; an indexed path with no matching heading is SRC002.
- CEILING: when the repo-owned ``WORKFLOW.md`` is present, its Symphony ceiling
  is a strict integer no greater than ten; an open Phase 15 pins ordinary
  ``main`` to one, while a CLOSED Phase 15 closure report must prove and
  accompany exactly ten. The explicit milestone validation context accepts only
  the pinned branch at one of the five declared levels; the ordinary context
  continues to reject every open-phase value other than one. ``max_turns``
  remains ten outside the ramp. Canonical authority, current-policy
  reconciliation, five-level gate ordering, rollback and mutation-authority
  wording remain mechanically present in the controlled-ramp docs.
- HANDOFF: the repository-owned agent contract and its supporting runbooks must
  not instruct an agent to poll or wait for CI/review, and must not present a
  scoped local validation result as repository-wide or completion authority.

Exit status: 0 when the doc set is clean, 1 when there are findings.
This linter only reports; repairing drift is ATLAS-5.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import subprocess
import sys
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from atlas.core.anchors import (
    AnchorIndex,
    IngestionError,
    MalformedAnchorError,
    SourceDocument,
    UnknownAnchorError,
    UnknownDocumentError,
)
from atlas.planning.ingestion import (
    _GLOBS,
    _ROOT_DOCS,
    _is_accepted_adr,
    _matches_inbox,
    _matches_processed,
    collect_inbox_documents,
    collect_input_documents,
    collect_processed_documents,
    durable_alias_documents,
)
from atlas.tools.schemas_export import SCHEMAS_DIR, expected_schemas

if TYPE_CHECKING:
    from atlas.storage import Database

MANIFEST_PATH = "docs/MANIFEST.md"
ROOT_ROADMAP_PATH = "ROADMAP.md"
IMPLEMENTATION_ROADMAP_PATH = "docs/atlas/implementation-roadmap.md"
DECISIONS_DIR = "docs/decisions"
PLANNING_DIR = "docs/planning"
ARCHIVE_DIR = "docs/archive"
CLOSURE_DIR = "docs/closure"
# The committed follow-up inbox (ADR-0007 carve-out): operator-authored stub
# names live in a different namespace from retired canonical-doc generations,
# so the legacy-NAME check exempts them (D-1/D-2). Every other check still runs.
INBOX_DIR = f"{PLANNING_DIR}/inbox"
PROCESSED_INBOX_DIR = f"{INBOX_DIR}/processed"
WORKFLOW_PATH = "WORKFLOW.md"
SYMPHONY_INTEGRATION_PATH = "docs/atlas/symphony-integration.md"
DELIVERY_CONTROL_PATH = "docs/atlas/multi-agent-delivery-control.md"
OPERATOR_ENVIRONMENT_PATH = "docs/runbooks/operator-environment.md"
PHASE_15_CLOSURE_PATH = "docs/closure/phase-15-closure-report.md"
AGENT_CONTRACT_PATHS = (
    WORKFLOW_PATH,
    "AGENTS.md",
    "docs/atlas/parallel-delivery-efficiency-and-integration-control.md",
    SYMPHONY_INTEGRATION_PATH,
    "docs/runbooks/agent-ticket-prompt.md",
    "docs/runbooks/local-development.md",
    "docs/runbooks/pr-acceptance.md",
)
SYMPHONY_MILESTONE_BRANCH = "phase-15-atlas-253-ceiling-ramp"
SYMPHONY_MILESTONE_LEVELS = (1, 3, 5, 7, 10)

# Directories whose Markdown files at any depth must all be listed in the
# MANIFEST.
CANONICAL_DIRS = (
    "docs/atlas",
    "docs/architecture",
    "docs/decisions",
    "docs/runbooks",
)

# Directories bare .md names in the MANIFEST may resolve against.
BARE_NAME_DIRS = ("", "docs/atlas", "docs/architecture")

ADR_REQUIRED_SECTIONS = (
    "Status",
    "Context",
    "Decision",
    "Rationale",
    "Consequences",
    "Alternatives considered",
)
ADR_STATUSES = ("proposed", "accepted", "superseded", "rejected")
ADR_FILENAME_RE = re.compile(r"^(\d{4})-[a-z0-9-]+\.md$")
ADR_TITLE_RE = re.compile(r"^# ADR-(\d{4}): \S")
ADR_REF_RE = re.compile(r"^\s*-\s+ADR-(\d{4})\b")

BACKTICK_RE = re.compile(r"`([^`]+)`")
PATH_SUFFIXES = (
    ".cfg",
    ".db",
    ".html",
    ".j2",
    ".json",
    ".md",
    ".mmd",
    ".py",
    ".sh",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
)
REPO_PATH_PREFIXES = (
    ".github/",
    "atlas/",
    "docs/",
    "scripts/",
    "tests/",
    "tools/",
)
PATH_TARGET_CARVEOUT_PREFIXES = (
    f"{ARCHIVE_DIR}/",
    f"{INBOX_DIR}/",
    ".atlas/",
    "docs/domain/",
)
BARE_PATH_DIRS = (
    "",
    "docs/atlas",
    "docs/architecture",
    "docs/runbooks",
    "docs/planning",
    "docs/closure",
    "docs/generated/schemas",
    "atlas/planning/prompts",
)

LEGACY_RES = (
    re.compile(r"-v[123]\.md\b", re.IGNORECASE),
    re.compile(r"ATLAS_V[123]", re.IGNORECASE),
    re.compile(r"_V[123]_", re.IGNORECASE),
    re.compile(r"roadmap\.html", re.IGNORECASE),
)
RETIREMENT_RE = re.compile(r"retired", re.IGNORECASE)

MD_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
MODULE_PATH_RE = re.compile(r"^[A-Za-z_][\w]*(\.[A-Za-z_][\w]*)+$")
PHASE_HEADING_RE = re.compile(
    r"^# Phase (?P<phase>\d+(?:\.\d+)?)\s+[—-]\s+(?P<title>.+)$"
)
FRACTIONAL_PHASE_RE = re.compile(r"^Phase (?P<phase>\d+\.\d+)\b")
PHASE_STATUS_RE = re.compile(r"^Status:\s*(?P<status>.+)$")
ROOT_CLOSED_RANGE_RE = re.compile(
    r"\bPhases?\s+(?P<start>\d+(?:\.\d+)?)\s+through\s+"
    r"(?P<end>\d+(?:\.\d+)?)\s+are\s+closed\b",
    re.IGNORECASE,
)
ROOT_CLOSED_SINGLE_RE = re.compile(
    r"\bPhase\s+(?P<phase>\d+(?:\.\d+)?)\s+(?:is\s+)?closed\b",
    re.IGNORECASE,
)
CURRENT_WORK_RE = re.compile(r"^Current work:\s*(?P<claim>.+)$", re.IGNORECASE)
PHASE_CLOSURE_REPORT_RE = re.compile(
    r"^phase-(?P<phase>\d+(?:\.\d+)?)-closure-report\.md$"
)
SOURCE_ANCHOR_RE = re.compile(r"^\s*source_anchor:\s*(?P<value>.*)$")
ENTITY_KEY_RE = re.compile(r"^\s*(?:-\s*)?key:\s*(?P<value>.+?)\s*$")

# ``.codex`` holds vendored Symphony skill files (e.g. .codex/skills/linear),
# which follow Symphony's own conventions and are adapted-not-forked (ATLAS-136
# D7); like ``.claude`` agent tooling they are not Atlas-authored docs and are
# outside the schema-fence / link discipline this linter enforces.
SKIP_DIRS = {".git", ".venv", "__pycache__", "node_modules", ".claude", ".codex"}


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    code: str
    message: str

    def render(self) -> str:
        return f"{self.path}:{self.line}: {self.code} {self.message}"


@dataclass(frozen=True)
class PhaseSection:
    phase: str
    line: int
    title: str
    status: str | None = None
    status_line: int | None = None


@dataclass(frozen=True)
class SourceAnchorRecord:
    """One persisted or rendered source_anchor occurrence."""

    path: str
    line: int
    anchor: str
    label: str


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _active_md_files(root: Path) -> list[Path]:
    """Every .md file outside docs/archive/ and tooling directories."""
    files = []
    for path in sorted(root.rglob("*.md")):
        rel = _rel(root, path)
        parts = set(Path(rel).parts)
        if parts & SKIP_DIRS or rel.startswith(f"{ARCHIVE_DIR}/"):
            continue
        files.append(path)
    return files


def _path_checked_md_files(root: Path) -> list[Path]:
    """Active Markdown files whose backticked path spans are live references."""
    files = []
    for path in _active_md_files(root):
        rel = _rel(root, path)
        if rel.startswith(f"{CLOSURE_DIR}/") or rel.startswith(f"{INBOX_DIR}/"):
            continue
        files.append(path)
    return files


def _blank_fenced_blocks(lines: list[str]) -> list[str]:
    """Replace fenced code-block lines with blanks, preserving numbering."""
    out = []
    in_fence = False
    for line in lines:
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            out.append("")
            continue
        out.append("" if in_fence else line)
    return out


def _split_sections(lines: list[str]) -> dict[str, list[str]]:
    """Map each H2 heading to its body lines."""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines:
        if line.startswith("## "):
            current = line[3:].strip()
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    return sections


def check_adrs(root: Path) -> list[Finding]:
    findings = []
    for path in sorted((root / DECISIONS_DIR).glob("*.md")):
        rel = _rel(root, path)
        name_match = ADR_FILENAME_RE.match(path.name)
        if not name_match:
            findings.append(
                Finding(rel, 1, "ADR001", "filename is not NNNN-kebab-title.md")
            )
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        title_match = ADR_TITLE_RE.match(lines[0]) if lines else None
        if not title_match:
            findings.append(
                Finding(rel, 1, "ADR002", "first line is not '# ADR-NNNN: <title>'")
            )
        elif title_match.group(1) != name_match.group(1):
            findings.append(
                Finding(rel, 1, "ADR003", "ADR number does not match filename")
            )
        sections = _split_sections(lines)
        for required in ADR_REQUIRED_SECTIONS:
            body = sections.get(required)
            if body is None:
                findings.append(
                    Finding(rel, 1, "ADR004", f"missing required section: {required}")
                )
            elif not any(line.strip() for line in body):
                findings.append(
                    Finding(rel, 1, "ADR005", f"section is empty: {required}")
                )
        status_body = [
            line.strip() for line in sections.get("Status", []) if line.strip()
        ]
        if status_body and not status_body[0].lower().startswith(ADR_STATUSES):
            findings.append(
                Finding(
                    rel,
                    1,
                    "ADR006",
                    f"status {status_body[0]!r} does not begin with one of "
                    f"{', '.join(ADR_STATUSES)}",
                )
            )
    return findings


def _manifest_tokens(manifest: Path) -> list[tuple[int, str]]:
    tokens = []
    for lineno, line in enumerate(
        manifest.read_text(encoding="utf-8").splitlines(), start=1
    ):
        for match in BACKTICK_RE.finditer(line):
            tokens.append((lineno, match.group(1)))
    return tokens


def _resolve_bare_name(root: Path, name: str) -> Path | None:
    for directory in BARE_NAME_DIRS:
        candidate = root / directory / name if directory else root / name
        if candidate.is_file():
            return candidate
    return None


def check_manifest(root: Path) -> list[Finding]:
    manifest = root / MANIFEST_PATH
    if not manifest.is_file():
        return [Finding(MANIFEST_PATH, 1, "MAN001", "manifest file is missing")]

    findings = []
    referenced: set[str] = set()

    for lineno, token in _manifest_tokens(manifest):
        if " " in token:  # backticked command, e.g. `atlas apply`
            continue
        if token.endswith("/"):
            if not (root / token).is_dir():
                findings.append(
                    Finding(
                        MANIFEST_PATH,
                        lineno,
                        "MAN002",
                        f"listed directory does not exist: {token}",
                    )
                )
            continue
        if "/" in token:
            if (root / token).is_file():
                referenced.add(token)
            else:
                findings.append(
                    Finding(
                        MANIFEST_PATH,
                        lineno,
                        "MAN003",
                        f"listed path does not exist: {token}",
                    )
                )
            continue
        if token.endswith(PATH_SUFFIXES):
            resolved = _resolve_bare_name(root, token)
            if resolved is None:
                findings.append(
                    Finding(
                        MANIFEST_PATH,
                        lineno,
                        "MAN003",
                        f"listed path does not exist: {token}",
                    )
                )
            else:
                referenced.add(_rel(root, resolved))

    adr_numbers: set[str] = set()
    for lineno, line in enumerate(
        manifest.read_text(encoding="utf-8").splitlines(), start=1
    ):
        ref = ADR_REF_RE.match(line)
        if not ref:
            continue
        number = ref.group(1)
        matches = sorted((root / DECISIONS_DIR).glob(f"{number}-*.md"))
        if matches:
            adr_numbers.add(number)
            referenced.add(_rel(root, matches[0]))
        else:
            findings.append(
                Finding(
                    MANIFEST_PATH,
                    lineno,
                    "MAN004",
                    f"listed ADR-{number} has no file in {DECISIONS_DIR}/",
                )
            )

    for directory in CANONICAL_DIRS:
        for path in sorted((root / directory).rglob("*.md")):
            rel = _rel(root, path)
            if rel in referenced:
                continue
            adr_name = ADR_FILENAME_RE.match(path.name)
            if adr_name and adr_name.group(1) in adr_numbers:
                continue
            findings.append(
                Finding(rel, 1, "MAN005", "canonical doc is not listed in MANIFEST")
            )
    return findings


def _inbox_path_spans(line: str) -> list[tuple[int, int]]:
    """Character spans of backticked paths under docs/planning/inbox/ (D-2).

    Inbox stub filenames are operator-authored run inputs; a run record must be
    able to name them verbatim. The exemption is span-scoped: only the interior
    of such a backtick is exempt, so a non-inbox legacy reference elsewhere on
    the same line still fires."""
    spans = []
    for match in BACKTICK_RE.finditer(line):
        if match.group(1).startswith(f"{INBOX_DIR}/"):
            spans.append((match.start(1), match.end(1)))
    return spans


def check_legacy_names(root: Path) -> list[Finding]:
    findings = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = _rel(root, path)
        parts = set(Path(rel).parts)
        if parts & SKIP_DIRS or rel.startswith(f"{ARCHIVE_DIR}/"):
            continue
        # D-1: operator-authored stub names in the terminal inbox are a
        # different namespace from retired canonical-doc generations.
        if rel.startswith(f"{PROCESSED_INBOX_DIR}/"):
            continue
        for pattern in LEGACY_RES:
            if pattern.search(path.name):
                findings.append(
                    Finding(rel, 1, "LEG001", f"legacy document name: {path.name}")
                )
    for path in _active_md_files(root):
        rel = _rel(root, path)
        lines = _blank_fenced_blocks(path.read_text(encoding="utf-8").splitlines())
        for lineno, line in enumerate(lines, start=1):
            if RETIREMENT_RE.search(line):
                continue  # documented retirement records are not live use
            exempt_spans = _inbox_path_spans(line)
            for pattern in LEGACY_RES:
                for match in pattern.finditer(line):
                    # D-2: a match inside a backticked inbox path is exempt;
                    # keep scanning for a non-exempt match on the same line.
                    if any(
                        start <= match.start() and match.end() <= end
                        for start, end in exempt_spans
                    ):
                        continue
                    findings.append(
                        Finding(
                            rel,
                            lineno,
                            "LEG002",
                            f"legacy document name referenced: {match.group(0)}",
                        )
                    )
                    break
    return findings


def check_intra_doc_links(root: Path) -> list[Finding]:
    findings = []
    for path in _active_md_files(root):
        rel = _rel(root, path)
        lines = _blank_fenced_blocks(path.read_text(encoding="utf-8").splitlines())
        for lineno, line in enumerate(lines, start=1):
            for match in MD_LINK_RE.finditer(line):
                target = match.group(1).split("#", 1)[0]
                if not target.endswith(".md") or "://" in target:
                    continue
                base = root if target.startswith("/") else path.parent
                if not (base / target.lstrip("/")).is_file():
                    findings.append(
                        Finding(
                            rel,
                            lineno,
                            "LNK001",
                            f"relative link target does not resolve: {target}",
                        )
                    )
    return findings


def _is_path_candidate(token: str) -> bool:
    if (
        not token
        or any(char.isspace() for char in token)
        or "://" in token
        or token.startswith(("#", "$", "~/"))
        or token in PATH_SUFFIXES
        or any(char in token for char in "*?[]{}<>")
        or "…" in token
        or token.startswith("docs/...")
        or token.startswith("inbox-stub-")
    ):
        return False
    target = token.lstrip("/").split("#", 1)[0]
    if not target or target in PATH_SUFFIXES:
        return False
    if any(target.startswith(prefix) for prefix in PATH_TARGET_CARVEOUT_PREFIXES):
        return False
    if token.startswith("/") and not target.startswith(REPO_PATH_PREFIXES):
        return False
    if target == ".app.json":
        return False
    if MODULE_PATH_RE.match(target) and not target.endswith(PATH_SUFFIXES):
        return False
    if target.startswith(REPO_PATH_PREFIXES):
        return True
    return target.endswith(PATH_SUFFIXES)


def _resolve_path_reference(root: Path, source: Path, token: str) -> Path | None:
    target = token.lstrip("/").split("#", 1)[0]
    if target.startswith(("./", "../")):
        candidate = (source.parent / target).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return None
        return candidate if candidate.exists() else None
    if "/" in target:
        candidate = root / target
        return candidate if candidate.exists() else None
    for directory in BARE_PATH_DIRS:
        candidate = root / directory / target if directory else root / target
        if candidate.exists():
            return candidate
    return None


def check_backticked_paths(root: Path) -> list[Finding]:
    findings = []
    for path in _path_checked_md_files(root):
        rel = _rel(root, path)
        lines = _blank_fenced_blocks(path.read_text(encoding="utf-8").splitlines())
        for lineno, line in enumerate(lines, start=1):
            for match in BACKTICK_RE.finditer(line):
                token = match.group(1).strip()
                if not _is_path_candidate(token):
                    continue
                if _resolve_path_reference(root, path, token) is None:
                    findings.append(
                        Finding(
                            rel,
                            lineno,
                            "PTH001",
                            f"backticked repository path does not resolve: {token}",
                        )
                    )
    return findings


def check_planning_renders(root: Path) -> list[Finding]:
    planning = root / PLANNING_DIR
    if not planning.is_dir():
        return []
    # docs/planning/inbox/ is a *separate* committed input source, not an
    # atlas apply render: the follow-up producer is its machine writer and the
    # operator commits it (ADR-0007 carve-out, ATLAS-45/122; see
    # pm-engine-and-linear-sync.md and planning-engine-specification.md §"the
    # committed follow-up inbox"). Inbox stubs carry no render header by design,
    # so the render-header rule does not apply to them or to inbox/processed/.
    inbox = planning / "inbox"
    findings = []
    for path in sorted(planning.rglob("*")):
        if not path.is_file() or path.name == ".gitkeep":
            continue
        if path == inbox or inbox in path.parents:
            continue
        head = "\n".join(path.read_text(encoding="utf-8").splitlines()[:10]).lower()
        if "plan_run_id" not in head or "atlas apply" not in head:
            findings.append(
                Finding(
                    _rel(root, path),
                    1,
                    "PLAN001",
                    "planning file lacks the atlas apply render header "
                    "(hand-edit? renders are written only by atlas apply, "
                    "ADR-0007)",
                )
            )
    return findings


_WORKFLOW_FRONT_MATTER_RE = re.compile(
    r"\A---\n(?P<front>.*?)\n---(?:\n|\Z)", re.DOTALL
)
_PHASE_15_CLOSED_RE = re.compile(r"^Status:\s*CLOSED\b", re.IGNORECASE | re.MULTILINE)

_CI_WAITING_INSTRUCTION_RES = (
    re.compile(
        r"\b(?:poll|monitor|watch|wait\s+for)\s+(?:remote\s+)?"
        r"(?:CI|checks?|review)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bonce\s+(?:CI|checks?)\s+(?:is|are)\s+"
        r"(?:complete|finished|green|passing)\b",
        re.IGNORECASE,
    ),
)
_SCOPED_AUTHORITY_CLAIM_RES = (
    re.compile(
        r"\b(?:scoped|focused|selected|shorter)\s+(?:local\s+)?"
        r"(?:checks?|validation|runs?|results?)\b[^.!?]{0,160}"
        r"\b(?:prove|proves|certify|certifies|establish|establishes|"
        r"authorise|authorises|authorize|authorizes)\b[^.!?]{0,120}"
        r"\b(?:repository(?:-wide)?\s+(?:authority|completion|correctness)|"
        r"(?:complete|entire|whole)\s+repository|completion\s+evidence)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:scoped|focused|selected|shorter)\s+(?:local\s+)?"
        r"(?:checks?|validation|runs?|results?)\b[^.!?]{0,120}"
        r"\b(?:is|are|become|becomes|provide|provides)\b[^.!?]{0,80}"
        r"\b(?:repository-wide\s+authority|completion\s+evidence|"
        r"sufficient\s+for\s+repository\s+completion)\b",
        re.IGNORECASE,
    ),
)
_CONTRACT_NEGATION_RE = re.compile(
    r"\b(?:cannot|do\s+not|does\s+not|forbid|forbids|insufficient|never|no|"
    r"not|prohibit|prohibits|without)\b|"
    r"\b(?:don't|doesn't|isn't|aren't)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SymphonyMilestoneValidation:
    """Explicit validation context for the dedicated Phase 15 branch."""

    branch: str
    level: int


_CEILING_AUTHORITY_MARKERS = {
    WORKFLOW_PATH: (
        "single controlling Symphony worker",
        "The operator is the sole owner of this value",
        "observed occupied slots",
    ),
    SYMPHONY_INTEGRATION_PATH: (
        "### Symphony ceiling ownership",
        "It is not a second ceiling",
        "Actual occupied slots",
        "Historical migration `0025` and policy revision one remain immutable",
    ),
    DELIVERY_CONTROL_PATH: (
        "There is one operator-owned Symphony ceiling",
        "actual occupied slots are observed Symphony sessions",
        "None is a utilisation target",
        "Revision one is immutable historical bootstrap data",
    ),
}

_CEILING_GATE_MARKERS = (
    "### Gate 1 — serialized baseline admission, pause and rework",
    "### Gate 3 — first controlled increase and review pressure",
    "### Gate 5 — stable review and stale-write protection",
    "### Gate 7 — lanes, recovery and acceptance capacity",
    "### Gate 10 — maximum, not target, and closure",
)

_CEILING_RUNBOOK_MARKERS = (
    SYMPHONY_MILESTONE_BRANCH,
    "atlas:symphony-ceiling-gate v1",
    "origin_main_sha:",
    "merge_base_sha:",
    "The only permitted sequence is `1 -> 3`, `3 -> 5`, `5 -> 7`, then `7 -> 10`",
    "--symphony-milestone-level <1|3|5|7|10>",
    "Every level has one fixed 60-minute window",
    "Gate 3 cannot begin without the Gate 1 PASS receipt",
    "Gate 5 cannot begin without the Gate 3 PASS receipt",
    "Gate 7 cannot begin without the Gate 5 PASS receipt",
    "Gate 10 cannot begin without the Gate 7 PASS receipt, Phase 14 closure",
    "adequate exact-head acceptance throughput",
    "Only after that process-owned proof succeeds",
    "Current `origin/main` declares exactly one and keeps `max_turns: 10`",
    "Only the operator may change the milestone-branch declaration",
    "Values 3, 5 and 7 are valid only on that branch",
    "never independently mergeable to `main`",
    "existing governed Phase 15 policy-revision boundary",
    "vps-systemd-immutable-workflow-readback-v1",
    "atlas-symphony.service",
    "e5c5e48917e9e91ffb6709ab5a2a02c5af16bf02",
    "git show <gate-commit>:WORKFLOW.md > <immutable-workflow-file>",
    "process-owned `/api/v1/runtime`",
    "workflow_content_sha256",
    "fixture/schema regression only",
    "never production runtime evidence",
    "previous proven gate's exact immutable workflow file",
    "mainline progress alone does not force a Gate 1 restart",
    "### Stop, rollback and non-closure",
)


def _finding_line(text: str, marker: str) -> int:
    """Return a stable one-based line for a present marker, else line one."""
    offset = text.find(marker)
    return text.count("\n", 0, offset) + 1 if offset >= 0 else 1


def _sentence_prefix(text: str, offset: int) -> str:
    """Return the sentence/paragraph text that can negate a matched claim."""
    boundaries = (
        text.rfind(".", 0, offset),
        text.rfind("!", 0, offset),
        text.rfind("?", 0, offset),
        text.rfind(";", 0, offset),
        text.rfind(", but", 0, offset),
        text.rfind(", yet", 0, offset),
        text.rfind("\n\n", 0, offset),
    )
    return text[max(boundaries) + 1 : offset]


def _positive_contract_matches(
    text: str, patterns: tuple[re.Pattern[str], ...]
) -> list[re.Match[str]]:
    """Return positive prohibited claims while allowing explicit negations."""
    matches: list[re.Match[str]] = []
    seen: set[tuple[int, int]] = set()
    for pattern in patterns:
        for match in pattern.finditer(text):
            identity = (match.start(), match.end())
            if identity in seen:
                continue
            prefix = _sentence_prefix(text, match.start())
            if _CONTRACT_NEGATION_RE.search(prefix + match.group(0)):
                continue
            seen.add(identity)
            matches.append(match)
    return sorted(matches, key=lambda match: (match.start(), match.end()))


def check_scoped_validation_handoff_contract(root: Path) -> list[Finding]:
    """Reject agent-side CI waiting and local-validation authority overclaims.

    The check is conditional on ``WORKFLOW.md`` for the same reason as the
    Symphony ceiling check: small standalone linter fixtures do not model the
    Atlas agent contract. Missing canonical documents remain the manifest
    check's responsibility.
    """
    if not (root / WORKFLOW_PATH).is_file():
        return []

    findings: list[Finding] = []
    for rel in AGENT_CONTRACT_PATHS:
        path = root / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for match in _positive_contract_matches(text, _CI_WAITING_INSTRUCTION_RES):
            findings.append(
                Finding(
                    rel,
                    text.count("\n", 0, match.start()) + 1,
                    "HND001",
                    "agent contract must hand off to CI Pending without polling "
                    "or waiting for CI/review",
                )
            )
        for match in _positive_contract_matches(text, _SCOPED_AUTHORITY_CLAIM_RES):
            findings.append(
                Finding(
                    rel,
                    text.count("\n", 0, match.start()) + 1,
                    "HND002",
                    "scoped local validation is agent-tier confidence, not "
                    "repository-wide or completion authority",
                )
            )
    return findings


def check_symphony_ceiling_contract(
    root: Path,
    *,
    milestone: SymphonyMilestoneValidation | None = None,
) -> list[Finding]:
    """Validate the governed 1→3→5→7→10 Symphony ceiling documentation.

    The check is conditional on ``WORKFLOW.md`` so the doc-linter's deliberately
    small unit-test repositories do not have to model Symphony. A real Atlas
    checkout always has the workflow and therefore always exercises the gate.
    Intermediate milestone-branch values pass only when the caller explicitly
    supplies the exact pinned branch and expected level. The ordinary context
    intentionally fails SCG003 until the successful Gate 10 closure report
    accompanies exactly ten; this keeps intermediate or unaccompanied ceiling
    commits independently unmergeable to ``main``.
    """
    workflow_path = root / WORKFLOW_PATH
    if not workflow_path.is_file():
        return []

    findings: list[Finding] = []
    workflow = workflow_path.read_text(encoding="utf-8")
    front_match = _WORKFLOW_FRONT_MATTER_RE.match(workflow)
    ceiling: object | None = None
    max_turns: object | None = None
    if front_match is None:
        findings.append(
            Finding(
                WORKFLOW_PATH,
                1,
                "SCG001",
                "WORKFLOW.md must open with parseable YAML front matter",
            )
        )
    else:
        try:
            front = yaml.safe_load(front_match.group("front"))
        except yaml.YAMLError:
            front = None
        if isinstance(front, dict):
            agent = front.get("agent")
            if isinstance(agent, dict):
                ceiling = agent.get("max_concurrent_agents")
                max_turns = agent.get("max_turns")
        if isinstance(ceiling, bool) or not isinstance(ceiling, int):
            findings.append(
                Finding(
                    WORKFLOW_PATH,
                    _finding_line(workflow, "max_concurrent_agents"),
                    "SCG001",
                    "agent.max_concurrent_agents must be a strict integer",
                )
            )
        elif not 1 <= ceiling <= 10:
            findings.append(
                Finding(
                    WORKFLOW_PATH,
                    _finding_line(workflow, "max_concurrent_agents"),
                    "SCG002",
                    "agent.max_concurrent_agents must be between 1 and 10",
                )
            )
        if isinstance(max_turns, bool) or max_turns != 10:
            findings.append(
                Finding(
                    WORKFLOW_PATH,
                    _finding_line(workflow, "max_turns"),
                    "SCG007",
                    "agent.max_turns must remain exactly 10; it is outside "
                    "the concurrency ramp",
                )
            )

    closure_path = root / PHASE_15_CLOSURE_PATH
    closure = closure_path.read_text(encoding="utf-8") if closure_path.is_file() else ""
    closure_is_closed = bool(_PHASE_15_CLOSED_RE.search(closure))
    milestone_is_valid = milestone is not None
    if milestone is not None:
        context_errors: list[str] = []
        if milestone.branch != SYMPHONY_MILESTONE_BRANCH:
            context_errors.append(f"branch must be exactly {SYMPHONY_MILESTONE_BRANCH}")
        if isinstance(milestone.level, bool) or (
            milestone.level not in SYMPHONY_MILESTONE_LEVELS
        ):
            context_errors.append("level must be exactly one of 1, 3, 5, 7 or 10")
        if context_errors:
            milestone_is_valid = False
            findings.append(
                Finding(
                    WORKFLOW_PATH,
                    _finding_line(workflow, "max_concurrent_agents"),
                    "SCG008",
                    "invalid Symphony milestone validation context: "
                    + "; ".join(context_errors),
                )
            )
        elif isinstance(ceiling, int) and not isinstance(ceiling, bool):
            if ceiling != milestone.level:
                milestone_is_valid = False
                findings.append(
                    Finding(
                        WORKFLOW_PATH,
                        _finding_line(workflow, "max_concurrent_agents"),
                        "SCG008",
                        "milestone validation level does not match "
                        f"WORKFLOW.md: expected {milestone.level}, found {ceiling}",
                    )
                )
    if isinstance(ceiling, int) and not isinstance(ceiling, bool):
        if (
            not closure_is_closed
            and ceiling != 1
            and (milestone is None or not milestone_is_valid)
        ):
            findings.append(
                Finding(
                    WORKFLOW_PATH,
                    _finding_line(workflow, "max_concurrent_agents"),
                    "SCG003",
                    "open Phase 15 requires the unaccompanied mainline ceiling "
                    "to remain exactly 1",
                )
            )
        elif closure_is_closed:
            closure_markers = (
                "## Symphony ceiling ramp",
                "Gate 10 receipt",
                "max_concurrent_agents: 10",
            )
            missing_closure = [
                marker for marker in closure_markers if marker not in closure
            ]
            if ceiling != 10 or missing_closure:
                detail = (
                    f"; missing closure markers: {', '.join(missing_closure)}"
                    if missing_closure
                    else ""
                )
                findings.append(
                    Finding(
                        PHASE_15_CLOSURE_PATH,
                        1,
                        "SCG003",
                        "CLOSED Phase 15 must accompany and prove exactly "
                        f"max_concurrent_agents: 10{detail}",
                    )
                )

    for rel, markers in _CEILING_AUTHORITY_MARKERS.items():
        path = root / rel
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        flowed = " ".join(text.split())
        missing = [marker for marker in markers if marker not in flowed]
        if missing:
            findings.append(
                Finding(
                    rel,
                    1,
                    "SCG004",
                    "Symphony ceiling authority wording is incomplete; missing: "
                    + ", ".join(missing),
                )
            )

    runbook_path = root / OPERATOR_ENVIRONMENT_PATH
    runbook = runbook_path.read_text(encoding="utf-8") if runbook_path.is_file() else ""
    flowed_runbook = " ".join(runbook.split())
    missing_runbook = [
        marker for marker in _CEILING_RUNBOOK_MARKERS if marker not in flowed_runbook
    ]
    positions = [runbook.find(marker) for marker in _CEILING_GATE_MARKERS]
    if (
        missing_runbook
        or any(position < 0 for position in positions)
        or positions != sorted(positions)
    ):
        missing_gates = [
            marker
            for marker, position in zip(_CEILING_GATE_MARKERS, positions, strict=True)
            if position < 0
        ]
        details = missing_runbook + missing_gates
        if not details:
            details = ["Gate 1, 3, 5, 7 and 10 headings must remain in order"]
        findings.append(
            Finding(
                OPERATOR_ENVIRONMENT_PATH,
                1,
                "SCG005",
                "controlled-ramp gate contract is incomplete; missing: "
                + ", ".join(details),
            )
        )

    runbook_heading = "## Symphony ceiling controlled-ramp runbook"
    ramp = (
        runbook[runbook.find(runbook_heading) :] if runbook_heading in runbook else ""
    )
    flowed_ramp = " ".join(ramp.split())
    forbidden_mutation_path = re.search(
        r"(?im)^\s*(?:POST|PUT|PATCH|DELETE)\s+/|linear_graphql",
        ramp,
    )
    authority_boundary = (
        "The ramp adds no endpoint, CLI, agent action or automation that edits "
        "delivery policy"
    )
    repository_authority_boundary = (
        "No Atlas endpoint, CLI, agent or automation may edit `WORKFLOW.md`, "
        "Symphony configuration, acceptance evidence or milestone receipts"
    )
    if (
        authority_boundary not in flowed_ramp
        or repository_authority_boundary not in flowed_ramp
        or forbidden_mutation_path is not None
    ):
        findings.append(
            Finding(
                OPERATOR_ENVIRONMENT_PATH,
                _finding_line(runbook, runbook_heading),
                "SCG006",
                "controlled-ramp procedure must preserve the operator-only "
                "policy and repository mutation boundaries",
            )
        )

    return findings


def _recognise_phase_status(raw: str) -> str | None:
    status = re.sub(r"[*`]", "", raw).strip().upper().rstrip(".")
    if status.startswith("CLOSED"):
        return "CLOSED"
    if status.startswith("IN PROGRESS"):
        return "IN PROGRESS"
    return None


def _parse_implementation_phases(
    root: Path,
) -> tuple[dict[str, PhaseSection], list[Finding]]:
    roadmap = root / IMPLEMENTATION_ROADMAP_PATH
    if not roadmap.is_file():
        return (
            {},
            [
                Finding(
                    IMPLEMENTATION_ROADMAP_PATH,
                    1,
                    "PHS003",
                    "implementation roadmap is missing",
                )
            ],
        )
    sections: dict[str, PhaseSection] = {}
    findings = []
    current: str | None = None
    for lineno, line in enumerate(
        roadmap.read_text(encoding="utf-8").splitlines(), start=1
    ):
        heading = PHASE_HEADING_RE.match(line)
        fractional = FRACTIONAL_PHASE_RE.match(line)
        if heading:
            phase = heading.group("phase")
            sections[phase] = PhaseSection(
                phase=phase, line=lineno, title=heading.group("title").strip()
            )
            current = phase
            continue
        if fractional and fractional.group("phase") not in sections:
            phase = fractional.group("phase")
            sections[phase] = PhaseSection(
                phase=phase,
                line=lineno,
                title=line.removesuffix(":").strip(),
            )
            current = phase
            continue
        status = PHASE_STATUS_RE.match(line)
        if not status or current is None:
            continue
        recognised = _recognise_phase_status(status.group("status"))
        if recognised is None:
            findings.append(
                Finding(
                    IMPLEMENTATION_ROADMAP_PATH,
                    lineno,
                    "PHS004",
                    f"unrecognised roadmap phase status: {status.group('status')}",
                )
            )
            continue
        section = sections[current]
        sections[current] = PhaseSection(
            phase=section.phase,
            line=section.line,
            title=section.title,
            status=recognised,
            status_line=lineno,
        )
    return sections, findings


def _phase_number(phase: str) -> float:
    return float(phase)


def _closed_phases_from_root_roadmap(
    root: Path, sections: dict[str, PhaseSection]
) -> tuple[set[str], Finding | None]:
    roadmap = root / ROOT_ROADMAP_PATH
    if not roadmap.is_file():
        return set(), Finding(ROOT_ROADMAP_PATH, 1, "PHS003", "ROADMAP.md is missing")
    closed: set[str] = set()
    for line in roadmap.read_text(encoding="utf-8").splitlines():
        for match in ROOT_CLOSED_RANGE_RE.finditer(line):
            start = _phase_number(match.group("start"))
            end = _phase_number(match.group("end"))
            closed.update(
                phase
                for phase in sections
                if start <= _phase_number(phase) <= end and phase != "0"
            )
        for match in ROOT_CLOSED_SINGLE_RE.finditer(line):
            phase = match.group("phase")
            if phase in sections:
                closed.add(phase)
    return closed, None


def _phase_closure_reports(root: Path) -> dict[str, str]:
    closure = root / CLOSURE_DIR
    if not closure.is_dir():
        return {}
    reports = {}
    for path in sorted(closure.glob("*.md")):
        match = PHASE_CLOSURE_REPORT_RE.match(path.name)
        if match:
            reports[match.group("phase")] = _rel(root, path)
    return reports


def _current_work_claim(root: Path) -> tuple[int, str] | None:
    roadmap = root / ROOT_ROADMAP_PATH
    if not roadmap.is_file():
        return None
    for lineno, line in enumerate(
        roadmap.read_text(encoding="utf-8").splitlines(), start=1
    ):
        match = CURRENT_WORK_RE.match(line)
        if match:
            return lineno, match.group("claim")
    return None


def _normalised_words(text: str) -> set[str]:
    return {
        word
        for word in re.findall(r"[A-Za-z0-9]+", text.lower())
        if word not in {"a", "an", "and", "current", "phase", "the", "work"}
    }


def _current_claim_matches_section(
    claim: str, sections: dict[str, PhaseSection]
) -> bool:
    explicit_phase = re.search(r"\bPhase\s+(?P<phase>\d+(?:\.\d+)?)\b", claim)
    if explicit_phase:
        return explicit_phase.group("phase") in sections
    summary = claim.split("—", 1)[0].split("-", 1)[0]
    words = _normalised_words(summary)
    if not words:
        return False
    return any(
        words <= _normalised_words(section.title) for section in sections.values()
    )


def check_phase_status(root: Path) -> list[Finding]:
    sections, findings = _parse_implementation_phases(root)
    root_closed, root_finding = _closed_phases_from_root_roadmap(root, sections)
    if root_finding is not None:
        findings.append(root_finding)
    closed = root_closed | {
        phase for phase, section in sections.items() if section.status == "CLOSED"
    }
    reports = _phase_closure_reports(root)

    for phase in sorted(closed, key=_phase_number):
        if phase not in reports:
            section = sections[phase]
            findings.append(
                Finding(
                    IMPLEMENTATION_ROADMAP_PATH,
                    section.status_line or section.line,
                    "PHS001",
                    f"Phase {phase} is CLOSED but {CLOSURE_DIR}/"
                    f"phase-{phase}-closure-report.md is missing",
                )
            )
    for phase, path in sorted(reports.items(), key=lambda item: _phase_number(item[0])):
        if phase not in closed:
            findings.append(
                Finding(
                    path,
                    1,
                    "PHS002",
                    "phase closure report has no CLOSED roadmap section: "
                    f"Phase {phase}",
                )
            )

    claim = _current_work_claim(root)
    if claim is None:
        findings.append(
            Finding(
                ROOT_ROADMAP_PATH,
                1,
                "PHS003",
                "ROADMAP.md has no current-work phase claim",
            )
        )
    elif not _current_claim_matches_section(claim[1], sections):
        findings.append(
            Finding(
                ROOT_ROADMAP_PATH,
                claim[0],
                "PHS003",
                f"current-work phase claim names no roadmap phase section: {claim[1]}",
            )
        )
    return findings


# --- source_anchor integrity (ATLAS-196) ------------------------------------


def _is_git_worktree(root: Path) -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def _worktree_markdown_paths(root: Path) -> list[str]:
    return sorted(_rel(root, path) for path in root.rglob("*.md") if path.is_file())


def _worktree_document(root: Path, rel: str) -> SourceDocument:
    return SourceDocument(
        path=rel,
        sha="working-tree",
        content=(root / rel).read_text(encoding="utf-8"),
    )


def _worktree_input_documents(root: Path) -> list[SourceDocument]:
    """Fixture fallback for non-git repos, using ingestion's corpus constants."""
    paths = _worktree_markdown_paths(root)
    documents = [
        _worktree_document(root, path) for path in _ROOT_DOCS if (root / path).is_file()
    ]
    for pattern in _GLOBS:
        for path in (path for path in paths if fnmatch.fnmatch(path, pattern)):
            document = _worktree_document(root, path)
            if path.startswith(f"{DECISIONS_DIR}/") and not _is_accepted_adr(
                document.content
            ):
                continue
            documents.append(document)
    return documents


def _worktree_inbox_documents(root: Path) -> list[SourceDocument]:
    return [
        _worktree_document(root, path)
        for path in _worktree_markdown_paths(root)
        if _matches_inbox(path, INBOX_DIR)
    ]


def _worktree_processed_documents(root: Path) -> list[SourceDocument]:
    return [
        _worktree_document(root, path)
        for path in _worktree_markdown_paths(root)
        if _matches_processed(path, INBOX_DIR)
    ]


def _source_anchor_index(root: Path) -> tuple[AnchorIndex | None, Finding | None]:
    try:
        if _is_git_worktree(root):
            documents = collect_input_documents(root)
            inbox_documents = collect_inbox_documents(root, Path(INBOX_DIR))
            processed_documents = collect_processed_documents(root, Path(INBOX_DIR))
        else:
            documents = _worktree_input_documents(root)
            inbox_documents = _worktree_inbox_documents(root)
            processed_documents = _worktree_processed_documents(root)
        index_documents = (
            documents
            + inbox_documents
            + processed_documents
            + durable_alias_documents(inbox_documents, processed_documents)
        )
        return AnchorIndex.build(index_documents), None
    except IngestionError as error:
        return (
            None,
            Finding(
                ".",
                1,
                "SRC000",
                f"source_anchor corpus cannot be indexed: {error}",
            ),
        )


def _yaml_scalar(raw: str) -> str:
    try:
        value = yaml.safe_load(raw)
    except yaml.YAMLError:
        return raw.strip()
    if value is None:
        return ""
    return str(value)


def _render_anchor_records(root: Path) -> list[SourceAnchorRecord]:
    planning = root / PLANNING_DIR
    if not planning.is_dir():
        return []
    inbox = planning / "inbox"
    records = []
    for path in sorted(planning.rglob("*")):
        if not path.is_file() or path.suffix not in {".yaml", ".yml"}:
            continue
        if path == inbox or inbox in path.parents:
            continue
        rel = _rel(root, path)
        current_key = rel
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            key_match = ENTITY_KEY_RE.match(line)
            if key_match:
                current_key = _yaml_scalar(key_match.group("value"))
                continue
            anchor_match = SOURCE_ANCHOR_RE.match(line)
            if not anchor_match:
                continue
            records.append(
                SourceAnchorRecord(
                    path=rel,
                    line=lineno,
                    anchor=_yaml_scalar(anchor_match.group("value")),
                    label=f"render {current_key}",
                )
            )
    return records


def _anchor_failure(record: SourceAnchorRecord, error: IngestionError) -> Finding:
    if isinstance(error, UnknownDocumentError):
        return Finding(
            record.path,
            record.line,
            "SRC001",
            f"{record.label} source_anchor document is outside indexed input set: "
            f"{record.anchor}",
        )
    if isinstance(error, UnknownAnchorError):
        return Finding(
            record.path,
            record.line,
            "SRC002",
            f"{record.label} source_anchor heading does not resolve: {record.anchor}",
        )
    if isinstance(error, MalformedAnchorError):
        return Finding(
            record.path,
            record.line,
            "SRC003",
            f"{record.label} source_anchor is not <path>#<slug>: {record.anchor}",
        )
    return Finding(
        record.path,
        record.line,
        "SRC000",
        f"{record.label} source_anchor could not be checked: {error}",
    )


def check_source_anchor_records(
    root: Path, records: Iterable[SourceAnchorRecord]
) -> list[Finding]:
    records = list(records)
    if not records:
        return []
    index, index_finding = _source_anchor_index(root)
    if index_finding is not None:
        return [index_finding]
    assert index is not None

    findings = []
    for record in records:
        try:
            index.resolve(record.anchor)
        except IngestionError as error:
            findings.append(_anchor_failure(record, error))
    return findings


def check_render_source_anchors(root: Path) -> list[Finding]:
    """Validate source_anchor fields in docs/planning renders without a database."""
    return check_source_anchor_records(root, _render_anchor_records(root))


def check_store_source_anchors(root: Path, database: Database) -> list[Finding]:
    """Validate source_anchor fields in the operational store."""
    from atlas.storage import EpicRepo, TicketRepo

    try:
        records = [
            SourceAnchorRecord(
                path="store/epics",
                line=index,
                anchor=epic.source_anchor,
                label=f"store epic {epic.key}",
            )
            for index, epic in enumerate(EpicRepo(database).list(), start=1)
        ]
        records.extend(
            SourceAnchorRecord(
                path="store/tickets",
                line=index,
                anchor=ticket.source_anchor,
                label=f"store ticket {ticket.key}",
            )
            for index, ticket in enumerate(TicketRepo(database).list(), start=1)
        )
    except Exception as error:
        return [
            Finding(
                "store",
                1,
                "SRC000",
                f"store source_anchor scan failed: {error}",
            )
        ]
    return check_source_anchor_records(root, records)


# --- v2 (ATLAS-16): JSON examples and generated-schema integrity ----------

# Annotation keys carry no validation semantics; everything else the
# validator does not explicitly handle fails closed (JSN007).
_ANNOTATION_KEYS = {"$defs", "default", "description", "title"}
_HANDLED_SCHEMA_KEYS = _ANNOTATION_KEYS | {
    "$ref",
    "additionalProperties",
    "allOf",
    "anyOf",
    "enum",
    "format",
    "items",
    "maximum",
    "minimum",
    "properties",
    "required",
    "type",
}
_KNOWN_FORMATS = ("date-time", "uuid")
_JSON_TYPE_NAMES = {
    str: "string",
    bool: "boolean",
    int: "integer",
    float: "number",
    list: "array",
    dict: "object",
    type(None): "null",
}


def _json_type_matches(value: Any, type_name: str) -> bool | None:
    """True/False on a known type name, None on an unknown one."""
    if type_name == "string":
        return isinstance(value, str)
    if type_name == "boolean":
        return isinstance(value, bool)
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if type_name == "array":
        return isinstance(value, list)
    if type_name == "object":
        return isinstance(value, dict)
    if type_name == "null":
        return value is None
    return None


def _format_ok(value: str, fmt: str) -> bool:
    try:
        if fmt == "uuid":
            uuid.UUID(value)
        elif fmt == "date-time":
            datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _validate_value(
    value: Any,
    schema: dict[str, Any],
    defs: dict[str, Any],
    path: str,
    partial: bool,
) -> list[tuple[str, str]]:
    """Validate one JSON value against one schema node; fail closed."""
    unknown = set(schema) - _HANDLED_SCHEMA_KEYS
    if unknown:
        return [
            (
                "JSN007",
                f"unsupported schema construct(s) {sorted(unknown)} at {path}; "
                "the validator fails closed rather than skipping",
            )
        ]
    if "$ref" in schema:
        ref = schema["$ref"]
        name = ref.rsplit("/", 1)[-1]
        target = defs.get(name)
        if not ref.startswith("#/$defs/") or not isinstance(target, dict):
            return [("JSN007", f"unresolvable $ref {ref!r} at {path}")]
        return _validate_value(value, target, defs, path, partial)
    if "allOf" in schema:
        findings = []
        for sub in schema["allOf"]:
            findings.extend(_validate_value(value, sub, defs, path, partial))
        return findings
    if "anyOf" in schema:
        branches = [
            _validate_value(value, sub, defs, path, partial) for sub in schema["anyOf"]
        ]
        if any(not branch for branch in branches):
            return []
        closed = [f for branch in branches for f in branch if f[0] == "JSN007"]
        if closed:
            return closed
        return [("JSN005", f"value does not match any permitted type at {path}")]
    if "enum" in schema:
        if value not in schema["enum"]:
            return [("JSN005", f"{value!r} is not a permitted enum value at {path}")]
        return []
    if "type" not in schema:
        return []  # unconstrained (e.g. dict[str, Any] values)
    type_name = schema["type"]
    if not isinstance(type_name, str):
        return [("JSN007", f"unsupported type form {type_name!r} at {path}")]
    matches = _json_type_matches(value, type_name)
    if matches is None:
        return [("JSN007", f"unknown schema type {type_name!r} at {path}")]
    if not matches:
        actual = _JSON_TYPE_NAMES.get(type(value), type(value).__name__)
        return [("JSN005", f"expected {type_name}, got {actual} at {path}")]
    findings = []
    if type_name in ("integer", "number"):
        minimum = schema.get("minimum")
        if minimum is not None and value < minimum:
            findings.append(
                ("JSN005", f"{value!r} is below the minimum {minimum} at {path}")
            )
        maximum = schema.get("maximum")
        if maximum is not None and value > maximum:
            findings.append(
                ("JSN005", f"{value!r} is above the maximum {maximum} at {path}")
            )
    if type_name == "string" and "format" in schema:
        fmt = schema["format"]
        if fmt not in _KNOWN_FORMATS:
            findings.append(("JSN007", f"unknown string format {fmt!r} at {path}"))
        elif not _format_ok(value, fmt):
            findings.append(("JSN005", f"{value!r} is not a valid {fmt} at {path}"))
    elif type_name == "array" and isinstance(schema.get("items"), dict):
        for index, element in enumerate(value):
            findings.extend(
                _validate_value(
                    element, schema["items"], defs, f"{path}[{index}]", partial
                )
            )
    elif type_name == "object":
        properties = schema.get("properties")
        if isinstance(properties, dict):
            for key, item in value.items():
                if key not in properties:
                    findings.append(("JSN004", f"unknown key {path}.{key}"))
                else:
                    findings.extend(
                        _validate_value(
                            item, properties[key], defs, f"{path}.{key}", partial
                        )
                    )
            if not partial:
                for required in schema.get("required", []):
                    if required not in value:
                        findings.append(
                            ("JSN006", f"missing required key {path}.{required}")
                        )
        else:
            additional = schema.get("additionalProperties", True)
            if isinstance(additional, dict):
                for key, item in value.items():
                    findings.extend(
                        _validate_value(
                            item, additional, defs, f"{path}.{key}", partial
                        )
                    )
            elif additional is not True:
                findings.append(
                    ("JSN007", f"unsupported additionalProperties at {path}")
                )
    return findings


def _json_fences(lines: list[str]) -> list[tuple[int, list[str], str]]:
    """(opening line number, info tokens, body) per ```json fence."""
    fences = []
    in_fence = False
    tokens: list[str] = []
    start = 0
    body: list[str] = []
    for lineno, line in enumerate(lines, start=1):
        stripped = line.lstrip()
        if stripped.startswith("```"):
            if in_fence:
                if tokens and tokens[0] == "json":
                    fences.append((start, tokens, "\n".join(body)))
                in_fence = False
            else:
                in_fence = True
                tokens = stripped[3:].strip().split()
                start = lineno
                body = []
            continue
        if in_fence:
            body.append(line)
    return fences


def _parse_fence_marker(
    tokens: list[str],
) -> tuple[str | None, bool, bool, list[str]]:
    """-> (model, partial, exempt, bad tokens) from a json info string."""
    model: str | None = None
    partial = False
    exempt = False
    bad = []
    for token in tokens[1:]:
        if token == "partial":
            partial = True
        elif token == "no-schema":
            exempt = True
        elif token.startswith("model=") and len(token) > len("model="):
            model = token.removeprefix("model=")
        else:
            bad.append(token)
    return model, partial, exempt, bad


def check_json_examples(root: Path) -> list[Finding]:
    findings = []
    schemas_dir = root / SCHEMAS_DIR
    for path in _active_md_files(root):
        rel = _rel(root, path)
        lines = path.read_text(encoding="utf-8").splitlines()
        for lineno, tokens, body in _json_fences(lines):
            model, partial, exempt, bad = _parse_fence_marker(tokens)
            if bad:
                findings.append(
                    Finding(
                        rel,
                        lineno,
                        "JSN001",
                        f"unrecognised fence marker(s) {bad}: expected "
                        "model=<ModelName>, partial, or no-schema",
                    )
                )
                continue
            if exempt:
                if model or partial:
                    findings.append(
                        Finding(
                            rel,
                            lineno,
                            "JSN001",
                            "no-schema contradicts model=/partial markers",
                        )
                    )
                continue
            if model is None:
                findings.append(
                    Finding(
                        rel,
                        lineno,
                        "JSN001",
                        "json fence is not mapped: declare model=<ModelName> "
                        "or mark it no-schema (knowledge-core.md)",
                    )
                )
                continue
            schema_path = schemas_dir / f"{model}.json"
            if not schema_path.is_file():
                findings.append(
                    Finding(
                        rel,
                        lineno,
                        "JSN002",
                        f"model={model} has no schema in {SCHEMAS_DIR}/",
                    )
                )
                continue
            try:
                schema = json.loads(schema_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                findings.append(
                    Finding(
                        rel,
                        lineno,
                        "JSN002",
                        f"schema for model={model} is unreadable",
                    )
                )
                continue
            try:
                example = json.loads(body)
            except json.JSONDecodeError as error:
                findings.append(
                    Finding(rel, lineno, "JSN003", f"invalid JSON: {error}")
                )
                continue
            defs = schema.get("$defs", {})
            for code, message in _validate_value(example, schema, defs, model, partial):
                findings.append(Finding(rel, lineno, code, message))
    return findings


def check_generated_schemas(root: Path) -> list[Finding]:
    """docs/generated/schemas must byte-match an in-memory regeneration."""
    expected = expected_schemas()
    schemas_dir = root / SCHEMAS_DIR
    hint = "docs/generated is machine-written; run python -m atlas.tools.schemas_export"
    if not schemas_dir.is_dir():
        return [Finding(SCHEMAS_DIR, 1, "GEN001", f"directory is missing; {hint}")]
    findings = []
    for name, content in sorted(expected.items()):
        rel = f"{SCHEMAS_DIR}/{name}.json"
        path = schemas_dir / f"{name}.json"
        if not path.is_file():
            findings.append(Finding(rel, 1, "GEN001", f"schema is missing; {hint}"))
        elif path.read_text(encoding="utf-8") != content:
            findings.append(
                Finding(
                    rel,
                    1,
                    "GEN001",
                    f"schema does not match regeneration (hand-edited or "
                    f"stale); {hint}",
                )
            )
    for path in sorted(schemas_dir.glob("*.json")):
        if path.stem not in expected:
            findings.append(
                Finding(
                    f"{SCHEMAS_DIR}/{path.name}",
                    1,
                    "GEN001",
                    f"file is not a canonical model schema; {hint}",
                )
            )
    return findings


def lint_repo(
    root: Path,
    database: Database | None = None,
    *,
    symphony_milestone: SymphonyMilestoneValidation | None = None,
) -> list[Finding]:
    findings = [
        *check_adrs(root),
        *check_manifest(root),
        *check_legacy_names(root),
        *check_intra_doc_links(root),
        *check_backticked_paths(root),
        *check_planning_renders(root),
        *check_symphony_ceiling_contract(root, milestone=symphony_milestone),
        *check_scoped_validation_handoff_contract(root),
        *check_phase_status(root),
        *check_render_source_anchors(root),
        *check_json_examples(root),
        *check_generated_schemas(root),
    ]
    if database is not None:
        findings.extend(check_store_source_anchors(root, database))
    return sorted(findings, key=lambda f: (f.path, f.line, f.code))


def _current_git_branch(root: Path) -> str:
    result = subprocess.run(
        ["git", "symbolic-ref", "--quiet", "--short", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="doc_linter",
        description="Atlas doc linter (ATLAS-4 v1, ATLAS-16 v2, ATLAS-198 v3)",
    )
    parser.add_argument(
        "--repo", default=".", help="repository root (default: current directory)"
    )
    parser.add_argument(
        "--db",
        default=None,
        help="optional database URL for checking store source_anchor rows",
    )
    parser.add_argument(
        "--symphony-milestone-level",
        type=int,
        choices=SYMPHONY_MILESTONE_LEVELS,
        default=None,
        help=(
            "validate the declared level only when the checkout is the exact "
            "dedicated Phase 15 milestone branch"
        ),
    )
    args = parser.parse_args(argv)
    root = Path(args.repo).resolve()
    database = None
    if args.db is not None:
        from atlas.storage import Database

        database = Database(args.db)
    symphony_milestone = None
    if args.symphony_milestone_level is not None:
        symphony_milestone = SymphonyMilestoneValidation(
            branch=_current_git_branch(root),
            level=args.symphony_milestone_level,
        )
    findings = lint_repo(
        root,
        database=database,
        symphony_milestone=symphony_milestone,
    )
    for finding in findings:
        print(finding.render())
    print(
        f"doc-linter: {len(findings)} finding(s)" if findings else "doc-linter: clean"
    )
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
