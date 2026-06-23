"""PM-scheduler milestone proofs (ATLAS-50).

The four CI acceptance criteria, each with the wrong answer named, driven with a
fake/spied ``sync_tick``, an injected clock, and an injected interruptible sleep
— no real time, no signals, no network, no secrets (D4):

- AC1 (crash → record + continue): a tick that raises records exactly ONE
  ``TickFailure`` and the loop continues to the next tick. Wrong answer: the
  exception escapes ``run_scheduler``, or the loop stops at the crash.
- AC2 (dedup window): the same signature on N consecutive ticks inside
  ``CRASH_DEDUP_WINDOW`` records one row; a prior row older than the window lets a
  fresh one record; a different signature records its own. Wrong answer: one row
  per tick, or the window never lets a recurrence re-record.
- AC3 (--once / shutdown): ``--once`` runs exactly one tick and returns (never
  sleeps); the loop runs repeatedly until shutdown, then stops AFTER finishing the
  in-flight tick (never abandons it mid-write). Wrong answer: ``--once`` loops, or
  a signal kills the loop mid-tick.
- AC4 (injection): the exact keyword injection is passed into ``sync_tick`` from
  config; a successful tick records NO ``TickFailure``. Wrong answer: a mis-wired
  or hidden argument, or a phantom failure on a clean tick.

Plus the ``failure_signature`` shape (GAP 2), the create-on-crash record fields,
and the CLI seam (``atlas pm sync`` parsing, config-building from env, the
shutdown-handler wiring, and the missing-creds precondition exit).
"""

from __future__ import annotations

import signal
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from linear_fakes import InMemoryLinearClient

from atlas.cli import (
    EXIT_OK,
    EXIT_PRECONDITION,
    _build_tick_config,
    _install_shutdown_handlers,
    build_parser,
    main,
)
from atlas.core.enums import ActorType
from atlas.linear.client import (
    API_KEY_ENV,
    TEAM_ID_ENV,
    LinearAPIError,
    LinearGraphQLClient,
)
from atlas.linear.ownership import STATE_MAP_ENV, LinearStatusMap
from atlas.pm import SyncResult
from atlas.pm.scheduler import (
    CRASH_DEDUP_WINDOW,
    CREATED_BY,
    DEFAULT_INTERVAL_SECONDS,
    TickConfig,
    _signature,
    run_scheduler,
    run_tick,
)
from atlas.storage import Database, TicketRepo, TickFailureRepo

NOW = datetime(2026, 1, 1, tzinfo=UTC)


# --- test doubles -----------------------------------------------------------


class SpyTick:
    """A stand-in for ``sync_tick`` that records every keyword call and replays a
    scripted behaviour per call: an ``Exception`` instance is raised, anything
    else (or a missing entry past the script) is a clean tick returning a
    ``SyncResult``."""

    def __init__(self, behaviours: list[Exception | None] | None = None) -> None:
        self.behaviours = list(behaviours or [])
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> SyncResult:
        index = len(self.calls)
        self.calls.append(kwargs)
        if index < len(self.behaviours):
            behaviour = self.behaviours[index]
            if isinstance(behaviour, Exception):
                raise behaviour
        return SyncResult()


class FakeClock:
    """A deterministic injected clock: returns ``start``, then advances by
    ``step`` each call, so every tick gets a distinct, predictable ``now``."""

    def __init__(self, start: datetime = NOW, step: timedelta = timedelta(minutes=5)):
        self._t = start
        self._step = step
        self.calls = 0

    def __call__(self) -> datetime:
        self.calls += 1
        now = self._t
        self._t = self._t + self._step
        return now


class FakeSleep:
    """An injected interruptible sleep that returns False (slept fully) until its
    ``stop_after``-th call, then True (woken by shutdown) — so the loop runs
    exactly ``stop_after`` ticks. Records the intervals it was asked to wait."""

    def __init__(self, stop_after: int) -> None:
        self.stop_after = stop_after
        self.calls = 0
        self.intervals: list[float] = []

    def __call__(self, interval: float) -> bool:
        self.calls += 1
        self.intervals.append(interval)
        return self.calls >= self.stop_after


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{tmp_path}/atlas.db")
    database.create_all()
    return database


def make_config(db: Database, tmp_path: Path) -> TickConfig:
    """A ``TickConfig`` whose injection is real-but-inert: ``sync_tick`` is spied
    in every test, so the client/status_map are never exercised; ``db`` is real so
    the ``TickFailureRepo`` write path is genuine."""

    return TickConfig(
        tickets=TicketRepo(db),
        db=db,
        client=InMemoryLinearClient(),
        status_map=LinearStatusMap({}),
        team_id="team-1",
        inbox_dir=tmp_path / "inbox",
    )


def spy_sync_tick(monkeypatch: pytest.MonkeyPatch, spy: SpyTick) -> None:
    monkeypatch.setattr("atlas.pm.scheduler.sync_tick", spy)


# --- AC1: a crash is recorded and the loop continues ------------------------


