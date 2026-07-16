"""ATLAS-103: playbook generation from ACTIVE lessons under a tag."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from test_doc_linter import build_good_repo
from test_lesson_model import lesson_kwargs

from atlas.cli import EXIT_OK, EXIT_PRECONDITION, build_parser, main
from atlas.core.models import Lesson
from atlas.learning import (
    NoActiveLessonsForTagError,
    active_lessons_for_tag,
    generate_playbook_for_tag,
)
from atlas.storage import Database, LessonRepo
from atlas.tools.doc_linter import lint_repo

NOW = datetime(2026, 7, 16, 9, 30, 0, tzinfo=UTC)


class FakePlaybookClient:
    def __init__(
        self, output: str = "## Operating rules\n\nKeep the loop tight."
    ) -> None:
        self.output = output
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.output


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{tmp_path}/atlas.db")
    database.create_all()
    return database


def make_lesson(**overrides: Any) -> Lesson:
    return Lesson(
        **lesson_kwargs()
        | {
            "id": uuid4(),
            "status": "active",
            "confidence": 0.8,
            "title": "Active handoff lesson",
            "tags": ["handoff"],
            "created_at": NOW,
            "updated_at": NOW,
        }
        | overrides
    )


def seed_lessons(db: Database, lessons: list[Lesson]) -> None:
    repo = LessonRepo(db)
    for lesson in lessons:
        repo.add(lesson)


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def initialise_git_repo(repo: Path) -> None:
    git(repo, "init")
    git(repo, "add", ".")
    git(
        repo,
        "-c",
        "user.email=atlas@example.test",
        "-c",
        "user.name=Atlas Test",
        "commit",
        "-m",
        "initial docs",
    )


def test_playbook_generator_selects_only_active_lessons_for_tag() -> None:
    old = make_lesson(
        title="Oldest active lesson",
        tags=["handoff"],
        created_at=NOW - timedelta(days=2),
    )
    newest = make_lesson(title="Newest active lesson", tags=["HANDOFF"])
    draft = make_lesson(
        status="draft",
        confidence=None,
        title="Draft handoff lesson",
        tags=["handoff"],
    )
    archived = make_lesson(
        status="archived",
        title="Archived handoff lesson",
        tags=["handoff"],
    )
    wrong_tag = make_lesson(title="Wrong tag lesson", tags=["planning"])

    selected = active_lessons_for_tag(
        [newest, draft, wrong_tag, old, archived], "handoff"
    )

    assert [lesson.title for lesson in selected] == [
        "Oldest active lesson",
        "Newest active lesson",
    ]


def test_playbook_generation_raises_typed_error_without_active_lessons() -> None:
    draft = make_lesson(status="draft", confidence=None, tags=["handoff"])

    with pytest.raises(NoActiveLessonsForTagError, match="no ACTIVE lessons"):
        generate_playbook_for_tag([draft], "handoff", client=FakePlaybookClient())


def test_playbook_provenance_lists_source_lesson_ids_and_titles() -> None:
    lesson = make_lesson(title="Review branch discipline", tags=["handoff"])

    generated = generate_playbook_for_tag(
        [lesson],
        "handoff",
        client=FakePlaybookClient("## Review checklist\n\n- Check the branch."),
    )

    assert generated.source_lessons == [lesson]
    assert f"- `{lesson.id}` - Review branch discipline" in generated.markdown
    assert "## Provenance" in generated.markdown


def test_playbook_prompt_contains_only_contributing_active_lessons() -> None:
    active = make_lesson(title="Prompt-visible active lesson", tags=["handoff"])
    draft = make_lesson(
        status="draft",
        confidence=None,
        title="Prompt-hidden draft lesson",
        tags=["handoff"],
    )
    client = FakePlaybookClient()

    generate_playbook_for_tag([draft, active], "handoff", client=client)

    assert len(client.prompts) == 1
    assert "Prompt-visible active lesson" in client.prompts[0]
    assert str(active.id) in client.prompts[0]
    assert "Prompt-hidden draft lesson" not in client.prompts[0]


def test_cli_lessons_playbook_writes_markdown_on_new_branch_and_lints(
    tmp_path: Path,
    db: Database,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    build_good_repo(repo)
    initialise_git_repo(repo)
    lesson = make_lesson(title="Use small PR branches", tags=["handoff"])
    seed_lessons(db, [lesson])

    code = main(
        ["lessons", "playbook", "handoff", "--repo", str(repo)],
        database=db,
        client=FakePlaybookClient("## Operating rules\n\n- Keep PRs reviewable."),
    )
    output = capsys.readouterr().out

    assert code == EXIT_OK
    branch = git(repo, "branch", "--show-current")
    assert branch.startswith("playbook/handoff-")
    path = repo / "docs" / "atlas" / "playbooks" / "handoff.md"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "# handoff Playbook" in text
    assert str(lesson.id) in text
    assert "Use small PR branches" in text
    assert "branch " + branch in output
    assert "operator review and merge" in output
    assert lint_repo(repo) == []


def test_cli_lessons_playbook_cleanly_fails_without_active_lessons(
    tmp_path: Path,
    db: Database,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    build_good_repo(repo)
    initialise_git_repo(repo)
    seed_lessons(db, [make_lesson(status="draft", confidence=None, tags=["handoff"])])
    client = FakePlaybookClient()

    code = main(
        ["lessons", "playbook", "handoff", "--repo", str(repo)],
        database=db,
        client=client,
    )
    err = capsys.readouterr().err

    assert code == EXIT_PRECONDITION
    assert "no ACTIVE lessons exist for tag 'handoff'" in err
    assert not git(repo, "branch", "--show-current").startswith("playbook/handoff-")
    assert client.prompts == []
    assert not (repo / "docs" / "atlas" / "playbooks" / "handoff.md").exists()


def test_lessons_playbook_parser_has_help_text_flags() -> None:
    args = build_parser().parse_args(
        ["lessons", "playbook", "handoff", "--repo", "/tmp/repo"]
    )

    assert args.command == "lessons"
    assert args.lessons_command == "playbook"
    assert args.tag == "handoff"
    assert args.repo == "/tmp/repo"
