"""Versioned planner prompt renderer (ATLAS-22).

Implements spec §2.1 step 3: load a released template by version,
validate front matter and the presence of every declared variable
BEFORE rendering, render under Jinja2 StrictUndefined, and return
deterministic text with its prompt_hash — the middle link of the
provenance chain input_doc_shas → prompt_hash → raw_output_hash
(data-model §3.10).

The current release is declared explicitly in ``prompts/CURRENT``
(gate-approved mechanism); the renderer never infers it from the
directory listing. ``proposal_json_schema`` is a caller-supplied
variable (D2 seam): generation belongs to the Proposal models
(ATLAS-23). Calling the model and persisting PlanRun rows are
``atlas plan``'s job (ATLAS-26/28), not this module's.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, StrictUndefined
from jinja2.exceptions import UndefinedError

PROMPTS_DIR = Path(__file__).parent / "prompts"
CURRENT_POINTER = "CURRENT"

_VERSION_RE = re.compile(r"^planner-v\d+\.\d+\.\d+$")
_FRONT_MATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.S)


class RendererError(ValueError):
    """Base for renderer failures; always typed, never a fallback."""


class CurrentReleaseError(RendererError):
    """The CURRENT pointer is missing, empty, or malformed."""


class UnknownTemplateVersionError(RendererError):
    """A requested or pointed-at version has no template file."""


class FrontMatterError(RendererError):
    """A template's front matter is absent, unparseable, or invalid."""


class MissingVariableError(RendererError):
    """Declared template variables were not supplied (README rendering
    contract: presence is validated before rendering — a missing
    frozen_ticket_keys fails here, never renders an empty section)."""

    def __init__(self, version: str, names: list[str]) -> None:
        super().__init__(
            f"missing template variable(s) {names} declared by {version}; "
            "presence is validated before rendering"
        )
        self.names = names


class UndeclaredVariableError(RendererError):
    """Variables were supplied that the front matter does not declare."""

    def __init__(self, version: str, names: list[str]) -> None:
        super().__init__(
            f"undeclared variable(s) {names} supplied to {version}; the "
            "front matter is the contract — declare them or drop them"
        )
        self.names = names


class RenderError(RendererError):
    """StrictUndefined rendering failure, naming the offending variable."""


@dataclass(frozen=True)
class RenderedPrompt:
    """A deterministic render and its provenance hash."""

    text: str
    prompt_version: str
    prompt_hash: str  # SHA-256 hex of text


def current_release(prompts_dir: Path | None = None) -> str:
    """The explicitly declared current release (prompts/CURRENT)."""
    directory = prompts_dir or PROMPTS_DIR
    pointer = directory / CURRENT_POINTER
    if not pointer.is_file():
        raise CurrentReleaseError(
            f"{pointer} is missing; the current release is declared "
            "explicitly, never inferred from the directory (prompts README)"
        )
    content = pointer.read_text(encoding="utf-8").strip()
    if not _VERSION_RE.match(content):
        raise CurrentReleaseError(
            f"CURRENT contains {content!r}, not planner-vMAJOR.MINOR.PATCH"
        )
    return content


def _load_template(version: str, directory: Path) -> tuple[dict[str, Any], str]:
    path = directory / f"{version}.md.j2"
    if not path.is_file():
        raise UnknownTemplateVersionError(
            f"no template file for {version!r} in {directory}"
        )
    raw = path.read_text(encoding="utf-8")
    match = _FRONT_MATTER_RE.match(raw)
    if not match:
        raise FrontMatterError(f"{path.name} has no YAML front matter")
    try:
        meta = yaml.safe_load(match.group(1))
    except yaml.YAMLError as error:
        raise FrontMatterError(
            f"{path.name} front matter is not valid YAML: {error}"
        ) from error
    if not isinstance(meta, dict):
        raise FrontMatterError(f"{path.name} front matter is not a mapping")
    return meta, raw[match.end() :]


def _validate_front_matter(meta: dict[str, Any], version: str) -> list[str]:
    declared_version = meta.get("prompt_version")
    if declared_version != version:
        raise FrontMatterError(
            f"front matter declares {declared_version!r} but the file is "
            f"{version!r}; filename and front matter must agree"
        )
    engine = meta.get("template_engine")
    if engine != "jinja2":
        raise FrontMatterError(
            f"unknown template_engine {engine!r}; this renderer is jinja2"
        )
    declared = meta.get("template_variables")
    if not isinstance(declared, list) or not declared:
        raise FrontMatterError("front matter is missing its template_variables list")
    return [str(name) for name in declared]


def render_planner_prompt(
    variables: Mapping[str, object],
    *,
    version: str | None = None,
    prompts_dir: Path | None = None,
) -> RenderedPrompt:
    """Render a released planner template; every failure is typed.

    ``variables`` must supply exactly the template's declared
    variables — missing and undeclared names both fail before
    rendering (fail closed in both directions).
    """
    directory = prompts_dir or PROMPTS_DIR
    resolved = version or current_release(directory)
    meta, body = _load_template(resolved, directory)
    declared = _validate_front_matter(meta, resolved)

    missing = sorted(set(declared) - set(variables))
    if missing:
        raise MissingVariableError(resolved, missing)
    extra = sorted(set(variables) - set(declared))
    if extra:
        raise UndeclaredVariableError(resolved, extra)

    environment = Environment(undefined=StrictUndefined)
    try:
        text = environment.from_string(body).render(dict(variables))
    except UndefinedError as error:
        raise RenderError(
            f"{resolved} referenced an undefined variable: {error.message}"
        ) from error
    prompt_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return RenderedPrompt(text=text, prompt_version=resolved, prompt_hash=prompt_hash)
