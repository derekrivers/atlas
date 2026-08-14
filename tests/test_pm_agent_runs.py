"""ATLAS-166: AgentRun reconstruction from observed board and PR facts.

Fixture-driven, offline, with the ATLAS-161 lifecycle named explicitly. Seeded
red with ``assert 1 == 2`` (B011), not ``assert False``. The wrong answers are
pinned by the tests: no row, duplicate rows on a second tick, re-dispatch
overwriting the first run, evidence absence raising, or a sync-tick
implementation that needs an extra Linear call.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from linear_fakes import InMemoryLinearClient
from test_models_validation import ticket_kwargs

from atlas.core.enums import ActorType, EvidenceStatus
from atlas.core.models import (
    AgentRun,
    Evidence,
    EvidenceType,
    Ticket,
    TicketStatus,
    TicketStatusTransition,
    VerificationCheck,
    VerificationCheckType,
)
from atlas.linear.client import LinearComment, LinearIssue, WorkflowState
from atlas.linear.ownership import PACK_HEADER_PREFIX, LinearStatusMap
from atlas.pm import agent_run_observation, reconstruct_agent_runs, sync_tick
from atlas.pm.sync import CREATED_BY
from atlas.storage import (
    AgentRunRepo,
    Database,
    EvidenceRepo,
    TicketRepo,
    TicketStatusTransitionRepo,
    VerificationCheckRepo,
)

TEAM_ID = "team-1"
PROJECT_ID = "project-1"
T0 = datetime(2026, 7, 1, 9, tzinfo=UTC)
T1 = T0 + timedelta(hours=1)
T2 = T0 + timedelta(hours=5)
T3 = T0 + timedelta(hours=6)
HEAD = "abc123def456abc123def456abc123def456abcd"

PLANNED = WorkflowState(id="state-planned", name="Todo", type="unstarted")
READY = WorkflowState(id="state-ready", name="Ready for Agent", type="unstarted")
STARTED = WorkflowState(id="state-started", name="In Progress", type="started")
REVIEW_REQUIRED = WorkflowState(
    id="state-review-required", name="Review Required", type="started"
)
CHANGES_REQUESTED = WorkflowState(
    id="state-changes-requested", name="Changes Requested", type="started"
)
NEEDS_HUMAN = WorkflowState(id="state-needs-human", name="Needs Human", type="started")
DONE = WorkflowState(id="state-done", name="Done", type="completed")


class CountingAgentRunClient(InMemoryLinearClient):
    def __init__(self) -> None:
        super().__init__(
            workflow_states=[
                PLANNED,
                READY,
                STARTED,
                REVIEW_REQUIRED,
                CHANGES_REQUESTED,
                NEEDS_HUMAN,
                DONE,
            ]
        )
        self.calls: dict[str, int] = {}

    def _count(self, method: str) -> None:
        self.calls[method] = self.calls.get(method, 0) + 1

    def fetch_project_issues(self, project_id: str) -> list[LinearIssue]:
        self._count("fetch_project_issues")
        return super().fetch_project_issues(project_id)

    def fetch_comments(self, issue_id: str) -> list[LinearComment]:
        self._count("fetch_comments")
        return super().fetch_comments(issue_id)


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{tmp_path}/atlas.db")
    database.create_all()
    return database


def status_map() -> LinearStatusMap:
    return LinearStatusMap(
        {
            PLANNED.id: TicketStatus.PLANNED,
            READY.id: TicketStatus.READY_FOR_AGENT,
            STARTED.id: TicketStatus.IN_PROGRESS,
            REVIEW_REQUIRED.id: TicketStatus.REVIEW_REQUIRED,
            CHANGES_REQUESTED.id: TicketStatus.CHANGES_REQUESTED,
            NEEDS_HUMAN.id: TicketStatus.NEEDS_HUMAN_DECISION,
            DONE.id: TicketStatus.DONE,
        }
    )


def make_ticket(
    key: str,
    *,
    status: TicketStatus = TicketStatus.PLANNED,
    external_linear_id: str | None = "issue-atlas-161",
) -> Ticket:
    return Ticket(
        **ticket_kwargs()
        | {
            "id": uuid4(),
            "key": key,
            "status": status,
            "external_linear_id": external_linear_id,
            "created_at": T0,
            "updated_at": T0,
            "linear_synced_at": T0,
        }
    )


def transition(
    ticket: Ticket,
    from_status: TicketStatus,
    to_status: TicketStatus,
    when: datetime,
) -> TicketStatusTransition:
    return TicketStatusTransition(
        id=uuid4(),
        ticket_id=ticket.id,
        from_status=from_status.value,
        to_status=to_status.value,
        occurred_at=when,
        created_by_type=ActorType.SYSTEM,
        created_by_id=CREATED_BY,
    )


def seed_transitions(db: Database, rows: list[TicketStatusTransition]) -> None:
    repo = TicketStatusTransitionRepo(db)
    for row in rows:
        repo.record(row)


def evidence(
    ticket: Ticket,
    *,
    commit: str = HEAD,
    pr_number: int = 166,
    created_at: datetime = T3,
) -> Evidence:
    return Evidence(
        id=uuid4(),
        product_id=ticket.product_id,
        ticket_id=ticket.id,
        evidence_type=EvidenceType.TEST_RESULT,
        status=EvidenceStatus.PASSED,
        summary=f"CI passed for PR #{pr_number}",
        commit_sha=commit,
        external_run_id=f"pr:{pr_number}:run-1",
        payload_hash="hash",
        source_uri=f"https://github.com/acme/atlas/pull/{pr_number}",
        raw_payload={"pull_request": {"number": pr_number}},
        created_by_type=ActorType.SYSTEM,
        created_by_id="github-actions",
        created_at=created_at,
    )


def pack_description(pack_id: UUID) -> str:
    return (
        "Atlas definition\n\n---\n"
        f"{PACK_HEADER_PREFIX} | pack_id: {pack_id} | "
        "rendered_at: 2026-07-01T08:00:00+00:00\n"
        "## Objective\nRun the work."
    )


@pytest.fixture
def atlas_161_lifecycle(
    db: Database,
) -> tuple[Ticket, UUID, str, TicketStatusTransition]:
    """Named replay: planned -> ready -> in_progress -> review_required."""

    pack_id = uuid4()
    ticket = make_ticket("ATLAS-161", status=TicketStatus.REVIEW_REQUIRED)
    TicketRepo(db).add(ticket)
    dispatch = transition(
        ticket, TicketStatus.READY_FOR_AGENT, TicketStatus.IN_PROGRESS, T1
    )
    seed_transitions(
        db,
        [
            transition(ticket, TicketStatus.PLANNED, TicketStatus.READY_FOR_AGENT, T0),
            dispatch,
            transition(
                ticket,
                TicketStatus.IN_PROGRESS,
                TicketStatus.REVIEW_REQUIRED,
                T2,
            ),
        ],
    )
    EvidenceRepo(db).add(evidence(ticket))
    return ticket, pack_id, pack_description(pack_id), dispatch


def run_reconstruction(
    db: Database, issue_descriptions_by_id: dict[str, str | None]
) -> None:
    reconstruct_agent_runs(
        tickets=TicketRepo(db),
        db=db,
        issue_descriptions_by_id=issue_descriptions_by_id,
        now=T3,
    )


def only_run(db: Database) -> AgentRun:
    rows = AgentRunRepo(db).list()
    assert len(rows) == 1
    return rows[0]


def test_atlas_161_lifecycle_reconstructs_one_complete_agent_run(
    db: Database,
    atlas_161_lifecycle: tuple[Ticket, UUID, str, TicketStatusTransition],
) -> None:
    ticket, pack_id, description, dispatch = atlas_161_lifecycle

    result = reconstruct_agent_runs(
        tickets=TicketRepo(db),
        db=db,
        issue_descriptions_by_id={ticket.external_linear_id or "": description},
        now=T3,
    )

    assert result.created == 1
    run = only_run(db)
    details = agent_run_observation(run)
    assert run.ticket_id == ticket.id
    assert run.started_at == T1
    assert run.completed_at == T2
    assert run.input_context_pack_id == pack_id
    assert details["dispatch_transition_id"] == str(dispatch.id)
    assert details["handoff_state"] == TicketStatus.REVIEW_REQUIRED.value
    assert details["pr_number"] == 166
    assert details["head_commit"] == HEAD


def test_atlas_255_ci_pending_completes_the_agent_run_at_handoff(
    db: Database,
) -> None:
    ticket = make_ticket("ATLAS-255", status=TicketStatus.CI_PENDING)
    TicketRepo(db).add(ticket)
    seed_transitions(
        db,
        [
            transition(ticket, TicketStatus.PLANNED, TicketStatus.READY_FOR_AGENT, T0),
            transition(
                ticket, TicketStatus.READY_FOR_AGENT, TicketStatus.IN_PROGRESS, T1
            ),
            transition(ticket, TicketStatus.IN_PROGRESS, TicketStatus.PR_OPEN, T2),
            transition(
                ticket,
                TicketStatus.PR_OPEN,
                TicketStatus.CI_PENDING,
                T3,
            ),
        ],
    )

    result = reconstruct_agent_runs(
        tickets=TicketRepo(db),
        db=db,
        issue_descriptions_by_id={ticket.external_linear_id or "": None},
        now=T3,
    )

    run = only_run(db)
    assert result.created == 1
    assert run.completed_at == T3
    assert agent_run_observation(run)["handoff_state"] == "ci_pending"


def test_idempotence_and_genuine_redispatch_creates_second_run(
    db: Database,
    atlas_161_lifecycle: tuple[Ticket, UUID, str, TicketStatusTransition],
) -> None:
    ticket, _pack_id, description, _dispatch = atlas_161_lifecycle

    first = reconstruct_agent_runs(
        tickets=TicketRepo(db),
        db=db,
        issue_descriptions_by_id={ticket.external_linear_id or "": description},
        now=T3,
    )
    second = reconstruct_agent_runs(
        tickets=TicketRepo(db),
        db=db,
        issue_descriptions_by_id={ticket.external_linear_id or "": description},
        now=T3 + timedelta(minutes=5),
    )

    assert first.created == 1
    assert second.created == 0
    assert AgentRunRepo(db).list_for_ticket(ticket.id) == AgentRunRepo(db).list()
    assert len(AgentRunRepo(db).list()) == 1

    redispatch_at = T3 + timedelta(minutes=10)
    second_dispatch = transition(
        ticket, TicketStatus.CHANGES_REQUESTED, TicketStatus.IN_PROGRESS, redispatch_at
    )
    seed_transitions(
        db,
        [
            transition(
                ticket,
                TicketStatus.REVIEW_REQUIRED,
                TicketStatus.CHANGES_REQUESTED,
                redispatch_at - timedelta(minutes=5),
            ),
            second_dispatch,
            transition(
                ticket,
                TicketStatus.IN_PROGRESS,
                TicketStatus.NEEDS_HUMAN_DECISION,
                redispatch_at + timedelta(hours=2),
            ),
        ],
    )
    EvidenceRepo(db).add(
        evidence(
            ticket,
            commit="feedfacefeedfacefeedfacefeedfacefeedface",
            pr_number=167,
            created_at=redispatch_at + timedelta(hours=1),
        )
    )

    redispatch = reconstruct_agent_runs(
        tickets=TicketRepo(db),
        db=db,
        issue_descriptions_by_id={ticket.external_linear_id or "": description},
        now=T3 + timedelta(hours=3),
    )

    rows = AgentRunRepo(db).list_for_ticket(ticket.id)
    assert redispatch.created == 1
    assert len(rows) == 2
    newest = rows[-1]
    details = agent_run_observation(newest)
    assert details["dispatch_transition_id"] == str(second_dispatch.id)
    assert details["handoff_state"] == TicketStatus.NEEDS_HUMAN_DECISION.value
    assert details["pr_number"] == 167
    assert details["head_commit"] == "feedfacefeedfacefeedfacefeedfacefeedface"


def test_partial_observation_records_null_pr_and_commit(db: Database) -> None:
    ticket = make_ticket("ATLAS-162", status=TicketStatus.REVIEW_REQUIRED)
    TicketRepo(db).add(ticket)
    seed_transitions(
        db,
        [
            transition(ticket, TicketStatus.PLANNED, TicketStatus.READY_FOR_AGENT, T0),
            transition(
                ticket, TicketStatus.READY_FOR_AGENT, TicketStatus.IN_PROGRESS, T1
            ),
            transition(
                ticket,
                TicketStatus.IN_PROGRESS,
                TicketStatus.REVIEW_REQUIRED,
                T2,
            ),
        ],
    )

    result = reconstruct_agent_runs(
        tickets=TicketRepo(db),
        db=db,
        issue_descriptions_by_id={ticket.external_linear_id or "": None},
        now=T3,
    )

    assert result.created == 1
    run = only_run(db)
    details = agent_run_observation(run)
    assert run.started_at == T1
    assert run.completed_at == T2
    assert run.input_context_pack_id is None
    assert details["pr_number"] is None
    assert details["head_commit"] is None
    assert details["handoff_state"] == TicketStatus.REVIEW_REQUIRED.value


def test_verification_linked_evidence_supplies_pr_and_head_commit(
    db: Database,
) -> None:
    pack_id = uuid4()
    ticket = make_ticket("ATLAS-164", status=TicketStatus.REVIEW_REQUIRED)
    TicketRepo(db).add(ticket)
    seed_transitions(
        db,
        [
            transition(ticket, TicketStatus.PLANNED, TicketStatus.READY_FOR_AGENT, T0),
            transition(
                ticket, TicketStatus.READY_FOR_AGENT, TicketStatus.IN_PROGRESS, T1
            ),
            transition(
                ticket,
                TicketStatus.IN_PROGRESS,
                TicketStatus.REVIEW_REQUIRED,
                T2,
            ),
        ],
    )
    unscoped = evidence(ticket).model_copy(update={"ticket_id": None})
    EvidenceRepo(db).add(unscoped)
    VerificationCheckRepo(db).add(
        VerificationCheck(
            id=uuid4(),
            ticket_id=ticket.id,
            check_type=VerificationCheckType.TESTS,
            status=EvidenceStatus.PASSED,
            summary="tests passed from PR verification",
            evidence_ids=[unscoped.id],
            created_at=T3,
            completed_at=T3,
        )
    )

    result = reconstruct_agent_runs(
        tickets=TicketRepo(db),
        db=db,
        issue_descriptions_by_id={
            ticket.external_linear_id or "": pack_description(pack_id)
        },
        now=T3,
    )

    assert result.created == 1
    details = agent_run_observation(only_run(db))
    assert details["pr_number"] == 166
    assert details["head_commit"] == HEAD


def test_sync_tick_updates_partial_row_after_later_handoff_and_evidence(
    db: Database, tmp_path: Path
) -> None:
    client = CountingAgentRunClient()
    pack_id = uuid4()
    issue = client.create_issue(
        {"title": "ATLAS-163: Run", "description": pack_description(pack_id)},
        team_id=TEAM_ID,
        project_id=PROJECT_ID,
    )
    client.simulate_linear_state(issue.id, STARTED)
    ticket = make_ticket(
        "ATLAS-163",
        status=TicketStatus.READY_FOR_AGENT,
        external_linear_id=issue.id,
    )
    TicketRepo(db).add(ticket)
    client.calls.clear()

    first = sync_tick(
        tickets=TicketRepo(db),
        db=db,
        client=client,
        status_map=status_map(),
        team_id=TEAM_ID,
        project_id=PROJECT_ID,
        inbox_dir=tmp_path,
        documents=lambda: [],
        now=T1,
    )

    rows = AgentRunRepo(db).list()
    assert first.agent_runs_reconstructed == 1
    assert len(rows) == 1
    assert rows[0].started_at == T1
    assert rows[0].completed_at is None
    assert agent_run_observation(rows[0])["head_commit"] is None
    assert client.calls["fetch_project_issues"] == 1

    EvidenceRepo(db).add(evidence(ticket, created_at=T2))
    client.simulate_linear_state(issue.id, REVIEW_REQUIRED)
    client.calls.clear()

    second = sync_tick(
        tickets=TicketRepo(db),
        db=db,
        client=client,
        status_map=status_map(),
        team_id=TEAM_ID,
        project_id=PROJECT_ID,
        inbox_dir=tmp_path,
        documents=lambda: [],
        now=T2,
    )

    rows = AgentRunRepo(db).list()
    details = agent_run_observation(rows[0])
    assert second.agent_runs_reconstructed == 0
    assert second.agent_runs_updated == 1
    assert len(rows) == 1
    assert rows[0].completed_at == T2
    assert rows[0].input_context_pack_id == pack_id
    assert details["handoff_state"] == TicketStatus.REVIEW_REQUIRED.value
    assert details["pr_number"] == 166
    assert details["head_commit"] == HEAD
    assert client.calls["fetch_project_issues"] == 1
    assert client.calls.get("fetch_issue", 0) == 0
