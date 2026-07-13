"""PM-Engine verified-completion milestone proofs (ATLAS-131, step 3b).

The Verification Engine persists a per-ticket verdict (append-only
``VerificationCheck`` rows) but never transitions a ticket; this step is the PM-side
consumer that moves every ``review_required`` ticket whose PERSISTED verdict is PASSED
to ``Done`` in Linear, via the same sanctioned, Linear-only ``set_state`` path
``promote_ready`` uses. Built and tested against SEEDED ``VerificationCheck`` rows (the
synthetic-verdict path) — the evaluators are not exercised here.

Falsifiable, with the wrong answer named in each case:

- AC-1 PASS -> Done: a review_required ticket with a latest PASSED row for every
  required check type is moved to the unique Done state via set_state, returns 1.
  Wrong answer: no set_state.
- AC-2 missing required holds PENDING: one required type has no row -> verdict PENDING
  -> no Done, returns 0. Wrong answer: a false Done.
- AC-3 FAILED -> no Done: a required check's latest row is FAILED -> verdict FAILED ->
  no Done. Wrong answer: moved anyway.
- AC-5 only review_required: a fully-PASSED ticket in in_progress is never moved.
  Wrong answer: eligibility widened past review_required.
- AC-6 no Linear id -> skip: review_required + PASSED + external_linear_id None is
  skipped with a log, returns 0, no crash. Wrong answer: a crash or an incorrect call.
- AC-7 load-time guard: a status map with zero or ambiguous Done state raises up front,
  even with nothing completable. Wrong answer: a lazy resolve lets a no-op run succeed.
- AC-8 Linear-only: after a move the Atlas status is still review_required (the pull
  reconciles next tick). Wrong answer: an Atlas-side status write.
- AC-9 idempotent re-run: two calls before any pull both issue an idempotent set_state,
  no crash, no duplicate transition; the counter re-increments.
- AC-10 tick integration: sync_tick invokes the step after promote_ready and surfaces
  the count in SyncResult.completed; one eligible PASSED ticket reports completed == 1
  and records the set_state(Done) on the fake client.

Deterministic: the in-memory fake, no network, no secrets.
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from test_models_validation import NOW, ticket_kwargs
from test_pm_sync import (
    DONE_STATE,
    NEEDS_HUMAN,
    PROJECT_ID,
    READY,
    STARTED,
    TEAM_ID,
    UNSTARTED,
    RecordingClient,
    status_map,
)

from atlas.core.enums import ActorType, EvidenceStatus
from atlas.core.models import Evidence, Ticket, VerificationCheck
from atlas.core.models.evidence import EvidenceType
from atlas.core.models.ticket import TicketStatus
from atlas.core.models.verification_check import VerificationCheckType
from atlas.evidence import build_merge_evidence
from atlas.linear.client import WorkflowState
from atlas.linear.ownership import LinearStatusMap, LinearStatusMapError
from atlas.pm import SyncResult, complete_verified, sync_tick
from atlas.storage import Database, EvidenceRepo, TicketRepo, VerificationCheckRepo
from atlas.verification import required_checks

EARLIER = NOW
LATER = NOW + timedelta(hours=1)

# The PR head commit the verdict's proof and the merge record are pinned to. A
# system-tier proof Evidence at this commit (referenced by the PASSED check rows)
# is what the ATLAS-134 gate reads the verdict's commit from; a PR_MERGED at the
# SAME commit satisfies the merge gate (a different commit is the stale-merge case).
PROOF_COMMIT = "a" * 40
OTHER_COMMIT = "b" * 40

# A state mapped to review_required (type "started", per _ACCEPTED_TYPES), so the
# AC-10 tick's pull reads the issue back as review_required (set-to-same) and the
# ticket stays eligible through the pull/push loop until the completion step.
REVIEW_STATE = WorkflowState(id="state-review", name="In Review", type="started")


def tick_status_map() -> LinearStatusMap:
    """The shared map plus a review_required-mapped state — a self-contained map
    carrying the three states sync_tick resolves up front (ready_for_agent,
    needs_human_decision, done) and a review state for the set-to-same pull."""
    return LinearStatusMap(
        {
            UNSTARTED.id: TicketStatus.PLANNED,
            STARTED.id: TicketStatus.IN_PROGRESS,
            READY.id: TicketStatus.READY_FOR_AGENT,
            NEEDS_HUMAN.id: TicketStatus.NEEDS_HUMAN_DECISION,
            DONE_STATE.id: TicketStatus.DONE,
            REVIEW_STATE.id: TicketStatus.REVIEW_REQUIRED,
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
    with_issue: bool = True,
    issue_state: WorkflowState | None = None,
) -> Ticket:
    """Insert a ticket, optionally joined to a fake Linear issue. Clears the
    recorder so only this leg's writes are observed. The ticket is a feature/low
    ticket (ticket_kwargs default), so its gating required set is
    {TESTS, LINT, ACCEPTANCE_CRITERIA, SCOPE}."""

    external_id: str | None = None
    if with_issue:
        issue = client.create_issue(
            {"title": "Linear Title", "description": "linear"},
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
            "external_linear_id": external_id,
            "created_at": EARLIER,
            "updated_at": EARLIER,
            "linear_synced_at": EARLIER,
        }
    )
    TicketRepo(db).add(ticket)
    client.creates.clear()
    client.updates.clear()
    client.state_writes.clear()
    return ticket


def _check(
    ticket: Ticket,
    check_type: VerificationCheckType,
    status: EvidenceStatus,
    *,
    created_at: datetime = EARLIER,
    evidence_ids: list[UUID] | None = None,
) -> VerificationCheck:
    return VerificationCheck(
        id=uuid4(),
        ticket_id=ticket.id,
        check_type=check_type,
        status=status,
        summary="seeded row",
        required=True,
        evidence_ids=evidence_ids or [],
        created_at=created_at,
    )


def _system_evidence(
    ticket: Ticket,
    evidence_type: EvidenceType,
    commit: str,
    *,
    created_by_type: ActorType = ActorType.SYSTEM,
) -> Evidence:
    """A commit-pinned ticket-scoped Evidence (system-tier by default). The pin
    triple satisfies EvidenceRepo.add's ADR-0008 guard for system-tier records."""
    return Evidence(
        id=uuid4(),
        product_id=ticket.product_id,
        ticket_id=ticket.id,
        evidence_type=evidence_type,
        status=EvidenceStatus.PASSED,
        summary="seeded evidence",
        commit_sha=commit,
        external_run_id=f"{evidence_type.value}:{commit}",
        payload_hash="sha256:" + "0" * 64,
        created_by_type=created_by_type,
        created_by_id="github-actions",
        created_at=EARLIER,
    )


