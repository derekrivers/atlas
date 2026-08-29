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
import json
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, StrictUndefined
from jinja2.exceptions import UndefinedError

from atlas.core.models.planner_call_telemetry import (
    PlannerDigestAlgorithm,
    PlannerExecutionParameters,
    PlannerIdentity,
    PlannerInputIdentity,
    PlannerLogicalCall,
    PlannerLogicalCallIdentity,
    PlannerPayloadSize,
    PlannerPromptSegmentSize,
    PlannerPromptTemplateIdentity,
    PlanningExecutionIdentity,
)

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


class StageIdentityError(RendererError):
    """A call stage is absent, malformed, or incompatible with its template."""


@dataclass(frozen=True)
class PromptTemplateArtifact:
    """Exact released template artifact frozen before provider invocation."""

    template_name: str
    prompt_version: str
    template_sha256: str
    declared_stage: str | None

    def identity(self, stage: str) -> PlannerPromptTemplateIdentity:
        _validate_template_stage(stage, self.declared_stage)
        try:
            return PlannerPromptTemplateIdentity(
                stage=stage,
                template_name=self.template_name,
                prompt_version=self.prompt_version,
                template_sha256=self.template_sha256,
            )
        except ValueError as error:
            raise StageIdentityError(f"invalid planner stage {stage!r}") from error


@dataclass(frozen=True)
class RenderedPrompt:
    """A deterministic render plus bounded, content-free call facts."""

    text: str
    prompt_version: str
    prompt_hash: str  # SHA-256 hex of text
    template: PlannerPromptTemplateIdentity
    input_identities: tuple[PlannerInputIdentity, ...]
    prompt_size: PlannerPayloadSize
    prompt_segments: tuple[PlannerPromptSegmentSize, ...]

    def logical_call(
        self,
        *,
        execution: PlanningExecutionIdentity,
        planner: PlannerIdentity,
        execution_parameters: PlannerExecutionParameters,
        logical_attempt_no: int,
    ) -> PlannerLogicalCall:
        """Build the immutable logical-call record before a provider call."""

        return PlannerLogicalCall(
            identity=PlannerLogicalCallIdentity(
                execution=execution,
                stage=self.template.stage,
                logical_attempt_no=logical_attempt_no,
            ),
            planner=planner,
            template=self.template,
            execution_parameters=execution_parameters,
            input_identities=self.input_identities,
            prompt_size=self.prompt_size,
            prompt_segments=self.prompt_segments,
        )


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


def _load_template(
    version: str, directory: Path
) -> tuple[dict[str, Any], str, str, Path]:
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
    return meta, raw[match.end() :], raw, path


def _validate_front_matter(
    meta: dict[str, Any], version: str
) -> tuple[list[str], str | None]:
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
    declared_stage = meta.get("stage")
    if declared_stage is not None and not isinstance(declared_stage, str):
        raise FrontMatterError("front matter stage must be a string when present")
    return [str(name) for name in declared], declared_stage


def _validate_template_stage(stage: str, declared_stage: str | None) -> None:
    if declared_stage is None:
        if stage != "single":
            raise StageIdentityError(
                f"single-call template cannot identify stage {stage!r}"
            )
        return
    if stage != declared_stage and not stage.startswith(f"{declared_stage}."):
        raise StageIdentityError(
            f"template stage {declared_stage!r} cannot identify call stage {stage!r}"
        )


def resolve_prompt_template_artifact(
    *,
    version: str | None = None,
    prompts_dir: Path | None = None,
    stage: str = "single",
) -> PromptTemplateArtifact:
    """Resolve and validate one exact template without rendering or calling."""

    directory = prompts_dir or PROMPTS_DIR
    resolved = version or current_release(directory)
    meta, _body, raw, path = _load_template(resolved, directory)
    _declared, declared_stage = _validate_front_matter(meta, resolved)
    artifact = PromptTemplateArtifact(
        template_name=path.name,
        prompt_version=resolved,
        template_sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        declared_stage=declared_stage,
    )
    artifact.identity(stage)  # fail closed on unknown/malformed stage
    return artifact


