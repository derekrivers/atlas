"""ATLAS-47: the `atlas pm report` delivery-metrics surface.

Two layers, mirroring tests/test_deps_cli.py: the CLI wiring is driven through
``main(["pm", "report", ...], database=db)`` against an in-memory SQLite
database seeded with tickets and DebtItems (asserting on capsys output and the
documented exit code), and the value-bearing computations — especially the
current-dwell proxy, which depends on ``now`` — go through the pure builder
``build_delivery_report`` with a fixed clock so every metric is deterministic.

Falsifiable, with the wrong answer named:

- anomaly counts match the stored DebtItems by type; a seeded extra row MOVES
  the count and a type with no rows reads 0 (miscount/omission fails);
- recurring anomalies are flagged via ``recurring`` (3 rows recur, 2 do not);
- ready-queue depth equals the ``ready_for_agent`` count; a seeded ready ticket
  moves it;
- throughput buckets ``done`` tickets by ISO week of ``status_entered_at``, with
  a null-entry ``done`` ticket landing in the ``unknown`` bucket, not dropped;
- the cycle-time section is the current-dwell proxy, LABELLED as such and never
  claimed historical, with null-entry tickets excluded from the durations;
- an empty database yields a well-formed zeroed report, not an error.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from test_debt_item_model import debt_item_kwargs
from test_models_validation import ticket_kwargs
from test_tick_failure_model import tick_failure_kwargs

from atlas.cli import EXIT_OK, main
from atlas.core.models import AnomalyType, DebtItem, Ticket, TickFailure
from atlas.pm import build_delivery_report, render_markdown, report_json
from atlas.storage import Database, DebtItemRepo, TicketRepo, TickFailureRepo

# A fixed clock for the current-dwell proxy: every duration below is measured
# against this instant, so min/median/max are deterministic.
NOW = datetime(2026, 6, 22, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{tmp_path}/atlas.db")
    database.create_all()
    return database


def make_ticket(key: str, **overrides: object) -> Ticket:
    return Ticket(**ticket_kwargs() | {"id": uuid4(), "key": key} | overrides)


def make_debt(
    ticket_id: UUID, anomaly_type: AnomalyType, **overrides: object
) -> DebtItem:
    return DebtItem(
        **debt_item_kwargs()
        | {"id": uuid4(), "ticket_id": ticket_id, "anomaly_type": anomaly_type}
        | overrides
    )


def seed_tickets(db: Database, tickets: list[Ticket]) -> None:
    repo = TicketRepo(db)
    for ticket in tickets:
        repo.add(ticket)


def seed_debt(db: Database, items: list[DebtItem]) -> None:
    repo = DebtItemRepo(db)
    for item in items:
        repo.record(item)


def make_failure(**overrides: object) -> TickFailure:
    return TickFailure(**tick_failure_kwargs() | {"id": uuid4()} | overrides)


def seed_failures(db: Database, items: list[TickFailure]) -> None:
    repo = TickFailureRepo(db)
    for item in items:
        repo.record(item)


# --- CLI wiring: all five sections render as markdown -----------------------


def test_report_markdown_has_all_five_sections(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    done = make_ticket(
        "ATLAS-1", status="done", status_entered_at=datetime(2026, 6, 17, tzinfo=UTC)
    )
    ready = make_ticket("ATLAS-2", status="ready_for_agent")
    in_flight = make_ticket(
        "ATLAS-3",
        status="in_progress",
        status_entered_at=datetime(2026, 6, 21, tzinfo=UTC),
    )
    seed_tickets(db, [done, ready, in_flight])
    seed_debt(db, [make_debt(in_flight.id, AnomalyType.DWELL_BREACH)])

    code = main(["pm", "report"], database=db)
    out = capsys.readouterr().out

    assert code == EXIT_OK
    assert "# Delivery metrics" in out
    assert "## Throughput (tickets done per week)" in out
    assert "## Current dwell per state" in out
    assert "## Ready-queue depth" in out
    assert "## Anomaly counts" in out
    assert "## Dwell breaches" in out
    # The proxy is labelled, never claimed historical (gap resolution).
    assert "**not** historical cycle time" in out


def test_report_json_parses_to_the_same_data(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    ready = make_ticket("ATLAS-2", status="ready_for_agent")
    done = make_ticket(
        "ATLAS-1", status="done", status_entered_at=datetime(2026, 6, 17, tzinfo=UTC)
    )
    seed_tickets(db, [ready, done])

    code = main(["pm", "report", "--json"], database=db)
    payload = json.loads(capsys.readouterr().out)

    assert code == EXIT_OK
    assert payload["ready_queue_depth"] == 1
    assert payload["throughput"] == [{"week": "2026-W25", "done_count": 1}]
    # Every anomaly type is present (none silently omitted), all zero here.
    types = {row["anomaly_type"]: row["count"] for row in payload["anomaly_counts"]}
    assert types == {kind.value: 0 for kind in AnomalyType}
    assert payload["dwell_breaches"] == []


# --- anomaly counts: by type, with the miscount/omission wrong answers named -


def test_anomaly_counts_match_stored_rows_and_a_seeded_row_moves_the_count(
    db: Database,
) -> None:
    a = make_ticket("ATLAS-1")
    b = make_ticket("ATLAS-2")
    seed_tickets(db, [a, b])
    # Two out-of-ownership rows for A (not recurring), three dwell breaches for B
    # (recurring); zero review-cycle rows.
    seed_debt(
        db,
        [
            make_debt(a.id, AnomalyType.OUT_OF_OWNERSHIP_TRANSITION),
            make_debt(a.id, AnomalyType.OUT_OF_OWNERSHIP_TRANSITION),
            make_debt(b.id, AnomalyType.DWELL_BREACH),
            make_debt(b.id, AnomalyType.DWELL_BREACH),
            make_debt(b.id, AnomalyType.DWELL_BREACH),
        ],
    )

    report = build_delivery_report(
        TicketRepo(db), DebtItemRepo(db), TickFailureRepo(db), now=NOW
    )
    counts = {c.anomaly_type: c for c in report.anomaly_counts}

    # By-type counts match exactly; the wrong answer miscounts or omits a type.
    assert counts[AnomalyType.OUT_OF_OWNERSHIP_TRANSITION.value].count == 2
    assert counts[AnomalyType.DWELL_BREACH.value].count == 3
    assert counts[AnomalyType.REVIEW_CYCLE.value].count == 0
    # Recurrence: the 3-row dwell breach recurs for one ticket; the 2-row one
    # does not.
    assert counts[AnomalyType.DWELL_BREACH.value].recurring_ticket_count == 1
    assert (
        counts[AnomalyType.OUT_OF_OWNERSHIP_TRANSITION.value].recurring_ticket_count
        == 0
    )

    # A seeded row moves exactly that type's count (and only it).
    seed_debt(db, [make_debt(a.id, AnomalyType.REVIEW_CYCLE)])
    moved = {
        c.anomaly_type: c
        for c in build_delivery_report(
            TicketRepo(db), DebtItemRepo(db), TickFailureRepo(db), now=NOW
        ).anomaly_counts
    }
    assert moved[AnomalyType.REVIEW_CYCLE.value].count == 1
    assert moved[AnomalyType.OUT_OF_OWNERSHIP_TRANSITION.value].count == 2


# --- ready-queue depth -----------------------------------------------------


def test_ready_queue_depth_equals_ready_for_agent_count(db: Database) -> None:
    seed_tickets(
        db,
        [
            make_ticket("ATLAS-1", status="ready_for_agent"),
            make_ticket("ATLAS-2", status="ready_for_agent"),
            make_ticket("ATLAS-3", status="in_progress"),
            make_ticket("ATLAS-4", status="backlog"),
        ],
    )

    depth = build_delivery_report(
        TicketRepo(db), DebtItemRepo(db), TickFailureRepo(db), now=NOW
    ).ready_queue_depth
    # The wrong answer counts non-ready tickets or misses a ready one.
    assert depth == 2

    seed_tickets(db, [make_ticket("ATLAS-5", status="ready_for_agent")])
    moved = build_delivery_report(
        TicketRepo(db), DebtItemRepo(db), TickFailureRepo(db), now=NOW
    ).ready_queue_depth
    assert moved == 3


# --- throughput ------------------------------------------------------------


def test_throughput_buckets_done_by_iso_week_with_unknown_for_null_entry(
    db: Database,
) -> None:
    seed_tickets(
        db,
        [
            # ISO week 25 of 2026 (Mon 2026-06-15 .. Sun 2026-06-21).
            make_ticket(
                "ATLAS-1",
                status="done",
                status_entered_at=datetime(2026, 6, 17, tzinfo=UTC),
            ),
            make_ticket(
                "ATLAS-2",
                status="done",
                status_entered_at=datetime(2026, 6, 19, tzinfo=UTC),
            ),
            # ISO week 24 (the week before).
            make_ticket(
                "ATLAS-3",
                status="done",
                status_entered_at=datetime(2026, 6, 10, tzinfo=UTC),
            ),
            # done but no entry time -> the unknown bucket, not dropped.
            make_ticket("ATLAS-4", status="done", status_entered_at=None),
            # not done -> not counted at all.
            make_ticket(
                "ATLAS-5",
                status="in_progress",
                status_entered_at=datetime(2026, 6, 18, tzinfo=UTC),
            ),
        ],
    )

    buckets = {
        b.week: b.done_count
        for b in build_delivery_report(
            TicketRepo(db), DebtItemRepo(db), TickFailureRepo(db), now=NOW
        ).throughput
    }
    assert buckets == {"2026-W24": 1, "2026-W25": 2, "unknown": 1}


# --- cycle-time proxy (the gap resolution) ---------------------------------


def test_cycle_time_is_current_dwell_proxy_excluding_unknown_entry(
    db: Database,
) -> None:
    seed_tickets(
        db,
        [
            # 24h and 12h in in_progress at NOW; median([24, 12]) == 18.
            make_ticket(
                "ATLAS-1",
                status="in_progress",
                status_entered_at=datetime(2026, 6, 21, 12, tzinfo=UTC),
            ),
            make_ticket(
                "ATLAS-2",
                status="in_progress",
                status_entered_at=datetime(2026, 6, 22, 0, tzinfo=UTC),
            ),
            # in_progress but unknown entry -> counted, excluded from durations.
            make_ticket("ATLAS-3", status="in_progress", status_entered_at=None),
            # terminal -> excluded from the in-flight proxy entirely.
            make_ticket(
                "ATLAS-4",
                status="done",
                status_entered_at=datetime(2026, 6, 1, tzinfo=UTC),
            ),
        ],
    )

    report = build_delivery_report(
        TicketRepo(db), DebtItemRepo(db), TickFailureRepo(db), now=NOW
    )
    stats = {s.status: s for s in report.dwell_per_state}

    # Only the non-terminal status appears.
    assert set(stats) == {"in_progress"}
    in_progress = stats["in_progress"]
    assert in_progress.ticket_count == 3
    assert in_progress.measured_count == 2
    assert in_progress.unknown_entry_count == 1
    # The wrong answer folds the null-entry ticket into the durations (0h) and
    # drags min/median down.
    assert in_progress.min_hours == 12
    assert in_progress.median_hours == 18
    assert in_progress.max_hours == 24


# --- dwell breaches surface per ticket -------------------------------------


def test_dwell_breaches_surface_per_ticket_with_recurrence(db: Database) -> None:
    a = make_ticket("ATLAS-1")
    b = make_ticket("ATLAS-2")
    seed_tickets(db, [a, b])
    seed_debt(
        db,
        [make_debt(a.id, AnomalyType.DWELL_BREACH)]
        + [make_debt(b.id, AnomalyType.DWELL_BREACH) for _ in range(3)],
    )

    report = build_delivery_report(
        TicketRepo(db), DebtItemRepo(db), TickFailureRepo(db), now=NOW
    )
    breaches = {breach.ticket_key: breach for breach in report.dwell_breaches}

    assert breaches["ATLAS-1"].count == 1
    assert breaches["ATLAS-1"].recurring is False
    assert breaches["ATLAS-2"].count == 3
    assert breaches["ATLAS-2"].recurring is True


# --- empty DB: a well-formed zeroed report, not an error -------------------


def test_empty_db_yields_zeroed_report(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["pm", "report", "--json"], database=db)
    payload = json.loads(capsys.readouterr().out)

    assert code == EXIT_OK
    assert payload["throughput"] == []
    assert payload["dwell_per_state"] == []
    assert payload["ready_queue_depth"] == 0
    assert payload["dwell_breaches"] == []
    # No tick failures recorded -> zero, not an error (ATLAS-125).
    assert payload["tick_failure_count"] == 0
    # Every anomaly type is still present, each zero — absence is meaningful.
    assert all(row["count"] == 0 for row in payload["anomaly_counts"])
    assert {row["anomaly_type"] for row in payload["anomaly_counts"]} == {
        kind.value for kind in AnomalyType
    }


def test_empty_db_markdown_is_well_formed(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["pm", "report"], database=db)
    out = capsys.readouterr().out

    assert code == EXIT_OK
    # Each section states its zero state in prose rather than crashing or
    # emitting an empty table.
    assert "No tickets are done yet." in out
    assert "No in-flight tickets." in out
    assert "0 ticket(s) in `ready_for_agent`." in out
    assert "No dwell breaches recorded." in out
    assert "0 recorded PM-scheduler tick failure(s)." in out


# --- tick failures: a recorded crash moves the count (ATLAS-125) ------------


def test_tick_failure_count_is_total_of_stored_rows(db: Database) -> None:
    # Zero on an empty DB; each recorded crash moves the count by one,
    # regardless of signature (the report surfaces a total, not a per-signature
    # breakdown). Pure reader: no row is written by the report.
    assert (
        build_delivery_report(
            TicketRepo(db), DebtItemRepo(db), TickFailureRepo(db), now=NOW
        ).tick_failure_count
        == 0
    )

    seed_failures(
        db,
        [
            make_failure(failure_signature="LinearTimeoutError@sync_tick.pull"),
            make_failure(failure_signature="LinearTimeoutError@sync_tick.pull"),
            make_failure(failure_signature="ValueError@sync_tick.promote"),
        ],
    )
    assert (
        build_delivery_report(
            TicketRepo(db), DebtItemRepo(db), TickFailureRepo(db), now=NOW
        ).tick_failure_count
        == 3
    )


def test_tick_failure_count_surfaces_in_json_and_markdown(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_failures(db, [make_failure(), make_failure()])

    code = main(["pm", "report", "--json"], database=db)
    payload = json.loads(capsys.readouterr().out)
    assert code == EXIT_OK
    assert payload["tick_failure_count"] == 2

    report = build_delivery_report(
        TicketRepo(db), DebtItemRepo(db), TickFailureRepo(db), now=NOW
    )
    assert "## Tick failures" in render_markdown(report)
    assert "2 recorded PM-scheduler tick failure(s)." in render_markdown(report)
    assert report_json(report)["tick_failure_count"] == 2