def seed_all_passed(
    db: Database,
    ticket: Ticket,
    *,
    commit: str = PROOF_COMMIT,
    merged: bool = True,
    merge_commit: str | None = None,
) -> None:
    """Persist a latest-PASSED row for every GATING required check type of
    ``ticket`` (driven off the resolver, so it tracks the matrix), each referencing
    a system-tier proof Evidence pinned to ``commit`` -- so the ATLAS-134 gate can
    read the verdict's commit from its proof. By default also seeds a system-tier
    ``PR_MERGED`` at ``commit`` so the merge gate is satisfied (the post-ATLAS-134
    "completable" shape); pass ``merged=False`` for the unmerged case or
    ``merge_commit`` to place the merge at a different (stale) commit."""
    ev_repo = EvidenceRepo(db)
    proof = _system_evidence(ticket, EvidenceType.TEST_RESULT, commit)
    ev_repo.add(proof)
    repo = VerificationCheckRepo(db)
    for rc in required_checks(ticket):
        if rc.required:
            repo.add(
                _check(
                    ticket,
                    rc.check_type,
                    EvidenceStatus.PASSED,
                    evidence_ids=[proof.id],
                )
            )
    if merged:
        ev_repo.add(
            _system_evidence(ticket, EvidenceType.PR_MERGED, merge_commit or commit)
        )


