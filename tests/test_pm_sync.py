"""PM-Engine sync-tick milestone proofs (ATLAS-42).

Falsifiable, with the wrong answer named in each case:

- Idempotency: a second tick over an unchanged fake Linear writes NOTHING —
  no redundant status set (status_pulled stays 0) and no re-push (update_issue
  called exactly once total). Wrong answer: the cursor doesn't hold and the
  definition re-pushes every tick.
- Pull: a Linear status change lands in Atlas in one tick. An unmapped Linear
  state leaves status unchanged, increments the counter, and (ATLAS-118)
  appends one OUT_OF_OWNERSHIP_TRANSITION DebtItem on the transition into it.
  Wrong answer: a guessed status, a crash, or a status write on the anomaly
  path. The dedup proofs (one row per transition, not per tick) are the
  "out-of-ownership anomaly" section below.
- Push: a changed pre-dispatch ticket is pushed; an unchanged one is not
  (cursor); a frozen In-Progress ticket is not pushed even when newer; the
  payload carries only title + description (priority deferred like labels —
  wrong answer: a raw priority crosses).
- Directionality: a Linear-side title divergence never overwrites Atlas, and
  an Atlas status is mechanically incapable of crossing (no state key in any
  pushed definition).
- Dwell breach (ATLAS-119): a ticket past its per-status horizon logs ONE
  DWELL_BREACH per dwell episode (not one per tick); inside the horizon, a
  no-horizon status, and a NULL entry time each log nothing; when the status
  changes the episode advances and a new breach logs again. status_entered_at
  is stamped only on a real status change and never bumps updated_at.
- Review cycling (ATLAS-120): the counter fires only on changes_requested ->
  pr_open (no other transition; never bumps updated_at); over the threshold the
  step-5 pass routes to needs_human_decision via set_state and logs ONE
  REVIEW_CYCLE note, at or under it neither; an already-reconciled ticket is not
  re-routed; a not-yet-reconciled route across N ticks logs exactly one row
  while the route is called idempotently every tick (the load-bearing dedup).

Deterministic: the in-memory fake, no network, no secrets.
"""

from __future__ import annotations

import tempfile
from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from linear_fakes import InMemoryLinearClient
from test_models_validation import NOW, ticket_kwargs

from atlas.core.enums import ActorType
from atlas.core.models import AnomalyType, Ticket
from atlas.core.models.ticket import TicketStatus
from atlas.linear.client import LinearIssue, WorkflowState
from atlas.linear.ownership import LinearStatusMap
from atlas.pm import SyncResult, sync_tick
from atlas.storage import Database, DebtItemRepo, TicketRepo

# Workflow states the fake exposes and the status map keys off (stable ids).
STARTED = WorkflowState(id="state-started", name="In Progress", type="started")
UNSTARTED = WorkflowState(id="state-unstarted", name="Todo", type="unstarted")
UNMAPPED = WorkflowState(id="state-orphan", name="Orphan", type="started")
# A second unmapped state: a transition from UNMAPPED into a *different*
# out-of-ownership state must log a new row.
UNMAPPED2 = WorkflowState(id="state-orphan-2", name="Orphan Two", type="started")
# The unique Ready-for-Agent state the readiness promotion (step 3) writes into.
READY = WorkflowState(id="state-ready", name="Ready for Agent", type="unstarted")
# A state mapped to pr_open — used to drive a real in_progress -> pr_open
# transition (re-stamping status_entered_at) for the dwell episode-advance proof.
PR_OPEN_STATE = WorkflowState(id="state-pr-open", name="PR Open", type="started")
# A state mapped to changes_requested — drives the changes_requested -> pr_open
# round trip the review-cycling counter (ATLAS-120) counts.
CHANGES_REQUESTED_STATE = WorkflowState(
    id="state-changes", name="Changes Requested", type="started"
)
# The unique Needs-Human state the review-cycling route (step 5, ATLAS-120) writes
# into via set_state — the one anomaly that moves a ticket.
NEEDS_HUMAN = WorkflowState(id="state-needs-human", name="Needs Human", type="started")
TEAM_ID = "team-1"

EARLIER = NOW
LATER = NOW + timedelta(hours=1)


