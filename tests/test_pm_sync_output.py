"""ATLAS-178: sync stdout itemises exceptional skips, aggregates routine skips.

Fixture-driven with captured stdout and in-memory Linear fakes only
(``ATLAS_LIVE_TESTS=0`` posture). B011 seed for the first probe was
``assert 1 == 2`` before replacing it with the behaviour assertions below.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import pytest
from test_models_validation import NOW
from test_pm_sync import (
    PACK_DOC,
    PROJECT_ID,
    TEAM_ID,
    RecordingClient,
    seed_ticket,
    status_map,
)

from atlas.cli import EXIT_OK, main
from atlas.core.models.ticket import TicketStatus
from atlas.linear.ownership import PACK_HEADER_PREFIX
from atlas.pm.scheduler import TickConfig
from atlas.storage import Database, TicketRepo


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{tmp_path}/atlas.db")
    database.create_all()
    return database


def _isolate_root_logging() -> tuple[int, list[logging.Handler]]:
    root = logging.getLogger()
    original_level = root.level
    original_handlers = list(root.handlers)
    for handler in original_handlers:
        root.removeHandler(handler)
    root.setLevel(logging.WARNING)
    return original_level, original_handlers


def _restore_root_logging(
    original_level: int, original_handlers: list[logging.Handler]
) -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    for handler in original_handlers:
        root.addHandler(handler)
    root.setLevel(original_level)


def _run_sync_cli(
    *,
    db: Database,
    client: RecordingClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    args: Sequence[str],
) -> list[str]:
    def fake_config(parsed_args: object, resolved_db: Database) -> TickConfig:
        return TickConfig(
            tickets=TicketRepo(resolved_db),
            db=resolved_db,
            client=client,
            status_map=status_map(),
            team_id=TEAM_ID,
            project_id=PROJECT_ID,
            inbox_dir=tmp_path / "inbox",
            documents=lambda: [PACK_DOC],
            repair_packs=bool(getattr(parsed_args, "repair_packs", False)),
        )

    monkeypatch.setattr("atlas.cli.build_tick_config", fake_config)
    monkeypatch.setattr("atlas.cli._install_shutdown_handlers", lambda event: None)
    original_level, original_handlers = _isolate_root_logging()
    try:
        code = main(["pm", "sync", *args], database=db)
    finally:
        _restore_root_logging(original_level, original_handlers)

    assert code == EXIT_OK
    return capsys.readouterr().out.strip().splitlines()


def test_sync_once_terminal_routine_skips_print_one_aggregated_summary_line(
    db: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = RecordingClient()
    for index in range(2):
        seed_ticket(
            db,
            client,
            key=f"ATLAS-{400 + index}",
            status=TicketStatus.DONE,
            with_issue=False,
        )
    seed_ticket(
        db,
        client,
        key="ATLAS-402",
        status=TicketStatus.REJECTED,
        with_issue=False,
    )

    lines = _run_sync_cli(
        db=db,
        client=client,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        capsys=capsys,
        args=["--once"],
    )

    assert len(lines) == 2
    assert lines[1].startswith("pm sync actions:")
    assert "push_skipped=3 (not pushable: done=2, rejected=1)" in lines[0]
    assert not any(line.startswith("push skipped ") for line in lines)


def test_repair_pack_notable_skip_is_itemised_amid_routine_header_skips(
    db: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = RecordingClient()
    for index in range(5):
        ticket = seed_ticket(
            db,
            client,
            key=f"ATLAS-{410 + index}",
            status=TicketStatus.PLANNED,
            updated_at=NOW,
            linear_synced_at=NOW,
        )
        assert ticket.external_linear_id is not None
        client.update_issue(
            ticket.external_linear_id,
            {"description": f"{PACK_HEADER_PREFIX} existing pack"},
        )
    seed_ticket(
        db,
        client,
        key="ATLAS-415",
        status=TicketStatus.PLANNED,
        updated_at=NOW,
        linear_synced_at=NOW,
        with_issue=False,
    )

    lines = _run_sync_cli(
        db=db,
        client=client,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        capsys=capsys,
        args=["--repair-packs"],
    )

    assert "header already present=5" in lines[0]
    assert [line for line in lines if line.startswith("pack repair skipped ")] == [
        "pack repair skipped ATLAS-415: no external id"
    ]


def test_verbose_sync_once_itemises_routine_push_skips(
    db: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = RecordingClient()
    seed_ticket(
        db,
        client,
        key="ATLAS-420",
        status=TicketStatus.DONE,
        with_issue=False,
    )
    seed_ticket(
        db,
        client,
        key="ATLAS-421",
        status=TicketStatus.REJECTED,
        with_issue=False,
    )

    lines = _run_sync_cli(
        db=db,
        client=client,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        capsys=capsys,
        args=["--once", "-v"],
    )

    assert "push_skipped=2 (not pushable: done=1, rejected=1)" in lines[0]
    assert "push skipped ATLAS-420: status not pushable (done)" in lines
    assert "push skipped ATLAS-421: status not pushable (rejected)" in lines


def test_push_skip_summary_distinguishes_nonpushable_from_cursor_stamped(
    db: Database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = RecordingClient()
    for index, status in enumerate(
        [
            TicketStatus.BACKLOG,
            TicketStatus.PLANNED,
            TicketStatus.READY_FOR_AGENT,
        ]
    ):
        seed_ticket(
            db,
            client,
            key=f"ATLAS-{430 + index}",
            status=status,
            updated_at=datetime(2026, 1, 1, tzinfo=NOW.tzinfo),
            linear_synced_at=datetime(2026, 1, 1, tzinfo=NOW.tzinfo),
            with_issue=False,
        )

    lines = _run_sync_cli(
        db=db,
        client=client,
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        capsys=capsys,
        args=["--once"],
    )

    assert len(lines) == 3
    assert lines[1].startswith("pm sync actions:")
    assert lines[2].startswith("admission stale none: reason=snapshot_incomplete")
    assert (
        "push_skipped=3 (not pushable: backlog=1; cursor already stamped=2)" in lines[0]
    )
    assert not any(line.startswith("push skipped ") for line in lines)