def gating_types(ticket: Ticket) -> list[VerificationCheckType]:
    return [rc.check_type for rc in required_checks(ticket) if rc.required]


def run_complete(db: Database, client: RecordingClient) -> int:
    return complete_verified(
        tickets=TicketRepo(db), db=db, client=client, status_map=status_map()
    )


# --- AC-1: PASS -> Done -----------------------------------------------------


def test_passed_verdict_moves_ticket_to_done(db: Database) -> None:
    client = RecordingClient()
    ticket = seed_ticket(
        db, client, key="ATLAS-400", status=TicketStatus.REVIEW_REQUIRED
    )
    seed_all_passed(db, ticket)

    completed = run_complete(db, client)

    assert completed == 1  # wrong answer: 0 (no set_state issued)
    # The sanctioned path wrote the unique Done state, nothing else.
    assert client.state_writes == [(ticket.external_linear_id, DONE_STATE.id)]
    issue = client.fetch_issue(ticket.external_linear_id or "")
    assert issue is not None and issue.state_id == DONE_STATE.id


# --- AC-2: missing required holds PENDING -> no Done ------------------------


def test_missing_required_check_is_not_completed(db: Database) -> None:
    client = RecordingClient()
    ticket = seed_ticket(
        db, client, key="ATLAS-401", status=TicketStatus.REVIEW_REQUIRED
    )
    # PASSED for every required type EXCEPT one (no row for it) -> verdict PENDING.
    repo = VerificationCheckRepo(db)
    for check_type in gating_types(ticket)[:-1]:
        repo.add(_check(ticket, check_type, EvidenceStatus.PASSED))

    completed = run_complete(db, client)

    assert completed == 0  # wrong answer: 1 (a missing required type held the verdict)
    assert client.state_writes == []


# --- AC-3: FAILED -> no Done ------------------------------------------------


def test_failed_verdict_is_not_completed(db: Database) -> None:
    client = RecordingClient()
    ticket = seed_ticket(
        db, client, key="ATLAS-402", status=TicketStatus.REVIEW_REQUIRED
    )
    seed_all_passed(db, ticket)
    # Supersede nothing; add a FAILED row for one required type whose latest now fails.
    VerificationCheckRepo(db).add(
        _check(ticket, gating_types(ticket)[0], EvidenceStatus.FAILED, created_at=LATER)
    )

    completed = run_complete(db, client)

    assert completed == 0  # wrong answer: 1 (fail precedence ignored)
    assert client.state_writes == []


# --- AC-5: only from review_required ----------------------------------------


def test_passed_ticket_not_in_review_required_is_not_completed(db: Database) -> None:
    client = RecordingClient()
    # Fully PASSED but in in_progress: eligibility is review_required only.
    ticket = seed_ticket(db, client, key="ATLAS-403", status=TicketStatus.IN_PROGRESS)
    seed_all_passed(db, ticket)

    completed = run_complete(db, client)

    assert completed == 0  # wrong answer: 1 (eligibility widened past review_required)
    assert client.state_writes == []


# --- AC-6: no Linear id -> skip ---------------------------------------------


def test_passed_ticket_without_linear_id_is_skipped(db: Database) -> None:
    client = RecordingClient()
    ticket = seed_ticket(
        db,
        client,
        key="ATLAS-404",
        status=TicketStatus.REVIEW_REQUIRED,
        with_issue=False,  # external_linear_id is None
    )
    seed_all_passed(db, ticket)
    assert ticket.external_linear_id is None

    completed = run_complete(db, client)  # wrong answer: a crash / an incorrect call

    assert completed == 0
    assert client.state_writes == []


# --- AC-7: load-time guard --------------------------------------------------


