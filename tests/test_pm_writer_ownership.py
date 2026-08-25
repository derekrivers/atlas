"""Cross-process PM writer-ownership proofs (ATLAS-068M)."""

from __future__ import annotations

import hashlib
import multiprocessing as mp
import os
import signal
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
import sqlalchemy as sa
from linear_fakes import InMemoryLinearClient

import atlas.pm.scheduler as scheduler_module
from atlas.cli import EXIT_PRECONDITION, main
from atlas.core.models import TicketStatus
from atlas.linear.client import LinearRateLimitError
from atlas.linear.ownership import LinearStatusMap
from atlas.pm import SyncResult
from atlas.pm.scheduler import TickConfig, run_scheduler
from atlas.pm.writer_ownership import (
    PMWriterAlreadyActiveError,
    PMWriterOwnershipUnavailableError,
    PMWriterOwnershipUnsupportedError,
    _open_ownership_descriptor,
    pm_writer_ownership,
)
from atlas.storage import Database, TicketRepo, TickFailureRepo

PROCESS_TIMEOUT_SECONDS = 10.0


def _config(database: Database, root: Path) -> TickConfig:
    return TickConfig(
        tickets=TicketRepo(database),
        db=database,
        client=InMemoryLinearClient(),
        status_map=LinearStatusMap(
            {
                "state-ready": TicketStatus.READY_FOR_AGENT,
                "state-needs": TicketStatus.NEEDS_HUMAN_DECISION,
                "state-done": TicketStatus.DONE,
            }
        ),
        team_id="team-1",
        project_id="project-1",
        inbox_dir=root / "inbox",
        documents=lambda: [],
    )


def _hold_lock(
    database_url: str,
    entered: Any,
    release: Any,
    *,
    working_directory: str | None = None,
) -> None:
    if working_directory is not None:
        os.chdir(working_directory)
    database = Database(database_url)
    with pm_writer_ownership(database):
        entered.set()
        release.wait(PROCESS_TIMEOUT_SECONDS)


def _run_recurring_until_sleep(
    database_url: str,
    root: str,
    entered: Any,
    release: Any,
    *,
    rate_limited: bool,
) -> None:
    database = Database(database_url)
    config = _config(database, Path(root))

    if rate_limited:

        def rate_limited_tick(**kwargs: Any) -> SyncResult:
            raise LinearRateLimitError("limited", reset_after_seconds=900.0)

        scheduler_module.__dict__["sync_tick"] = rate_limited_tick

    def blocking_sleep(interval: float) -> bool:
        entered.set()
        release.wait(PROCESS_TIMEOUT_SECONDS)
        return True

    run_scheduler(config, interval=60, sleep=blocking_sleep)


def _run_blocked_one_shot(
    database_url: str,
    root: str,
    entered: Any,
    release: Any,
) -> None:
    database = Database(database_url)
    config = _config(database, Path(root))

    def blocking_tick(**kwargs: Any) -> SyncResult:
        entered.set()
        release.wait(PROCESS_TIMEOUT_SECONDS)
        return SyncResult()

    scheduler_module.__dict__["sync_tick"] = blocking_tick
    run_scheduler(config, once=True)


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    path = tmp_path / "atlas.db"
    Database(f"sqlite:///{path}").create_all()
    return path


def _fork_process(target: Any, *args: Any, **kwargs: Any) -> Any:
    context = mp.get_context("fork")
    process = context.Process(target=target, args=args, kwargs=kwargs)
    process.start()
    return process


def _release_process(process: Any, release: Any) -> None:
    release.set()
    process.join(PROCESS_TIMEOUT_SECONDS)
    if process.is_alive():
        process.terminate()
        process.join(PROCESS_TIMEOUT_SECONDS)
    assert process.exitcode == 0


@pytest.mark.parametrize("rate_limited", [False, True])
def test_recurring_owner_blocks_one_shot_during_sleep_and_backoff(
    database_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rate_limited: bool,
) -> None:
    context = mp.get_context("fork")
    entered = context.Event()
    release = context.Event()
    database_url = f"sqlite:///{database_path}"
    owner = _fork_process(
        _run_recurring_until_sleep,
        database_url,
        str(tmp_path),
        entered,
        release,
        rate_limited=rate_limited,
    )
    assert entered.wait(PROCESS_TIMEOUT_SECONDS)

    database = Database(database_url)
    config = _config(database, tmp_path)
    failures_before = TickFailureRepo(database).list()
    failure_repo_calls: list[str] = []
    tick_calls: list[str] = []
    monkeypatch.setattr(
        scheduler_module,
        "TickFailureRepo",
        lambda resolved_db: failure_repo_calls.append("called"),
    )
    monkeypatch.setattr(
        scheduler_module,
        "run_tick",
        lambda *args, **kwargs: tick_calls.append("called"),
    )

    with pytest.raises(PMWriterAlreadyActiveError, match="PM writer already active"):
        run_scheduler(config, once=True)

    assert failure_repo_calls == []
    assert tick_calls == []
    assert TickFailureRepo(database).list() == failures_before
    _release_process(owner, release)


