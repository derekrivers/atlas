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

import json
import tempfile
from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from linear_fakes import InMemoryLinearClient
from test_lesson_model import lesson_kwargs
from test_models_validation import NOW, dependency_kwargs, ticket_kwargs

from atlas.core.anchors import SourceDocument
from atlas.core.enums import ActorType, EntityStatus, EvidenceStatus
from atlas.core.models import (
    AnomalyType,
    ContextPack,
    DebtItem,
    Lesson,
    Ticket,
    TicketDependency,
    VerificationCheck,
)
from atlas.core.models.ticket import TicketStatus
from atlas.linear.client import LinearComment, LinearIssue, WorkflowState
from atlas.linear.ownership import LinearStatusMap
from atlas.pm import SyncResult, sync_tick
from atlas.pm.sync import CREATED_BY
from atlas.storage import (
    AgentRunRepo,
    ContextPackRepo,
    Database,
    DebtItemRepo,
    LessonRepo,
    TicketDependencyRepo,
    TicketRepo,
    TicketStatusTransitionRepo,
    VerificationCheckRepo,
)
from atlas.verification import required_checks

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
# The unique Done state the verified-completion step (step 3b, ATLAS-131) writes into
# via set_state. sync_tick resolves state_id_for(DONE) up front every tick (the
# load-time guard), so the shared status map must carry it.
DONE_STATE = WorkflowState(id="state-done", name="Done", type="completed")
REJECTED_STATE = WorkflowState(id="state-canceled", name="Canceled", type="canceled")
TEAM_ID = "team-1"
# The Linear project (id/UUID) issue creation is scoped to (ATLAS-135). Threaded
# through sync_tick exactly like TEAM_ID; the RecordingClient records it per create
# so a first-sync push can be asserted to carry it.
PROJECT_ID = "project-1"

EARLIER = NOW
LATER = NOW + timedelta(hours=1)
PACK_DOC = SourceDocument(
    path="docs/atlas/implementation-roadmap.md",
    sha="sha-pack-doc",
    content="# Phase 1\n\nA resolvable fixture section for sync tests.\n",
)


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
                DONE_STATE,
                REJECTED_STATE,
            ]
        )
        self.creates: list[dict[str, Any]] = []
        # The creation scope (team id, project id) recorded per create, so a test
        # can assert the configured project crossed Atlas -> Linear (ATLAS-135).
        self.create_scopes: list[tuple[str, str]] = []
        self.updates: list[tuple[str, dict[str, Any]]] = []
        self.state_writes: list[tuple[str, str]] = []
        # The issue ids fetch_comments was called for (ATLAS-148): the comment
        # scan is request-budgeted, so tests assert WHO was scanned, not just
        # what got stubbed — a skipped ticket must cost zero requests.
        self.comment_scans: list[str] = []

    def fetch_comments(self, issue_id: str) -> list[LinearComment]:
        self.comment_scans.append(issue_id)
        return super().fetch_comments(issue_id)

    def create_issue(
        self, definition: Mapping[str, Any], *, team_id: str, project_id: str
    ) -> LinearIssue:
        self.creates.append(dict(definition))
        self.create_scopes.append((team_id, project_id))
        return super().create_issue(definition, team_id=team_id, project_id=project_id)

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
            # The unique Done state the verified-completion step (ATLAS-131) resolves
            # via state_id_for(DONE), likewise resolved up front every tick.
            DONE_STATE.id: TicketStatus.DONE,
            REJECTED_STATE.id: TicketStatus.REJECTED,
        }
    )


