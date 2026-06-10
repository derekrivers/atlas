"""Doc linter v1 (ATLAS-4): mechanical validation of the canonical doc set.

Checks, per the implementation roadmap and ADR-0006/0007:

- ADR: every ``docs/decisions/*.md`` matches the ADR model in
  data-model-and-schemas.md §3.2 — title heading ``# ADR-NNNN: <title>``
  with the number matching the filename, a recognised status, and
  non-empty Status / Context / Decision / Rationale / Consequences /
  Alternatives considered sections.
- MANIFEST: every path referenced in docs/MANIFEST.md exists, and every
  canonical doc (docs/atlas/, docs/architecture/, docs/decisions/,
  docs/runbooks/) is listed. Stub directories the MANIFEST declares as
  awaiting content (docs/product/, docs/tech-debt/) are not yet
  required to be listed.
- LEGACY: retired v1/v2/v3 document names are banned outside
  docs/archive/ — ``*-v[123].md`` forms, ``ATLAS_V[123]``, ``_V[123]_``
  infixes, and ``roadmap.html``. Lines that explicitly mark retirement
  (containing "retired") are allowed: the roadmap's "Retired:" lines are
  the documented mechanism for recording retirements, not live use.
- LINK: relative ``.md`` link targets in active docs must resolve to
  existing files. ``#fragment`` validation is deferred to linter v2.
- PLANNING: docs/planning/ files are renders written only by
  ``atlas apply`` (ADR-0007). Per the render format in
  docs/architecture/knowledge-core.md, a render carries a generated
  header recording ``plan_run_id`` and naming ``atlas apply``; any file
  without that header is a hand-edit. Header presence cannot detect an
  edit that preserves the header — content-hash integrity arrives with
  PlanRun ingestion in later phases.

Exit status: 0 when the doc set is clean, 1 when there are findings.
This linter only reports; repairing drift is ATLAS-5.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

MANIFEST_PATH = "docs/MANIFEST.md"
DECISIONS_DIR = "docs/decisions"
PLANNING_DIR = "docs/planning"
ARCHIVE_DIR = "docs/archive"

# Directories whose *.md files must all be listed in the MANIFEST.
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
PATH_SUFFIXES = (".md", ".j2", ".py", ".yaml", ".yml", ".mmd", ".html")

LEGACY_RES = (
    re.compile(r"-v[123]\.md\b", re.IGNORECASE),
    re.compile(r"ATLAS_V[123]", re.IGNORECASE),
    re.compile(r"_V[123]_", re.IGNORECASE),
    re.compile(r"roadmap\.html", re.IGNORECASE),
)
RETIREMENT_RE = re.compile(r"retired", re.IGNORECASE)

MD_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")

SKIP_DIRS = {".git", ".venv", "__pycache__", "node_modules", ".claude"}


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    code: str
    message: str

    def render(self) -> str:
        return f"{self.path}:{self.line}: {self.code} {self.message}"


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
        for path in sorted((root / directory).glob("*.md")):
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


def check_legacy_names(root: Path) -> list[Finding]:
    findings = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = _rel(root, path)
        parts = set(Path(rel).parts)
        if parts & SKIP_DIRS or rel.startswith(f"{ARCHIVE_DIR}/"):
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
            for pattern in LEGACY_RES:
                match = pattern.search(line)
                if match:
                    findings.append(
                        Finding(
                            rel,
                            lineno,
                            "LEG002",
                            f"legacy document name referenced: {match.group(0)}",
                        )
                    )
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


def check_planning_renders(root: Path) -> list[Finding]:
    planning = root / PLANNING_DIR
    if not planning.is_dir():
        return []
    findings = []
    for path in sorted(planning.rglob("*")):
        if not path.is_file() or path.name == ".gitkeep":
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


def lint_repo(root: Path) -> list[Finding]:
    findings = [
        *check_adrs(root),
        *check_manifest(root),
        *check_legacy_names(root),
        *check_intra_doc_links(root),
        *check_planning_renders(root),
    ]
    return sorted(findings, key=lambda f: (f.path, f.line, f.code))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="doc_linter", description="Atlas doc linter v1 (ATLAS-4)"
    )
    parser.add_argument(
        "--repo", default=".", help="repository root (default: current directory)"
    )
    args = parser.parse_args(argv)
    root = Path(args.repo).resolve()
    findings = lint_repo(root)
    for finding in findings:
        print(finding.render())
    print(
        f"doc-linter: {len(findings)} finding(s)" if findings else "doc-linter: clean"
    )
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
