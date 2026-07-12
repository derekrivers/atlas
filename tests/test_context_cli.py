"""ATLAS-58: the `atlas context` CLI surface (render / validate / show).

Drives ``main(["context", ...], database=db)`` against an in-memory SQLite
database seeded with a ticket (plus an ACTIVE and a DRAFT lesson) and a REAL,
committed git fixture repo whose corpus carries the ticket's ``source_anchor``
heading. The commands are thin wrappers over the pure ``atlas.context``
builder/validator and the shared loader, so these tests prove the WIRING — the
loader, the three presentations, the clean-error exits, and the Phase-5
milestone — not the builder/validator computations (covered by test_pack /
test_context_validation).

Falsifiable, with the wrong answer named:

- render prints the section markdown; ``--json`` parses to a pack dict;
- a tiny ``--budget`` exits non-zero with a NAMED over-budget message, not a
  traceback;
- validate exits non-zero on a broken-fixture ticket (and names the failure),
  zero on a good one;
- show prints a SUMMARY (counts/estimate), not the raw ``rendered_markdown``;
- an unknown KEY is a clean precondition exit (no traceback, empty stdout);
- the MILESTONE: required sections present, ACTIVE-only lessons (DRAFT absent),
  a token estimate, and a doc edit after rendering is DETECTABLE — re-render
  after mutating+recommitting a corpus doc and assert its ``input_doc_shas``
  entry changed (real staleness, not a field-presence check).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from test_ingestion import git, make_repo
from test_lesson_model import lesson_kwargs
from test_models_validation import ticket_kwargs

from atlas.cli import EXIT_OK, EXIT_PRECONDITION, main
from atlas.core.models import Lesson, Ticket
from atlas.storage import Database, LessonRepo, TicketRepo

# The anchor doc: a "## Target Section" heading (slug "target-section") with a
# distinctive body phrase, and a following same-level heading that ends it.
_BODY_PHRASE = "The renderer assembles the minimum high-value context."
CONTEXT_SPEC = "\n".join(
    [
        "# Context Spec",
        "",
        "## Target Section",
        "",
        _BODY_PHRASE,
        "",
        "## Next Section",
        "",
        "next body",
        "",
    ]
)

ANCHOR_PATH = "docs/atlas/context-spec.md"
ANCHOR = f"{ANCHOR_PATH}#target-section"

# A retired inbox stub at its durable processed/ address (ATLAS-162): the pack
# path resolves a stub-minted ticket's anchor here, exactly as gate 4 does.
_STUB_PHRASE = "What gate 4 can resolve, a pack can cite."
PROCESSED_STUB_PATH = "docs/planning/inbox/processed/inbox-stub-fixture.md"
PROCESSED_STUB = "\n".join(
    [
        "# Stub Fixture",
        "",
        "## Packs See Processed",
        "",
        _STUB_PHRASE,
        "",
    ]
)
STUB_ANCHOR = f"{PROCESSED_STUB_PATH}#packs-see-processed"


def corpus_files(spec: str = CONTEXT_SPEC) -> dict[str, str]:
    """The committed §2.1 input set the loader re-ingests from HEAD: the four
    root docs plus the anchor doc the ticket points into."""
    return {
        "PRODUCT.md": "# Product\n\n## Vision\n",
        "ARCHITECTURE.md": "# Architecture\n",
        "ROADMAP.md": "# Roadmap\n\n## Phase 5\n",
        "WORKFLOW.md": "# Workflow\n",
        ANCHOR_PATH: spec,
    }


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return make_repo(tmp_path, corpus_files())


@pytest.fixture
def repo_with_processed(tmp_path: Path) -> Path:
    """The corpus plus one committed retired stub under inbox/processed/."""
    return make_repo(tmp_path, corpus_files() | {PROCESSED_STUB_PATH: PROCESSED_STUB})


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{tmp_path}/atlas.db")
    database.create_all()
    return database


def make_ticket(key: str = "ATLAS-58", **overrides: Any) -> Ticket:
    """A fully-populated, anchor-resolving ticket: every required section is
    sourced (objective, acceptance criteria, test commands, non-goals, DoD), and
    the tag matches the seeded lessons."""
    base = {
        "id": uuid4(),
        "key": key,
        "title": "Context CLI",
        "objective": "Render the context pack for a ticket.",
        "context": "Phase 5 execution context.",
        "status": "planned",
        "ticket_type": "feature",
        "risk_level": "high",
        "source_anchor": ANCHOR,
        "acceptance_criteria": ["A pack is produced.", "Only ACTIVE lessons appear."],
        "non_goals": ["Persistence is out of scope."],
        "test_requirements": ["uv run pytest tests/test_context_cli.py"],
        "definition_of_done": ["Gate suite green."],
        "tags": ["context"],
    }
    return Ticket(**ticket_kwargs() | base | overrides)


def make_lesson(title: str, status: str, **overrides: Any) -> Lesson:
    return Lesson(
        **lesson_kwargs()
        | {"id": uuid4(), "title": title, "status": status, "tags": ["context"]}
        | overrides
    )


def seed_full(db: Database, ticket: Ticket | None = None) -> Ticket:
    """Seed the ticket plus a tag-matching ACTIVE lesson and a DRAFT lesson (the
    DRAFT must never reach the pack — ADR-0009)."""
    ticket = ticket or make_ticket()
    TicketRepo(db).add(ticket)
    LessonRepo(db).add(make_lesson("ACTIVE_LESSON_MARKER", "active"))
    LessonRepo(db).add(make_lesson("DRAFT_LESSON_MARKER", "draft"))
    return ticket


# --- render ----------------------------------------------------------------


def test_render_prints_markdown_and_json_is_a_pack(
    repo: Path, db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_full(db)

    code = main(["context", "render", "ATLAS-58", "--repo", str(repo)], database=db)
    out = capsys.readouterr().out

    assert code == EXIT_OK
    assert "## Objective" in out
    assert "## Acceptance Criteria" in out

    code = main(
        ["context", "render", "ATLAS-58", "--repo", str(repo), "--json"], database=db
    )
    payload = json.loads(capsys.readouterr().out)

    assert code == EXIT_OK
    # The wrong answer: --json prints the markdown blob. It must be the pack.
    assert "rendered_markdown" in payload
    assert "compression_applied" in payload
    assert "## Objective" in payload["rendered_markdown"]


def test_render_tiny_budget_is_a_clean_over_budget_error(
    repo: Path, db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_full(db)

    code = main(
        ["context", "render", "ATLAS-58", "--repo", str(repo), "--budget", "1"],
        database=db,
    )
    captured = capsys.readouterr()

    # The wrong answer: a traceback, or a silently-truncated pack on stdout. It
    # must be a clean precondition exit naming the ticket and the over-budget.
    assert code == EXIT_PRECONDITION
    assert captured.out == ""
    assert "ATLAS-58" in captured.err
    assert "over budget" in captured.err.lower()
    assert "Traceback" not in captured.err


# --- validate --------------------------------------------------------------


def test_validate_good_ticket_exits_zero(
    repo: Path, db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_full(db)

    code = main(["context", "validate", "ATLAS-58", "--repo", str(repo)], database=db)
    out = capsys.readouterr().out

    assert code == EXIT_OK
    assert "valid" in out.lower()
    # The slug-level check ran (the CLI has the ticket).
    assert "slug" in out


def test_validate_broken_ticket_exits_non_zero_and_names_failure(
    repo: Path, db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    # A ticket with no test commands builds a pack, but fails the validator's
    # ">=1 test command" check.
    seed_full(db, make_ticket("ATLAS-59", test_requirements=[]))

    code = main(["context", "validate", "ATLAS-59", "--repo", str(repo)], database=db)
    out = capsys.readouterr().out

    assert code == EXIT_PRECONDITION
    # The wrong answer: a silent pass, or a verdict that does not name the cause.
    assert "test_commands" in out
    assert "INVALID" in out


# --- show ------------------------------------------------------------------


def test_show_prints_summary_not_raw_markdown(
    repo: Path, db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_full(db)

    code = main(["context", "show", "ATLAS-58", "--repo", str(repo)], database=db)
    out = capsys.readouterr().out

    assert code == EXIT_OK
    # The summary carries counts + estimate...
    assert "Token estimate:" in out
    assert "Acceptance criteria: 2" in out
    assert "Lessons: 1" in out
    # ...but NOT the raw rendered_markdown body (the wrong answer is dumping it).
    assert "## Objective" not in out
    assert _BODY_PHRASE not in out
    # The wrong answer: a ``## `` heading inside the verbatim doc-section body is
    # listed as a pack section. Only canonical section titles appear.
    assert "Target Section" not in out


# --- ticket-not-found ------------------------------------------------------


def test_unknown_key_is_a_clean_error(
    repo: Path, db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["context", "render", "ATLAS-404", "--repo", str(repo)], database=db)
    captured = capsys.readouterr()

    # The wrong answer: a None-dereference traceback. It must be a clean exit.
    assert code == EXIT_PRECONDITION
    assert "ATLAS-404" in captured.err
    assert captured.out == ""
    assert "Traceback" not in captured.err


# --- Phase-5 milestone -----------------------------------------------------


def test_milestone_sections_active_lessons_estimate_and_staleness(
    repo: Path, db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_full(db)

    code = main(
        ["context", "render", "ATLAS-58", "--repo", str(repo), "--json"], database=db
    )
    pack = json.loads(capsys.readouterr().out)
    assert code == EXIT_OK

    # 1. Every required section is present, in order.
    md = pack["rendered_markdown"]
    required = [
        "## Objective",
        "## Acceptance Criteria",
        "## Non-goals",
        "## Relevant Docs",
        "## Lessons",
        "## Risks",
        "## Test Commands",
        "## Definition of Done",
    ]
    positions = [md.find(header) for header in required]
    assert all(p != -1 for p in positions), positions
    assert positions == sorted(positions)

    # 2. ACTIVE lessons only — the DRAFT fixture lesson is absent.
    assert "ACTIVE_LESSON_MARKER" in md
    assert "DRAFT_LESSON_MARKER" not in md

    # 3. A token estimate is carried.
    assert isinstance(pack["token_estimate"], int)
    assert pack["token_estimate"] > 0

    # 4. A doc edit after rendering is detectable via input_doc_shas. Mutate the
    # anchor doc (heading kept so the anchor still resolves), recommit, re-render.
    before_sha = pack["input_doc_shas"][ANCHOR_PATH]
    mutated = CONTEXT_SPEC.replace(_BODY_PHRASE, _BODY_PHRASE + " Now revised.")
    (repo / ANCHOR_PATH).write_text(mutated, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "mutate anchor doc")

    main(["context", "render", "ATLAS-58", "--repo", str(repo), "--json"], database=db)
    after = json.loads(capsys.readouterr().out)
    after_sha = after["input_doc_shas"][ANCHOR_PATH]

    # The wrong answer: the recorded SHA is stable across a real content edit.
    assert before_sha != after_sha


# --- processed-stub anchors (ATLAS-162) --------------------------------------


def test_render_stub_anchored_ticket_resolves_and_excerpts_stub(
    repo_with_processed: Path, db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC1: a ticket anchored at inbox/processed/<name>.md#slug renders — the
    stub content is the pack's source excerpt and its SHA is pinned."""
    seed_full(db, make_ticket("ATLAS-162", source_anchor=STUB_ANCHOR))

    args = ["context", "render", "ATLAS-162", "--repo", str(repo_with_processed)]
    code = main([*args, "--json"], database=db)
    captured = capsys.readouterr()

    # The wrong answer: the pre-fix corpus-only index — UnknownDocumentError
    # and a precondition exit.
    assert code == EXIT_OK, captured.err
    pack = json.loads(captured.out)
    assert _STUB_PHRASE in pack["rendered_markdown"]
    assert PROCESSED_STUB_PATH in pack["input_doc_shas"]


