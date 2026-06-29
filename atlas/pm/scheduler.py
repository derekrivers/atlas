"""PM-Engine recurring scheduler with create-on-crash (ATLAS-50).

The daemon half of the Phase 4 PM Engine: a plain loop that calls
:func:`atlas.pm.sync.sync_tick` on a cadence (default 60s) and survives a
crashing tick by recording one durable ``TickFailure`` (ATLAS-125) and
continuing. It WRAPS the tick — it never reimplements or alters the
pull/push/promote/scan/detect logic; the only behaviour it adds is the cadence,
the create-on-crash record, and graceful shutdown.

The scheduler is the SOLE writer of ``TickFailure`` (pm-engine-and-linear-sync.md
"Sync loop"): it calls :meth:`TickFailureRepo.record` /
:meth:`TickFailureRepo.recorded_since` and nothing more, never mutating a row and
never bypassing the dedup predicate.

Cadence contract (pm-engine-and-linear-sync.md): every tick, default 60s; ticks
are idempotent; a missed tick costs latency only; a crash is retried next tick.
That contract is what makes create-on-crash safe — the next tick re-runs the
idempotent ``sync_tick`` from scratch, so swallowing a crash never loses work, it
only defers it by one cadence.

GAP 1 — loop and shutdown shape. The single-tick body (:func:`run_tick`) is one
function shared by ``--once`` and the loop. :func:`run_scheduler` is a thin loop
that, after each tick returns, checks for shutdown and then performs an
interruptible sleep; ``now``/``sleep``/``shutdown`` are all injectable, so CI
drives the whole loop with a fake clock, a fake sleep, and a fake client — no
real time, no signals, no network. Shutdown is a :class:`threading.Event` set by
SIGTERM/SIGINT handlers; crucially it is consulted only AFTER ``run_tick``
returns, so an in-flight tick is never abandoned mid-write — resilience by
construction. The signal handlers are installed only at the CLI entry
(:func:`atlas.cli._pm_sync`); the loop itself never touches :mod:`signal`, which
is what keeps it unit-testable.

GAP 2 — the ``failure_signature``. It is the caught exception's fully-qualified
type name (:func:`_signature`), so a recurring transient transport error
(e.g. ``atlas.linear.client.LinearAPIError``) dedups together regardless of its
per-message text, while ``detail`` keeps the specific text. Accepted failure
mode: two distinct bugs sharing one exception type collapse to one signature
inside the dedup window — fine for a dedup key, which only bounds row volume; the
operator reads ``detail`` and the delivery-report count (ATLAS-47) for specifics.

Layering: this is ``atlas.pm``, above ``atlas.storage``/``atlas.linear``/
``atlas.core`` in the import spine. It performs no I/O of its own beyond the
injected ``LinearClient`` (through ``sync_tick``) and the ``TickFailureRepo``
write, so CI runs with no network and no secrets.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from atlas.core.enums import ActorType
from atlas.core.models.tick_failure import TickFailure
from atlas.linear.client import LinearClient
from atlas.linear.ownership import LinearStatusMap
from atlas.pm.sync import sync_tick
from atlas.storage.db import Database
from atlas.storage.repositories import TicketRepo, TickFailureRepo

logger = logging.getLogger("atlas.pm.scheduler")

# The cadence default (pm-engine-and-linear-sync.md "Sync loop": "Every tick
# (default 60s)"). The loop owns the cadence; the operator may override it with
# ``--interval``.
DEFAULT_INTERVAL_SECONDS = 60

# Create-on-crash dedup window (D2). A persistent crash records at most once per
# hour per signature, not once per tick: ``run_tick`` records a ``TickFailure``
# only when no row of the same signature was recorded within this window. A
# module constant, deliberately NOT a flag (out of ATLAS-50's scope) — the dedup
# window is policy, not per-run configuration.
CRASH_DEDUP_WINDOW = timedelta(hours=1)

# System-actor id for the create-on-crash record (data-model §6.3:
# ``created_by_type = system``). Names the writer distinctly from sync.py's
# ``pm-engine`` so a TickFailure is attributable to the scheduler, its sole
# writer.
CREATED_BY = "pm-scheduler"


def _utcnow() -> datetime:
    """The production tick clock: a fresh timezone-aware UTC instant per tick
    (D1). Injected so tests drive the loop with a deterministic clock and the
    create-on-crash record's ``occurred_at`` is reproducible."""

    return datetime.now(UTC)


@dataclass(frozen=True)
class TickConfig:
    """The injection a single ``sync_tick`` needs, built once and reused every
    tick. Mirrors ``sync_tick``'s keyword-only signature exactly (``tickets``,
    ``db``, ``client``, ``status_map``, ``team_id``, ``project_id``,
    ``inbox_dir``); ``now`` is the one thing taken fresh per tick (D1) and so is
    not held here."""

    tickets: TicketRepo
    db: Database
    client: LinearClient
    status_map: LinearStatusMap
    team_id: str
    project_id: str
    inbox_dir: Path