def test_one_shot_owner_blocks_second_one_shot(
    database_path: Path, tmp_path: Path
) -> None:
    context = mp.get_context("fork")
    entered = context.Event()
    release = context.Event()
    database_url = f"sqlite:///{database_path}"
    owner = _fork_process(
        _run_blocked_one_shot,
        database_url,
        str(tmp_path),
        entered,
        release,
    )
    assert entered.wait(PROCESS_TIMEOUT_SECONDS)

    with pytest.raises(PMWriterAlreadyActiveError):
        run_scheduler(_config(Database(database_url), tmp_path), once=True)

    _release_process(owner, release)


def test_same_absolute_store_contends_from_different_checkout(
    database_path: Path, tmp_path: Path
) -> None:
    context = mp.get_context("fork")
    entered = context.Event()
    release = context.Event()
    checkout = tmp_path / "other-checkout"
    checkout.mkdir()
    database_url = f"sqlite:///{database_path}"
    owner = _fork_process(
        _hold_lock,
        database_url,
        entered,
        release,
        working_directory=str(checkout),
    )
    assert entered.wait(PROCESS_TIMEOUT_SECONDS)

    with (
        pytest.raises(PMWriterAlreadyActiveError),
        pm_writer_ownership(Database(database_url)),
    ):
        pass

    _release_process(owner, release)


@pytest.mark.parametrize("alias_kind", ["symlink", "hardlink"])
def test_filesystem_aliases_contend_on_same_store_inode(
    database_path: Path, tmp_path: Path, alias_kind: str
) -> None:
    alias = tmp_path / f"{alias_kind}.db"
    if alias_kind == "symlink":
        alias.symlink_to(database_path)
    else:
        os.link(database_path, alias)

    context = mp.get_context("fork")
    entered = context.Event()
    release = context.Event()
    owner = _fork_process(
        _hold_lock,
        f"sqlite:///{alias}",
        entered,
        release,
    )
    assert entered.wait(PROCESS_TIMEOUT_SECONDS)

    with (
        pytest.raises(PMWriterAlreadyActiveError),
        pm_writer_ownership(Database(f"sqlite:///{database_path}")),
    ):
        pass

    _release_process(owner, release)


def test_distinct_stores_acquire_independently(tmp_path: Path) -> None:
    first_path = tmp_path / "first.db"
    second_path = tmp_path / "second.db"
    Database(f"sqlite:///{first_path}").create_all()
    Database(f"sqlite:///{second_path}").create_all()
    context = mp.get_context("fork")
    first_entered = context.Event()
    second_entered = context.Event()
    first_release = context.Event()
    second_release = context.Event()

    first = _fork_process(
        _hold_lock,
        f"sqlite:///{first_path}",
        first_entered,
        first_release,
    )
    second = _fork_process(
        _hold_lock,
        f"sqlite:///{second_path}",
        second_entered,
        second_release,
    )

    assert first_entered.wait(PROCESS_TIMEOUT_SECONDS)
    assert second_entered.wait(PROCESS_TIMEOUT_SECONDS)
    _release_process(first, first_release)
    _release_process(second, second_release)


def test_normal_completion_releases_ownership(database_path: Path) -> None:
    context = mp.get_context("fork")
    entered = context.Event()
    release = context.Event()
    database_url = f"sqlite:///{database_path}"
    owner = _fork_process(_hold_lock, database_url, entered, release)
    assert entered.wait(PROCESS_TIMEOUT_SECONDS)
    _release_process(owner, release)

    with pm_writer_ownership(Database(database_url)):
        pass


def test_process_death_releases_ownership_without_cleanup(database_path: Path) -> None:
    context = mp.get_context("fork")
    entered = context.Event()
    release = context.Event()
    database_url = f"sqlite:///{database_path}"
    owner = _fork_process(_hold_lock, database_url, entered, release)
    assert entered.wait(PROCESS_TIMEOUT_SECONDS)
    assert owner.pid is not None

    os.kill(owner.pid, signal.SIGKILL)
    owner.join(PROCESS_TIMEOUT_SECONDS)
    assert owner.exitcode == -signal.SIGKILL

    with pm_writer_ownership(Database(database_url)):
        pass


