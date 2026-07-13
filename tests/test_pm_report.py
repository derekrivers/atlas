"""ATLAS-47: the `atlas pm report` delivery-metrics surface.

Two layers, mirroring tests/test_deps_cli.py: the CLI wiring is driven through
``main(["pm", "report", ...], database=db)`` against an in-memory SQLite
database seeded with tickets, DebtItems, and status transitions (asserting on
capsys output and the documented exit code), and the value-bearing computations
— especially historical cycle time, computed from the transition log — go
through the pure builder ``build_delivery_report`` with a fixed clock so every
metric is deterministic.

Falsifiable, with the wrong answer named:

- anomaly counts match the stored DebtItems by type; a seeded extra row MOVES
  the count and a type with no rows reads 0 (miscount/omission fails);
- recurring anomalies are flagged via ``recurring`` (3 rows recur, 2 do not);
- ready-queue depth equals the ``ready_for_agent`` count; a seeded ready ticket
  moves it;
- throughput buckets ``done`` tickets by ISO week of ``status_entered_at``, with
  a null-entry ``done`` ticket landing in the ``unknown`` bucket, not dropped;
- cycle time per state is historical, over completed episodes from the
  transition log: the initial state and the open current episode are NOT
  counted, re-visits each count, and the section is labelled historical (the
  current-dwell proxy is gone);
- an empty database yields a well-formed zeroed report, not an error.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from test_debt_item_model import debt_item_kwargs
from test_lesson_model import lesson_kwargs
from test_models_validation import ticket_kwargs
from test_tick_failure_model import tick_failure_kwargs
from test_ticket_status_transition_model import transition_kwargs

from atlas.cli import EXIT_OK, main
from atlas.core.models import (
    AgentProvider,
    AgentRun,
    AgentRunStatus,
    AnomalyType,
    DebtItem,
    Lesson,
    Ticket,
    TicketStatusTransition,
    TickFailure,
)
from atlas.pm import build_delivery_report, render_markdown, report_json
from atlas.storage import (
    AgentRunRepo,
    Database,
    DebtItemRepo,
    LessonRepo,
    TicketRepo,
    TicketStatusTransitionRepo,
    TickFailureRepo,
)

# A fixed clock for the report's `generated_at`. Historical cycle time is
# computed from recorded transition instants, so it does not depend on `now`;
# the throughput/breach metrics still take it for determinism.
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


def make_lesson(**overrides: object) -> Lesson:
    return Lesson(**lesson_kwargs() | {"id": uuid4()} | overrides)


def seed_lessons(db: Database, items: list[Lesson]) -> None:
    repo = LessonRepo(db)
    for item in items:
        repo.add(item)


def make_failure(**overrides: object) -> TickFailure:
    return TickFailure(**tick_failure_kwargs() | {"id": uuid4()} | overrides)


def seed_failures(db: Database, items: list[TickFailure]) -> None:
    repo = TickFailureRepo(db)
    for item in items:
        repo.record(item)


def make_agent_run(**overrides: object) -> AgentRun:
    data: dict[str, Any] = {
        "id": uuid4(),
        "product_id": uuid4(),
        "ticket_id": uuid4(),
        "provider": AgentProvider.SYMPHONY,
        "status": AgentRunStatus.SUCCEEDED,
        "objective": "Implement a ticket.",
        "started_at": NOW,
        "completed_at": NOW + timedelta(hours=2),
        "created_at": NOW + timedelta(hours=3),
    }
    data.update(overrides)
    return AgentRun(**data)


def seed_agent_runs(db: Database, items: list[AgentRun]) -> None:
    repo = AgentRunRepo(db)
    for item in items:
        repo.add(item)


def make_transition(
    ticket_id: UUID, to_status: str, occurred_at: datetime, **overrides: object
) -> TicketStatusTransition:
    return TicketStatusTransition(
        **transition_kwargs()
        | {
            "id": uuid4(),
            "ticket_id": ticket_id,
            "to_status": to_status,
            "occurred_at": occurred_at,
        }
        | overrides
    )


def seed_transitions(db: Database, items: list[TicketStatusTransition]) -> None:
    repo = TicketStatusTransitionRepo(db)
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
    assert "## Cycle time per state (historical)" in out
    assert "## Ready-queue depth" in out
    assert "## Anomaly counts" in out
    assert "## Dwell breaches" in out
    assert "## Draft lessons" in out
    assert "## Agent runs" in out


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
    assert payload["draft_lessons"] == []
    assert payload["agent_runs"] == {
        "count": 0,
        "handed_off_count": 0,
        "mean_dispatch_to_handoff_hours": None,
    }


def test_report_surfaces_draft_lessons_with_anomaly_evidence(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    ticket = make_ticket("ATLAS-9", status="in_progress")
    seed_tickets(db, [ticket])
    debt = make_debt(ticket.id, AnomalyType.DWELL_BREACH)
    seed_debt(db, [debt])
    draft = make_lesson(
        product_id=ticket.product_id,
        status="draft",
        title="Detected dwell_breach pattern for ATLAS-9",
        related_ticket_ids=[ticket.id],
        tags=[
            "anomaly-draft",
            "anomaly:dwell_breach",
            "anomaly-draft-key:dwell_breach:ATLAS-9",
            "ticket:ATLAS-9",
            f"debt-item:{debt.id}",
        ],
    )
    seed_lessons(db, [draft])

    code = main(["pm", "report"], database=db)
    out = capsys.readouterr().out

    assert code == EXIT_OK
    assert "## Draft lessons" in out
    assert draft.title in out
    assert "dwell_breach" in out
    assert ticket.key in out
    assert str(debt.id) in out

    code = main(["pm", "report", "--json"], database=db)
    payload = json.loads(capsys.readouterr().out)
    assert code == EXIT_OK
    assert payload["draft_lessons"] == [
        {
            "lesson_id": str(draft.id),
            "title": draft.title,
            "category": "failure_pattern",
            "pattern": "dwell_breach",
            "ticket_keys": [ticket.key],
            "debt_item_ids": [str(debt.id)],
        }
    ]


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
        TicketRepo(db),
        DebtItemRepo(db),
        TickFailureRepo(db),
        TicketStatusTransitionRepo(db),
        now=NOW,
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
            TicketRepo(db),
            DebtItemRepo(db),
            TickFailureRepo(db),
            TicketStatusTransitionRepo(db),
            now=NOW,
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
        TicketRepo(db),
        DebtItemRepo(db),
        TickFailureRepo(db),
        TicketStatusTransitionRepo(db),
        now=NOW,
    ).ready_queue_depth
    # The wrong answer counts non-ready tickets or misses a ready one.
    assert depth == 2

    seed_tickets(db, [make_ticket("ATLAS-5", status="ready_for_agent")])
    moved = build_delivery_report(
        TicketRepo(db),
        DebtItemRepo(db),
        TickFailureRepo(db),
        TicketStatusTransitionRepo(db),
        now=NOW,
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
            TicketRepo(db),
            DebtItemRepo(db),
            TickFailureRepo(db),
            TicketStatusTransitionRepo(db),
            now=NOW,
        ).throughput
    }
    assert buckets == {"2026-W24": 1, "2026-W25": 2, "unknown": 1}


# --- historical cycle time (ATLAS-126; the gap ATLAS-47 deferred) ----------


def _at(day: int, hour: int = 0) -> datetime:
    """A June-2026 instant — terse seeding of transition timestamps."""
    return datetime(2026, 6, day, hour, tzinfo=UTC)


def test_completed_episodes_only_initial_and_open_uncounted(db: Database) -> None:
    # Three transitions T1..T3 -> exactly two completed episodes: the two MIDDLE
    # states (to_1, to_2). T1 enters `in_progress` at day 1; the episode in
    # `in_progress` ends at T2 (day 2) -> 24h. T2 enters `in_review` at day 2;
    # that episode ends at T3 (day 4) -> 48h. The state entered by T3 (`done`)
    # is the OPEN episode (no recorded exit) and the state before T1 (`backlog`)
    # is the initial state (no recorded entry) -> neither is counted.
    ticket = make_ticket("ATLAS-1")
    seed_tickets(db, [ticket])
    seed_transitions(
        db,
        [
            make_transition(ticket.id, "in_progress", _at(1), from_status="backlog"),
            make_transition(ticket.id, "in_review", _at(2), from_status="in_progress"),
            make_transition(ticket.id, "done", _at(4), from_status="in_review"),
        ],
    )

    report = build_delivery_report(
        TicketRepo(db),
        DebtItemRepo(db),
        TickFailureRepo(db),
        TicketStatusTransitionRepo(db),
        now=NOW,
    )
    stats = {s.status: s for s in report.cycle_time_per_state}

    # Exactly the two middle states; the open `done` and the pre-history
    # `backlog` are absent. The wrong answer counts the open episode (the proxy
    # we replaced) or fabricates a duration for the initial state.
    assert set(stats) == {"in_progress", "in_review"}
    assert stats["in_progress"].episode_count == 1
    assert stats["in_progress"].median_hours == 24
    assert stats["in_review"].episode_count == 1
    assert stats["in_review"].median_hours == 48


def test_revisits_each_count_as_a_separate_episode(db: Database) -> None:
    # A ticket entering `in_progress` twice contributes TWO episodes to that
    # state (12h then 36h), not one. The wrong answer overwrites the first.
    ticket = make_ticket("ATLAS-1")
    seed_tickets(db, [ticket])
    seed_transitions(
        db,
        [
            make_transition(ticket.id, "in_progress", _at(1, 0)),
            make_transition(ticket.id, "in_review", _at(1, 12)),  # 12h episode
            make_transition(ticket.id, "in_progress", _at(2, 0)),
            make_transition(ticket.id, "in_review", _at(3, 12)),  # 36h episode
            make_transition(ticket.id, "done", _at(4, 0)),
        ],
    )

    report = build_delivery_report(
        TicketRepo(db),
        DebtItemRepo(db),
        TickFailureRepo(db),
        TicketStatusTransitionRepo(db),
        now=NOW,
    )
    stats = {s.status: s for s in report.cycle_time_per_state}

    assert stats["in_progress"].episode_count == 2
    assert stats["in_progress"].min_hours == 12
    assert stats["in_progress"].max_hours == 36
    assert stats["in_progress"].median_hours == 24  # median([12, 36])


def test_per_state_aggregation_across_tickets(db: Database) -> None:
    # `in_progress` episodes of 10h, 20h, 30h come from two tickets; aggregate
    # min/median/max and the episode count are over ALL of them, per state.
    a = make_ticket("ATLAS-1")
    b = make_ticket("ATLAS-2")
    seed_tickets(db, [a, b])
    seed_transitions(
        db,
        [
            make_transition(a.id, "in_progress", _at(1, 0)),
            make_transition(a.id, "in_review", _at(1, 10)),  # A: 10h
            make_transition(a.id, "done", _at(1, 15)),  # A in_review: 5h
            make_transition(b.id, "in_progress", _at(2, 0)),
            make_transition(b.id, "blocked", _at(2, 20)),  # B: 20h
            make_transition(b.id, "in_progress", _at(2, 22)),
            make_transition(b.id, "done", _at(4, 4)),  # B 2nd in_progress: 30h
        ],
    )

    report = build_delivery_report(
        TicketRepo(db),
        DebtItemRepo(db),
        TickFailureRepo(db),
        TicketStatusTransitionRepo(db),
        now=NOW,
    )
    stats = {s.status: s for s in report.cycle_time_per_state}

    assert stats["in_progress"].episode_count == 3
    assert stats["in_progress"].min_hours == 10
    assert stats["in_progress"].median_hours == 20  # median([10, 20, 30])
    assert stats["in_progress"].max_hours == 30
    # `in_review` carries A's single 5h episode only.
    assert stats["in_review"].episode_count == 1
    assert stats["in_review"].median_hours == 5


def test_fewer_than_two_transitions_yields_no_episode(db: Database) -> None:
    # A ticket with one transition has no completed episode (one entry, no
    # recorded exit); a ticket with none contributes nothing. No crash, no
    # zero-duration phantom episode.
    one = make_ticket("ATLAS-1")
    none = make_ticket("ATLAS-2")
    seed_tickets(db, [one, none])
    seed_transitions(db, [make_transition(one.id, "in_progress", _at(1))])

    report = build_delivery_report(
        TicketRepo(db),
        DebtItemRepo(db),
        TickFailureRepo(db),
        TicketStatusTransitionRepo(db),
        now=NOW,
    )
    assert report.cycle_time_per_state == []


def test_empty_log_renders_and_is_empty(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    # No transitions anywhere -> empty cycle-time metric; both renders succeed.
    seed_tickets(db, [make_ticket("ATLAS-1", status="in_progress")])

    report = build_delivery_report(
        TicketRepo(db),
        DebtItemRepo(db),
        TickFailureRepo(db),
        TicketStatusTransitionRepo(db),
        now=NOW,
    )
    assert report.cycle_time_per_state == []
    assert report_json(report)["cycle_time_per_state"] == []
    assert "No completed cycles recorded." in render_markdown(report)


def test_proxy_is_gone_section_is_historical_and_breaches_untouched(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    # The metric is labelled historical, no current-time-in-state is called
    # cycle time, and the dwell-breach metric (ATLAS-119) is unaffected.
    ticket = make_ticket("ATLAS-1", status="in_progress")
    seed_tickets(db, [ticket])
    seed_debt(db, [make_debt(ticket.id, AnomalyType.DWELL_BREACH)])

    code = main(["pm", "report"], database=db)
    out = capsys.readouterr().out

    assert code == EXIT_OK
    assert "## Cycle time per state (historical)" in out
    assert "completed episodes" in out
    # The retired proxy's label and heading are gone.
    assert "current dwell" not in out.lower()
    assert "Current dwell per state" not in out
    # The dwell-breach metric still surfaces the seeded breach.
    assert "## Dwell breaches" in out
    assert "ATLAS-1" in out


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
        TicketRepo(db),
        DebtItemRepo(db),
        TickFailureRepo(db),
        TicketStatusTransitionRepo(db),
        now=NOW,
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
    assert payload["cycle_time_per_state"] == []
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
    assert "No completed cycles recorded." in out
    assert "0 ticket(s) in `ready_for_agent`." in out
    assert "No dwell breaches recorded." in out
    assert "0 recorded PM-scheduler tick failure(s)." in out
    assert "0 reconstructed agent run(s)." in out


# --- tick failures: a recorded crash moves the count (ATLAS-125) ------------


def test_tick_failure_count_is_total_of_stored_rows(db: Database) -> None:
    # Zero on an empty DB; each recorded crash moves the count by one,
    # regardless of signature (the report surfaces a total, not a per-signature
    # breakdown). Pure reader: no row is written by the report.
    assert (
        build_delivery_report(
            TicketRepo(db),
            DebtItemRepo(db),
            TickFailureRepo(db),
            TicketStatusTransitionRepo(db),
            now=NOW,
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
            TicketRepo(db),
            DebtItemRepo(db),
            TickFailureRepo(db),
            TicketStatusTransitionRepo(db),
            now=NOW,
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
        TicketRepo(db),
        DebtItemRepo(db),
        TickFailureRepo(db),
        TicketStatusTransitionRepo(db),
        now=NOW,
    )
    assert "## Tick failures" in render_markdown(report)
    assert "2 recorded PM-scheduler tick failure(s)." in render_markdown(report)
    assert report_json(report)["tick_failure_count"] == 2


# --- agent runs: reconstructed dispatch attribution (ATLAS-166) -------------


def test_agent_runs_surface_count_and_mean_dispatch_to_handoff(
    db: Database, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_agent_runs(
        db,
        [
            make_agent_run(
                started_at=NOW,
                completed_at=NOW + timedelta(hours=2),
            ),
            make_agent_run(
                started_at=NOW + timedelta(hours=1),
                completed_at=NOW + timedelta(hours=7),
            ),
            # Partial observation: counted, excluded from the handoff mean.
            make_agent_run(
                status=AgentRunStatus.RUNNING,
                started_at=NOW + timedelta(hours=8),
                completed_at=None,
            ),
        ],
    )

    code = main(["pm", "report", "--json"], database=db)
    payload = json.loads(capsys.readouterr().out)

    assert code == EXIT_OK
    assert payload["agent_runs"] == {
        "count": 3,
        "handed_off_count": 2,
        "mean_dispatch_to_handoff_hours": 4.0,
    }

    report = build_delivery_report(
        TicketRepo(db),
        DebtItemRepo(db),
        TickFailureRepo(db),
        TicketStatusTransitionRepo(db),
        AgentRunRepo(db),
        now=NOW,
    )
    markdown = render_markdown(report)
    assert "## Agent runs" in markdown
    assert "3 reconstructed agent run(s)." in markdown
    assert "2 handed off; mean dispatch-to-handoff: 4 h." in markdown
