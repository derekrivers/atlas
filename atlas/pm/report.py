"""PM-Engine delivery metrics (ATLAS-47).

The read side of the Phase 4 anomaly stack and ticket lifecycle: a PURE
READER that surfaces what the PM Engine has already recorded as the five
``pm-engine-and-linear-sync.md`` "Delivery metrics" — throughput, cycle time
per state, ready-queue depth, anomaly counts, and dwell breaches — for the
``atlas pm report`` CLI to render as markdown or ``--json``.

It makes NO Linear calls and writes NOTHING. Every metric is computed from
stored ``Ticket``s, ``DebtItem``s, and ``TicketStatusTransition``s via
``TicketRepo``/``DebtItemRepo``/``TicketStatusTransitionRepo`` reads only —
never from the per-tick, ephemeral ``SyncResult`` (which is not persisted) —
so it runs with no network and no secrets.

:func:`build_delivery_report` is a pure builder: it takes ``now`` explicitly
(the CLI boundary supplies ``datetime.now(UTC)``), so every metric is
deterministic under a fixed clock. :func:`render_markdown` and
:func:`report_json` are the two presentations the CLI emits.

Cycle time per state (the gap ATLAS-47 deferred, closed by ATLAS-121/126):
``apply_linear_status`` now appends a ``TicketStatusTransition`` on every real
status change, so a durable transition log accumulates and true historical
cycle time is computable — deterministic timestamp subtraction over recorded
transitions, no judgement (ADR-0005 satisfied by construction: nothing here
assigns a value, it measures one). Cycle time is measured over *completed
episodes* only: for a ticket's ordered transitions ``T1..Tn``, episode ``i``
(``i`` in ``1..n-1``) is the state ``to_i`` entered at ``t_i`` and exited at
``t_{i+1}``, its duration ``t_{i+1} - t_i``. Two episodes are deliberately not
counted — the initial state before ``T1`` has no recorded entry, and the
current state after ``Tn`` has no recorded exit (the open episode, i.e. the
current-dwell the retired ATLAS-47 proxy reported). Each completed episode is
one data point, so a state re-visited N times contributes N episodes; a state
with no completed episodes simply does not appear.

Layering: this is ``atlas.pm``, above ``atlas.storage``/``atlas.core`` in the
import spine — it reads
``TicketRepo``/``DebtItemRepo``/``TickFailureRepo``/``TicketStatusTransitionRepo``,
so it imports downward only and the import-linter stays green.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise
from statistics import median
from uuid import UUID

from atlas.core.models.debt_item import AnomalyType, DebtItem
from atlas.core.models.ticket import Ticket, TicketStatus
from atlas.core.models.ticket_status_transition import TicketStatusTransition
from atlas.storage.repositories import (
    DebtItemRepo,
    TicketRepo,
    TicketStatusTransitionRepo,
    TickFailureRepo,
)

# Default recurrence threshold, mirroring DebtItemRepo.recurring's default and
# pm-engine-and-linear-sync.md ("three or more rows for the same ticket and
# anomaly type"). The report calls out anomalies that meet it per type.
RECURRENCE_THRESHOLD = 3

# Bucket label for `done` tickets whose `status_entered_at` is null (unknown
# completion week) and for any other unknown-entry grouping.
UNKNOWN_BUCKET = "unknown"


@dataclass(frozen=True)
class ThroughputBucket:
    """Tickets that reached ``done`` in one ISO week (``YYYY-Www``), or the
    ``unknown`` bucket for a ``done`` ticket with a null ``status_entered_at``."""

    week: str
    done_count: int


@dataclass(frozen=True)
class CycleTimeStat:
    """Historical cycle time for one status (ATLAS-126), over completed
    episodes drawn from the ``TicketStatusTransition`` log.

    ``episode_count`` is how many completed episodes this status accrued across
    all tickets (a re-visited state contributes one per visit); the min/median/
    max hours are computed over exactly those episodes. Every completed episode
    has both a recorded entry and exit by construction, so there is nothing
    unmeasurable to carve out and the three hour figures are always populated —
    a status only appears here when it has at least one completed episode."""

    status: str
    episode_count: int
    min_hours: float
    median_hours: float
    max_hours: float


@dataclass(frozen=True)
class AnomalyCount:
    """``DebtItem``s of one ``AnomalyType``: the total row count and the number
    of distinct tickets for which that type recurs (>= ``RECURRENCE_THRESHOLD``
    rows, via ``DebtItemRepo.recurring``)."""

    anomaly_type: str
    count: int
    recurring_ticket_count: int


@dataclass(frozen=True)
class DwellBreach:
    """The ``DWELL_BREACH`` subset (ATLAS-119), per ticket: how many breach
    rows the ticket carries and whether the type recurs for it."""

    ticket_key: str
    count: int
    recurring: bool


@dataclass(frozen=True)
class DeliveryReport:
    """The five delivery metrics at ``generated_at`` (the injected ``now``),
    plus the PM-scheduler tick-failure count (ATLAS-125)."""

    generated_at: datetime
    throughput: list[ThroughputBucket]
    cycle_time_per_state: list[CycleTimeStat]
    ready_queue_depth: int
    anomaly_counts: list[AnomalyCount]
    dwell_breaches: list[DwellBreach]
    tick_failure_count: int


def _hours_between(later: datetime, earlier: datetime) -> float:
    """Hours (to two decimals) between two instants — the single duration
    helper, so every time metric measures the same way."""
    return round((later - earlier).total_seconds() / 3600, 2)


def _throughput(tickets: list[Ticket]) -> list[ThroughputBucket]:
    """``done`` tickets bucketed by the ISO week of ``status_entered_at``.

    A ``done`` ticket with a null entry time falls in the ``unknown`` bucket
    rather than being dropped, so the count never silently undercounts."""
    counts: dict[str, int] = {}
    for ticket in tickets:
        if ticket.status is not TicketStatus.DONE:
            continue
        entered = ticket.status_entered_at
        if entered is None:
            week = UNKNOWN_BUCKET
        else:
            iso = entered.isocalendar()
            week = f"{iso.year}-W{iso.week:02d}"
        counts[week] = counts.get(week, 0) + 1
    # Real weeks ascending, then the unknown bucket last (it sorts after any
    # YYYY-Www token lexically, but pin the order explicitly rather than rely
    # on that).
    real = sorted(week for week in counts if week != UNKNOWN_BUCKET)
    ordered = real + ([UNKNOWN_BUCKET] if UNKNOWN_BUCKET in counts else [])
    return [ThroughputBucket(week=week, done_count=counts[week]) for week in ordered]


def _cycle_time_per_state(
    transitions: list[TicketStatusTransition],
) -> list[CycleTimeStat]:
    """Historical per-state cycle time over completed episodes (ATLAS-126).

    ``transitions`` is the whole log, already ordered by ticket then
    ``occurred_at`` then id (``TicketStatusTransitionRepo.list_all``), so this
    groups by ticket in memory and walks each ticket's transitions in that
    order. For a ticket's ``T1..Tn``, episode ``i`` (``i`` in ``1..n-1``) is the
    state ``to_i`` entered at ``t_i`` and exited at the next transition's
    ``t_{i+1}``; its duration is ``t_{i+1} - t_i``. The initial state (before
    ``T1``, no recorded entry) and the current open episode (after ``Tn``, no
    recorded exit) are NOT counted. Each completed episode is one data point, so
    a re-visited state accrues one per visit. A status with no completed episodes
    does not appear."""
    by_ticket: dict[UUID, list[TicketStatusTransition]] = defaultdict(list)
    for transition in transitions:
        by_ticket[transition.ticket_id].append(transition)

    durations_by_state: dict[str, list[float]] = defaultdict(list)
    for ticket_transitions in by_ticket.values():
        for current, following in pairwise(ticket_transitions):
            durations_by_state[current.to_status].append(
                _hours_between(following.occurred_at, current.occurred_at)
            )

    stats: list[CycleTimeStat] = []
    for status in sorted(durations_by_state):
        durations = durations_by_state[status]
        stats.append(
            CycleTimeStat(
                status=status,
                episode_count=len(durations),
                min_hours=min(durations),
                median_hours=round(median(durations), 2),
                max_hours=max(durations),
            )
        )
    return stats


def _anomaly_counts(
    debt_items: list[DebtItem], debt_repo: DebtItemRepo
) -> list[AnomalyCount]:
    """``DebtItem``s grouped by ``AnomalyType``, with recurring tickets called
    out per type via ``DebtItemRepo.recurring``.

    Every ``AnomalyType`` is reported (a zero count is meaningful — it says the
    type has been observed nowhere), so a stored row of any type always moves a
    visible count and none can be silently omitted."""
    tickets_by_type: dict[str, set[UUID]] = {kind.value: set() for kind in AnomalyType}
    counts: dict[str, int] = {kind.value: 0 for kind in AnomalyType}
    for item in debt_items:
        key = item.anomaly_type.value
        counts[key] += 1
        tickets_by_type[key].add(item.ticket_id)

    result: list[AnomalyCount] = []
    for kind in AnomalyType:
        recurring = sum(
            1
            for ticket_id in tickets_by_type[kind.value]
            if debt_repo.recurring(ticket_id, kind, threshold=RECURRENCE_THRESHOLD)
        )
        result.append(
            AnomalyCount(
                anomaly_type=kind.value,
                count=counts[kind.value],
                recurring_ticket_count=recurring,
            )
        )
    return result


def _dwell_breaches(
    debt_items: list[DebtItem],
    tickets: list[Ticket],
    debt_repo: DebtItemRepo,
) -> list[DwellBreach]:
    """The ``DWELL_BREACH`` subset (ATLAS-119), one entry per breached ticket,
    ticket-key ordered. A ticket id with no stored ticket is rendered by its
    raw id so a breach is never dropped for a missing key."""
    key_by_id = {ticket.id: ticket.key for ticket in tickets}
    counts: dict[UUID, int] = {}
    for item in debt_items:
        if item.anomaly_type is AnomalyType.DWELL_BREACH:
            counts[item.ticket_id] = counts.get(item.ticket_id, 0) + 1

    breaches = [
        DwellBreach(
            ticket_key=key_by_id.get(ticket_id, str(ticket_id)),
            count=count,
            recurring=debt_repo.recurring(
                ticket_id, AnomalyType.DWELL_BREACH, threshold=RECURRENCE_THRESHOLD
            ),
        )
        for ticket_id, count in counts.items()
    ]
    return sorted(breaches, key=lambda breach: breach.ticket_key)


def build_delivery_report(
    ticket_repo: TicketRepo,
    debt_repo: DebtItemRepo,
    tick_failure_repo: TickFailureRepo,
    transition_repo: TicketStatusTransitionRepo,
    *,
    now: datetime,
) -> DeliveryReport:
    """Compute the five delivery metrics plus the tick-failure count from
    stored state (ATLAS-47; tick failures ATLAS-125; historical cycle time
    ATLAS-126).

    A pure builder: it performs read-only
    ``TicketRepo``/``DebtItemRepo``/``TickFailureRepo``/``TicketStatusTransitionRepo``
    queries, takes ``now`` explicitly so every metric is deterministic, and
    returns a :class:`DeliveryReport`. It writes nothing and makes no Linear
    call. An empty database yields a well-formed, fully zeroed report (empty
    lists, a zero ready-queue depth, and a zero tick-failure count), never an
    error.
    """
    tickets = ticket_repo.list()
    debt_items = debt_repo.list()
    ready_depth = sum(
        1 for ticket in tickets if ticket.status is TicketStatus.READY_FOR_AGENT
    )
    return DeliveryReport(
        generated_at=now,
        throughput=_throughput(tickets),
        cycle_time_per_state=_cycle_time_per_state(transition_repo.list_all()),
        ready_queue_depth=ready_depth,
        anomaly_counts=_anomaly_counts(debt_items, debt_repo),
        dwell_breaches=_dwell_breaches(debt_items, tickets, debt_repo),
        tick_failure_count=len(tick_failure_repo.list()),
    )


def report_json(report: DeliveryReport) -> dict[str, object]:
    """The ``--json`` form: the same data the markdown carries, as a JSON-ready
    dict (the CLI hands it to ``json.dumps``)."""
    return {
        "generated_at": report.generated_at.isoformat(),
        "throughput": [
            {"week": bucket.week, "done_count": bucket.done_count}
            for bucket in report.throughput
        ],
        "cycle_time_per_state": [
            {
                "status": stat.status,
                "episode_count": stat.episode_count,
                "min_hours": stat.min_hours,
                "median_hours": stat.median_hours,
                "max_hours": stat.max_hours,
            }
            for stat in report.cycle_time_per_state
        ],
        "ready_queue_depth": report.ready_queue_depth,
        "anomaly_counts": [
            {
                "anomaly_type": count.anomaly_type,
                "count": count.count,
                "recurring_ticket_count": count.recurring_ticket_count,
            }
            for count in report.anomaly_counts
        ],
        "dwell_breaches": [
            {
                "ticket_key": breach.ticket_key,
                "count": breach.count,
                "recurring": breach.recurring,
            }
            for breach in report.dwell_breaches
        ],
        "tick_failure_count": report.tick_failure_count,
    }


def _hours(value: float | None) -> str:
    return "—" if value is None else f"{value:g}"


def render_markdown(report: DeliveryReport) -> str:
    """Render the five metrics as markdown (Revision 1: CLI/markdown, no
    dashboard). An empty database still renders every section, each stating its
    zero state in prose rather than an empty table."""
    lines: list[str] = ["# Delivery metrics", ""]
    lines.append(
        f"_Generated {report.generated_at.isoformat()} — read-only; computed "
        "from stored tickets and DebtItems (no Linear calls, no writes)._"
    )
    lines.append("")

    # 1. Throughput.
    lines.append("## Throughput (tickets done per week)")
    lines.append("")
    if report.throughput:
        lines.append("| Week | Done |")
        lines.append("| --- | --- |")
        for bucket in report.throughput:
            lines.append(f"| {bucket.week} | {bucket.done_count} |")
    else:
        lines.append("No tickets are done yet.")
    lines.append("")

    # 2. Cycle time per state — historical, from the transition log (ATLAS-126).
    lines.append("## Cycle time per state (historical)")
    lines.append("")
    lines.append(
        "> Historical per-state cycle time over **completed episodes** from the "
        "`TicketStatusTransition` log (ATLAS-121/126). An episode is a state "
        "entered and later exited; the initial state before the first recorded "
        "transition (no recorded entry) and the current open episode after the "
        "last (no recorded exit) are **not** counted. A state re-visited N times "
        "contributes N episodes."
    )
    lines.append("")
    if report.cycle_time_per_state:
        lines.append("| State | Episodes | Min (h) | Median (h) | Max (h) |")
        lines.append("| --- | --- | --- | --- | --- |")
        for stat in report.cycle_time_per_state:
            lines.append(
                f"| {stat.status} | {stat.episode_count} | {_hours(stat.min_hours)} "
                f"| {_hours(stat.median_hours)} | {_hours(stat.max_hours)} |"
            )
    else:
        lines.append("No completed cycles recorded.")
    lines.append("")

    # 3. Ready-queue depth.
    lines.append("## Ready-queue depth")
    lines.append("")
    lines.append(f"{report.ready_queue_depth} ticket(s) in `ready_for_agent`.")
    lines.append("")

    # 4. Anomaly counts.
    lines.append("## Anomaly counts")
    lines.append("")
    lines.append("| Type | Count | Recurring tickets |")
    lines.append("| --- | --- | --- |")
    for count in report.anomaly_counts:
        lines.append(
            f"| {count.anomaly_type} | {count.count} | {count.recurring_ticket_count} |"
        )
    lines.append("")

    # 5. Dwell breaches.
    lines.append("## Dwell breaches")
    lines.append("")
    if report.dwell_breaches:
        lines.append("| Ticket | Breaches | Recurring |")
        lines.append("| --- | --- | --- |")
        for breach in report.dwell_breaches:
            recurring = "yes" if breach.recurring else "no"
            lines.append(f"| {breach.ticket_key} | {breach.count} | {recurring} |")
    else:
        lines.append("No dwell breaches recorded.")
    lines.append("")

    # 6. Tick failures (ATLAS-125): PM-scheduler crashes recorded by the
    # create-on-crash path (the writer is ATLAS-50).
    lines.append("## Tick failures")
    lines.append("")
    lines.append(f"{report.tick_failure_count} recorded PM-scheduler tick failure(s).")
    lines.append("")

    return "\n".join(lines)