def test_descriptor_is_read_write_no_create_no_truncate_and_close_on_exec(
    database_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_open = os.open
    observed: dict[str, int] = {}

    def recording_open(path: Path, flags: int) -> int:
        descriptor = real_open(path, flags)
        observed["flags"] = flags
        observed["descriptor"] = descriptor
        return descriptor

    monkeypatch.setattr("atlas.pm.writer_ownership.os.open", recording_open)

    descriptor = _open_ownership_descriptor(database_path)
    try:
        flags = observed["flags"]
        assert flags & os.O_RDWR == os.O_RDWR
        assert flags & os.O_CREAT == 0
        assert flags & os.O_TRUNC == 0
        if hasattr(os, "O_CLOEXEC"):
            assert flags & os.O_CLOEXEC == os.O_CLOEXEC
        assert os.get_inheritable(descriptor) is False
    finally:
        os.close(descriptor)


def test_missing_database_is_not_created(tmp_path: Path) -> None:
    missing = tmp_path / "missing.db"

    with (
        pytest.raises(
            PMWriterOwnershipUnavailableError,
            match="PM writer ownership unavailable",
        ),
        pm_writer_ownership(Database(f"sqlite:///{missing}")),
    ):
        pass

    assert not missing.exists()


def test_body_exception_still_closes_and_releases_descriptor(
    database_path: Path,
) -> None:
    database = Database(f"sqlite:///{database_path}")

    with (
        pytest.raises(RuntimeError, match="body failed"),
        pm_writer_ownership(database),
    ):
        raise RuntimeError("body failed")

    with pm_writer_ownership(database):
        pass


@pytest.mark.parametrize(
    "url",
    [
        "sqlite://",
        "sqlite:///:memory:",
        "sqlite:///file:memory-store?mode=memory&cache=shared&uri=true",
        "postgresql://secret-user:secret-password@db.example/atlas",
    ],
)
def test_unsupported_store_refusal_is_fail_closed_and_sanitized(url: str) -> None:
    structured_url = sa.make_url(url)
    database = cast(
        Database,
        SimpleNamespace(engine=SimpleNamespace(url=structured_url)),
    )

    with (
        pytest.raises(PMWriterOwnershipUnsupportedError) as caught,
        pm_writer_ownership(database),
    ):
        pass

    output = str(caught.value)
    rendered = repr(caught.value)
    assert "single-host file-backed SQLite" in output
    assert url not in output
    assert url not in rendered
    assert "secret-user" not in output
    assert "secret-user" not in rendered
    assert "secret-password" not in output
    assert "secret-password" not in rendered
    assert "db.example" not in output
    assert "db.example" not in rendered


def test_cli_contention_is_clean_precondition_not_tick_failure(
    database_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = Database(f"sqlite:///{database_path}")
    config = _config(database, tmp_path)
    monkeypatch.setattr("atlas.cli.assert_schema_at_head", lambda resolved_db: None)
    monkeypatch.setattr("atlas.cli.build_tick_config", lambda args, resolved_db: config)
    monkeypatch.setattr("atlas.cli._install_shutdown_handlers", lambda event: None)

    def refuse(*args: Any, **kwargs: Any) -> SyncResult:
        raise PMWriterAlreadyActiveError

    monkeypatch.setattr("atlas.cli.run_scheduler", refuse)

    code = main(["pm", "sync", "--once"], database=database)

    captured = capsys.readouterr()
    assert code == EXIT_PRECONDITION
    assert captured.out == ""
    assert captured.err.strip() == "PM writer already active"
    assert TickFailureRepo(database).list() == []


def test_ownership_has_no_schema_or_database_content_side_effect(
    database_path: Path,
) -> None:
    before = hashlib.sha256(database_path.read_bytes()).hexdigest()

    with pm_writer_ownership(Database(f"sqlite:///{database_path}")):
        pass

    after = hashlib.sha256(database_path.read_bytes()).hexdigest()
    assert after == before


def test_uncontended_one_shot_behavior_is_unchanged(
    database_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []

    def clean_tick(**kwargs: Any) -> SyncResult:
        calls.append(kwargs)
        return SyncResult()

    monkeypatch.setattr(scheduler_module, "sync_tick", clean_tick)
    result = run_scheduler(
        _config(Database(f"sqlite:///{database_path}"), tmp_path),
        once=True,
        now=lambda: datetime(2026, 8, 25, tzinfo=UTC),
    )

    assert result == SyncResult()
    assert len(calls) == 1