def _normalise_input(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return _normalise_input(asdict(value))
    if isinstance(value, Mapping):
        return {
            str(key): _normalise_input(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_normalise_input(item) for item in value]
    if isinstance(value, Path):
        return value.as_posix()
    return value


def _canonical_input_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(
        _normalise_input(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def _measure(value: str) -> PlannerPayloadSize:
    return PlannerPayloadSize(
        byte_count=len(value.encode("utf-8")),
        character_count=len(value),
    )


def _measure_input(value: object) -> PlannerPayloadSize:
    """Measure raw values the template actually reads, excluding structure."""

    if is_dataclass(value) and not isinstance(value, type):
        return _measure_input(asdict(value))
    if isinstance(value, Mapping):
        measured = [_measure_input(item) for item in value.values()]
    elif isinstance(value, (list, tuple)):
        measured = [_measure_input(item) for item in value]
    elif value is None:
        measured = []
    else:
        return _measure(_canonical_input_text(value))
    return PlannerPayloadSize(
        byte_count=sum(item.byte_count for item in measured),
        character_count=sum(item.character_count for item in measured),
    )


def _prompt_facts(
    variables: Mapping[str, object], text: str
) -> tuple[
    tuple[PlannerInputIdentity, ...],
    PlannerPayloadSize,
    tuple[PlannerPromptSegmentSize, ...],
]:
    """Measure actual render inputs, retaining only sizes and SHA-256 facts."""

    documents = variables.get("documents", ())
    anchors = variables.get("valid_anchors", ())
    backlog = variables.get("current_backlog_yaml")
    schema = variables.get("proposal_json_schema", variables.get("stage_output_schema"))
    reserved = {
        "documents",
        "valid_anchors",
        "current_backlog_yaml",
        "proposal_json_schema",
        "stage_output_schema",
    }
    dynamic_stage = _canonical_input_text(
        {name: value for name, value in variables.items() if name not in reserved}
    )
    dynamic_values = {
        name: value for name, value in variables.items() if name not in reserved
    }
    segment_inputs = {
        "documents": documents,
        "anchors": anchors,
        "backlog": backlog,
        "schema": schema,
        "dynamic_stage": dynamic_values,
    }
    segments = tuple(
        PlannerPromptSegmentSize(name=name, **_measure_input(value).model_dump())
        for name, value in segment_inputs.items()
    )
    input_text = {
        "rendered_prompt": text,
        "documents": _canonical_input_text(documents),
        "anchors": _canonical_input_text(anchors),
        "backlog": _canonical_input_text(backlog),
        "schema": _canonical_input_text(schema),
        "dynamic_stage": dynamic_stage,
    }
    identities = tuple(
        PlannerInputIdentity(
            name=name,
            algorithm=PlannerDigestAlgorithm.SHA256,
            digest=hashlib.sha256(value.encode("utf-8")).hexdigest(),
        )
        for name, value in input_text.items()
    )
    return identities, _measure(text), segments


def render_planner_prompt(
    variables: Mapping[str, object],
    *,
    version: str | None = None,
    prompts_dir: Path | None = None,
    stage: str = "single",
) -> RenderedPrompt:
    """Render a released planner template; every failure is typed.

    ``variables`` must supply exactly the template's declared
    variables — missing and undeclared names both fail before
    rendering (fail closed in both directions).
    """
    directory = prompts_dir or PROMPTS_DIR
    resolved = version or current_release(directory)
    meta, body, raw, path = _load_template(resolved, directory)
    declared, declared_stage = _validate_front_matter(meta, resolved)
    artifact = PromptTemplateArtifact(
        template_name=path.name,
        prompt_version=resolved,
        template_sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        declared_stage=declared_stage,
    )
    template = artifact.identity(stage)

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
    input_identities, prompt_size, prompt_segments = _prompt_facts(variables, text)
    return RenderedPrompt(
        text=text,
        prompt_version=resolved,
        prompt_hash=prompt_hash,
        template=template,
        input_identities=input_identities,
        prompt_size=prompt_size,
        prompt_segments=prompt_segments,
    )