class RecordingClient(InMemoryLinearClient):
    """An ``InMemoryLinearClient`` that records every write, so a test can
    assert exactly what crossed Atlas -> Linear (and how often)."""

    def __init__(self) -> None:
        super().__init__(
            workflow_states=[
                STARTED,
                UNSTARTED,
                UNMAPPED,
                READY,
                PR_OPEN_STATE,
                CHANGES_REQUESTED_STATE,
                NEEDS_HUMAN,
            ]
        )
        self.creates: list[dict[str, Any]] = []
        self.updates: list[tuple[str, dict[str, Any]]] = []
        self.state_writes: list[tuple[str, str]] = []

    def create_issue(
        self, definition: Mapping[str, Any], *, team_id: str
    ) -> LinearIssue:
        self.creates.append(dict(definition))
        return super().create_issue(definition, team_id=team_id)

    def update_issue(self, issue_id: str, definition: Mapping[str, Any]) -> LinearIssue:
        self.updates.append((issue_id, dict(definition)))
        return super().update_issue(issue_id, definition)

    def set_state(self, issue_id: str, state_id: str) -> LinearIssue:
        self.state_writes.append((issue_id, state_id))
        return super().set_state(issue_id, state_id)


def status_map() -> LinearStatusMap:
    # unstarted -> planned, started -> in_progress, state-ready -> the unique
    # ready_for_agent promotion target. state-orphan is in the workspace but
    # absent from the map: the unmapped case.
    return LinearStatusMap(
        {
            UNSTARTED.id: TicketStatus.PLANNED,
            STARTED.id: TicketStatus.IN_PROGRESS,
            READY.id: TicketStatus.READY_FOR_AGENT,
            PR_OPEN_STATE.id: TicketStatus.PR_OPEN,
            CHANGES_REQUESTED_STATE.id: TicketStatus.CHANGES_REQUESTED,
            # The unique Needs-Human state the review-cycling route resolves via
            # state_id_for(NEEDS_HUMAN_DECISION); sync_tick resolves it up front
            # every tick (the load-time guard), so the map must carry it.
            NEEDS_HUMAN.id: TicketStatus.NEEDS_HUMAN_DECISION,
        }
    )


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{tmp_path}/atlas.db")
    database.create_all()
    return database


def seed_ticket(
    db: Database,
    client: RecordingClient,
    *,
    key: str,
    status: TicketStatus,
    updated_at: datetime = EARLIER,
    linear_synced_at: datetime | None = None,
    status_entered_at: datetime | None = None,
    review_cycle_count: int = 0,
    title: str = "Atlas Title",
    priority: int = 10,
    with_issue: bool = True,
    issue_state: WorkflowState | None = None,
) -> Ticket:
    """Insert a ticket, optionally joined to a fake Linear issue in
    ``issue_state``. Recorder lists are cleared after seeding so only the
    tick's own writes are observed."""

    external_id: str | None = None
    if with_issue:
        issue = client.create_issue(
            {"title": "Linear Title", "description": "linear"}, team_id=TEAM_ID
        )
        external_id = issue.id
        if issue_state is not None:
            client.simulate_linear_state(external_id, issue_state)
    ticket = Ticket(
        **ticket_kwargs()
        | {
            "id": uuid4(),
            "key": key,
            "status": status,
            "title": title,
            "priority": priority,
            "external_linear_id": external_id,
            "created_at": updated_at,
            "updated_at": updated_at,
            "linear_synced_at": linear_synced_at,
            "status_entered_at": status_entered_at,
            "review_cycle_count": review_cycle_count,
        }
    )
    TicketRepo(db).add(ticket)
    client.creates.clear()
    client.updates.clear()
    client.state_writes.clear()
    return ticket


def run(
    db: Database,
    client: RecordingClient,
    *,
    now: datetime = NOW,
    inbox_dir: Path | None = None,
) -> SyncResult:
    # The follow-up scan (step 4, ATLAS-45) needs an inbox dir; these sync tests
    # seed no comments, so it writes nothing. A throwaway temp dir per call keeps
    # them isolated. The follow-up behaviour itself is covered in
    # tests/test_pm_follow_ups.py.
    return sync_tick(
        tickets=TicketRepo(db),
        db=db,
        client=client,
        status_map=status_map(),
        team_id=TEAM_ID,
        inbox_dir=inbox_dir or Path(tempfile.mkdtemp()),
        now=now,
    )


