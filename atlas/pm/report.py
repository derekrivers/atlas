"""PM-Engine delivery metrics (ATLAS-47).

The read side of the Phase 4 anomaly stack and ticket lifecycle: a PURE
READER that surfaces what the PM Engine has already recorded as the five
``pm-engine-and-linear-sync.md`` "Delivery metrics" — throughput, cycle time
per state, ready-queue depth, anomaly counts, and dwell breaches — for the
``atlas pm report`` CLI to render as markdown or ``--json``.

It makes NO Linear calls and writes NOTHING. Every metric is computed from
stored ``Ticket``s and ``DebtItem``s via ``TicketRepo``/``DebtItemRepo``
reads only — never from the per-tick, ephemeral ``SyncResult`` (which is not
persisted) — so it runs with no network and no secrets.

:func:`build_delivery_report` is a pure builder: it takes ``now`` explicitly
(the CLI boundary supplies ``datetime.now(UTC)``), so every metric is
deterministic under a fixed clock. :func:`render_markdown` and
:func:`report_json` are the two presentations the CLI emits.

The cycle-time gap (resolved at the ATLAS-47 gate): the data model carries
only ``Ticket.status_entered_at`` (entry into the *current* status) and no
transition history, so historical per-state cycle time is not computable. v1
reports the computable proxy — current time-in-state for in-flight
(non-terminal) tickets, aggregated per state — labelled *current dwell per
state*, NEVER claimed as historical cycle time. A ticket with a null
``status_entered_at`` (unknown entry) is excluded from the durations and
counted separately. The true metric needs an append-only transition log the
model does not yet carry; that schema is deferred to ATLAS-121 and is NOT
added here.

Layering: this is ``atlas.pm``, above ``atlas.storage``/``atlas.core`` in the
import spine — it reads ``TicketRepo``/``DebtItemRepo``, so it imports
downward only and the import-linter stays green.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from statistics import median
from uuid import UUID

from atlas.core.models.debt_item import AnomalyType, DebtItem
from atlas.core.models.ticket import Ticket, TicketStatus
from atlas.dependencies.validation import TERMINAL_STATUSES
from atlas.storage.repositories import DebtItemRepo, TicketRepo, TickFailureRepo

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
class DwellStat:
    """The current-dwell proxy for one non-terminal status (ATLAS-47 gap).

    ``ticket_count`` is every in-flight ticket in this status; the min/median/
    max hours are computed over only those with a known ``status_entered_at``
    (``measured_count`` of them). ``unknown_entry_count`` are the rest, carved
    out of the durations rather than guessed. When nothing is measurable the
    three hour figures are ``None``. This is *current dwell*, NOT historical
    cycle time."""

    status: str
    ticket_count: int
    measured_count: int
    unknown_entry_count: int
    min_hours: float | None
    median_hours: float | None
    max_hours: float | None


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
    dwell_per_state: list[DwellStat]
    ready_queue_depth: int
    anomaly_counts: list[AnomalyCount]
    dwell_breaches: list[DwellBreach]
    tick_failure_count: int


def _hours_between(now: datetime, entered: datetime) -> float:
    """Whole hours (to two decimals) a ticket has dwelt in its current state."""
    return round((now - entered).total_seconds() / 3600, 2)


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


def _dwell_per_state(tickets: list[Ticket], *, now: datetime) -> list[DwellStat]:
    """The current-dwell proxy: per non-terminal status, the current
    time-in-state of its in-flight tickets (ATLAS-47 gap resolution).

    Terminal statuses (``done``/``rejected``) are excluded — they are not
    in-flight. Tickets with a null ``status_entered_at`` are counted but kept
    out of the duration aggregates."""
    by_status: dict[str, list[Ticket]] = {}
    for ticket in tickets:
        if ticket.status.value in TERMINAL_STATUSES:
            continue
        by_status.setdefault(ticket.status.value, []).append(ticket)

    stats: list[DwellStat] = []
    for status in sorted(by_status):
        group = by_status[status]
        durations = [
            _hours_between(now, ticket.status_entered_at)
            for ticket in group
            if ticket.status_entered_at is not None
        ]
        unknown = len(group) - len(durations)
        stats.append(
            DwellStat(
                status=status,
                ticket_count=len(group),
                measured_count=len(durations),
                unknown_entry_count=unknown,
                min_hours=min(durations) if durations else None,
                median_hours=round(median(durations), 2) if durations else None,
                max_hours=max(durations) if durations else None,
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
    *,
    now: datetime,
) -> DeliveryReport:
    """Compute the five delivery metrics plus the tick-failure count from
    stored state (ATLAS-47; tick failures ATLAS-125).

    A pure builder: it performs read-only
    ``TicketRepo``/``DebtItemRepo``/``TickFailureRepo`` queries, takes ``now``
    explicitly so the current-dwell proxy is deterministic, and returns a
    :class:`DeliveryReport`. It writes nothing and makes no Linear call. An
    empty database yields a well-formed, fully zeroed report (empty lists, a
    zero ready-queue depth, and a zero tick-failure count), never an error.
    """
    tickets = ticket_repo.list()
    debt_items = debt_repo.list()
    ready_depth = sum(
        1 for ticket in tickets if ticket.status is TicketStatus.READY_FOR_AGENT
    )
    return DeliveryReport(
        generated_at=now,
        throughput=_throughput(tickets),
        dwell_per_state=_dwell_per_state(tickets, now=now),
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
        "dwell_per_state": [
            {
                "status": stat.status,
                "ticket_count": stat.ticket_count,
                "measured_count": stat.measured_count,
                "unknown_entry_count": stat.unknown_entry_count,
                "min_hours": stat.min_hours,
                "median_hours": stat.median_hours,
                "max_hours": stat.max_hours,
            }
            for stat in report.dwell_per_state
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

    # 2. Cycle time per state — the current-dwell proxy (NOT historical).
    lines.append("## Current dwell per state")
    lines.append("")
    lines.append(
        "> v1 proxy: current time-in-state for in-flight tickets, **not** "
        "historical cycle time — the model carries no transition history "
        "(deferred to ATLAS-121). Tickets with an unknown entry time are "
        "counted but excluded from the hour figures."
    )
    lines.append("")
    if report.dwell_per_state:
        lines.append(
            "| State | Tickets | Measured | Unknown entry | "
            "Min (h) | Median (h) | Max (h) |"
        )
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        for stat in report.dwell_per_state:
            lines.append(
                f"| {stat.status} | {stat.ticket_count} | {stat.measured_count} "
                f"| {stat.unknown_entry_count} | {_hours(stat.min_hours)} "
                f"| {_hours(stat.median_hours)} | {_hours(stat.max_hours)} |"
            )
    else:
        lines.append("No in-flight tickets.")
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
