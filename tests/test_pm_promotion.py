"""PM-Engine readiness-promotion milestone proofs (ATLAS-43, step 3).

Falsifiable, with the wrong answer named in each case:

- Promotion: a dependency-ready ticket in a pre-dispatch status is promoted --
  the unique Ready-for-Agent Linear state is written via the sanctioned
  ``set_state``. Wrong answer: the issue stays in its old state / no set_state.
- Gating: a not-ready ticket is NOT promoted. ``is_ready`` enforces the FULL
  predicate, so both a missing-criteria ticket and one blocked by an unfinished
  dependency are held back. Wrong answer: promoted anyway.
- Idempotency: an already-``ready_for_agent`` ticket is a no-op (``is_ready``
  reports wrong_status). Wrong answer: re-promoted every tick.
- Round-trip: after promotion, ATLAS-42's next pull reads the written state
  back as ``ready_for_agent`` and no-ops -- no flap, no spurious re-push, and
  the Atlas ``updated_at`` is never bumped. Wrong answer: a flap, a re-push, or
  a bumped ``updated_at``.
- Narrow exception: ``set_state`` is the only status-write path; the definition
  path still rejects a ``stateId``. Wrong answer: ``stateId`` accepted by
  ``update_issue``/``definition_payload``.
- Inverse-map guard: zero or ambiguous Ready-for-Agent states raise at load.
  Wrong answer: a state silently chosen, or no raise.

Deterministic: the in-memory fake, no network, no secrets.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pytest
from linear_fakes import InMemoryLinearClient
from test_models_validation import NOW, dependency_kwargs, ticket_kwargs
from test_pm_sync import READY, STARTED, TEAM_ID, UNSTARTED, RecordingClient, status_map

from atlas.core.models import Ticket, TicketDependency
from atlas.core.models.ticket import TicketStatus
from atlas.linear.client import UnownedFieldError, WorkflowState
from atlas.linear.ownership import (
    LinearStatusMap,
    LinearStatusMapError,
    definition_payload,
)
from atlas.pm import SyncResult, sync_tick
from atlas.storage import Database, TicketDependencyRepo, TicketRepo

EARLIER = NOW


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{tmp_path}/atlas.db")
    database.create_all()
    return database


def seed(
    db: Database,
    client: RecordingClient,
    *,
    key: str,
    status: TicketStatus,
    acceptance_criteria: list[str],
    with_issue: bool = True,
    issue_state: WorkflowState | None = UNSTARTED,
    updated_at: datetime = EARLIER,
    linear_synced_at: datetime | None = EARLIER,
) -> Ticket:
    """Insert a ticket, optionally joined to a fake Linear issue. Defaults to a
    clean cursor (synced == updated) so the push step is a no-op and the test
    observes the promotion in isolation."""

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
            "acceptance_criteria": acceptance_criteria,
            "external_linear_id": external_id,
            "created_at": updated_at,
            "updated_at": updated_at,
            "linear_synced_at": linear_synced_at,
        }
    )
    TicketRepo(db).add(ticket)
    client.creates.clear()
    client.updates.clear()
    client.state_writes.clear()
    return ticket


def run(db: Database, client: RecordingClient) -> SyncResult:
    return sync_tick(
        tickets=TicketRepo(db),
        db=db,
        client=client,
        status_map=status_map(),
        team_id=TEAM_ID,
    )


# --- promotion --------------------------------------------------------------


def test_dependency_ready_ticket_is_promoted(db: Database) -> None:
    client = RecordingClient()
    ticket = seed(
        db,
        client,
        key="ATLAS-300",
        status=TicketStatus.PLANNED,
        acceptance_criteria=["ship it"],
    )

    result = run(db, client)

    assert result.promoted == 1
    # The sanctioned path wrote the unique ready_for_agent state, nothing else.
    assert client.state_writes == [(ticket.external_linear_id, READY.id)]
    issue = client.fetch_issue(ticket.external_linear_id or "")
    assert issue is not None
    assert issue.state_id == READY.id  # wrong answer: still UNSTARTED
    # Linear-only: Atlas is not written this tick; the next pull reconciles it.
    after = TicketRepo(db).get_by_key("ATLAS-300")
    assert after is not None and after.status == TicketStatus.PLANNED


def test_missing_criteria_ticket_is_not_promoted(db: Database) -> None:
    client = RecordingClient()
    seed(
        db,
        client,
        key="ATLAS-301",
        status=TicketStatus.PLANNED,
        acceptance_criteria=[],  # is_ready -> NO_ACCEPTANCE_CRITERIA
    )

    result = run(db, client)

    assert result.promoted == 0  # wrong answer: 1 (criteria gate is real)
    assert client.state_writes == []


def test_ticket_blocked_by_unfinished_dependency_is_not_promoted(db: Database) -> None:
    client = RecordingClient()
    # A depends_on B; B is in_progress (not done), so A is not dependency-ready
    # even though it has criteria and a pre-dispatch status.
    a = seed(
        db,
        client,
        key="ATLAS-310",
        status=TicketStatus.PLANNED,
        acceptance_criteria=["c"],
    )
    b = seed(
        db,
        client,
        key="ATLAS-311",
        status=TicketStatus.IN_PROGRESS,
        acceptance_criteria=["c"],
        with_issue=False,  # no pull/push interference for the dependency
    )
    TicketDependencyRepo(db).add(
        TicketDependency(
            **dependency_kwargs()
            | {
                "id": uuid4(),
                "source_ticket_id": a.id,
                "target_entity_type": "ticket",
                "target_entity_id": b.id,
                "dependency_type": "depends_on",
            }
        )
    )

    result = run(db, client)

    assert result.promoted == 0  # wrong answer: A promoted while B unfinished
    assert (a.external_linear_id, READY.id) not in client.state_writes


def test_already_ready_for_agent_is_not_repromoted(db: Database) -> None:
    client = RecordingClient()
    # is_ready reports WRONG_STATUS for a ready_for_agent ticket, so promotion
    # is a no-op -- idempotent. The issue already sits in the ready state.
    seed(
        db,
        client,
        key="ATLAS-302",
        status=TicketStatus.READY_FOR_AGENT,
        acceptance_criteria=["c"],
        issue_state=READY,
    )

    result = run(db, client)

    assert result.promoted == 0  # wrong answer: re-promoted
    assert client.state_writes == []


# --- round-trip / no flap ---------------------------------------------------


def test_promotion_round_trips_without_flap_or_repush(db: Database) -> None:
    client = RecordingClient()
    ticket = seed(
        db,
        client,
        key="ATLAS-303",
        status=TicketStatus.PLANNED,
        acceptance_criteria=["c"],
    )
    before = TicketRepo(db).get_by_key("ATLAS-303")
    assert before is not None

    first = run(db, client)  # tick 1: promote in Linear only
    after_first = TicketRepo(db).get_by_key("ATLAS-303")
    second = run(db, client)  # tick 2: pull reconciles Atlas, promotion no-ops
    after_second = TicketRepo(db).get_by_key("ATLAS-303")

    assert after_first is not None and after_second is not None
    assert first.promoted == 1 and second.promoted == 0
    # The pull reads the written state back as ready_for_agent exactly once.
    assert first.status_pulled == 0  # tick 1 pull was set-to-same (planned)
    assert second.status_pulled == 1
    assert after_second.status == TicketStatus.READY_FOR_AGENT
    # No flap: set_state fired exactly once across both ticks.
    assert client.state_writes == [(ticket.external_linear_id, READY.id)]
    # No spurious re-push: the definition path never fired, and the inbound
    # status change did not bump updated_at (so the cursor never re-triggers).
    assert client.updates == [] and client.creates == []
    assert first.pushed_updated == 0 and second.pushed_updated == 0
    assert after_second.updated_at == before.updated_at


# --- narrow exception -------------------------------------------------------


def test_set_state_is_the_only_status_write_path() -> None:
    client = InMemoryLinearClient()
    issue = client.create_issue({"title": "t", "description": "d"}, team_id="t")

    # The sanctioned path moves state...
    moved = client.set_state(issue.id, READY.id)
    assert moved.state_id == READY.id

    # ...but the definition path still cannot carry one (wrong answer: accepted).
    with pytest.raises(UnownedFieldError):
        client.update_issue(issue.id, {"title": "t", "stateId": READY.id})


def test_definition_payload_never_carries_a_state() -> None:
    ticket = Ticket(**ticket_kwargs() | {"id": uuid4(), "status": TicketStatus.DONE})
    payload = definition_payload(ticket)
    assert "stateId" not in payload and "state" not in payload


# --- inverse-map guard (load-time) -----------------------------------------


def test_zero_ready_for_agent_states_raises(db: Database) -> None:
    client = RecordingClient()
    seed(
        db,
        client,
        key="ATLAS-304",
        status=TicketStatus.PLANNED,
        acceptance_criteria=["c"],
    )
    no_ready_map = LinearStatusMap(
        {UNSTARTED.id: TicketStatus.PLANNED, STARTED.id: TicketStatus.IN_PROGRESS}
    )
    with pytest.raises(LinearStatusMapError, match="no Linear state for"):
        sync_tick(
            tickets=TicketRepo(db),
            db=db,
            client=client,
            status_map=no_ready_map,
            team_id=TEAM_ID,
        )


def test_ambiguous_ready_for_agent_states_raise() -> None:
    ambiguous = LinearStatusMap(
        {
            "state-ready-1": TicketStatus.READY_FOR_AGENT,
            "state-ready-2": TicketStatus.READY_FOR_AGENT,
        }
    )
    with pytest.raises(LinearStatusMapError, match="exactly one"):
        ambiguous.state_id_for(TicketStatus.READY_FOR_AGENT)


def test_unique_ready_for_agent_state_resolves() -> None:
    smap = status_map()
    assert smap.state_id_for(TicketStatus.READY_FOR_AGENT) == READY.id