# --- idempotency -----------------------------------------------------------


def test_second_tick_over_unchanged_linear_writes_nothing(db: Database) -> None:
    client = RecordingClient()
    # planned + unstarted issue: pull is set-to-same (no-op); first push stamps.
    seed_ticket(db, client, key="ATLAS-200", status=TicketStatus.PLANNED)

    first = run(db, client)
    second = run(db, client)

    assert first.pushed_updated == 1  # first tick pushes once
    assert second.pushed_updated == 0  # cursor holds: no re-push
    assert len(client.updates) == 1  # exactly one update_issue ever
    # No redundant status set on either tick: set-to-same never writes.
    assert first.status_pulled == 0 and second.status_pulled == 0
    assert first.status_unchanged == 1 and second.status_unchanged == 1


# --- pull (Linear -> Atlas) ------------------------------------------------


def test_linear_status_change_lands_in_one_tick(db: Database) -> None:
    client = RecordingClient()
    # planned ticket, Linear issue moved to a 'started' state -> in_progress.
    seed_ticket(
        db, client, key="ATLAS-201", status=TicketStatus.PLANNED, issue_state=STARTED
    )

    result = run(db, client)

    pulled = TicketRepo(db).get_by_key("ATLAS-201")
    assert pulled is not None
    assert pulled.status == TicketStatus.IN_PROGRESS  # wrong answer: still planned
    assert result.status_pulled == 1


def test_unmapped_state_logs_one_debt_item_without_changing_status(
    db: Database,
) -> None:
    client = RecordingClient()
    ticket = seed_ticket(
        db, client, key="ATLAS-202", status=TicketStatus.PLANNED, issue_state=UNMAPPED
    )

    result = run(db, client)  # must not raise

    pulled = TicketRepo(db).get_by_key("ATLAS-202")
    assert pulled is not None
    assert pulled.status == TicketStatus.PLANNED  # unchanged, never guessed
    assert result.unmapped == 1
    assert result.anomalies_logged == 1
    # The first writer of the ATLAS-116 model: exactly one system-written
    # OUT_OF_OWNERSHIP_TRANSITION row for this ticket. Wrong answer = no row,
    # the wrong type, or not system-written.
    items = DebtItemRepo(db).list()
    assert len(items) == 1
    (item,) = items
    assert item.anomaly_type == AnomalyType.OUT_OF_OWNERSHIP_TRANSITION
    assert item.created_by_type == ActorType.SYSTEM
    assert item.ticket_id == ticket.id
    assert item.product_id == ticket.product_id
    assert item.observed_at == NOW
    # The transition signal was stamped so a persisting state won't re-log.
    assert pulled.last_observed_linear_state_id == UNMAPPED.id
    # No ticket-state write on the anomaly path: status stayed, and nothing
    # crossed Atlas -> Linear (no push of a planned set-to-... and no set_state).
    assert client.state_writes == []


def test_terminal_ticket_is_not_pulled(db: Database) -> None:
    client = RecordingClient()
    # A done ticket whose Linear issue sits in a 'started' state: a naive pull
    # would drag it back to in_progress. Terminal work is not polled.
    seed_ticket(
        db, client, key="ATLAS-203", status=TicketStatus.DONE, issue_state=STARTED
    )

    result = run(db, client)

    pulled = TicketRepo(db).get_by_key("ATLAS-203")
    assert pulled is not None
    assert pulled.status == TicketStatus.DONE
    assert result.status_pulled == 0


# --- push (Atlas -> Linear) ------------------------------------------------


def test_unchanged_ticket_is_not_repushed(db: Database) -> None:
    client = RecordingClient()
    # Already synced: linear_synced_at == updated_at, so the cursor says clean.
    seed_ticket(
        db,
        client,
        key="ATLAS-204",
        status=TicketStatus.PLANNED,
        updated_at=EARLIER,
        linear_synced_at=EARLIER,
    )

    result = run(db, client)

    assert result.pushed_updated == 0  # wrong answer: re-pushes a clean ticket
    assert client.updates == []


