"""One-shot PM failure reporting and persistence-boundary proofs (AUD-010)."""

from __future__ import annotations

import subprocess
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from atlas.cli import EXIT_RECORDED_FAILURE, main
from atlas.linear.ownership import LinearStatusMap
from atlas.pm.scheduler import OneShotSyncFailure, TickConfig, run_scheduler
from atlas.storage import Database, PmSyncReceiptRepo, TicketRepo, TickFailureRepo

NOW = datetime(2026, 9, 12, tzinfo=UTC)


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{tmp_path}/atlas.db")
    database.create_all()
    return database


class ControlledClient:
    """The empty-board client keeps the actual sync body network-free."""

    def fetch_issues(self) -> list[object]:
        return []


def config(db: Database, tmp_path: Path) -> TickConfig:
    return TickConfig(
        tickets=TicketRepo(db),
        db=db,
        client=ControlledClient(),  # type: ignore[arg-type]
        status_map=LinearStatusMap({}),
        team_id="team",
        project_id="project",
        inbox_dir=tmp_path / "inbox",
        documents=lambda: [],
    )


def invoke_actual_cli(
    monkeypatch: pytest.MonkeyPatch,
    db: Database,
    tick_config: TickConfig,
    args: list[str] | None = None,
) -> int:
    monkeypatch.setattr("atlas.cli.assert_schema_at_head", lambda database: None)
    monkeypatch.setattr(
        "atlas.cli.build_tick_config", lambda parsed, database: tick_config
    )
    monkeypatch.setattr("atlas.cli._install_shutdown_handlers", lambda event: None)
    return main(args or ["pm", "sync", "--once"], database=db)