def test_validate_stub_anchored_ticket_is_valid_at_slug_depth(
    repo_with_processed: Path, db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC1 (validation twin): the validator shares the loaded document set, so
    the same stub-minted ticket passes the spec-true slug-level anchor check."""
    seed_full(db, make_ticket("ATLAS-162", source_anchor=STUB_ANCHOR))

    code = main(
        ["context", "validate", "ATLAS-162", "--repo", str(repo_with_processed)],
        database=db,
    )
    captured = capsys.readouterr()

    assert code == EXIT_OK, captured.err
    assert "INVALID" not in captured.out
    assert "slug" in captured.out


def test_render_dangling_processed_anchor_is_clean_precondition(
    repo_with_processed: Path, db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC3: resolution gains the processed/ set, not leniency — an anchor to a
    nonexistent processed/ document still fails, cleanly and by name."""
    dangling = "docs/planning/inbox/processed/no-such-stub.md#nope"
    seed_full(db, make_ticket("ATLAS-90", source_anchor=dangling))

    code = main(
        ["context", "render", "ATLAS-90", "--repo", str(repo_with_processed)],
        database=db,
    )
    captured = capsys.readouterr()

    # The wrong answer: a silent pass (leniency) or a traceback.
    assert code == EXIT_PRECONDITION
    assert captured.out == ""
    assert "no-such-stub" in captured.err
    assert "Traceback" not in captured.err


def test_corpus_anchored_pack_byte_identical_with_processed_stubs_present(
    repo: Path, db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC4: committing a processed/ stub changes NOTHING for a corpus-anchored
    ticket — rendered_markdown and input_doc_shas are byte-identical."""
    seed_full(db)

    main(["context", "render", "ATLAS-58", "--repo", str(repo), "--json"], database=db)
    before = json.loads(capsys.readouterr().out)

    target = repo / PROCESSED_STUB_PATH
    target.parent.mkdir(parents=True)
    target.write_text(PROCESSED_STUB, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "add processed stub")

    main(["context", "render", "ATLAS-58", "--repo", str(repo), "--json"], database=db)
    after = json.loads(capsys.readouterr().out)

    # The wrong answer: the stub leaks into a corpus ticket's pack or shas.
    assert after["rendered_markdown"] == before["rendered_markdown"]
    assert after["input_doc_shas"] == before["input_doc_shas"]


def test_uncommitted_processed_stub_fails_closed(
    repo_with_processed: Path, db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    """The reused collector's contract holds at this seam: a dirty processed/
    file is a clean DirtyInputError precondition exit, never a silent read."""
    seed_full(db, make_ticket("ATLAS-162", source_anchor=STUB_ANCHOR))
    stub = repo_with_processed / PROCESSED_STUB_PATH
    stub.write_text(PROCESSED_STUB + "\ndirty edit\n", encoding="utf-8")

    code = main(
        ["context", "render", "ATLAS-162", "--repo", str(repo_with_processed)],
        database=db,
    )
    captured = capsys.readouterr()

    # The wrong answer: rendering from the dirty tree (pre-fix, the processed/
    # set was outside every committed-state gate on this path).
    assert code == EXIT_PRECONDITION
    assert captured.out == ""
    assert PROCESSED_STUB_PATH in captured.err
    assert "dirty or untracked" in captured.err
    assert "Traceback" not in captured.err