def test_frozen_in_progress_ticket_is_not_pushed_even_when_newer(db: Database) -> None:
    client = RecordingClient()
    # In Progress (frozen), definition edited after the last sync (newer
    # updated_at). The freeze must win over the cursor. The issue sits in a
    # 'started' state so the pull is a no-op and cannot unfreeze it.
    seed_ticket(
        db,
        client,
        key="ATLAS-205",
        status=TicketStatus.IN_PROGRESS,
        updated_at=LATER,
        linear_synced_at=EARLIER,
        issue_state=STARTED,
    )

    result = run(db, client)

    assert result.pushed_updated == 0  # wrong answer: a frozen ticket is pushed
    assert client.updates == []


def test_pushed_payload_carries_only_title_and_description(db: Database) -> None:
    client = RecordingClient()
    seed_ticket(
        db, client, key="ATLAS-206", status=TicketStatus.READY_FOR_AGENT, priority=10
    )

    run(db, client)

    assert len(client.updates) == 1
    _issue_id, definition = client.updates[0]
    assert set(definition) == {"title", "description"}
    # priority is owned but deferred like labels; a raw 10 must NOT cross.
    assert "priority" not in definition
    # status is mechanically incapable of crossing Atlas -> Linear.
    assert "state" not in definition and "stateId" not in definition


def test_unsynced_ticket_without_issue_is_created_then_idempotent(db: Database) -> None:
    client = RecordingClient()
    # No external_linear_id yet: the first tick creates the issue, writes back
    # the join key, and stamps the cursor; the second tick is a no-op.
    seed_ticket(
        db,
        client,
        key="ATLAS-207",
        status=TicketStatus.PLANNED,
        with_issue=False,
    )

    first = run(db, client)
    after = TicketRepo(db).get_by_key("ATLAS-207")
    second = run(db, client)

    assert first.pushed_created == 1
    assert after is not None and after.external_linear_id is not None
    assert second.pushed_created == 0 and second.pushed_updated == 0
    assert len(client.creates) == 1  # created once, never duplicated


# --- directionality --------------------------------------------------------


def test_linear_title_edit_does_not_overwrite_atlas(db: Database) -> None:
    client = RecordingClient()
    # Synced + set-to-same status, so neither push nor status write fires; the
    # Linear issue carries a divergent title ("Linear Title").
    seed_ticket(
        db,
        client,
        key="ATLAS-208",
        status=TicketStatus.PLANNED,
        title="Atlas Title",
        updated_at=EARLIER,
        linear_synced_at=EARLIER,
        issue_state=UNSTARTED,
    )

    run(db, client)

    pulled = TicketRepo(db).get_by_key("ATLAS-208")
    assert pulled is not None
    assert pulled.title == "Atlas Title"  # wrong answer: Linear title leaks in
    assert client.updates == [] and client.creates == []


# --- out-of-ownership anomaly (ATLAS-118): per-transition dedup -------------
#
# The load-bearing proofs: one DebtItem per *transition* into an unmapped
# state, not one per tick — or recurrence (3+ rows) is meaningless.


def test_persisting_unmapped_state_logs_exactly_one_row_across_n_ticks(
    db: Database,
) -> None:
    client = RecordingClient()
    seed_ticket(
        db, client, key="ATLAS-210", status=TicketStatus.PLANNED, issue_state=UNMAPPED
    )

    results = [run(db, client) for _ in range(5)]

    # The dedup: five ticks over a state that stays unmapped yield ONE row.
    # Wrong answer: five rows (one per tick), which makes recurring(...) lie.
    assert len(DebtItemRepo(db).list()) == 1
    assert results[0].anomalies_logged == 1
    assert all(r.anomalies_logged == 0 for r in results[1:])
    assert all(r.unmapped == 1 for r in results)  # the state is observed every tick


def test_transition_into_a_different_unmapped_state_logs_a_new_row(
    db: Database,
) -> None:
    client = RecordingClient()
    ticket = seed_ticket(
        db, client, key="ATLAS-211", status=TicketStatus.PLANNED, issue_state=UNMAPPED
    )
    assert ticket.external_linear_id is not None

    run(db, client)  # transition into UNMAPPED -> row 1
    client.simulate_linear_state(ticket.external_linear_id, UNMAPPED2)
    run(db, client)  # transition into a DIFFERENT unmapped state -> row 2

    items = DebtItemRepo(db).list_for_ticket(ticket.id)
    assert len(items) == 2  # wrong answer: 1 (a naive "already logged" dedup)
    # One row per distinct out-of-ownership state (order-independent: both
    # observations share observed_at, so the list tiebreaks by id).
    summaries = " ".join(i.summary for i in items)
    assert UNMAPPED.id in summaries and UNMAPPED2.id in summaries


