"""Doc linter (v1: ATLAS-4; v2: ATLAS-16): mechanical validation of the
canonical doc set.

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

Exit status: 0 when the doc set is clean, 1 when there are findings.
This linter only reports; repairing drift is ATLAS-5.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from atlas.tools.schemas_export import SCHEMAS_DIR, expected_schemas

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


def lint_repo(root: Path) -> list[Finding]:
    findings = [
        *check_adrs(root),
        *check_manifest(root),
        *check_legacy_names(root),
        *check_intra_doc_links(root),
        *check_planning_renders(root),
        *check_json_examples(root),
        *check_generated_schemas(root),
    ]
    return sorted(findings, key=lambda f: (f.path, f.line, f.code))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="doc_linter", description="Atlas doc linter (ATLAS-4 v1, ATLAS-16 v2)"
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