def test_zero_done_states_raises_up_front_with_nothing_completable(
    db: Database,
) -> None:
    client = RecordingClient()
    # No ticket is completable (empty backlog), yet the Done resolution must fire.
    no_done_map = LinearStatusMap(
        {
            "state-unstarted": TicketStatus.PLANNED,
            READY.id: TicketStatus.READY_FOR_AGENT,
        }
    )
    with pytest.raises(LinearStatusMapError, match="no Linear state for"):
        complete_verified(
            tickets=TicketRepo(db), db=db, client=client, status_map=no_done_map
        )


def test_ambiguous_done_states_raise_up_front(db: Database) -> None:
    client = RecordingClient()
    ambiguous = LinearStatusMap(
        {"state-done-1": TicketStatus.DONE, "state-done-2": TicketStatus.DONE}
    )
    with pytest.raises(LinearStatusMapError, match="exactly one"):
        complete_verified(
            tickets=TicketRepo(db), db=db, client=client, status_map=ambiguous
        )


# --- AC-8: Linear-only; Atlas-status writer preserved -----------------------


def test_completion_does_not_write_atlas_status(db: Database) -> None:
    client = RecordingClient()
    ticket = seed_ticket(
        db, client, key="ATLAS-405", status=TicketStatus.REVIEW_REQUIRED
    )
    seed_all_passed(db, ticket)

    run_complete(db, client)

    after = TicketRepo(db).get_by_key("ATLAS-405")
    # wrong answer: done — only Linear was written; the next pull reconciles Atlas.
    assert after is not None and after.status == TicketStatus.REVIEW_REQUIRED


# --- AC-9: idempotent re-run ------------------------------------------------


def test_re_run_is_idempotent(db: Database) -> None:
    client = RecordingClient()
    ticket = seed_ticket(
        db, client, key="ATLAS-406", status=TicketStatus.REVIEW_REQUIRED
    )
    seed_all_passed(db, ticket)

    first = run_complete(db, client)
    second = run_complete(db, client)  # before any pull reconciles

    assert first == 1 and second == 1  # counter re-increments each attempt
    # set_state fired both times (idempotent no-op the second); no crash, no flap.
    assert client.state_writes == [
        (ticket.external_linear_id, DONE_STATE.id),
        (ticket.external_linear_id, DONE_STATE.id),
    ]
    issue = client.fetch_issue(ticket.external_linear_id or "")
    assert issue is not None and issue.state_id == DONE_STATE.id


# --- AC-10: tick integration ------------------------------------------------


def test_sync_tick_surfaces_completed_count(db: Database) -> None:
    client = RecordingClient()
    # The issue sits in a review_required-mapped state so the tick's pull is
    # set-to-same and the ticket stays review_required through the pull/push loop;
    # the completion step (after promote_ready) then moves it to Done.
    ticket = seed_ticket(
        db,
        client,
        key="ATLAS-407",
        status=TicketStatus.REVIEW_REQUIRED,
        issue_state=REVIEW_STATE,
    )
    seed_all_passed(db, ticket)

    result = sync_tick(
        tickets=TicketRepo(db),
        db=db,
        client=client,
        status_map=tick_status_map(),
        team_id=TEAM_ID,
        project_id=PROJECT_ID,
        inbox_dir=Path(tempfile.mkdtemp()),
        documents=lambda: [],
        now=NOW,
    )

    assert isinstance(result, SyncResult)
    assert result.completed == 1  # wrong answer: 0 (step not wired into the tick)
    assert client.state_writes == [(ticket.external_linear_id, DONE_STATE.id)]
    # Linear-only: Atlas is not written this tick (the pull was set-to-same).
    after = TicketRepo(db).get_by_key("ATLAS-407")
    assert after is not None and after.status == TicketStatus.REVIEW_REQUIRED


# === ATLAS-134: merged-PR gate on review_required -> done ====================
# Done now requires BOTH a PASSED verdict AND a merged PR (operator ruling,
# Option A). The verdict's meaning is unchanged; the merge is a SEPARATE
# condition, read from the system-tier PR_MERGED evidence at the verdict commit.