def test_unmapped_then_mapped_then_unmapped_logs_a_new_row(db: Database) -> None:
    client = RecordingClient()
    ticket = seed_ticket(
        db, client, key="ATLAS-212", status=TicketStatus.PLANNED, issue_state=UNMAPPED
    )
    assert ticket.external_linear_id is not None

    run(db, client)  # transition into UNMAPPED -> row 1
    client.simulate_linear_state(ticket.external_linear_id, STARTED)
    run(db, client)  # mapped: status moves, no DebtItem
    client.simulate_linear_state(ticket.external_linear_id, UNMAPPED)
    run(db, client)  # re-occurrence is a genuine NEW transition -> row 2

    assert len(DebtItemRepo(db).list_for_ticket(ticket.id)) == 2  # wrong answer: 1
    pulled = TicketRepo(db).get_by_key("ATLAS-212")
    assert pulled is not None
    assert pulled.status == TicketStatus.IN_PROGRESS  # the mapped pull stuck


def test_mapped_state_logs_no_debt_item(db: Database) -> None:
    client = RecordingClient()
    seed_ticket(
        db, client, key="ATLAS-213", status=TicketStatus.PLANNED, issue_state=STARTED
    )

    result = run(db, client)

    assert DebtItemRepo(db).list() == []  # a mapped transition is not an anomaly
    assert result.anomalies_logged == 0


def test_recurring_reports_the_true_count_after_three_transitions(
    db: Database,
) -> None:
    client = RecordingClient()
    ticket = seed_ticket(
        db, client, key="ATLAS-214", status=TicketStatus.PLANNED, issue_state=UNMAPPED
    )
    assert ticket.external_linear_id is not None
    repo = DebtItemRepo(db)
    kind = AnomalyType.OUT_OF_OWNERSHIP_TRANSITION

    run(db, client)  # transition 1
    for _ in range(2):  # two more transitions, each via an intervening mapped pull
        client.simulate_linear_state(ticket.external_linear_id, STARTED)
        run(db, client)
        client.simulate_linear_state(ticket.external_linear_id, UNMAPPED)
        run(db, client)

    assert len(repo.list_for_ticket(ticket.id)) == 3
    # Three real transitions -> recurring is True (the predicate is now honest
    # because dedup kept the count to genuine transitions, not ticks).
    assert repo.recurring(ticket.id, kind) is True


# --- dwell breach (ATLAS-119): per-episode dedup ---------------------------
#
# The dwell clock is status_entered_at; the breach is report-only (never moves
# a ticket); one DWELL_BREACH per dwell *episode*, not per tick.

# in_progress horizon is 24h (DWELL_HORIZONS).
PAST_IN_PROGRESS = NOW + timedelta(hours=25)  # 1h past the 24h horizon
INSIDE_IN_PROGRESS = NOW + timedelta(hours=23)  # still inside the 24h horizon


def test_dwell_breach_logged_once_past_horizon(db: Database) -> None:
    client = RecordingClient()
    # In Progress since NOW; no Linear issue, so pull/push/promote are all no-ops
    # and only the step-5 dwell pass acts (in_progress is frozen, not promotable).
    ticket = seed_ticket(
        db,
        client,
        key="ATLAS-220",
        status=TicketStatus.IN_PROGRESS,
        status_entered_at=NOW,
        with_issue=False,
    )

    result = run(db, client, now=PAST_IN_PROGRESS)

    items = DebtItemRepo(db).list()
    assert len(items) == 1  # wrong answer: zero (horizon not enforced)
    (item,) = items
    assert item.anomaly_type == AnomalyType.DWELL_BREACH
    assert item.created_by_type == ActorType.SYSTEM  # system-written, not evidence
    assert item.ticket_id == ticket.id
    assert item.product_id == ticket.product_id
    assert item.observed_at == PAST_IN_PROGRESS  # the injected tick clock
    assert result.dwell_breaches == 1
    # Report-only: no ticket-state write of any kind.
    assert client.state_writes == []
    pulled = TicketRepo(db).get_by_key("ATLAS-220")
    assert pulled is not None and pulled.status == TicketStatus.IN_PROGRESS