def _signature(exc: BaseException) -> str:
    """The ``failure_signature`` for a caught tick crash (GAP 2): the exception's
    fully-qualified type name (``module.qualname``). Recurring crashes of the
    same type dedup together; the specific message lives in ``detail``."""

    cls = type(exc)
    return f"{cls.__module__}.{cls.__qualname__}"


def _record_crash(failures: TickFailureRepo, exc: Exception, now: datetime) -> bool:
    """Create-on-crash with dedup (D2). Records exactly one ``TickFailure`` for
    ``exc`` unless an identical signature was already recorded inside
    ``CRASH_DEDUP_WINDOW`` (``recorded_since`` over ``now - window``). Returns
    True when a row was written, False when deduped. The scheduler is the sole
    ``TickFailure`` writer and never bypasses ``recorded_since`` for the dedup."""

    signature = _signature(exc)
    if failures.recorded_since(signature, now - CRASH_DEDUP_WINDOW):
        logger.warning(
            "pm-scheduler: tick crashed (%s); a matching TickFailure was already "
            "recorded within the %s dedup window, not recording another: %s",
            signature,
            CRASH_DEDUP_WINDOW,
            exc,
        )
        return False
    failures.record(
        TickFailure(
            id=uuid4(),
            occurred_at=now,
            failure_signature=signature,
            detail=str(exc),
            created_by_type=ActorType.SYSTEM,
            created_by_id=CREATED_BY,
        )
    )
    logger.error(
        "pm-scheduler: tick crashed (%s); TickFailure recorded, loop continues "
        "(retried next tick): %s",
        signature,
        exc,
    )
    return True


def run_tick(config: TickConfig, failures: TickFailureRepo, *, now: datetime) -> bool:
    """Run exactly one sync tick. The single-tick body shared by ``--once`` and
    the loop.

    Calls :func:`sync_tick` with the exact keyword injection from ``config`` and
    the per-tick ``now``. On ANY exception it records one ``TickFailure`` (deduped
    per :func:`_record_crash`) and returns — a crashing tick NEVER escapes, so the
    loop is resilient by construction (AC1). Returns True on a clean tick, False
    when the tick crashed (and the crash was recorded or deduped)."""

    try:
        sync_tick(
            tickets=config.tickets,
            db=config.db,
            client=config.client,
            status_map=config.status_map,
            team_id=config.team_id,
            project_id=config.project_id,
            inbox_dir=config.inbox_dir,
            now=now,
        )
    except Exception as exc:  # create-on-crash: a tick crash never kills the loop
        _record_crash(failures, exc, now)
        return False
    return True


def run_scheduler(
    config: TickConfig,
    *,
    interval: float = DEFAULT_INTERVAL_SECONDS,
    once: bool = False,
    now: Callable[[], datetime] = _utcnow,
    shutdown: threading.Event | None = None,
    sleep: Callable[[float], bool] | None = None,
) -> None:
    """Drive ``sync_tick`` on a cadence until shutdown (or once, with ``--once``).

    The loop body is thin: run one tick (:func:`run_tick`, which absorbs a crash),
    then — unless ``once`` — stop if shutdown was signalled during the tick, else
    sleep the interval. ``now`` is called fresh per tick (D1). ``shutdown`` is a
    :class:`threading.Event` (default a fresh one) set by the CLI's signal
    handlers; it is consulted only AFTER the tick returns, so the in-flight tick
    is never abandoned mid-write (AC3). ``sleep`` is the interruptible sleep —
    it returns True when shutdown was signalled during the wait; the default is
    ``shutdown.wait`` (woken early by the signal). All of ``now``/``shutdown``/
    ``sleep`` are injectable so CI exercises the full loop with a fake clock and a
    fake sleep, no real time and no signals.

    ``--once`` (``once=True``) runs exactly one tick and returns, ignoring the
    cadence entirely (AC3)."""

    shutdown = shutdown if shutdown is not None else threading.Event()
    interruptible_sleep = sleep if sleep is not None else shutdown.wait
    failures = TickFailureRepo(config.db)

    logger.info(
        "pm-scheduler: starting (%s)",
        "single tick (--once)" if once else f"interval {interval}s",
    )
    while True:
        run_tick(config, failures, now=now())
        if once:
            return
        if shutdown.is_set():
            # The signal arrived during the tick we just finished: stop now,
            # after the in-flight tick, never mid-write.
            logger.info("pm-scheduler: shutdown signalled; stopping after this tick")
            return
        if interruptible_sleep(interval):
            # Woken early by the shutdown signal: stop without another tick.
            logger.info("pm-scheduler: shutdown signalled during sleep; stopping")
            return