def test_passed_and_merged_at_verdict_commit_completes(db: Database) -> None:
    """ATLAS-134 AC-4: PASSED verdict proven at C + PR_MERGED at C -> Done."""
    client = RecordingClient()
    ticket = seed_ticket(
        db, client, key="ATLAS-410", status=TicketStatus.REVIEW_REQUIRED
    )
    seed_all_passed(db, ticket, commit=PROOF_COMMIT, merged=True)

    completed = run_complete(db, client)

    assert completed == 1  # wrong answer: 0 (merge at the verdict commit not honoured)
    assert client.state_writes == [(ticket.external_linear_id, DONE_STATE.id)]


def test_passed_but_unmerged_is_not_completed(db: Database) -> None:
    """ATLAS-134 AC-5: PASSED verdict, NO PR_MERGED record -> not completed."""
    client = RecordingClient()
    ticket = seed_ticket(
        db, client, key="ATLAS-411", status=TicketStatus.REVIEW_REQUIRED
    )
    seed_all_passed(db, ticket, commit=PROOF_COMMIT, merged=False)

    completed = run_complete(db, client)

    # wrong answer: 1 (a missing merge record treated as satisfied)
    assert completed == 0
    assert client.state_writes == []


def test_merge_at_different_commit_is_not_completed(db: Database) -> None:
    """ATLAS-134 AC-6 (stale-merge guard): PASSED proven at C, but the only
    PR_MERGED is at C' != C (a re-push / second PR) -> not completed."""
    client = RecordingClient()
    ticket = seed_ticket(
        db, client, key="ATLAS-412", status=TicketStatus.REVIEW_REQUIRED
    )
    seed_all_passed(
        db, ticket, commit=PROOF_COMMIT, merged=True, merge_commit=OTHER_COMMIT
    )

    completed = run_complete(db, client)

    # wrong answer: 1 (matching any PR_MERGED regardless of commit completes a
    # re-pushed head off a stale merge)
    assert completed == 0
    assert client.state_writes == []


def test_non_system_tier_merge_claim_is_ignored(db: Database) -> None:
    """ATLAS-134 AC-7: a PR_MERGED that is not system-tier does not satisfy the gate."""
    client = RecordingClient()
    ticket = seed_ticket(
        db, client, key="ATLAS-413", status=TicketStatus.REVIEW_REQUIRED
    )
    # PASSED verdict proven at C, but no system-tier merge: only a HUMAN-tier
    # PR_MERGED at C (human-tier needs no pin and any status is allowed).
    seed_all_passed(db, ticket, commit=PROOF_COMMIT, merged=False)
    EvidenceRepo(db).add(
        _system_evidence(
            ticket,
            EvidenceType.PR_MERGED,
            PROOF_COMMIT,
            created_by_type=ActorType.HUMAN,
        )
    )

    completed = run_complete(db, client)

    assert completed == 0  # wrong answer: 1 (tier check dropped)
    assert client.state_writes == []


def test_builder_record_is_read_by_the_gate_round_trip(db: Database) -> None:
    """ATLAS-134 AC-8 (writer-reader tie): a merge record built by the PRODUCER
    (build_merge_evidence) at C, fed with a PASSED verdict proven at C, completes
    -- proving verify writes exactly what the gate reads."""
    client = RecordingClient()
    ticket = seed_ticket(
        db, client, key="ATLAS-414", status=TicketStatus.REVIEW_REQUIRED
    )
    seed_all_passed(db, ticket, commit=PROOF_COMMIT, merged=False)
    record = build_merge_evidence(
        {"merged": True},
        head_commit=PROOF_COMMIT,
        ticket_id=ticket.id,
        product_id=ticket.product_id,
        evidence_id=uuid4(),
        now=EARLIER,
    )
    assert record is not None  # the builder produced a record for a merged PR
    EvidenceRepo(db).add(record)

    completed = run_complete(db, client)

    # wrong answer: 0 (e.g. the builder pins commit_sha=None -> the gate cannot read it)
    assert completed == 1
    assert client.state_writes == [(ticket.external_linear_id, DONE_STATE.id)]