def test_no_breach_inside_horizon(db: Database) -> None:
    client = RecordingClient()
    seed_ticket(
        db,
        client,
        key="ATLAS-221",
        status=TicketStatus.IN_PROGRESS,
        status_entered_at=NOW,
        with_issue=False,
    )

    result = run(db, client, now=INSIDE_IN_PROGRESS)

    # 23h < 24h: not yet a breach. Wrong answer: one row (off-by-a-tick).
    assert DebtItemRepo(db).list() == []
    assert result.dwell_breaches == 0


def test_status_without_horizon_never_breaches(db: Database) -> None:
    client = RecordingClient()
    # ready_for_agent carries no dwell horizon. Already synced (clean cursor) so
    # the push is skipped; with no issue there is nothing to pull or promote.
    seed_ticket(
        db,
        client,
        key="ATLAS-222",
        status=TicketStatus.READY_FOR_AGENT,
        status_entered_at=NOW,
        updated_at=EARLIER,
        linear_synced_at=EARLIER,
        with_issue=False,
    )

    # Far past any horizon: only a horizoned status could breach here.
    result = run(db, client, now=NOW + timedelta(days=100))

    assert DebtItemRepo(db).list() == []  # wrong answer: a breach on a no-horizon state
    assert result.dwell_breaches == 0


def test_null_status_entered_at_is_skipped_not_breached(db: Database) -> None:
    client = RecordingClient()
    # status_entered_at unknown (NULL) — e.g. a ticket whose status predates the
    # field. Dwell must SKIP it, never guess a breach.
    seed_ticket(
        db,
        client,
        key="ATLAS-223",
        status=TicketStatus.IN_PROGRESS,
        status_entered_at=None,
        with_issue=False,
    )

    result = run(db, client, now=NOW + timedelta(days=100))

    assert DebtItemRepo(db).list() == []  # wrong answer: a breach off a NULL clock
    assert result.dwell_breaches == 0


def test_dwell_breach_logs_exactly_one_row_across_n_ticks(db: Database) -> None:
    client = RecordingClient()
    seed_ticket(
        db,
        client,
        key="ATLAS-224",
        status=TicketStatus.IN_PROGRESS,
        status_entered_at=NOW,
        with_issue=False,
    )

    # Five ticks, each well past the 24h horizon, the status never changing.
    results = [run(db, client, now=NOW + timedelta(hours=25 + n)) for n in range(5)]

    # The load-bearing dedup: one row for the whole episode, NOT one per tick.
    # Wrong answer: five rows, which makes recurring(...) lie.
    assert len(DebtItemRepo(db).list()) == 1
    assert results[0].dwell_breaches == 1
    assert all(r.dwell_breaches == 0 for r in results[1:])


def test_episode_advances_on_status_change_and_logs_a_new_row(db: Database) -> None:
    client = RecordingClient()
    # In Progress since NOW, joined to a Linear issue sitting in a started state
    # (maps to in_progress, so the first pulls are set-to-same — no re-stamp).
    ticket = seed_ticket(
        db,
        client,
        key="ATLAS-225",
        status=TicketStatus.IN_PROGRESS,
        status_entered_at=NOW,
        issue_state=STARTED,
    )
    assert ticket.external_linear_id is not None
    repo = DebtItemRepo(db)

    # Tick 1: 25h into in_progress -> breach row 1 (in_progress episode).
    run(db, client, now=PAST_IN_PROGRESS)
    assert len(repo.list_for_ticket(ticket.id)) == 1

    # Linear moves the issue to pr_open: the next pull re-stamps status_entered_at
    # to that tick, opening a FRESH episode whose horizon (48h) is not yet met.
    client.simulate_linear_state(ticket.external_linear_id, PR_OPEN_STATE)
    moved_at = NOW + timedelta(hours=26)
    run(db, client, now=moved_at)
    pulled = TicketRepo(db).get_by_key("ATLAS-225")
    assert pulled is not None
    assert pulled.status == TicketStatus.PR_OPEN
    assert pulled.status_entered_at == moved_at  # the dwell clock advanced
    assert len(repo.list_for_ticket(ticket.id)) == 1  # 0h into pr_open: no new row

    # Tick 3: 49h into the pr_open episode (past its 48h horizon) -> NEW row.
    run(db, client, now=moved_at + timedelta(hours=49))

    items = repo.list_for_ticket(ticket.id)
    assert len(items) == 2  # wrong answer: 1 (a naive "already breached" dedup)
    assert all(i.anomaly_type == AnomalyType.DWELL_BREACH for i in items)
    summaries = " ".join(i.summary for i in items)
    assert "in_progress" in summaries and "pr_open" in summaries