def test_actual_cli_scheduler_failure_is_nonzero_sanitized_and_single_tick(
    db: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = 0

    def fail_tick(**kwargs: Any) -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("provider-secret-response")

    monkeypatch.setattr("atlas.pm.scheduler.sync_tick", fail_tick)
    monkeypatch.setattr(
        threading.Event,
        "wait",
        lambda self, timeout=None: pytest.fail("one-shot scheduler slept"),
    )

    code = invoke_actual_cli(monkeypatch, db, config(db, tmp_path))
    captured = capsys.readouterr()

    assert code == EXIT_RECORDED_FAILURE
    assert calls == 1
    assert captured.out == ""
    assert "pm sync: tick failed" in captured.err
    assert "builtins.RuntimeError" in captured.err
    assert "provider-secret-response" not in captured.err
    assert "no work performed" not in captured.err
    assert "pushes=" not in captured.err
    assert len(TickFailureRepo(db).list()) == 1


def test_repair_packs_uses_same_effective_once_failure_contract(
    db: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    base_config = config(db, tmp_path)
    tick_config = TickConfig(**{**base_config.__dict__, "repair_packs": True})
    monkeypatch.setattr(
        "atlas.pm.scheduler.sync_tick",
        lambda **kwargs: (_ for _ in ()).throw(ValueError("raw")),
    )

    code = invoke_actual_cli(
        monkeypatch, db, tick_config, ["pm", "sync", "--repair-packs"]
    )
    captured = capsys.readouterr()

    assert code == EXIT_RECORDED_FAILURE
    assert "repair-packs: completed" not in captured.out
    assert "builtins.ValueError" in captured.err
    assert "raw" not in captured.err


def test_receipt_persistence_failure_after_successful_body_is_not_success(
    db: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        PmSyncReceiptRepo,
        "record",
        lambda self, receipt: (_ for _ in ()).throw(OSError("disk-secret")),
    )

    code = invoke_actual_cli(monkeypatch, db, config(db, tmp_path))
    captured = capsys.readouterr()

    assert code == EXIT_RECORDED_FAILURE
    assert "SyncReceiptPersistenceError" in captured.err
    assert "disk-secret" not in captured.err
    assert PmSyncReceiptRepo(db).list() == []
    assert len(TickFailureRepo(db).list()) == 1


def test_tick_failure_persistence_failure_cannot_become_one_shot_success(
    db: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        "atlas.pm.scheduler.sync_tick",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("provider-secret")),
    )
    monkeypatch.setattr(
        TickFailureRepo,
        "record",
        lambda self, failure: (_ for _ in ()).throw(OSError("database-secret")),
    )

    code = invoke_actual_cli(monkeypatch, db, config(db, tmp_path))
    captured = capsys.readouterr()

    assert code == EXIT_RECORDED_FAILURE
    assert "tick_failure=not-recorded" in captured.err
    assert "builtins.OSError" in captured.err
    assert "provider-secret" not in captured.err
    assert "database-secret" not in captured.err
    assert TickFailureRepo(db).list() == []


def test_scheduler_returns_typed_failure_without_success_result(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "atlas.pm.scheduler.sync_tick",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    result = run_scheduler(config(db, tmp_path), once=True, now=lambda: NOW)

    assert isinstance(result, OneShotSyncFailure)
    assert result.failure_signature == "builtins.RuntimeError"
    assert result.tick_failure_recorded is True


def test_atomic_stub_survives_failed_once_and_fresh_process_deduplicates(
    tmp_path: Path,
) -> None:
    """A real sync body writes the stub before receipt failure; restart reuses it."""

    database_path = tmp_path / "restart.db"
    inbox = tmp_path / "inbox"
    script = r"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / "tests"))
from test_pm_sync import PACK_DOC, RecordingClient, STARTED, seed_ticket, status_map
from atlas.cli import main
from atlas.core.models import TicketStatus
from atlas.pm.scheduler import TickConfig
from atlas.storage import Database, PmSyncReceiptRepo, TicketRepo
import atlas.cli as cli

phase, database_name, inbox_name = sys.argv[1:]
db = Database(f"sqlite:///{database_name}")
client = RecordingClient()
if phase == "first":
    db.create_all()
    ticket = seed_ticket(
        db, client, key="ATLAS-RESTART", status=TicketStatus.IN_PROGRESS,
        issue_state=STARTED,
    )
else:
    issue = client.create_issue(
        {"title": "Linear Title", "description": "linear"},
        team_id="team-1", project_id="project-1",
    )
    assert issue.id == "issue-1"
    client.simulate_linear_state(issue.id, STARTED)
    ticket = TicketRepo(db).list()[0]
    assert ticket.external_linear_id == issue.id
client.seed_comment(
    ticket.external_linear_id,
    "first atlas:proposed-follow-up",
    comment_id="c-1",
)
if phase == "second":
    client.seed_comment(
        ticket.external_linear_id,
        "second atlas:proposed-follow-up",
        comment_id="c-2",
    )
config = TickConfig(
    tickets=TicketRepo(db), db=db, client=client, status_map=status_map(),
    team_id="team-1", project_id="project-1", inbox_dir=Path(inbox_name),
    documents=lambda: [PACK_DOC],
)
cli.assert_schema_at_head = lambda database: None
cli.build_tick_config = lambda args, database: config
cli._install_shutdown_handlers = lambda event: None
if phase == "first":
    PmSyncReceiptRepo.record = lambda self, receipt: (_ for _ in ()).throw(
        OSError("interrupted")
    )
code = main(["pm", "sync", "--once"], database=db)
files = sorted(path.name for path in Path(inbox_name).glob("*.md"))
if phase == "first":
    assert code == 1
    assert files == ["ATLAS-RESTART-1.md"]
    assert PmSyncReceiptRepo(db).list() == []
else:
    assert code == 0
    assert files == ["ATLAS-RESTART-1.md", "ATLAS-RESTART-2.md"]
    first = (Path(inbox_name) / files[0]).read_text()
    second = (Path(inbox_name) / files[1]).read_text()
    assert first.count("c-1") == 2 and "c-2" not in first
    assert second.count("c-2") == 2 and "c-1" not in second
    assert len(PmSyncReceiptRepo(db).list()) == 1
"""
    first = subprocess.run(
        [sys.executable, "-c", script, "first", str(database_path), str(inbox)],
        cwd=Path(__file__).resolve().parent.parent,
        capture_output=True,
        text=True,
    )
    assert first.returncode == 0, first.stdout + first.stderr
    second = subprocess.run(
        [sys.executable, "-c", script, "second", str(database_path), str(inbox)],
        cwd=Path(__file__).resolve().parent.parent,
        capture_output=True,
        text=True,
    )
    assert second.returncode == 0, second.stdout + second.stderr