def test_crash_records_one_failure_and_loop_continues(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # First tick raises; the rest succeed.
    spy = SpyTick(behaviours=[RuntimeError("boom")])
    spy_sync_tick(monkeypatch, spy)
    config = make_config(db, tmp_path)

    run_scheduler(
        config,
        interval=60,
        now=FakeClock(),
        sleep=FakeSleep(stop_after=2),
    )

    # Loop continued past the crash (the wrong answer: the exception escaped, or
    # the loop stopped at the crash and called sync_tick once).
    assert len(spy.calls) == 2
    failures = TickFailureRepo(db).list()
    assert len(failures) == 1
    record = failures[0]
    assert record.failure_signature == "builtins.RuntimeError"
    assert record.detail == "boom"
    assert record.occurred_at == NOW  # the crashing tick's clock instant
    assert record.created_by_type is ActorType.SYSTEM
    assert record.created_by_id == CREATED_BY


def test_clean_loop_records_no_failure(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spy = SpyTick()  # every tick clean
    spy_sync_tick(monkeypatch, spy)
    config = make_config(db, tmp_path)

    run_scheduler(config, interval=60, now=FakeClock(), sleep=FakeSleep(stop_after=3))

    assert len(spy.calls) == 3
    assert TickFailureRepo(db).list() == []


# --- AC2: the dedup window holds --------------------------------------------


def test_same_signature_within_window_records_once(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Every tick raises the same type; clock steps stay well inside the 1h window.
    spy = SpyTick(behaviours=[RuntimeError("x")] * 5)
    spy_sync_tick(monkeypatch, spy)
    config = make_config(db, tmp_path)
    failures = TickFailureRepo(db)

    for i in range(5):
        run_tick(config, failures, now=NOW + timedelta(minutes=10 * i))

    # One row for the whole window (the wrong answer: one row per tick).
    rows = failures.list()
    assert len(rows) == 1
    assert rows[0].occurred_at == NOW  # the first crash in the window


def test_recurrence_past_window_records_again(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spy = SpyTick(behaviours=[RuntimeError("x"), RuntimeError("x")])
    spy_sync_tick(monkeypatch, spy)
    config = make_config(db, tmp_path)
    failures = TickFailureRepo(db)

    run_tick(config, failures, now=NOW)
    # A crash older than the window no longer dedups a fresh one.
    run_tick(config, failures, now=NOW + CRASH_DEDUP_WINDOW + timedelta(seconds=1))

    rows = failures.list()
    assert len(rows) == 2  # the wrong answer: the window never lets it re-record


def test_distinct_signatures_each_record(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Two different exception types within the same window: each is its own
    # signature, so each records (the dedup is per signature).
    spy = SpyTick(behaviours=[RuntimeError("a"), ValueError("b")])
    spy_sync_tick(monkeypatch, spy)
    config = make_config(db, tmp_path)
    failures = TickFailureRepo(db)

    run_tick(config, failures, now=NOW)
    run_tick(config, failures, now=NOW + timedelta(minutes=1))

    signatures = {row.failure_signature for row in failures.list()}
    assert signatures == {"builtins.RuntimeError", "builtins.ValueError"}


# --- AC3: --once and graceful shutdown --------------------------------------


def test_once_runs_exactly_one_tick_and_never_sleeps(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spy = SpyTick()
    spy_sync_tick(monkeypatch, spy)
    config = make_config(db, tmp_path)
    sleep = FakeSleep(stop_after=99)  # must never be consulted

    run_scheduler(config, once=True, now=FakeClock(), sleep=sleep)

    assert len(spy.calls) == 1  # the wrong answer: --once loops
    assert sleep.calls == 0  # --once ignores the cadence entirely


def test_loop_runs_repeatedly_until_shutdown(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spy = SpyTick()
    spy_sync_tick(monkeypatch, spy)
    config = make_config(db, tmp_path)
    clock = FakeClock()
    sleep = FakeSleep(stop_after=5)

    run_scheduler(config, interval=42, now=clock, sleep=sleep)

    # Ran repeatedly until the injected sleep reported shutdown, then stopped.
    assert len(spy.calls) == 5
    assert clock.calls == 5  # a fresh now per tick (D1)
    assert sleep.intervals == [42, 42, 42, 42, 42]  # the configured cadence


def test_shutdown_during_tick_finishes_in_flight_then_stops(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shutdown = threading.Event()

    class SignallingTick(SpyTick):
        # Simulate a signal arriving DURING the second tick: the flag is set
        # while the tick is still running.
        def __call__(self, **kwargs: Any) -> SyncResult:
            result = super().__call__(**kwargs)
            if len(self.calls) == 2:
                shutdown.set()
            return result

    spy = SignallingTick()
    spy_sync_tick(monkeypatch, spy)
    config = make_config(db, tmp_path)
    # stop_after large: only the shutdown flag (checked AFTER the tick) can stop
    # the loop, proving the post-tick is_set() path — not the sleep.
    sleep = FakeSleep(stop_after=99)

    run_scheduler(config, interval=60, now=FakeClock(), shutdown=shutdown, sleep=sleep)

    # The in-flight (2nd) tick completed, THEN the loop stopped — never a 3rd tick.
    assert len(spy.calls) == 2
    assert sleep.calls == 1  # one sleep between tick 1 and tick 2; none after


def test_pre_set_shutdown_stops_after_one_tick_with_default_sleep(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Default sleep (shutdown.wait) wiring, exercised without hanging: a shutdown
    # already signalled is caught by the post-tick is_set() check, so the loop runs
    # one tick and returns without ever blocking on the real sleep.
    spy = SpyTick()
    spy_sync_tick(monkeypatch, spy)
    config = make_config(db, tmp_path)
    shutdown = threading.Event()
    shutdown.set()

    run_scheduler(config, interval=60, now=FakeClock(), shutdown=shutdown)

    assert len(spy.calls) == 1


# --- AC4: the injection is built from config --------------------------------


def test_injection_passed_into_sync_tick_and_clean_tick_records_nothing(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spy = SpyTick()
    spy_sync_tick(monkeypatch, spy)
    config = make_config(db, tmp_path)
    failures = TickFailureRepo(db)

    run_tick(config, failures, now=NOW)

    assert spy.calls == [
        {
            "tickets": config.tickets,
            "db": config.db,
            "client": config.client,
            "status_map": config.status_map,
            "team_id": config.team_id,
            "inbox_dir": config.inbox_dir,
            "now": NOW,
        }
    ]
    assert failures.list() == []  # a clean tick records no TickFailure


# --- GAP 2: the failure signature -------------------------------------------


def test_signature_is_fully_qualified_type_name() -> None:
    transient = LinearAPIError("transient")
    assert _signature(transient) == "atlas.linear.client.LinearAPIError"
    assert _signature(ValueError("x")) == "builtins.ValueError"


# --- CLI seam ---------------------------------------------------------------


def test_sync_subparser_parses_flags() -> None:
    args = build_parser().parse_args(
        ["pm", "sync", "--once", "--interval", "30", "--inbox-dir", "x", "--db", "y"]
    )
    assert args.command == "pm"
    assert args.pm_command == "sync"
    assert args.once is True
    assert args.interval == 30
    assert args.inbox_dir == "x"
    assert args.db == "y"


def test_sync_flag_defaults() -> None:
    args = build_parser().parse_args(["pm", "sync"])
    assert args.once is False
    assert args.interval == DEFAULT_INTERVAL_SECONDS
    assert args.inbox_dir == "docs/planning/inbox"


def _live_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set fake-but-well-formed Linear env so the live config builds with no
    network (client construction and map parsing read env only)."""
    monkeypatch.setenv(API_KEY_ENV, "lin_api_fake")
    monkeypatch.setenv(TEAM_ID_ENV, "team-xyz")
    monkeypatch.setenv(
        STATE_MAP_ENV,
        '{"s-ready": "ready_for_agent", "s-needs": "needs_human_decision"}',
    )


def test_build_tick_config_wires_from_env(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _live_env(monkeypatch)
    args = build_parser().parse_args(["pm", "sync", "--inbox-dir", str(tmp_path)])

    config = _build_tick_config(args, db)

    assert config.db is db
    assert isinstance(config.client, LinearGraphQLClient)
    assert config.team_id == "team-xyz"  # from LINEAR_TEAM_ID
    assert config.inbox_dir == Path(str(tmp_path))  # from --inbox-dir
    assert config.status_map.status_for("s-ready") is not None  # from_env parsed


def test_sync_missing_creds_is_clean_precondition(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    code = main(["pm", "sync", "--once"], database=db)
    assert code == EXIT_PRECONDITION  # missing key fails loud, no traceback


def test_sync_missing_team_id_is_clean_precondition(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(API_KEY_ENV, "lin_api_fake")
    monkeypatch.setenv(STATE_MAP_ENV, '{"s-ready": "ready_for_agent"}')
    monkeypatch.delenv(TEAM_ID_ENV, raising=False)
    code = main(["pm", "sync", "--once"], database=db)
    assert code == EXIT_PRECONDITION


def test_sync_once_clean_path_no_network(
    db: Database, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A full --once run through the CLI with an EMPTY backlog: sync_tick iterates
    # zero tickets and makes no Linear call, so the live client is never dialled.
    # The shutdown handlers are stubbed so the test never mutates process signals.
    _live_env(monkeypatch)
    monkeypatch.setattr("atlas.cli._install_shutdown_handlers", lambda event: None)
    args = ["pm", "sync", "--once", "--inbox-dir", str(tmp_path / "inbox")]

    code = main(args, database=db)

    assert code == EXIT_OK
    assert TickFailureRepo(db).list() == []  # a clean tick recorded nothing


def test_install_shutdown_handlers_sets_event_on_signal() -> None:
    # Prove the handler wiring without sending a real signal: install, then invoke
    # the registered handler directly. Restore the prior handlers afterward.
    old_term = signal.getsignal(signal.SIGTERM)
    old_int = signal.getsignal(signal.SIGINT)
    try:
        event = threading.Event()
        _install_shutdown_handlers(event)
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler)
        handler(signal.SIGTERM, None)
        assert event.is_set()
    finally:
        signal.signal(signal.SIGTERM, old_term)
        signal.signal(signal.SIGINT, old_int)