def test_apply_linear_status_stamps_entry_only_on_real_change(db: Database) -> None:
    client = RecordingClient()
    ticket = seed_ticket(
        db,
        client,
        key="ATLAS-226",
        status=TicketStatus.PLANNED,
        updated_at=EARLIER,
        status_entered_at=None,
        with_issue=False,
    )
    repo = TicketRepo(db)
    first = NOW + timedelta(hours=1)
    later = NOW + timedelta(hours=2)

    # A real change stamps the entry time and leaves updated_at untouched.
    changed = repo.apply_linear_status("ATLAS-226", TicketStatus.IN_PROGRESS, now=first)
    assert changed.status_entered_at == first
    assert changed.updated_at == ticket.updated_at  # disjoint-column discipline

    # A set-to-same status does NOT re-stamp (the episode did not restart) and,
    # again, does not bump updated_at. Wrong answer: the clock resets every pull.
    same = repo.apply_linear_status("ATLAS-226", TicketStatus.IN_PROGRESS, now=later)
    assert same.status_entered_at == first
    assert same.updated_at == ticket.updated_at


# --- review cycling (ATLAS-120): the counter, the route, the per-episode log --
#
# The one anomaly that moves a ticket. The counter fires only on
# changes_requested -> pr_open; over the threshold the step-5 pass routes to
# needs_human_decision via the sanctioned set_state and logs ONE REVIEW_CYCLE.


class NonReconcilingClient(RecordingClient):
    """A ``RecordingClient`` whose ``set_state`` records the route but does NOT
    move the issue's pulled state — modelling a routed ticket whose pull has not
    yet reconciled it out of the cycling states (lagging or repeatedly-retried
    reconciliation). This is the window the per-episode log dedup must survive:
    the route fires every tick while the ticket stays cycling, and the log must
    still land exactly once."""

    def set_state(self, issue_id: str, state_id: str) -> LinearIssue:
        self.state_writes.append((issue_id, state_id))
        return self._issues[issue_id]  # deliberately unchanged: no reconciliation


def test_changes_requested_to_pr_open_increments_count(db: Database) -> None:
    client = RecordingClient()
    ticket = seed_ticket(
        db,
        client,
        key="ATLAS-230",
        status=TicketStatus.CHANGES_REQUESTED,
        updated_at=EARLIER,
        review_cycle_count=0,
        with_issue=False,
    )
    repo = TicketRepo(db)

    moved = repo.apply_linear_status("ATLAS-230", TicketStatus.PR_OPEN, now=LATER)

    # The one transition the round-trip counter counts.
    assert moved.review_cycle_count == 1  # wrong answer: 0 (transition not counted)
    # Status-coupled, disjoint from the definition cursor: never re-pushes.
    assert moved.updated_at == ticket.updated_at


def test_only_changes_requested_to_pr_open_increments(db: Database) -> None:
    client = RecordingClient()
    repo = TicketRepo(db)
    # in_progress -> pr_open is ALSO an arrival into pr_open, but it is NOT a
    # round trip. Wrong answer: any arrival into pr_open increments the counter.
    seed_ticket(
        db,
        client,
        key="ATLAS-231",
        status=TicketStatus.IN_PROGRESS,
        review_cycle_count=0,
        with_issue=False,
    )
    arrived = repo.apply_linear_status("ATLAS-231", TicketStatus.PR_OPEN, now=LATER)
    assert arrived.review_cycle_count == 0  # only changes_requested -> pr_open counts

    # The reverse leg pr_open -> changes_requested does not count either.
    bounced = repo.apply_linear_status(
        "ATLAS-231", TicketStatus.CHANGES_REQUESTED, now=LATER + timedelta(hours=1)
    )
    assert bounced.review_cycle_count == 0