class FakeLessonClient:
    def __init__(self, *, tag: str = "pm-sync") -> None:
        self.tag = tag
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return json.dumps(
            {
                "category": "failure_pattern",
                "title": f"Extracted {self.tag} lesson",
                "problem": f"Observed {self.tag}.",
                "solution": "Review the bounded evidence bundle.",
                "outcome": "A draft lesson was filed for operator review.",
                "tags": [self.tag],
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
    issue_title: str = "Linear Title",
) -> Ticket:
    """Insert a ticket, optionally joined to a fake Linear issue in
    ``issue_state`` titled ``issue_title`` (defaults deliberately differ from
    the ticket's title: the pull joins by external_linear_id ONLY, never by
    title — ATLAS-148 pins that with a swapped-titles fixture). Recorder lists
    are cleared after seeding so only the tick's own writes are observed."""

    external_id: str | None = None
    if with_issue:
        issue = client.create_issue(
            {"title": issue_title, "description": "linear"},
            team_id=TEAM_ID,
            project_id=PROJECT_ID,
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
    client.create_scopes.clear()
    client.updates.clear()
    client.state_writes.clear()
    client.comment_scans.clear()
    return ticket


def run(
    db: Database,
    client: RecordingClient,
    *,
    now: datetime = NOW,
    inbox_dir: Path | None = None,
    lesson_client: FakeLessonClient | None = None,
) -> SyncResult:
    # The follow-up scan (step 4, ATLAS-45) needs an inbox dir; these sync tests
    # seed no comments, so it writes nothing. A throwaway temp dir per call keeps
    # them isolated. The follow-up behaviour itself is covered in
    # tests/test_pm_follow_ups.py.
    #
    # The documents provider (ATLAS-164) supplies a minimal committed corpus
    # whose heading matches ticket_kwargs().source_anchor. This suite's cursor
    # and request-budget assertions stay about the generic sync loop; the
    # degraded pack-render posture is covered in tests/test_pm_pack_embedding.py.
    return sync_tick(
        tickets=TicketRepo(db),
        db=db,
        client=client,
        status_map=status_map(),
        team_id=TEAM_ID,
        project_id=PROJECT_ID,
        inbox_dir=inbox_dir or Path(tempfile.mkdtemp()),
        documents=lambda: [PACK_DOC],
        now=now,
        lesson_client=lesson_client,
    )


def debt_rows(
    db: Database, kind: AnomalyType, ticket_id: UUID | None = None
) -> list[DebtItem]:
    """The DebtItems of one anomaly class (optionally one ticket's)."""

    items = DebtItemRepo(db).list()
    return [
        item
        for item in items
        if item.anomaly_type == kind
        and (ticket_id is None or item.ticket_id == ticket_id)
    ]


def draft_lessons(db: Database) -> list[Lesson]:
    return [
        lesson
        for lesson in LessonRepo(db).list()
        if lesson.status is EntityStatus.DRAFT
    ]


def seed_passed_verification(db: Database, ticket: Ticket, now: datetime = NOW) -> None:
    repo = VerificationCheckRepo(db)
    for check in required_checks(ticket):
        if not check.required:
            continue
        repo.add(
            VerificationCheck(
                id=uuid4(),
                ticket_id=ticket.id,
                check_type=check.check_type,
                status=EvidenceStatus.PASSED,
                summary=f"{check.check_type.value} passed",
                required=True,
                evidence_ids=[],
                created_at=now,
                completed_at=now,
            )
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


def test_done_transition_extracts_lesson_when_notable(db: Database) -> None:
    client = RecordingClient()
    lesson_client = FakeLessonClient(tag="done")
    prior = seed_ticket(
        db,
        client,
        key="ATLAS-198",
        status=TicketStatus.REJECTED,
        status_entered_at=NOW - timedelta(days=3),
        with_issue=False,
    )
    ticket = seed_ticket(
        db,
        client,
        key="ATLAS-199",
        status=TicketStatus.REVIEW_REQUIRED,
        issue_state=DONE_STATE,
        updated_at=NOW - timedelta(hours=3),
        status_entered_at=NOW - timedelta(hours=1),
    )
    # Same ticket_type as the prior failure; PASSED on the first review cycle.
    assert prior.ticket_type == ticket.ticket_type
    seed_passed_verification(db, ticket)

    result = run(db, client, lesson_client=lesson_client)

    pulled = TicketRepo(db).get_by_key("ATLAS-199")
    assert pulled is not None and pulled.status == TicketStatus.DONE
    lessons = draft_lessons(db)
    assert result.draft_lessons_filed == 1
    assert len(lessons) == 1
    assert lessons[0].confidence is None
    assert lessons[0].source_ticket_id == ticket.id
    assert lessons[0].related_ticket_ids == []
    assert "ATLAS-199" in lesson_client.prompts[0]


def test_done_transition_records_citation_feedback_for_latest_pack(
    db: Database,
) -> None:
    client = RecordingClient()
    ticket = seed_ticket(
        db,
        client,
        key="ATLAS-196",
        status=TicketStatus.REVIEW_REQUIRED,
        issue_state=DONE_STATE,
    )
    lesson = Lesson(
        **lesson_kwargs()
        | {
            "id": uuid4(),
            "product_id": ticket.product_id,
            "status": EntityStatus.ACTIVE,
            "related_ticket_ids": [],
        }
    )
    LessonRepo(db).add(lesson)
    ContextPackRepo(db).add(
        ContextPack(
            id=uuid4(),
            product_id=ticket.product_id,
            ticket_id=ticket.id,
            title=ticket.title,
            objective=ticket.objective,
            historical_lessons=[lesson.id],
            rendered_markdown="## Lessons\n\n### Reused lesson",
            created_at=NOW - timedelta(minutes=1),
        )
    )

    result = run(db, client)

    pulled = TicketRepo(db).get_by_key("ATLAS-196")
    cited = LessonRepo(db).get(lesson.id)
    assert pulled is not None and pulled.status == TicketStatus.DONE
    assert result.status_pulled == 1
    assert cited is not None
    assert cited.source_ticket_id == lesson.source_ticket_id
    assert cited.related_ticket_ids == [ticket.id]


def test_rejected_transition_extracts_lesson(db: Database) -> None:
    client = RecordingClient()
    lesson_client = FakeLessonClient(tag="rejected")
    ticket = seed_ticket(
        db,
        client,
        key="ATLAS-197",
        status=TicketStatus.IN_PROGRESS,
        issue_state=REJECTED_STATE,
    )

    result = run(db, client, lesson_client=lesson_client)

    pulled = TicketRepo(db).get_by_key("ATLAS-197")
    assert pulled is not None and pulled.status == TicketStatus.REJECTED
    lessons = draft_lessons(db)
    assert result.draft_lessons_filed == 1
    assert len(lessons) == 1
    assert lessons[0].confidence is None
    assert lessons[0].source_ticket_id == ticket.id
    assert lessons[0].related_ticket_ids == []
    assert "ATLAS-197" in lesson_client.prompts[0]


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
    items = debt_rows(db, AnomalyType.OUT_OF_OWNERSHIP_TRANSITION)
    assert len(items) == 1
    (item,) = items
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


def test_first_sync_create_carries_the_configured_project(db: Database) -> None:
    # AC-2 (ATLAS-135): a first-sync _push threads the configured project id all the
    # way into create_issue, so the created issue is scoped to the Symphony-polled
    # project. The RecordingClient records (team_id, project_id) per create; the
    # wrong answer omits the project at _push and the scope's project is empty.
    client = RecordingClient()
    seed_ticket(
        db,
        client,
        key="ATLAS-208",
        status=TicketStatus.PLANNED,
        with_issue=False,
    )

    result = run(db, client)

    assert result.pushed_created == 1
    assert client.create_scopes == [(TEAM_ID, PROJECT_ID)]  # team AND project crossed


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
    assert len(debt_rows(db, AnomalyType.OUT_OF_OWNERSHIP_TRANSITION)) == 1
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

    items = debt_rows(db, AnomalyType.OUT_OF_OWNERSHIP_TRANSITION, ticket.id)
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

    rows = debt_rows(db, AnomalyType.OUT_OF_OWNERSHIP_TRANSITION, ticket.id)
    assert len(rows) == 2  # wrong answer: 1
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

    assert len(debt_rows(db, kind, ticket.id)) == 3
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


def test_dwell_breach_files_one_draft_lesson_and_second_tick_dedupes(
    db: Database,
) -> None:
    client = RecordingClient()
    lesson_client = FakeLessonClient(tag="dwell_breach")
    ticket = seed_ticket(
        db,
        client,
        key="ATLAS-219",
        status=TicketStatus.IN_PROGRESS,
        status_entered_at=NOW,
        with_issue=False,
    )

    first = run(db, client, now=PAST_IN_PROGRESS, lesson_client=lesson_client)
    second = run(
        db,
        client,
        now=PAST_IN_PROGRESS + timedelta(hours=1),
        lesson_client=lesson_client,
    )

    items = debt_rows(db, AnomalyType.DWELL_BREACH, ticket.id)
    assert len(items) == 1
    (item,) = items
    lessons = draft_lessons(db)
    assert first.draft_lessons_filed == 1
    assert second.draft_lessons_filed == 0
    assert len(lessons) == 1  # wrong answer: duplicate DRAFT on re-tick
    (lesson,) = lessons
    assert lesson.source_ticket_id == ticket.id
    assert lesson.related_ticket_ids == []
    assert lesson.confidence is None
    assert "dwell_breach" in lesson.tags
    assert ticket.key in lesson_client.prompts[0]
    assert str(item.id) in lesson_client.prompts[0]


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
    changed = repo.apply_linear_status(
        "ATLAS-226", TicketStatus.IN_PROGRESS, now=first, created_by_id=CREATED_BY
    )
    assert changed.status_entered_at == first
    assert changed.updated_at == ticket.updated_at  # disjoint-column discipline

    # A set-to-same status does NOT re-stamp (the episode did not restart) and,
    # again, does not bump updated_at. Wrong answer: the clock resets every pull.
    same = repo.apply_linear_status(
        "ATLAS-226", TicketStatus.IN_PROGRESS, now=later, created_by_id=CREATED_BY
    )
    assert same.status_entered_at == first
    assert same.updated_at == ticket.updated_at


# --- status-transition history (ATLAS-121): the append-only capture half ------
#
# apply_linear_status appends ONE TicketStatusTransition inline, atomically with
# the status change, ONLY in the real-change branch. The traps below are the
# falsifiable ones: from_status captured BEFORE reassignment (so from != to),
# and a set-to-same recording nothing.


def test_real_transition_records_exactly_one_row(db: Database) -> None:
    client = RecordingClient()
    ticket = seed_ticket(
        db,
        client,
        key="ATLAS-240",
        status=TicketStatus.IN_PROGRESS,
        updated_at=EARLIER,
        status_entered_at=None,
        with_issue=False,
    )
    repo = TicketRepo(db)
    transitions = TicketStatusTransitionRepo(db)
    moved_at = NOW + timedelta(hours=1)

    repo.apply_linear_status(
        "ATLAS-240", TicketStatus.PR_OPEN, now=moved_at, created_by_id=CREATED_BY
    )

    rows = transitions.list_for_ticket(ticket.id)
    assert len(rows) == 1  # wrong answer: 0 rows (no capture)
    (row,) = rows
    # from_status is the value BEFORE reassignment. Wrong answer: captured AFTER,
    # so from == to == "pr_open".
    assert row.from_status == "in_progress"
    assert row.to_status == "pr_open"
    assert row.from_status != row.to_status
    assert row.occurred_at == moved_at
    assert row.ticket_id == ticket.id
    assert row.created_by_type == ActorType.SYSTEM
    assert row.created_by_id == CREATED_BY


def test_set_to_same_status_records_no_transition(db: Database) -> None:
    client = RecordingClient()
    ticket = seed_ticket(
        db,
        client,
        key="ATLAS-241",
        status=TicketStatus.IN_PROGRESS,
        updated_at=EARLIER,
        status_entered_at=NOW,
        with_issue=False,
    )
    repo = TicketRepo(db)
    transitions = TicketStatusTransitionRepo(db)

    same = repo.apply_linear_status(
        "ATLAS-241",
        TicketStatus.IN_PROGRESS,
        now=NOW + timedelta(hours=5),
        created_by_id=CREATED_BY,
    )

    # No phantom transition for a no-op pull, and (as ATLAS-119) no re-stamp.
    assert transitions.list_for_ticket(ticket.id) == []
    assert same.status_entered_at == NOW


def test_transition_and_status_change_commit_together(db: Database) -> None:
    # Atomicity: after a real change both the new status and exactly one
    # transition are present, and updated_at is unchanged (no directionality
    # leak — the same disjoint-column discipline the dwell clock keeps).
    client = RecordingClient()
    ticket = seed_ticket(
        db,
        client,
        key="ATLAS-242",
        status=TicketStatus.PLANNED,
        updated_at=EARLIER,
        status_entered_at=None,
        with_issue=False,
    )
    repo = TicketRepo(db)
    transitions = TicketStatusTransitionRepo(db)
    moved_at = NOW + timedelta(hours=2)

    changed = repo.apply_linear_status(
        "ATLAS-242", TicketStatus.IN_PROGRESS, now=moved_at, created_by_id=CREATED_BY
    )

    assert changed.status == TicketStatus.IN_PROGRESS
    assert changed.updated_at == ticket.updated_at  # no re-push
    rows = transitions.list_for_ticket(ticket.id)
    assert len(rows) == 1
    assert (rows[0].from_status, rows[0].to_status) == ("planned", "in_progress")


def test_history_accumulates_one_ordered_row_per_real_change(db: Database) -> None:
    # N successive real changes leave N ordered rows; status_entered_at still
    # reflects ONLY the latest. Wrong answer: one row (overwrite), or unordered.
    client = RecordingClient()
    ticket = seed_ticket(
        db,
        client,
        key="ATLAS-243",
        status=TicketStatus.PLANNED,
        updated_at=EARLIER,
        status_entered_at=None,
        with_issue=False,
    )
    repo = TicketRepo(db)
    transitions = TicketStatusTransitionRepo(db)

    legs = [
        (TicketStatus.IN_PROGRESS, NOW + timedelta(hours=1)),
        (TicketStatus.PR_OPEN, NOW + timedelta(hours=2)),
        (TicketStatus.CHANGES_REQUESTED, NOW + timedelta(hours=3)),
        (TicketStatus.PR_OPEN, NOW + timedelta(hours=4)),
    ]
    for status, when in legs:
        latest = repo.apply_linear_status(
            "ATLAS-243", status, now=when, created_by_id=CREATED_BY
        )

    rows = transitions.list_for_ticket(ticket.id)
    assert len(rows) == len(legs)  # one per real change, not one overwritten row
    assert [(r.from_status, r.to_status) for r in rows] == [
        ("planned", "in_progress"),
        ("in_progress", "pr_open"),
        ("pr_open", "changes_requested"),
        ("changes_requested", "pr_open"),
    ]
    assert [r.occurred_at for r in rows] == [when for _, when in legs]  # ordered
    # The dwell clock holds only the latest episode's entry, not the history.
    assert latest.status_entered_at == legs[-1][1]


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

    moved = repo.apply_linear_status(
        "ATLAS-230", TicketStatus.PR_OPEN, now=LATER, created_by_id=CREATED_BY
    )

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
    arrived = repo.apply_linear_status(
        "ATLAS-231", TicketStatus.PR_OPEN, now=LATER, created_by_id=CREATED_BY
    )
    assert arrived.review_cycle_count == 0  # only changes_requested -> pr_open counts

    # The reverse leg pr_open -> changes_requested does not count either.
    bounced = repo.apply_linear_status(
        "ATLAS-231",
        TicketStatus.CHANGES_REQUESTED,
        now=LATER + timedelta(hours=1),
        created_by_id=CREATED_BY,
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


def test_review_cycle_breach_files_one_draft_lesson_and_second_tick_dedupes(
    db: Database,
) -> None:
    client = RecordingClient()
    lesson_client = FakeLessonClient(tag="review_cycle")
    ticket = seed_ticket(
        db,
        client,
        key="ATLAS-229",
        status=TicketStatus.PR_OPEN,
        status_entered_at=NOW,
        review_cycle_count=4,
        issue_state=PR_OPEN_STATE,
    )
    assert ticket.external_linear_id is not None

    first = run(db, client, now=LATER, lesson_client=lesson_client)
    second = run(
        db,
        client,
        now=LATER + timedelta(hours=1),
        lesson_client=lesson_client,
    )

    items = debt_rows(db, AnomalyType.REVIEW_CYCLE, ticket.id)
    assert len(items) == 1
    (item,) = items
    lessons = draft_lessons(db)
    assert first.draft_lessons_filed == 1
    assert second.draft_lessons_filed == 0
    assert len(lessons) == 1  # wrong answer: duplicate DRAFT on re-tick
    (lesson,) = lessons
    assert lesson.source_ticket_id == ticket.id
    assert lesson.related_ticket_ids == []
    assert lesson.confidence is None
    assert "review_cycle" in lesson.tags
    assert ticket.key in lesson_client.prompts[0]
    assert str(item.id) in lesson_client.prompts[0]


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


def test_below_threshold_anomalies_file_no_draft_lesson(db: Database) -> None:
    client = RecordingClient()
    seed_ticket(
        db,
        client,
        key="ATLAS-228",
        status=TicketStatus.PR_OPEN,
        status_entered_at=NOW,
        review_cycle_count=3,
        issue_state=PR_OPEN_STATE,
    )
    seed_ticket(
        db,
        client,
        key="ATLAS-227",
        status=TicketStatus.IN_PROGRESS,
        status_entered_at=NOW,
        with_issue=False,
    )

    result = run(db, client, now=INSIDE_IN_PROGRESS)

    assert result.draft_lessons_filed == 0
    assert debt_rows(db, AnomalyType.REVIEW_CYCLE) == []
    assert debt_rows(db, AnomalyType.DWELL_BREACH) == []
    assert draft_lessons(db) == []


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


# --- stale block (ATLAS-44): structural, report-only ------------------------
#
# A ticket marked `blocked` whose structural blockers have all cleared
# (`blocked(graph, key)` empty) appends ONE STALE_BLOCK per blocked *episode*.
# The check is structural (no horizon, no clock comparison — `now` only stamps
# the row); report-only — it NEVER moves a ticket and makes no Linear call.
# Every stale-block ticket is seeded with no issue and a CLEAN cursor
# (linear_synced_at == updated_at) so pull/push/promote are all no-ops and only
# the step-5 stale-block pass acts (blocked IS pushable, so an unsynced one would
# otherwise be created — the clean cursor freezes it).


def add_depends_on(db: Database, *, source: Ticket, target: Ticket) -> None:
    """Seed a ``source depends_on target`` edge so the projected graph carries a
    structural blocker (cleared iff ``target`` is done)."""

    TicketDependencyRepo(db).add(
        TicketDependency(
            **dependency_kwargs()
            | {
                "id": uuid4(),
                "source_ticket_id": source.id,
                "target_entity_type": "ticket",
                "target_entity_id": target.id,
                "dependency_type": "depends_on",
            }
        )
    )


def test_blocked_with_no_structural_blockers_logs_one_stale_block(
    db: Database,
) -> None:
    client = RecordingClient()
    # Blocked since NOW, no dependency edges: blocked(graph, key) is empty, so the
    # ticket is stranded in blocked and a STALE_BLOCK is logged.
    ticket = seed_ticket(
        db,
        client,
        key="ATLAS-240",
        status=TicketStatus.BLOCKED,
        status_entered_at=NOW,
        updated_at=EARLIER,
        linear_synced_at=EARLIER,
        with_issue=False,
    )

    result = run(db, client, now=LATER)

    items = DebtItemRepo(db).list()
    assert len(items) == 1  # wrong answer: zero (structural clear not detected)
    (item,) = items
    assert item.anomaly_type == AnomalyType.STALE_BLOCK
    assert item.created_by_type == ActorType.SYSTEM  # system-written, not evidence
    assert item.ticket_id == ticket.id
    assert item.product_id == ticket.product_id
    assert item.observed_at == LATER  # the injected tick clock only stamps the row
    assert result.stale_blocks == 1
    # Report-only: no ticket-state write of any kind, status still blocked.
    assert client.state_writes == []
    pulled = TicketRepo(db).get_by_key("ATLAS-240")
    assert pulled is not None and pulled.status == TicketStatus.BLOCKED


def test_blocked_with_all_blockers_done_logs_one_stale_block(db: Database) -> None:
    client = RecordingClient()
    # A depends_on B; B is DONE, so blocked(graph, A) is empty — A is stranded.
    # This proves the pass evaluates blocked() over the graph, not mere
    # edge-absence: an edge exists, but its target is satisfied.
    blocked_ticket = seed_ticket(
        db,
        client,
        key="ATLAS-241",
        status=TicketStatus.BLOCKED,
        status_entered_at=NOW,
        updated_at=EARLIER,
        linear_synced_at=EARLIER,
        with_issue=False,
    )
    done_target = seed_ticket(
        db,
        client,
        key="ATLAS-242",
        status=TicketStatus.DONE,
        updated_at=EARLIER,
        linear_synced_at=EARLIER,
        with_issue=False,
    )
    add_depends_on(db, source=blocked_ticket, target=done_target)

    result = run(db, client, now=LATER)

    items = DebtItemRepo(db).list()
    assert len(items) == 1  # wrong answer: zero (edge present but target done)
    (item,) = items
    assert item.anomaly_type == AnomalyType.STALE_BLOCK
    assert item.ticket_id == blocked_ticket.id
    assert result.stale_blocks == 1
    assert client.state_writes == []


def test_blocked_with_active_structural_blocker_logs_nothing(db: Database) -> None:
    client = RecordingClient()
    # A depends_on B; B is PLANNED (not done), so blocked(graph, A) is NON-empty —
    # A is genuinely blocked, not stranded. The wrong answer logs for a still
    # structurally blocked ticket.
    blocked_ticket = seed_ticket(
        db,
        client,
        key="ATLAS-243",
        status=TicketStatus.BLOCKED,
        status_entered_at=NOW,
        updated_at=EARLIER,
        linear_synced_at=EARLIER,
        with_issue=False,
    )
    planned_target = seed_ticket(
        db,
        client,
        key="ATLAS-244",
        status=TicketStatus.PLANNED,
        updated_at=EARLIER,
        linear_synced_at=EARLIER,
        with_issue=False,
    )
    add_depends_on(db, source=blocked_ticket, target=planned_target)

    result = run(db, client, now=LATER)

    assert (
        DebtItemRepo(db).list() == []
    )  # wrong answer: a row for a still-blocked ticket
    assert result.stale_blocks == 0


def test_non_blocked_ticket_logs_no_stale_block(db: Database) -> None:
    client = RecordingClient()
    # Planned, no dependencies — blocked(graph, key) would be empty, but the
    # ticket is not in `blocked`, so the pass must not log. The wrong answer logs
    # off the empty-blockers condition alone, ignoring the status gate.
    seed_ticket(
        db,
        client,
        key="ATLAS-245",
        status=TicketStatus.PLANNED,
        status_entered_at=NOW,
        updated_at=EARLIER,
        linear_synced_at=EARLIER,
        with_issue=False,
    )

    result = run(db, client, now=LATER)

    assert DebtItemRepo(db).list() == []  # wrong answer: a row for a non-blocked ticket
    assert result.stale_blocks == 0


def test_null_status_entered_at_is_skipped_not_stale_blocked(db: Database) -> None:
    client = RecordingClient()
    # status_entered_at unknown (NULL) — no episode boundary for the dedup. The
    # pass SKIPS it, never guessing a stale-block, exactly as dwell does.
    seed_ticket(
        db,
        client,
        key="ATLAS-246",
        status=TicketStatus.BLOCKED,
        status_entered_at=None,
        updated_at=EARLIER,
        linear_synced_at=EARLIER,
        with_issue=False,
    )

    result = run(db, client, now=LATER)

    assert DebtItemRepo(db).list() == []  # wrong answer: a row off a NULL clock
    assert result.stale_blocks == 0


def test_stale_block_logs_exactly_one_row_across_n_ticks(db: Database) -> None:
    client = RecordingClient()
    seed_ticket(
        db,
        client,
        key="ATLAS-247",
        status=TicketStatus.BLOCKED,
        status_entered_at=NOW,
        updated_at=EARLIER,
        linear_synced_at=EARLIER,
        with_issue=False,
    )

    # Five ticks, the blocked episode never changing.
    results = [run(db, client, now=NOW + timedelta(hours=1 + n)) for n in range(5)]

    # The load-bearing dedup: one row for the whole blocked episode, NOT one per
    # tick. Wrong answer: five rows, which makes recurring(...) lie.
    assert len(DebtItemRepo(db).list()) == 1
    assert results[0].stale_blocks == 1
    assert all(r.stale_blocks == 0 for r in results[1:])


def test_stale_block_detect_path_makes_no_linear_call(db: Database) -> None:
    client = RecordingClient()
    # AC4: the detect path is report-only — no set_state, and (with a clean cursor
    # and no issue) no create/update either. Assert every recorded Linear write
    # list is empty after a tick that logs a stale-block.
    seed_ticket(
        db,
        client,
        key="ATLAS-248",
        status=TicketStatus.BLOCKED,
        status_entered_at=NOW,
        updated_at=EARLIER,
        linear_synced_at=EARLIER,
        with_issue=False,
    )

    result = run(db, client, now=LATER)

    assert result.stale_blocks == 1  # the stale-block did fire...
    assert client.creates == []  # ...with no Linear call of any kind
    assert client.updates == []
    assert client.state_writes == []


# --- ATLAS-148: the request-budget milestone and the join-by-id proof ---------
#
# The bound test is the milestone anchor: a no-op tick over a 110-ticket board
# stays within a pinned client-call budget. It is falsifiable by construction —
# re-adding ONE per-ticket fetch anywhere in the tick (the seeded-defect form)
# adds ~100 calls and blows the bound unmissably.

# The pinned per-tick budget for the 110-ticket no-op board below. Arithmetic:
# 1 batched project-issues pull (110 issues <= one 250-page) + 10 comment scans
# (exactly the tickets in ACTIVE_COMMENT_SCAN_STATUSES) = 11 calls; pinned at
# 12 to leave one call of headroom for a future fixed-cost (per-tick, never
# per-ticket) query. The pre-148 shape was ~215 calls for the same board.
REQUEST_BUDGET = 12


class CountingClient(RecordingClient):
    """A ``RecordingClient`` that also counts EVERY protocol call by method
    name, so the budget test pins total requests, not just writes."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: dict[str, int] = {}

    def _count(self, method: str) -> None:
        self.calls[method] = self.calls.get(method, 0) + 1

    def create_issue(self, definition: Mapping[str, Any], **kwargs: Any) -> LinearIssue:
        self._count("create_issue")
        return super().create_issue(definition, **kwargs)

    def update_issue(self, issue_id: str, definition: Mapping[str, Any]) -> LinearIssue:
        self._count("update_issue")
        return super().update_issue(issue_id, definition)

    def set_state(self, issue_id: str, state_id: str) -> LinearIssue:
        self._count("set_state")
        return super().set_state(issue_id, state_id)

    def fetch_issue(self, issue_id: str) -> LinearIssue | None:
        self._count("fetch_issue")
        return super().fetch_issue(issue_id)

    def fetch_project_issues(self, project_id: str) -> list[LinearIssue]:
        self._count("fetch_project_issues")
        return super().fetch_project_issues(project_id)

    def fetch_workflow_states(self, team_id: str) -> list[WorkflowState]:
        self._count("fetch_workflow_states")
        return super().fetch_workflow_states(team_id)

    def fetch_comments(self, issue_id: str) -> list[LinearComment]:
        self._count("fetch_comments")
        return super().fetch_comments(issue_id)

    def total_calls(self) -> int:
        return sum(self.calls.values())


def _seed_110_ticket_board(db: Database, client: CountingClient) -> None:
    """The 110-ticket board of the request-bound milestone (D-5), every ticket
    joined to a Linear issue whose state maps back to the ticket's own status
    (so the pull changes nothing):

    * 90 ``planned`` — no acceptance criteria, so none is promotion-ready;
    * 5 parked ``needs_human_decision`` — excluded from the comment scan;
    * 5 terminal ``done`` — neither pulled nor scanned;
    * 10 across the five ACTIVE_COMMENT_SCAN_STATUSES (2 each) — the ONLY
      comment-scanned tickets. review_required has no mapped state in this
      suite's map, so those two issues sit in UNMAPPED (the pull observes and
      leaves them, which is itself a no-op for the budget).

    NO-OP MEANS THE PUSH IS CURRENT TOO: every ticket is seeded with
    ``linear_synced_at == updated_at``, so ZERO definition pushes run and the
    1 + 10 = 11-call arithmetic below is complete. A future push-path change
    that starts pushing on an untouched board breaks the bound loudly instead
    of silently widening the budget (gate amendment A-4).
    """

    spec: list[tuple[TicketStatus, WorkflowState]] = []
    spec += [(TicketStatus.PLANNED, UNSTARTED)] * 90
    spec += [(TicketStatus.NEEDS_HUMAN_DECISION, NEEDS_HUMAN)] * 5
    spec += [(TicketStatus.DONE, DONE_STATE)] * 5
    spec += [
        (TicketStatus.READY_FOR_AGENT, READY),
        (TicketStatus.READY_FOR_AGENT, READY),
        (TicketStatus.IN_PROGRESS, STARTED),
        (TicketStatus.IN_PROGRESS, STARTED),
        (TicketStatus.PR_OPEN, PR_OPEN_STATE),
        (TicketStatus.PR_OPEN, PR_OPEN_STATE),
        (TicketStatus.REVIEW_REQUIRED, UNMAPPED),
        (TicketStatus.REVIEW_REQUIRED, UNMAPPED),
        (TicketStatus.CHANGES_REQUESTED, CHANGES_REQUESTED_STATE),
        (TicketStatus.CHANGES_REQUESTED, CHANGES_REQUESTED_STATE),
    ]
    assert len(spec) == 110
    for index, (status, issue_state) in enumerate(spec):
        seed_ticket(
            db,
            client,
            key=f"ATLAS-{300 + index}",
            status=status,
            issue_state=issue_state,
            linear_synced_at=EARLIER,  # cursor current: no push (A-4)
        )


def test_noop_tick_over_110_ticket_board_stays_within_request_budget(
    db: Database,
) -> None:
    # THE MILESTONE ANCHOR (ATLAS-148 AC-1). Seeded-defect form: re-adding a
    # per-ticket fetch (e.g. one client.fetch_issue per non-terminal ticket in
    # the pull) adds ~100 calls and MUST break this bound.
    client = CountingClient()
    _seed_110_ticket_board(db, client)
    client.calls.clear()  # count the tick only, not the seeding

    run(db, client)

    assert client.total_calls() <= REQUEST_BUDGET, client.calls
    # The arithmetic behind the pin, itemised: one batched pull, one comment
    # scan per active-state ticket, and NOTHING per-ticket anywhere else.
    assert client.calls["fetch_project_issues"] == 1
    assert client.calls["fetch_comments"] == 10
    assert client.calls.get("fetch_issue", 0) == 0  # the pre-148 loop is gone
    assert client.calls.get("update_issue", 0) == 0  # cursors current: no push
    assert client.calls.get("create_issue", 0) == 0
    assert AgentRunRepo(db).list() == []  # no dispatch transitions, no runs


def test_second_noop_tick_costs_the_same_budget(db: Database) -> None:
    # Idempotency in request terms: the budget holds tick after tick — nothing
    # accumulates (the wrong answer: a second tick re-pushes or re-scans wider).
    client = CountingClient()
    _seed_110_ticket_board(db, client)
    run(db, client)
    client.calls.clear()

    run(db, client)

    assert client.total_calls() <= REQUEST_BUDGET, client.calls
    assert AgentRunRepo(db).list() == []


def test_pull_joins_by_external_linear_id_never_title(db: Database) -> None:
    # ATLAS-148 AC-1 join proof, swapped-titles fixture: ATLAS-500's issue is
    # titled with ATLAS-501's Atlas title and vice versa. Moving ATLAS-500's
    # issue (by id) to STARTED must pull ATLAS-500 — and only it — to
    # in_progress. A title-keyed join would move ATLAS-501 instead (its Atlas
    # title matches the moved issue's title); an identifier-keyed join has
    # nothing to key on (the fake mints none).
    client = RecordingClient()
    first = seed_ticket(
        db,
        client,
        key="ATLAS-500",
        status=TicketStatus.PLANNED,
        title="Alpha work",
        issue_title="Beta work",  # deliberately the OTHER ticket's title
        linear_synced_at=EARLIER,
    )
    seed_ticket(
        db,
        client,
        key="ATLAS-501",
        status=TicketStatus.PLANNED,
        title="Beta work",
        issue_title="Alpha work",
        linear_synced_at=EARLIER,
    )
    assert first.external_linear_id is not None
    client.simulate_linear_state(first.external_linear_id, STARTED)

    result = run(db, client)

    assert result.status_pulled == 1
    statuses = {t.key: t.status for t in TicketRepo(db).list()}
    assert statuses["ATLAS-500"] == TicketStatus.IN_PROGRESS  # joined by id
    assert statuses["ATLAS-501"] == TicketStatus.PLANNED  # title ignored


def test_missing_issue_in_project_pull_leaves_status_unchanged(
    db: Database,
) -> None:
    # A joined ticket whose issue is absent from the batched project pull
    # (deleted, or moved out of the project's poll scope) takes the
    # issue-missing path: status unchanged, no crash — same contract as the
    # pre-148 per-ticket fetch returning None.
    client = RecordingClient()
    ticket = seed_ticket(
        db,
        client,
        key="ATLAS-502",
        status=TicketStatus.IN_PROGRESS,
        linear_synced_at=EARLIER,
    )
    assert ticket.external_linear_id is not None
    del client._issues[ticket.external_linear_id]  # gone from the project

    result = run(db, client)

    assert result.status_pulled == 0
    statuses = {t.key: t.status for t in TicketRepo(db).list()}
    assert statuses["ATLAS-502"] == TicketStatus.IN_PROGRESS
