"""Continuous learning scheduler for automatic lesson extraction (ATLAS-106).

The scheduler is the deterministic recurring loop for learning-system.md's
automatic extraction triggers. It polls stored tickets and PM failure-analysis
DebtItems, identifies tickets whose extraction cursor is stale, calls the
learning extractor, then stamps ``Ticket.lesson_extraction_attempted_at`` so the
same ticket is not re-extracted on every tick.

Only :func:`atlas.learning.extractor.extract_lesson_for_ticket` may call a model.
This module decides *when* to call it, logs per-ticket failures, and keeps the
plain-loop cadence plus graceful shutdown shape used by the PM scheduler.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from atlas.core.models.debt_item import AnomalyType, DebtItem
from atlas.core.models.lesson import Lesson
from atlas.core.models.ticket import Ticket, TicketStatus
from atlas.learning.extractor import (
    ExtractionTrigger,
    LessonModelClient,
    extract_lesson_for_ticket,
)
from atlas.storage.db import Database
from atlas.storage.repositories import DebtItemRepo, TicketRepo

logger = logging.getLogger("atlas.learning.scheduler")

DEFAULT_INTERVAL_SECONDS = 300
PM_FAILURE_ANALYSIS_TYPES = frozenset(
    {AnomalyType.DWELL_BREACH, AnomalyType.REVIEW_CYCLE}
)


def _utcnow() -> datetime:
    """Production clock: a fresh timezone-aware UTC instant per poll cycle."""

    return datetime.now(UTC)


class LessonExtractor(Protocol):
    """Callable seam for tests; production uses ``extract_lesson_for_ticket``."""

    def __call__(
        self,
        ticket: Ticket,
        *,
        db: Database,
        client: LessonModelClient | None,
        now: datetime,
        trigger: ExtractionTrigger,
        failure_event: DebtItem | None = None,
        force: bool = False,
    ) -> Lesson | None: ...


@dataclass(frozen=True)
class LessonSchedulerConfig:
    """Injected scheduler dependencies.

    ``tickets`` supplies both the polling read and the sole write to
    ``lesson_extraction_attempted_at``. ``debt_items`` is read-only here.
    ``client`` is passed through to the extractor; the scheduler never calls it
    directly. ``extractor`` is injectable so unit tests can prove loop behaviour
    without invoking a model or prompt rendering.
    """

    db: Database
    tickets: TicketRepo
    debt_items: DebtItemRepo
    client: LessonModelClient | None
    extractor: LessonExtractor = extract_lesson_for_ticket


@dataclass(frozen=True)
class ScheduledExtraction:
    """One extraction the current poll cycle should attempt."""

    ticket: Ticket
    trigger: ExtractionTrigger
    failure_event: DebtItem | None = None


def _latest_pm_failure_event(
    ticket: Ticket, debt_items: Sequence[DebtItem]
) -> DebtItem | None:
    last_attempt = ticket.lesson_extraction_attempted_at
    candidates = [
        item
        for item in debt_items
        if item.ticket_id == ticket.id
        and item.anomaly_type in PM_FAILURE_ANALYSIS_TYPES
        and (last_attempt is None or item.created_at > last_attempt)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item.created_at, str(item.id)))


def _status_trigger(ticket: Ticket) -> ExtractionTrigger | None:
    if ticket.lesson_extraction_attempted_at is not None:
        return None
    if ticket.status is TicketStatus.DONE:
        return ExtractionTrigger.DONE
    if ticket.status is TicketStatus.REJECTED:
        return ExtractionTrigger.REJECTED
    return None


def find_tickets_needing_extraction(
    tickets: Sequence[Ticket], debt_items: Sequence[DebtItem]
) -> list[ScheduledExtraction]:
    """Return the deterministic extraction worklist for one poll cycle.

    PM failure-analysis events take precedence for a ticket because they are a
    specific failure trigger and force extraction. Otherwise, terminal
    ``done``/``rejected`` tickets with no recorded attempt are selected by
    status. Results are key-ordered for stable logs and tests.
    """

    work: list[ScheduledExtraction] = []
    for ticket in sorted(tickets, key=lambda item: item.key):
        failure_event = _latest_pm_failure_event(ticket, debt_items)
        if failure_event is not None:
            work.append(
                ScheduledExtraction(
                    ticket=ticket,
                    trigger=ExtractionTrigger.PM_FAILURE_ANALYSIS,
                    failure_event=failure_event,
                )
            )
            continue
        trigger = _status_trigger(ticket)
        if trigger is not None:
            work.append(ScheduledExtraction(ticket=ticket, trigger=trigger))
    return work


def run_poll_cycle(
    config: LessonSchedulerConfig, *, now: datetime
) -> list[ScheduledExtraction]:
    """Run exactly one learning-scheduler poll cycle.

    A failed extraction for one ticket is logged and isolated; the scheduler
    still stamps that ticket's attempt cursor and continues processing the rest
    of the worklist. Returning the attempted worklist gives tests and future
    callers a cheap audit surface without reading logs.
    """

    attempted: list[ScheduledExtraction] = []
    work = find_tickets_needing_extraction(
        config.tickets.list(), config.debt_items.list()
    )
    for item in work:
        try:
            config.extractor(
                item.ticket,
                db=config.db,
                client=config.client,
                now=now,
                trigger=item.trigger,
                failure_event=item.failure_event,
                force=item.trigger is ExtractionTrigger.PM_FAILURE_ANALYSIS,
            )
        except Exception as error:
            logger.warning(
                "lesson-scheduler: extraction failed for %s (%s): %s",
                item.ticket.key,
                type(error).__name__,
                error,
            )
        finally:
            config.tickets.mark_lesson_extraction_attempted(
                item.ticket.key, attempted_at=now
            )
        attempted.append(item)
    return attempted


def run_scheduler(
    config: LessonSchedulerConfig,
    *,
    interval: float = DEFAULT_INTERVAL_SECONDS,
    once: bool = False,
    now: Callable[[], datetime] = _utcnow,
    shutdown: threading.Event | None = None,
    sleep: Callable[[float], bool] | None = None,
) -> None:
    """Drive lesson extraction polling on a cadence until shutdown.

    ``--once`` runs one poll cycle and exits. Otherwise the loop checks the
    shutdown event only after a cycle returns, so SIGTERM/SIGINT finish the
    in-flight cycle before stopping. ``sleep`` is interruptible and injectable;
    by default it is ``shutdown.wait``.
    """

    shutdown = shutdown if shutdown is not None else threading.Event()
    interruptible_sleep = sleep if sleep is not None else shutdown.wait

    logger.info(
        "lesson-scheduler: starting (%s)",
        "single poll (--once)" if once else f"interval {interval}s",
    )
    while True:
        run_poll_cycle(config, now=now())
        if once:
            return
        if shutdown.is_set():
            logger.info(
                "lesson-scheduler: shutdown signalled; stopping after this cycle"
            )
            return
        if interruptible_sleep(interval):
            logger.info("lesson-scheduler: shutdown signalled during sleep; stopping")
            return