def test_over_threshold_routes_to_needs_human_and_logs_one_note(db: Database) -> None:
    client = RecordingClient()
    # 4 round trips (> 3), still in pr_open, joined to a Linear issue sitting in
    # pr_open so the pull is set-to-same and does not perturb the count/status.
    ticket = seed_ticket(
        db,
        client,
        key="ATLAS-232",
        status=TicketStatus.PR_OPEN,
        status_entered_at=NOW,
        review_cycle_count=4,
        issue_state=PR_OPEN_STATE,
    )
    assert ticket.external_linear_id is not None

    result = run(db, client, now=LATER)

    # Routed via the sanctioned set_state to the resolved Needs-Human state — the
    # SAME outbound path ATLAS-43 uses, not a new mechanism, not a status push.
    assert client.state_writes == [(ticket.external_linear_id, NEEDS_HUMAN.id)]
    assert result.routed_to_human == 1
    # No definition push carried a state: the general status-write path stays shut.
    assert all(
        "stateId" not in body and "state" not in body for _, body in client.updates
    )
    # Exactly one system-written REVIEW_CYCLE note (the failure analysis).
    items = DebtItemRepo(db).list()
    assert len(items) == 1  # wrong answer: zero (threshold not enforced)
    (item,) = items
    assert item.anomaly_type == AnomalyType.REVIEW_CYCLE
    assert item.created_by_type == ActorType.SYSTEM
    assert item.ticket_id == ticket.id
    assert item.product_id == ticket.product_id
    assert item.observed_at == LATER
    assert "4" in item.summary  # the round-trip count is in the note
    assert result.review_cycles_logged == 1


def test_at_threshold_does_not_route_or_log(db: Database) -> None:
    client = RecordingClient()
    # Exactly 3 round trips: "more than 3" routes, so 3 does NOT. Wrong answer:
    # routing at 3 (>= instead of >).
    seed_ticket(
        db,
        client,
        key="ATLAS-233",
        status=TicketStatus.PR_OPEN,
        status_entered_at=NOW,
        review_cycle_count=3,
        issue_state=PR_OPEN_STATE,
    )

    result = run(db, client, now=LATER)

    assert client.state_writes == []  # not routed
    assert DebtItemRepo(db).list() == []  # not logged
    assert result.routed_to_human == 0 and result.review_cycles_logged == 0


def test_already_reconciled_ticket_is_not_rerouted(db: Database) -> None:
    client = RecordingClient()
    # Over threshold but already reconciled into needs_human_decision: it has left
    # the cycling states, so the self-clearing guard skips it. Wrong answer: a
    # monotonic count > 3 re-routes a ticket already handed to a human.
    seed_ticket(
        db,
        client,
        key="ATLAS-234",
        status=TicketStatus.NEEDS_HUMAN_DECISION,
        status_entered_at=NOW,
        review_cycle_count=9,
        issue_state=NEEDS_HUMAN,
    )

    result = run(db, client, now=LATER)

    assert client.state_writes == []
    assert DebtItemRepo(db).list() == []
    assert result.routed_to_human == 0 and result.review_cycles_logged == 0


def test_over_threshold_logs_one_row_across_n_ticks_route_idempotent(
    db: Database,
) -> None:
    client = NonReconcilingClient()
    # Over threshold, joined to a pr_open issue, and the route never reconciles
    # (NonReconcilingClient leaves the pulled state in pr_open). The ticket stays
    # cycling with count > 3 across every tick.
    ticket = seed_ticket(
        db,
        client,
        key="ATLAS-235",
        status=TicketStatus.PR_OPEN,
        status_entered_at=NOW,
        review_cycle_count=4,
        issue_state=PR_OPEN_STATE,
    )
    assert ticket.external_linear_id is not None

    results = [run(db, client, now=NOW + timedelta(hours=n)) for n in range(5)]

    # The load-bearing dedup: ONE REVIEW_CYCLE for the whole pr_open episode, not
    # one per tick. Wrong answer: five rows, which makes recurring(...) lie.
    assert len(DebtItemRepo(db).list()) == 1
    assert results[0].review_cycles_logged == 1
    assert all(r.review_cycles_logged == 0 for r in results[1:])
    # The route is idempotent and re-attempted every tick until reconciliation:
    # five route calls, all to the Needs-Human state.
    assert results[0].routed_to_human == 1
    assert all(r.routed_to_human == 1 for r in results)
    assert client.state_writes == [
        (ticket.external_linear_id, NEEDS_HUMAN.id) for _ in range(5)
    ]
