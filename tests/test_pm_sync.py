"""PM-Engine sync-tick milestone proofs (ATLAS-42).

Falsifiable, with the wrong answer named in each case:

- Idempotency: a second tick over an unchanged fake Linear writes NOTHING —
  no redundant status set (status_pulled stays 0) and no re-push (update_issue
  called exactly once total). Wrong answer: the cursor doesn't hold and the
  definition re-pushes every tick.
- Pull: a Linear status change lands in Atlas in one tick. An unmapped Linear
  state leaves status unchanged, increments the counter, writes NO DebtItem
  (the ATLAS-118 split), and does not crash. Wrong answer: a guessed status,
  a crash, or a DebtItem leaking in from a later ticket.
- Push: a changed pre-dispatch ticket is pushed; an unchanged one is not
  (cursor); a frozen In-Progress ticket is not pushed even when newer; the
  payload carries only title + description (priority deferred like labels —
  wrong answer: a raw priority crosses).
- Directionality: a Linear-side title divergence never overwrites Atlas, and
  an Atlas status is mechanically incapable of crossing (no state key in any
  pushed definition).

Deterministic: the in-memory fake, no network, no secrets.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from linear_fakes import InMemoryLinearClient
from test_models_validation import NOW, ticket_kwargs

from atlas.core.models import Ticket
from atlas.core.models.ticket import TicketStatus
from atlas.linear.client import LinearIssue, WorkflowState
from atlas.linear.ownership import LinearStatusMap
from atlas.pm import SyncResult, sync_tick
from atlas.storage import Database, DebtItemRepo, TicketRepo

# Workflow states the fake exposes and the status map keys off (stable ids).
STARTED = WorkflowState(id="state-started", name="In Progress", type="started")
UNSTARTED = WorkflowState(id="state-unstarted", name="Todo", type="unstarted")
UNMAPPED = WorkflowState(id="state-orphan", name="Orphan", type="started")
TEAM_ID = "team-1"

EARLIER = NOW
LATER = NOW + timedelta(hours=1)


class RecordingClient(InMemoryLinearClient):
    """An ``InMemoryLinearClient`` that records every write, so a test can
    assert exactly what crossed Atlas -> Linear (and how often)."""

    def __init__(self) -> None:
        super().__init__(workflow_states=[STARTED, UNSTARTED, UNMAPPED])
        self.creates: list[dict[str, Any]] = []
        self.updates: list[tuple[str, dict[str, Any]]] = []

    def create_issue(
        self, definition: Mapping[str, Any], *, team_id: str
    ) -> LinearIssue:
        self.creates.append(dict(definition))
        return super().create_issue(definition, team_id=team_id)

    def update_issue(self, issue_id: str, definition: Mapping[str, Any]) -> LinearIssue:
        self.updates.append((issue_id, dict(definition)))
        return super().update_issue(issue_id, definition)


def status_map() -> LinearStatusMap:
    # unstarted -> planned, started -> in_progress. state-orphan is in the
    # workspace but absent from the map: the unmapped case.
    return LinearStatusMap(
        {
            UNSTARTED.id: TicketStatus.PLANNED,
            STARTED.id: TicketStatus.IN_PROGRESS,
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
            client.set_state(external_id, issue_state)
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
        }
    )
    TicketRepo(db).add(ticket)
    client.creates.clear()
    client.updates.clear()
    return ticket


def run(db: Database, client: RecordingClient) -> SyncResult:
    return sync_tick(
        tickets=TicketRepo(db), client=client, status_map=status_map(), team_id=TEAM_ID
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


def test_unmapped_state_leaves_status_and_writes_no_debt_item(db: Database) -> None:
    client = RecordingClient()
    seed_ticket(
        db, client, key="ATLAS-202", status=TicketStatus.PLANNED, issue_state=UNMAPPED
    )

    result = run(db, client)  # must not raise

    pulled = TicketRepo(db).get_by_key("ATLAS-202")
    assert pulled is not None
    assert pulled.status == TicketStatus.PLANNED  # unchanged, never guessed
    assert result.unmapped == 1
    # The split: the DebtItem write is ATLAS-118, not ATLAS-42.
    assert DebtItemRepo(db).list() == []


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
