"""Playbook generation from ACTIVE lessons.

The playbook flow is the docs-promotion side of the Learning System: gather the
operator-promoted lessons for one tag, ask the model to draft a Markdown body,
then place the resulting canonical-doc candidate on a review branch. It never
commits; the operator reviews, commits, pushes, and opens the PR.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

import yaml
from jinja2 import Environment, StrictUndefined
from jinja2.exceptions import UndefinedError

from atlas.core.enums import EntityStatus
from atlas.core.models.lesson import Lesson
from atlas.storage.repositories import LessonRepo

PROMPTS_DIR = Path(__file__).parent / "prompts"
PLAYBOOK_PROMPT_VERSION = "lesson-playbook-v1.0.0"
PLAYBOOKS_DIR = Path("docs") / "atlas" / "playbooks"

_VERSION_RE = re.compile(r"^lesson-playbook-v\d+\.\d+\.\d+$")
_FRONT_MATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.S)
_TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class PlaybookGenerationError(RuntimeError):
    """Base class for clean playbook-generation failures."""


class InvalidPlaybookTagError(PlaybookGenerationError):
    """The requested tag cannot safely name a branch and Markdown file."""


class NoActiveLessonsForTagError(PlaybookGenerationError):
    """No ACTIVE lessons exist for the requested tag."""


class PlaybookGitError(PlaybookGenerationError):
    """Git could not create the review branch."""


@runtime_checkable
class PlaybookModelClient(Protocol):
    """The model-call seam: one prompt in, one Markdown response out."""

    def generate(self, prompt: str) -> str: ...


@dataclass(frozen=True)
class RenderedPlaybookPrompt:
    text: str
    prompt_version: str
    prompt_hash: str


@dataclass(frozen=True)
class GeneratedPlaybook:
    tag: str
    markdown: str
    source_lessons: list[Lesson]
    prompt: RenderedPlaybookPrompt


@dataclass(frozen=True)
class PlaybookDraftResult:
    tag: str
    branch_name: str
    path: Path
    source_lesson_ids: list[UUID]


def validate_playbook_tag(tag: str) -> str:
    """Return a safe tag slug or raise a typed error.

    Tags become both a git branch path component and a Markdown filename, so the
    command accepts only a conservative slug shape instead of attempting to
    interpret path separators or shell-ish characters.
    """

    cleaned = tag.strip()
    if not _TAG_RE.fullmatch(cleaned):
        raise InvalidPlaybookTagError(
            f"playbook tag must match [A-Za-z0-9][A-Za-z0-9._-]*; got {tag!r}"
        )
    return cleaned


def active_lessons_for_tag(lessons: Sequence[Lesson], tag: str) -> list[Lesson]:
    """Return all ACTIVE lessons carrying ``tag`` exactly, case-insensitively."""

    needle = tag.casefold()
    matched = [
        lesson
        for lesson in lessons
        if lesson.status is EntityStatus.ACTIVE
        and any(candidate.casefold() == needle for candidate in lesson.tags)
    ]
    return sorted(
        matched,
        key=lambda lesson: (lesson.created_at, lesson.title.casefold(), str(lesson.id)),
    )


def _load_template(version: str, directory: Path) -> tuple[list[str], str]:
    if not _VERSION_RE.match(version):
        raise PlaybookGenerationError(f"invalid playbook prompt version {version!r}")
    path = directory / f"{version}.md.j2"
    if not path.is_file():
        raise PlaybookGenerationError(f"no playbook template for {version!r}")
    raw = path.read_text(encoding="utf-8")
    match = _FRONT_MATTER_RE.match(raw)
    if not match:
        raise PlaybookGenerationError(f"{path.name} has no YAML front matter")
    try:
        meta = yaml.safe_load(match.group(1))
    except yaml.YAMLError as error:
        raise PlaybookGenerationError(f"{path.name} front matter is invalid") from error
    if not isinstance(meta, dict):
        raise PlaybookGenerationError(f"{path.name} front matter is not a mapping")
    if meta.get("prompt_version") != version:
        raise PlaybookGenerationError(f"{path.name} front matter version mismatch")
    if meta.get("template_engine") != "jinja2":
        raise PlaybookGenerationError(f"{path.name} does not declare jinja2")
    variables = meta.get("template_variables")
    if not isinstance(variables, list) or not variables:
        raise PlaybookGenerationError(f"{path.name} has no template_variables list")
    return [str(name) for name in variables], raw[match.end() :]


def render_playbook_prompt(
    variables: Mapping[str, object],
    *,
    version: str = PLAYBOOK_PROMPT_VERSION,
    prompts_dir: Path | None = None,
) -> RenderedPlaybookPrompt:
    """Render the versioned playbook prompt with strict variables."""

    directory = prompts_dir or PROMPTS_DIR
    declared, body = _load_template(version, directory)
    missing = sorted(set(declared) - set(variables))
    extra = sorted(set(variables) - set(declared))
    if missing:
        raise PlaybookGenerationError(f"missing playbook prompt variables: {missing}")
    if extra:
        raise PlaybookGenerationError(f"undeclared playbook prompt variables: {extra}")
    try:
        text = (
            Environment(undefined=StrictUndefined)
            .from_string(body)
            .render(dict(variables))
        )
    except UndefinedError as error:
        raise PlaybookGenerationError(
            f"{version} referenced an undefined variable: {error.message}"
        ) from error
    return RenderedPlaybookPrompt(
        text=text,
        prompt_version=version,
        prompt_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def _lesson_payload(lesson: Lesson) -> dict[str, Any]:
    payload = lesson.model_dump(mode="json")
    return {
        "id": payload["id"],
        "title": payload["title"],
        "category": payload["category"],
        "problem": payload["problem"],
        "solution": payload["solution"],
        "outcome": payload["outcome"],
        "confidence": payload["confidence"],
        "tags": payload["tags"],
        "related_ticket_ids": payload["related_ticket_ids"],
        "related_adr_ids": payload["related_adr_ids"],
        "created_at": payload["created_at"],
        "updated_at": payload["updated_at"],
    }


def _strip_surrounding_markdown_fence(raw: str) -> str:
    text = raw.strip()
    lines = text.splitlines()
    if len(lines) >= 2 and lines[0].startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return text


def _one_line(value: str) -> str:
    return " ".join(value.split())


def build_playbook_markdown(
    *,
    tag: str,
    body_markdown: str,
    source_lessons: Sequence[Lesson],
) -> str:
    body = _strip_surrounding_markdown_fence(body_markdown)
    if not body:
        raise PlaybookGenerationError("playbook model output was empty")

    lines = [
        f"# {tag} Playbook",
        "",
        body,
        "",
        "## Provenance",
        "",
        f"Generated from ACTIVE lessons tagged `{tag}`.",
        "",
    ]
    lines.extend(
        f"- `{lesson.id}` - {_one_line(lesson.title)}" for lesson in source_lessons
    )
    return "\n".join(lines).rstrip() + "\n"


def generate_playbook_for_tag(
    lessons: Sequence[Lesson],
    tag: str,
    *,
    client: PlaybookModelClient,
) -> GeneratedPlaybook:
    """Generate a Markdown playbook from ACTIVE lessons under ``tag``."""

    safe_tag = validate_playbook_tag(tag)
    source_lessons = active_lessons_for_tag(lessons, safe_tag)
    if not source_lessons:
        raise NoActiveLessonsForTagError(
            f"no ACTIVE lessons exist for tag {safe_tag!r}"
        )
    prompt = render_playbook_prompt(
        {
            "tag": safe_tag,
            "lessons_json": json.dumps(
                [_lesson_payload(lesson) for lesson in source_lessons],
                indent=2,
                sort_keys=True,
            ),
        }
    )
    try:
        body_markdown = client.generate(prompt.text)
    except Exception as error:
        raise PlaybookGenerationError(f"playbook model call failed: {error}") from error
    return GeneratedPlaybook(
        tag=safe_tag,
        markdown=build_playbook_markdown(
            tag=safe_tag,
            body_markdown=body_markdown,
            source_lessons=source_lessons,
        ),
        source_lessons=source_lessons,
        prompt=prompt,
    )


def _run_git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(repo_root), *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as error:
        raise PlaybookGitError("git executable not found") from error
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or error.stdout or str(error)).strip()
        command = " ".join(["git", *args])
        raise PlaybookGitError(f"{command} failed: {detail}") from error


def _git_toplevel(repo_root: Path) -> Path:
    result = _run_git(repo_root, "rev-parse", "--show-toplevel")
    return Path(result.stdout.strip())


def _ensure_clean_worktree(git_root: Path) -> None:
    status = _run_git(git_root, "status", "--porcelain").stdout.strip()
    if status:
        raise PlaybookGitError("git worktree must be clean before drafting a playbook")


def _branch_name(tag: str, now: datetime) -> str:
    timestamp = now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"playbook/{tag}-{timestamp}"


def draft_playbook_branch(
    lesson_repo: LessonRepo,
    tag: str,
    *,
    client: PlaybookModelClient,
    repo_root: Path,
    now: datetime,
) -> PlaybookDraftResult:
    """Generate a playbook, create its review branch, and write the Markdown file."""

    generated = generate_playbook_for_tag(lesson_repo.list(), tag, client=client)
    git_root = _git_toplevel(repo_root)
    _ensure_clean_worktree(git_root)
    branch_name = _branch_name(generated.tag, now)
    _run_git(git_root, "switch", "-c", branch_name)

    relative_path = PLAYBOOKS_DIR / f"{generated.tag}.md"
    target = git_root / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(generated.markdown, encoding="utf-8")
    return PlaybookDraftResult(
        tag=generated.tag,
        branch_name=branch_name,
        path=relative_path,
        source_lesson_ids=[lesson.id for lesson in generated.source_lessons],
    )
