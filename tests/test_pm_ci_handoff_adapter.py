"""ATLAS-263 production PM-cadence reachability for CI handoff authority."""

from __future__ import annotations

import tempfile
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from threading import Barrier, Event
from typing import Any
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from github_fakes import FakeGitHubClient
from pm_temporal_harness import SimulatedProcessDeath
from test_models_validation import NOW
from test_pm_sync import (
    CI_PENDING_STATE,
    PACK_DOC,
    PROJECT_ID,
    READY,
    REVIEW_REQUIRED_STATE,
    STARTED,
    TEAM_ID,
    RecordingClient,
    seed_ticket,
    status_map,
)

from atlas.core.enums import ActorType
from atlas.core.models import (
    CIHandoffClassification,
    CIHandoffReason,
    Ticket,
    TicketStatus,
    TicketStatusTransition,
)
from atlas.core.models.pm_recovery import PmBlockerAuthorityKind, PmBlockerCode
from atlas.evidence.pull import drive_evidence_pull
from atlas.linear.client import LinearIssue, _github_publication_from_attachment
from atlas.pm import CIHandoffHooks, sync_tick
from atlas.pm.admission_sync import AdmissionSyncReason
from atlas.pm.ci_handoff import reconcile_ci_handoff_fence
from atlas.pm.ci_handoff_adapter import CIHandoffAdapterReason
from atlas.pm.ci_handoff_fairness import (
    CIHandoffFairnessError,
    select_fair_ci_handoff_candidate,
)
from atlas.pm.sync import CI_PENDING_POLL_COMPRESSION_CREATED_BY
from atlas.pm.workflow_write import PMWorkflowWriteGuard, WorkflowWriteWindowClosed
from atlas.storage import (
    AdmissionCoordinationRepo,
    AgentRunRepo,
    CIHandoffReconciliationRepo,
    CIHandoffWriteFenceError,
    Database,
    EvidenceRepo,
    PmRecoveryRepo,
    PmRecoveryStorageCode,
    PmRecoveryStorageError,
    PmSyncReceiptRepo,
    TicketRepo,
    TicketStatusTransitionRepo,
)
from atlas.storage.ci_handoff_coordination import CIHandoffCoordinationRepo
from atlas.storage.tables import AdmissionLeaseRow, PmRecoverySequenceCounterRow

HEAD = "a" * 40
OTHER_HEAD = "b" * 40
PRODUCT_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
PR_NUMBER = 335


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{tmp_path}/atlas.db")
    database.create_all()
    return database


def _pr_payload(head: str = HEAD) -> dict[str, Any]:
    return {
        "number": PR_NUMBER,
        "head": {"sha": head},
        "base": {"repo": {"full_name": "derekrivers/atlas"}},
    }


def _check_payload(
    *,
    name: str,
    run_id: int,
    conclusion: str | None = "success",
    source_event_at: Any = NOW - timedelta(seconds=20),
) -> dict[str, Any]:
    return {
        "id": run_id,
        "name": name,
        "status": "in_progress" if conclusion is None else "completed",
        "conclusion": conclusion,
        "completed_at": (
            None
            if source_event_at is None
            else source_event_at.isoformat().replace("+00:00", "Z")
        ),
        "html_url": f"https://github.com/derekrivers/atlas/runs/{run_id}",
        "repository": {"full_name": "derekrivers/atlas"},
        "pull_requests": [{"number": PR_NUMBER, "head": {"sha": HEAD}}],
    }


def _github(
    *,
    head: str = HEAD,
    test_conclusion: str | None = "success",
    test_source_event_at: Any = NOW - timedelta(seconds=20),
    include_test: bool = True,
) -> FakeGitHubClient:
    checks = [_check_payload(name="lint-python", run_id=2)]
    if include_test:
        checks.append(
            _check_payload(
                name="test-python",
                run_id=1,
                conclusion=test_conclusion,
                source_event_at=test_source_event_at,
            )
        )
    return FakeGitHubClient(
        check_runs=checks,
        pull_request=_pr_payload(head),
    )


def _draft_publication_issue(issue: LinearIssue) -> LinearIssue:
    publication = _github_publication_from_attachment(
        {
            "id": "github-publication-1",
            "url": f"https://github.com/derekrivers/atlas/pull/{PR_NUMBER}",
            "sourceType": "github",
            "metadata": {
                "id": "4295015089",
                "repoId": "1265218302",
                "repoLogin": "derekrivers",
                "repoName": "atlas",
                "number": PR_NUMBER,
                "linkKind": "closes",
                "targetBranch": "main",
                "status": "draft",
            },
        }
    )
    assert publication is not None
    return LinearIssue(
        id=issue.id,
        title=issue.title,
        state_id=issue.state_id,
        state_name=issue.state_name,
        state_type=issue.state_type,
        description=issue.description,
        identifier=issue.identifier,
        github_publications=(publication,),
        github_publications_complete=True,
    )


class SequencedPRGitHub(FakeGitHubClient):
    """Return a deterministic PR head sequence across repeated exact reads."""

    def __init__(self, heads: list[str]) -> None:
        self._heads = list(heads)
        super().__init__(
            check_runs=[
                _check_payload(name="lint-python", run_id=2),
                _check_payload(name="test-python", run_id=1),
            ],
            pull_request=_pr_payload(heads[0]),
        )

    def fetch_pull_request(
        self, owner: str, repo: str, pr_number: int
    ) -> dict[str, Any]:
        if self._heads:
            self._pull_request = _pr_payload(self._heads.pop(0))
        return super().fetch_pull_request(owner, repo, pr_number)


def _transition(
    ticket: Ticket,
    from_status: TicketStatus,
    to_status: TicketStatus,
    offset: timedelta,
) -> TicketStatusTransition:
    return TicketStatusTransition(
        id=uuid4(),
        ticket_id=ticket.id,
        from_status=from_status.value,
        to_status=to_status.value,
        occurred_at=NOW + offset,
        created_by_type=ActorType.SYSTEM,
        created_by_id="pm-engine",
    )


def _seed_ci_pending(
    db: Database,
    client: RecordingClient,
    *,
    key: str = "ATLAS-263",
    product_id: UUID = PRODUCT_ID,
) -> Ticket:
    ticket = seed_ticket(
        db,
        client,
        key=key,
        product_id=product_id,
        status=TicketStatus.CI_PENDING,
        issue_state=CI_PENDING_STATE,
        acceptance_criteria=["bounded transition"],
        linear_synced_at=NOW - timedelta(minutes=5),
        status_entered_at=NOW - timedelta(minutes=1),
    )
    transitions = TicketStatusTransitionRepo(db)
    transitions.record(
        _transition(
            ticket,
            TicketStatus.READY_FOR_AGENT,
            TicketStatus.IN_PROGRESS,
            -timedelta(minutes=4),
        )
    )
    transitions.record(
        _transition(
            ticket,
            TicketStatus.IN_PROGRESS,
            TicketStatus.PR_OPEN,
            -timedelta(minutes=2),
        )
    )
    transitions.record(
        _transition(
            ticket,
            TicketStatus.PR_OPEN,
            TicketStatus.CI_PENDING,
            -timedelta(minutes=1),
        )
    )
    assert ticket.external_linear_id is not None
    client.seed_github_publication(
        ticket.external_linear_id,
        owner="derekrivers",
        repo="atlas",
        pr_number=PR_NUMBER,
    )
    return ticket


def _seed_compressed_ci_pending(
    db: Database,
    client: RecordingClient,
    *,
    source: TicketStatus,
    with_publication: bool = True,
) -> Ticket:
    """Seed a board already at CI Pending with no observed transient edges."""

    entered_at = NOW - timedelta(minutes=5)
    ticket = seed_ticket(
        db,
        client,
        key="ATLAS-263",
        product_id=PRODUCT_ID,
        status=source,
        issue_state=CI_PENDING_STATE,
        acceptance_criteria=["bounded transition"],
        updated_at=entered_at,
        linear_synced_at=entered_at,
        status_entered_at=entered_at,
    )
    if with_publication:
        assert ticket.external_linear_id is not None
        client.seed_github_publication(
            ticket.external_linear_id,
            owner="derekrivers",
            repo="atlas",
            pr_number=PR_NUMBER,
        )
    return ticket


def _run(
    db: Database,
    client: RecordingClient,
    github: FakeGitHubClient,
    *,
    hooks: CIHandoffHooks | None = None,
    now: Any = NOW,
) -> Any:
    return sync_tick(
        tickets=TicketRepo(db),
        db=db,
        client=client,
        status_map=status_map(),
        team_id=TEAM_ID,
        project_id=PROJECT_ID,
        inbox_dir=Path(tempfile.mkdtemp()),
        documents=lambda: [PACK_DOC],
        now=now,
        completion_clock=lambda: now + timedelta(seconds=1),
        github_client=github,
        ci_handoff_hooks=hooks,
    )


def _rebuilt_client(source: RecordingClient) -> RecordingClient:
    rebuilt = RecordingClient()
    rebuilt._issues = {
        issue.id: replace(issue) for issue in source.fetch_project_issues(PROJECT_ID)
    }
    return rebuilt


def test_production_tick_resolves_exact_identity_writes_once_and_ends_window(
    db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = RecordingClient()
    ticket = _seed_ci_pending(db, client)
    github = _github()

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a later workflow writer ran after CI handoff")

    monkeypatch.setattr("atlas.pm.sync.admit_one_ready", forbidden)
    monkeypatch.setattr("atlas.pm.sync.complete_verified", forbidden)

    result = _run(db, client, github)

    assert client.state_writes == [(ticket.external_linear_id, "state-review-required")]
    stored = TicketRepo(db).get_by_key(ticket.key)
    assert stored is not None
    assert stored.status is TicketStatus.REVIEW_REQUIRED
    assert result.ci_handoff_evaluated == 1
    assert result.ci_handoff_held == 0
    assert result.ci_handoff_mutations == 1
    assert result.ci_handoff_decisions[0].identity is not None
    identity = result.ci_handoff_decisions[0].identity
    assert identity.repository_owner == "derekrivers"
    assert identity.repository_name == "atlas"
    assert identity.pr_number == PR_NUMBER
    assert identity.head_commit == HEAD
    assert all(call[1:3] == ("derekrivers", "atlas") for call in github.calls)
    assert github.calls[:5] == [
        ("pull_request", "derekrivers", "atlas", PR_NUMBER),
        ("workflow_runs", "derekrivers", "atlas", HEAD),
        ("check_runs", "derekrivers", "atlas", HEAD),
        ("pr_reviews", "derekrivers", "atlas", PR_NUMBER),
        ("pr_files", "derekrivers", "atlas", PR_NUMBER),
    ]
    assert EvidenceRepo(db).list_for_ticket(ticket.id) == []
    assert {
        record.ticket_id for record in EvidenceRepo(db).list_for_product(PRODUCT_ID)
    } == {None}
    reconciliations = CIHandoffReconciliationRepo(db).list()
    assert len(reconciliations) == 1
    assert reconciliations[0].head_commit == HEAD
    receipt = PmSyncReceiptRepo(db).list()[-1]
    assert receipt.counters["ci_handoff_evaluated"] == 1
    assert receipt.counters["ci_handoff_held"] == 0
    assert receipt.counters["ci_handoff_mutations"] == 1


def test_production_tick_accepts_draft_publication_and_reconciles_green_exact_head(
    db: Database,
) -> None:
    client = RecordingClient()
    ticket = _seed_ci_pending(db, client)
    assert ticket.external_linear_id is not None
    issue = client.fetch_issue(ticket.external_linear_id)
    assert issue is not None
    client._issues[ticket.external_linear_id] = _draft_publication_issue(issue)
    github = _github()

    result = _run(db, client, github)

    decision = result.ci_handoff_decisions[0]
    assert decision.reason.value == "reconciled"
    assert decision.identity is not None
    assert decision.identity.head_commit == HEAD
    assert decision.reconciliation is not None
    assert decision.reconciliation.classification is CIHandoffClassification.PASSED
    assert client.state_writes == [(ticket.external_linear_id, "state-review-required")]
    assert github.calls[:3] == [
        ("pull_request", "derekrivers", "atlas", PR_NUMBER),
        ("workflow_runs", "derekrivers", "atlas", HEAD),
        ("check_runs", "derekrivers", "atlas", HEAD),
    ]
    assert {
        record.commit_sha for record in EvidenceRepo(db).list_for_product(PRODUCT_ID)
    } == {HEAD}


@pytest.mark.parametrize(
    "source",
    [TicketStatus.READY_FOR_AGENT, TicketStatus.IN_PROGRESS],
)
def test_supported_tick_recovers_poll_compression_without_invented_transitions(
    db: Database,
    source: TicketStatus,
) -> None:
    client = RecordingClient()
    ticket = _seed_compressed_ci_pending(db, client, source=source)
    github = _github()

    result = _run(db, client, github)

    assert client.state_writes == [(ticket.external_linear_id, "state-review-required")]
    decision = result.ci_handoff_decisions[0]
    assert decision.identity is not None
    assert decision.identity.repository_owner == "derekrivers"
    assert decision.identity.repository_name == "atlas"
    assert decision.identity.pr_number == PR_NUMBER
    assert decision.identity.head_commit == HEAD
    assert decision.reconciliation is not None
    assert decision.reconciliation.classification is CIHandoffClassification.PASSED
    assert decision.reconciliation.linear_mutations == 1
    transitions = TicketStatusTransitionRepo(db).list_for_ticket(ticket.id)
    assert {
        (row.from_status, row.to_status, row.created_by_id) for row in transitions
    } == {
        (
            source.value,
            TicketStatus.CI_PENDING.value,
            CI_PENDING_POLL_COMPRESSION_CREATED_BY,
        ),
        (
            TicketStatus.CI_PENDING.value,
            TicketStatus.REVIEW_REQUIRED.value,
            "ci-handoff-reconciler",
        ),
    }
    assert AgentRunRepo(db).list_for_ticket(ticket.id) == []
    [reconciliation] = CIHandoffReconciliationRepo(db).list_for_ticket(ticket.id)
    assert reconciliation.head_commit == HEAD


def test_compressed_observation_with_missing_publication_holds_before_provider_calls(
    db: Database,
) -> None:
    client = RecordingClient()
    ticket = _seed_compressed_ci_pending(
        db,
        client,
        source=TicketStatus.READY_FOR_AGENT,
        with_publication=False,
    )
    github = _github()

    result = _run(db, client, github)

    decision = result.ci_handoff_decisions[0]
    assert decision.reason.value == "trusted_publication_unavailable"
    assert decision.reconciliation is None
    assert result.ci_handoff_held == 1
    stored = TicketRepo(db).get_by_key(ticket.key)
    assert stored is not None and stored.status is TicketStatus.CI_PENDING
    assert client.state_writes == []
    assert github.calls == []
    assert CIHandoffReconciliationRepo(db).list_for_ticket(ticket.id) == []


def test_compressed_publication_ambiguity_holds_before_provider_calls(
    db: Database,
) -> None:
    client = RecordingClient()
    ticket = _seed_compressed_ci_pending(
        db,
        client,
        source=TicketStatus.IN_PROGRESS,
    )
    assert ticket.external_linear_id is not None
    client.seed_github_publication(
        ticket.external_linear_id,
        owner="other",
        repo="atlas",
        pr_number=PR_NUMBER + 1,
        attachment_id="github-publication-2",
        append=True,
    )
    github = _github()

    result = _run(db, client, github)

    decision = result.ci_handoff_decisions[0]
    assert decision.reason.value == "trusted_publication_ambiguous"
    assert decision.reconciliation is None
    assert client.state_writes == []
    assert github.calls == []


def test_two_distinct_attachments_for_same_pr_are_ambiguous(db: Database) -> None:
    client = RecordingClient()
    ticket = _seed_ci_pending(db, client)
    assert ticket.external_linear_id is not None
    issue = client.fetch_issue(ticket.external_linear_id)
    assert issue is not None
    [publication] = issue.github_publications
    duplicate = replace(publication, attachment_id="github-publication-2")
    client._issues[ticket.external_linear_id] = replace(
        issue, github_publications=(publication, duplicate)
    )
    github = _github()

    result = _run(db, client, github)

    decision = result.ci_handoff_decisions[0]
    assert decision.reason is CIHandoffAdapterReason.PUBLICATION_AMBIGUOUS
    assert decision.reconciliation is None
    assert client.state_writes == []
    assert github.calls == []


def test_publication_replacement_during_final_revalidation_blocks_write(
    db: Database,
) -> None:
    client = RecordingClient()
    ticket = _seed_ci_pending(db, client)
    assert ticket.external_linear_id is not None

    def replace_publication() -> None:
        client.seed_github_publication(
            ticket.external_linear_id or "",
            owner="derekrivers",
            repo="atlas",
            pr_number=PR_NUMBER + 1,
            attachment_id="replacement-publication",
        )

    result = _run(
        db,
        client,
        _github(),
        hooks=CIHandoffHooks(after_revalidation=replace_publication),
    )

    reconciliation = result.ci_handoff_decisions[0].reconciliation
    assert reconciliation is not None
    assert reconciliation.reason is CIHandoffReason.SNAPSHOT_CHANGED
    assert client.state_writes == []
    stored = TicketRepo(db).get_by_key(ticket.key)
    assert stored is not None and stored.status is TicketStatus.CI_PENDING


def test_compressed_stale_head_holds_after_exact_provider_revalidation(
    db: Database,
) -> None:
    client = RecordingClient()
    ticket = _seed_compressed_ci_pending(
        db, client, source=TicketStatus.READY_FOR_AGENT
    )
    github = SequencedPRGitHub([HEAD, OTHER_HEAD])

    result = _run(db, client, github)

    handoff = result.ci_handoff_decisions[0].reconciliation
    assert handoff is not None
    assert handoff.classification is CIHandoffClassification.STALE
    assert handoff.reason is CIHandoffReason.PR_HEAD_MOVED
    assert handoff.linear_mutations == 0
    assert client.state_writes == []
    stored = TicketRepo(db).get_by_key(ticket.key)
    assert stored is not None and stored.status is TicketStatus.CI_PENDING


def test_compressed_board_movement_holds_at_final_revalidation(
    db: Database,
) -> None:
    client = RecordingClient()
    ticket = _seed_compressed_ci_pending(db, client, source=TicketStatus.IN_PROGRESS)
    github = _github()

    def move_board() -> None:
        assert ticket.external_linear_id is not None
        client.simulate_linear_state(ticket.external_linear_id, STARTED)

    result = _run(
        db,
        client,
        github,
        hooks=CIHandoffHooks(after_classification=move_board),
    )

    handoff = result.ci_handoff_decisions[0].reconciliation
    assert handoff is not None
    assert handoff.classification is CIHandoffClassification.STALE
    assert handoff.reason is CIHandoffReason.BOARD_STATE_MOVED
    assert handoff.linear_mutations == 0
    assert client.state_writes == []


@pytest.mark.parametrize(
    "source",
    [TicketStatus.PLANNED, TicketStatus.REVIEW_REQUIRED, TicketStatus.DONE],
)
def test_ci_pending_observation_from_non_agent_or_terminal_state_never_catches_up(
    db: Database,
    source: TicketStatus,
) -> None:
    client = RecordingClient()
    ticket = seed_ticket(
        db,
        client,
        key="ATLAS-263",
        product_id=PRODUCT_ID,
        status=source,
        issue_state=CI_PENDING_STATE,
        acceptance_criteria=["bounded transition"],
        linear_synced_at=NOW,
    )
    github = _github()

    result = _run(db, client, github)

    stored = TicketRepo(db).get_by_key(ticket.key)
    assert stored is not None and stored.status is source
    assert result.ci_handoff_evaluated == 0
    assert result.ci_handoff_mutations == 0
    assert client.state_writes == []
    assert github.calls == []
    assert TicketStatusTransitionRepo(db).list_for_ticket(ticket.id) == []


def test_duplicate_tick_does_not_repeat_publication_handoff_write(db: Database) -> None:
    client = RecordingClient()
    _seed_ci_pending(db, client)
    github = _github()

    first = _run(db, client, github)
    second = _run(db, client, github)

    assert first.ci_handoff_mutations == 1
    assert second.ci_handoff_mutations == 0
    assert len(client.state_writes) == 1
    assert len(CIHandoffReconciliationRepo(db).list()) == 1


def test_candidate_discovery_bootstraps_stably_and_mutates_only_one_per_tick(
    db: Database,
) -> None:
    client = RecordingClient()
    first = _seed_ci_pending(db, client, key="ATLAS-263")
    second = _seed_ci_pending(db, client, key="ATLAS-264")
    seed_ticket(
        db,
        client,
        key="ATLAS-262",
        product_id=PRODUCT_ID,
        status=TicketStatus.PR_OPEN,
        linear_synced_at=NOW,
    )
    github = _github()

    result = _run(db, client, github)

    assert result.ci_handoff_decisions[0].candidate_count == 2
    assert result.ci_handoff_decisions[0].ticket_key == first.key
    stored_first = TicketRepo(db).get_by_key(first.key)
    stored_second = TicketRepo(db).get_by_key(second.key)
    assert stored_first is not None
    assert stored_second is not None
    assert stored_first.status is TicketStatus.REVIEW_REQUIRED
    assert stored_second.status is TicketStatus.CI_PENDING
    assert len(client.state_writes) == 1


def test_poisoned_first_candidate_rotates_and_next_candidate_advances(
    db: Database,
) -> None:
    seed_client = RecordingClient()
    first = _seed_ci_pending(db, seed_client, key="ATLAS-263")
    second = _seed_ci_pending(db, seed_client, key="ATLAS-264")
    assert first.external_linear_id is not None
    first_issue = seed_client.fetch_issue(first.external_linear_id)
    assert first_issue is not None
    # This is the DTO shape produced by the boundary for an inactive merged
    # attachment: it is not usable as an ordinary live publication.
    seed_client._issues[first.external_linear_id] = replace(
        first_issue,
        github_publications=(),
        github_publications_complete=False,
    )
    authoritative_issues = seed_client.fetch_project_issues(PROJECT_ID)

    def rebuild_client() -> RecordingClient:
        rebuilt = RecordingClient()
        rebuilt._issues = {issue.id: replace(issue) for issue in authoritative_issues}
        return rebuilt

    first_db = Database(str(db.engine.url))
    first_client = rebuild_client()
    held = _run(first_db, first_client, _github(), now=NOW)
    first_db.engine.dispose()

    second_db = Database(str(db.engine.url))
    second_client = rebuild_client()
    advanced = _run(
        second_db,
        second_client,
        _github(),
        now=NOW + timedelta(seconds=1),
    )
    second_db.engine.dispose()

    assert held.ci_handoff_decisions[0].ticket_key == first.key
    assert held.ci_handoff_held == 1
    assert advanced.ci_handoff_decisions[0].ticket_key == second.key
    assert advanced.ci_handoff_mutations == 1
    assert first_client.state_writes == []
    assert second_client.state_writes == [
        (second.external_linear_id, "state-review-required")
    ]
    stored_first = TicketRepo(db).get_by_key(first.key)
    stored_second = TicketRepo(db).get_by_key(second.key)
    assert stored_first is not None and stored_first.status is TicketStatus.CI_PENDING
    assert stored_second is not None
    assert stored_second.status is TicketStatus.REVIEW_REQUIRED


def test_post_commit_crash_fence_recovers_without_a_local_ci_candidate(
    db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = RecordingClient()
    ticket = _seed_ci_pending(db, client)
    github = _github()
    original_finalize = CIHandoffCoordinationRepo.finalize_owned_target
    crashed = False

    def crash_after_local_commit(
        coordination: CIHandoffCoordinationRepo,
        *,
        product_id: UUID,
        owner_id: UUID,
        reconciliation_id: UUID,
        ticket_id: UUID,
        ticket_key: str,
        target_status: TicketStatus,
        observed_at: Any,
        status_observed_at: Any,
        created_by_id: str,
    ) -> None:
        nonlocal crashed
        if not crashed:
            crashed = True
            TicketRepo(db).apply_linear_status(
                ticket_key,
                target_status,
                now=status_observed_at,
                created_by_id=created_by_id,
            )
            raise RuntimeError("seeded process death after local target commit")
        original_finalize(
            coordination,
            product_id=product_id,
            owner_id=owner_id,
            reconciliation_id=reconciliation_id,
            ticket_id=ticket_id,
            ticket_key=ticket_key,
            target_status=target_status,
            observed_at=observed_at,
            status_observed_at=status_observed_at,
            created_by_id=created_by_id,
        )

    monkeypatch.setattr(
        CIHandoffCoordinationRepo,
        "finalize_owned_target",
        crash_after_local_commit,
    )
    with pytest.raises(RuntimeError, match="seeded process death"):
        _run(db, client, github, now=NOW)
    stored = TicketRepo(db).get_by_key(ticket.key)
    assert stored is not None and stored.status is TicketStatus.REVIEW_REQUIRED
    assert CIHandoffCoordinationRepo(db).get_fence(PRODUCT_ID) is not None
    assert ticket.external_linear_id is not None
    target_issue = client._issues.pop(ticket.external_linear_id)

    for offset in (1, 2):
        rebuilt = Database(str(db.engine.url))
        unresolved = _run(
            rebuilt,
            client,
            github,
            now=NOW + timedelta(seconds=offset),
        )
        rebuilt.engine.dispose()
        decision = unresolved.ci_handoff_decisions[0]
        assert decision.reconciliation is not None
        assert decision.reconciliation.reason is CIHandoffReason.FENCE_STILL_UNRESOLVED
        assert decision.ends_workflow_write_window
        assert CIHandoffCoordinationRepo(db).get_fence(PRODUCT_ID) is not None
        assert client.state_writes == [
            (ticket.external_linear_id, "state-review-required")
        ]
        [episode] = PmRecoveryRepo(db).list_active_episodes_ordered(PRODUCT_ID)
        assert episode.closed_at is None

    [blocker] = PmRecoveryRepo(db).list_blockers(
        product_id=PRODUCT_ID, active_only=True
    )
    assert blocker.code is PmBlockerCode.WRITE_FENCE_UNRESOLVED
    assert blocker.consecutive_observations == 2
    client._issues[ticket.external_linear_id] = target_issue

    rebuilt = Database(str(db.engine.url))
    recovered = _run(rebuilt, client, github, now=NOW + timedelta(seconds=3))
    rebuilt.engine.dispose()

    decision = recovered.ci_handoff_decisions[0]
    assert decision.reconciliation is not None
    assert decision.reconciliation.reason is CIHandoffReason.FENCE_RECONCILED_TARGET
    assert decision.ends_workflow_write_window
    assert CIHandoffCoordinationRepo(db).get_fence(PRODUCT_ID) is None
    assert client.state_writes == [(ticket.external_linear_id, "state-review-required")]


@pytest.mark.parametrize(
    ("provider_outcome", "expected_reason", "fence_remains", "target_applied"),
    [
        (
            "target",
            CIHandoffReason.FENCE_RECONCILED_TARGET,
            False,
            True,
        ),
        (
            "source",
            CIHandoffReason.FENCE_RECONCILED_SOURCE,
            False,
            False,
        ),
        (
            "moved",
            CIHandoffReason.FENCE_RECONCILED_MOVED,
            False,
            False,
        ),
        (
            "unresolved",
            CIHandoffReason.FENCE_STILL_UNRESOLVED,
            True,
            False,
        ),
    ],
)
def test_fence_outcomes_survive_complete_database_and_client_reconstruction(
    db: Database,
    provider_outcome: str,
    expected_reason: CIHandoffReason,
    fence_remains: bool,
    target_applied: bool,
) -> None:
    client = RecordingClient()
    ticket = _seed_ci_pending(db, client)
    selected = select_fair_ci_handoff_candidate(
        db=db,
        tickets=TicketRepo(db),
        initial_issues=client.fetch_project_issues(PROJECT_ID),
        now=NOW,
    )
    assert selected.episode is not None
    owner_id = uuid4()
    lease = AdmissionCoordinationRepo(db)
    assert lease.try_acquire(
        product_id=PRODUCT_ID,
        owner_id=owner_id,
        acquired_at=NOW,
        ttl=timedelta(minutes=1),
    )
    assert ticket.external_linear_id is not None
    fence = CIHandoffCoordinationRepo(db).begin_write(
        product_id=PRODUCT_ID,
        owner_id=owner_id,
        reconciliation_id=uuid4(),
        ticket_id=ticket.id,
        ticket_key=ticket.key,
        issue_id=ticket.external_linear_id,
        source_state_id=CI_PENDING_STATE.id,
        target_state_id="state-review-required",
        target_status=TicketStatus.REVIEW_REQUIRED,
        created_at=NOW,
    )
    lease.release(product_id=PRODUCT_ID, owner_id=owner_id)
    if provider_outcome == "target":
        client.simulate_linear_state(ticket.external_linear_id, REVIEW_REQUIRED_STATE)
    elif provider_outcome == "moved":
        client.simulate_linear_state(ticket.external_linear_id, STARTED)
    elif provider_outcome == "unresolved":
        client._issues.pop(ticket.external_linear_id)

    rebuilt_client = _rebuilt_client(client)
    database_url = str(db.engine.url)
    db.engine.dispose()
    rebuilt_db = Database(database_url)
    result = _run(
        rebuilt_db,
        rebuilt_client,
        _github(),
        now=NOW + timedelta(seconds=1),
    )

    decision = result.ci_handoff_decisions[0]
    assert decision.ticket_key == ticket.key
    assert decision.reconciliation is not None
    assert decision.reconciliation.reason is expected_reason
    assert decision.ends_workflow_write_window
    assert rebuilt_client.state_writes == []
    assert (
        CIHandoffCoordinationRepo(rebuilt_db).get_fence(PRODUCT_ID) is not None
    ) is fence_remains
    stored = TicketRepo(rebuilt_db).get_by_key(ticket.key)
    assert stored is not None
    assert (stored.status is TicketStatus.REVIEW_REQUIRED) is target_applied
    episode = PmRecoveryRepo(rebuilt_db).get_episode(selected.episode.id)
    assert episode is not None
    assert (episode.closed_at is not None) is target_applied
    active_blockers = PmRecoveryRepo(rebuilt_db).list_blockers(
        product_id=PRODUCT_ID,
        active_only=True,
    )
    if provider_outcome == "unresolved":
        assert [blocker.code for blocker in active_blockers] == [
            PmBlockerCode.WRITE_FENCE_UNRESOLVED
        ]
    elif provider_outcome == "moved":
        assert [blocker.code for blocker in active_blockers] == [
            PmBlockerCode.AUTHORITY_CHANGED
        ]
    else:
        assert active_blockers == []
    assert CIHandoffCoordinationRepo(rebuilt_db).get_fence(PRODUCT_ID) == (
        fence if fence_remains else None
    )
    rebuilt_db.engine.dispose()


def test_unresolved_fence_rotates_to_independent_product_fence(db: Database) -> None:
    client = RecordingClient()
    first = _seed_ci_pending(db, client, key="ATLAS-263")
    second_product_id = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
    second = _seed_ci_pending(
        db,
        client,
        key="OTHER-263",
        product_id=second_product_id,
    )
    owner_id = uuid4()
    coordination = AdmissionCoordinationRepo(db)
    for ticket in (first, second):
        assert ticket.external_linear_id is not None
        assert coordination.try_acquire(
            product_id=ticket.product_id,
            owner_id=owner_id,
            acquired_at=NOW,
            ttl=timedelta(minutes=1),
        )
        CIHandoffCoordinationRepo(db).begin_write(
            product_id=ticket.product_id,
            owner_id=owner_id,
            reconciliation_id=uuid4(),
            ticket_id=ticket.id,
            ticket_key=ticket.key,
            issue_id=ticket.external_linear_id,
            source_state_id=CI_PENDING_STATE.id,
            target_state_id="state-review-required",
            target_status=TicketStatus.REVIEW_REQUIRED,
            created_at=NOW,
        )
        coordination.release(product_id=ticket.product_id, owner_id=owner_id)

    assert first.external_linear_id is not None
    client._issues.pop(first.external_linear_id)
    first_tick = _run(db, client, _github(), now=NOW)
    assert (
        first_tick.ci_handoff_decisions[0].reconciliation.reason
        is CIHandoffReason.FENCE_STILL_UNRESOLVED
    )
    assert CIHandoffCoordinationRepo(db).get_fence(PRODUCT_ID) is not None
    assert CIHandoffCoordinationRepo(db).get_fence(second_product_id) is not None

    rebuilt = Database(str(db.engine.url))
    second_tick = _run(
        rebuilt,
        client,
        _github(),
        now=NOW + timedelta(seconds=1),
    )
    rebuilt.engine.dispose()
    assert (
        second_tick.ci_handoff_decisions[0].reconciliation.reason
        is CIHandoffReason.FENCE_RECONCILED_SOURCE
    )
    assert CIHandoffCoordinationRepo(db).get_fence(PRODUCT_ID) is not None
    assert CIHandoffCoordinationRepo(db).get_fence(second_product_id) is None
    assert client.state_writes == []


def test_contended_fence_defers_to_next_fenced_product(db: Database) -> None:
    client = RecordingClient()
    first = _seed_ci_pending(db, client, key="ATLAS-263")
    second_product_id = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
    second = _seed_ci_pending(
        db,
        client,
        key="OTHER-263",
        product_id=second_product_id,
    )
    lease = AdmissionCoordinationRepo(db)
    for ticket in (first, second):
        owner_id = uuid4()
        assert ticket.external_linear_id is not None
        assert lease.try_acquire(
            product_id=ticket.product_id,
            owner_id=owner_id,
            acquired_at=NOW,
            ttl=timedelta(minutes=1),
        )
        CIHandoffCoordinationRepo(db).begin_write(
            product_id=ticket.product_id,
            owner_id=owner_id,
            reconciliation_id=uuid4(),
            ticket_id=ticket.id,
            ticket_key=ticket.key,
            issue_id=ticket.external_linear_id,
            source_state_id=CI_PENDING_STATE.id,
            target_state_id="state-review-required",
            target_status=TicketStatus.REVIEW_REQUIRED,
            created_at=NOW,
        )
        lease.release(product_id=ticket.product_id, owner_id=owner_id)
    selected = select_fair_ci_handoff_candidate(
        db=db,
        tickets=TicketRepo(db),
        initial_issues=client.fetch_project_issues(PROJECT_ID),
        now=NOW,
    )
    assert selected.candidate == first and selected.episode is not None
    initial_cursor = selected.episode.fairness_cursor
    initial_last_evaluated_sequence = selected.episode.last_evaluated_sequence
    live_owner = uuid4()
    assert lease.try_acquire(
        product_id=PRODUCT_ID,
        owner_id=live_owner,
        acquired_at=NOW,
        ttl=timedelta(minutes=5),
    )

    held = _run(db, client, _github(), now=NOW)
    held_reconciliation = held.ci_handoff_decisions[0].reconciliation
    assert held_reconciliation is not None
    assert held_reconciliation.reason is CIHandoffReason.LEASE_UNAVAILABLE
    retained = PmRecoveryRepo(db).get_episode(selected.episode.id)
    assert retained is not None
    assert retained.fairness_cursor == initial_cursor
    assert retained.last_evaluated_sequence == initial_last_evaluated_sequence
    [blocker] = PmRecoveryRepo(db).list_blockers(
        product_id=PRODUCT_ID, active_only=True
    )
    assert blocker.code is PmBlockerCode.LEASE_UNAVAILABLE
    assert client.state_writes == []

    rebuilt = Database(str(db.engine.url))
    rotated = _run(rebuilt, client, _github(), now=NOW + timedelta(seconds=1))
    rebuilt.engine.dispose()

    rotated_reconciliation = rotated.ci_handoff_decisions[0].reconciliation
    assert rotated_reconciliation is not None
    assert rotated_reconciliation.ticket_key == second.key
    assert rotated_reconciliation.reason is CIHandoffReason.FENCE_RECONCILED_SOURCE
    assert CIHandoffCoordinationRepo(db).get_fence(PRODUCT_ID) is not None
    assert CIHandoffCoordinationRepo(db).get_fence(second_product_id) is None
    assert client.state_writes == []
    lease.release(product_id=PRODUCT_ID, owner_id=live_owner)

    retry_client = _rebuilt_client(client)
    rebuilt = Database(str(db.engine.url))
    converged = _run(
        rebuilt,
        retry_client,
        _github(),
        now=NOW + timedelta(minutes=1, seconds=1),
    )
    rebuilt.engine.dispose()
    converged_reconciliation = converged.ci_handoff_decisions[0].reconciliation
    assert converged_reconciliation is not None
    assert converged_reconciliation.ticket_key == first.key
    assert converged_reconciliation.reason is CIHandoffReason.FENCE_RECONCILED_SOURCE
    assert CIHandoffCoordinationRepo(db).get_fence(PRODUCT_ID) is None
    recovered_blocker = PmRecoveryRepo(db).get_blocker(blocker.id)
    assert recovered_blocker is not None
    assert recovered_blocker.superseded_at is not None
    assert retry_client.state_writes == []


def test_unresolved_fence_retains_precedence_over_independent_ordinary_candidate(
    db: Database,
) -> None:
    client = RecordingClient()
    fenced = _seed_ci_pending(db, client, key="ATLAS-263")
    second_product_id = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
    healthy = _seed_ci_pending(
        db,
        client,
        key="OTHER-263",
        product_id=second_product_id,
    )
    owner_id = uuid4()
    lease = AdmissionCoordinationRepo(db)
    assert lease.try_acquire(
        product_id=PRODUCT_ID,
        owner_id=owner_id,
        acquired_at=NOW,
        ttl=timedelta(minutes=1),
    )
    assert fenced.external_linear_id is not None
    CIHandoffCoordinationRepo(db).begin_write(
        product_id=PRODUCT_ID,
        owner_id=owner_id,
        reconciliation_id=uuid4(),
        ticket_id=fenced.id,
        ticket_key=fenced.key,
        issue_id=fenced.external_linear_id,
        source_state_id=CI_PENDING_STATE.id,
        target_state_id="state-review-required",
        target_status=TicketStatus.REVIEW_REQUIRED,
        created_at=NOW,
    )
    lease.release(product_id=PRODUCT_ID, owner_id=owner_id)
    client._issues.pop(fenced.external_linear_id)

    first = _run(db, client, _github(), now=NOW)
    assert (
        first.ci_handoff_decisions[0].reconciliation.reason
        is CIHandoffReason.FENCE_STILL_UNRESOLVED
    )
    rebuilt = Database(str(db.engine.url))
    second = _run(
        rebuilt,
        client,
        _github(),
        now=NOW + timedelta(seconds=1),
    )
    rebuilt.engine.dispose()

    assert second.ci_handoff_decisions[0].ticket_key == fenced.key
    assert (
        second.ci_handoff_decisions[0].reconciliation.reason
        is CIHandoffReason.FENCE_STILL_UNRESOLVED
    )
    assert second.ci_handoff_mutations == 0
    assert client.state_writes == []
    stored_healthy = TicketRepo(db).get_by_key(healthy.key)
    assert stored_healthy is not None
    assert stored_healthy.status is TicketStatus.CI_PENDING
    assert CIHandoffCoordinationRepo(db).get_fence(PRODUCT_ID) is not None


def test_replaced_lease_stops_zombie_writer_after_fence_persistence(
    db: Database,
) -> None:
    client = RecordingClient()
    _seed_ci_pending(db, client)
    replacement_owner = uuid4()

    def replace_expired_owner() -> None:
        assert AdmissionCoordinationRepo(db).try_acquire(
            product_id=PRODUCT_ID,
            owner_id=replacement_owner,
            acquired_at=NOW + timedelta(minutes=6),
            ttl=timedelta(minutes=5),
        )

    stopped = _run(
        db,
        client,
        _github(),
        hooks=CIHandoffHooks(after_fence_persisted=replace_expired_owner),
        now=NOW,
    )

    decision = stopped.ci_handoff_decisions[0]
    assert decision.reconciliation is not None
    assert decision.reconciliation.reason is CIHandoffReason.LEASE_LOST
    assert decision.ends_workflow_write_window
    assert client.state_writes == []
    assert CIHandoffCoordinationRepo(db).get_fence(PRODUCT_ID) is not None

    AdmissionCoordinationRepo(db).release(
        product_id=PRODUCT_ID, owner_id=replacement_owner
    )
    recovered_client = _rebuilt_client(client)
    rebuilt = Database(str(db.engine.url))
    recovered = _run(
        rebuilt,
        recovered_client,
        _github(),
        now=NOW + timedelta(minutes=6, seconds=1),
    )
    rebuilt.engine.dispose()
    assert (
        recovered.ci_handoff_decisions[0].reconciliation.reason
        is CIHandoffReason.FENCE_RECONCILED_SOURCE
    )
    assert CIHandoffCoordinationRepo(db).get_fence(PRODUCT_ID) is None
    assert client.state_writes == []


def test_passively_expired_lease_stops_writer_after_fence_persistence(
    db: Database,
) -> None:
    client = RecordingClient()
    _seed_ci_pending(db, client)
    monotonic_times = iter((0.0, 301.0))

    stopped = _run(
        db,
        client,
        _github(),
        hooks=CIHandoffHooks(monotonic_clock=lambda: next(monotonic_times)),
        now=NOW,
    )

    decision = stopped.ci_handoff_decisions[0]
    assert decision.reconciliation is not None
    assert decision.reconciliation.reason is CIHandoffReason.LEASE_LOST
    assert decision.ends_workflow_write_window
    assert client.state_writes == []
    assert CIHandoffCoordinationRepo(db).get_fence(PRODUCT_ID) is not None


def test_lost_owner_after_provider_success_leaves_fence_for_atomic_recovery(
    db: Database,
) -> None:
    client = RecordingClient()
    ticket = _seed_ci_pending(db, client)
    replacement_owner = uuid4()

    def replace_after_provider_success() -> None:
        assert AdmissionCoordinationRepo(db).try_acquire(
            product_id=PRODUCT_ID,
            owner_id=replacement_owner,
            acquired_at=NOW + timedelta(minutes=6),
            ttl=timedelta(minutes=5),
        )

    stopped = _run(
        db,
        client,
        _github(),
        hooks=CIHandoffHooks(after_provider_write=replace_after_provider_success),
        now=NOW,
    )

    decision = stopped.ci_handoff_decisions[0]
    assert decision.reconciliation is not None
    assert decision.reconciliation.reason is CIHandoffReason.LEASE_LOST
    assert decision.reconciliation.linear_mutations == 1
    stored = TicketRepo(db).get_by_key(ticket.key)
    assert stored is not None and stored.status is TicketStatus.CI_PENDING
    assert CIHandoffCoordinationRepo(db).get_fence(PRODUCT_ID) is not None
    assert client.state_writes == [(ticket.external_linear_id, "state-review-required")]
    [blocker] = PmRecoveryRepo(db).list_blockers(
        product_id=PRODUCT_ID,
        active_only=True,
    )
    assert blocker.code is PmBlockerCode.WRITE_FENCE_UNRESOLVED
    assert blocker.authority_kind is PmBlockerAuthorityKind.FENCE
    assert decision.reconciliation.reconciliation_id is not None
    assert blocker.authority_id.endswith(str(decision.reconciliation.reconciliation_id))

    AdmissionCoordinationRepo(db).release(
        product_id=PRODUCT_ID, owner_id=replacement_owner
    )
    recovered_client = _rebuilt_client(client)
    rebuilt = Database(str(db.engine.url))
    recovered = _run(
        rebuilt,
        recovered_client,
        _github(),
        now=NOW + timedelta(minutes=6, seconds=1),
    )
    rebuilt.engine.dispose()

    recovered_decision = recovered.ci_handoff_decisions[0]
    assert recovered_decision.reconciliation is not None
    assert (
        recovered_decision.reconciliation.reason
        is CIHandoffReason.FENCE_RECONCILED_TARGET
    )
    stored = TicketRepo(db).get_by_key(ticket.key)
    assert stored is not None and stored.status is TicketStatus.REVIEW_REQUIRED
    assert CIHandoffCoordinationRepo(db).get_fence(PRODUCT_ID) is None
    assert client.state_writes == [(ticket.external_linear_id, "state-review-required")]
    assert recovered_client.state_writes == []
    recovered_blocker = PmRecoveryRepo(db).get_blocker(blocker.id)
    assert recovered_blocker is not None
    assert recovered_blocker.superseded_at is not None


@pytest.mark.parametrize("replacement", [False, True])
def test_expected_fence_identity_rejects_disappearance_or_replacement(
    db: Database,
    replacement: bool,
) -> None:
    client = RecordingClient()
    first = _seed_ci_pending(db, client, key="ATLAS-263")
    second = _seed_ci_pending(db, client, key="ATLAS-264")
    lease = AdmissionCoordinationRepo(db)
    owner_id = uuid4()
    assert lease.try_acquire(
        product_id=PRODUCT_ID,
        owner_id=owner_id,
        acquired_at=NOW,
        ttl=timedelta(minutes=1),
    )
    assert first.external_linear_id is not None
    expected = CIHandoffCoordinationRepo(db).begin_write(
        product_id=PRODUCT_ID,
        owner_id=owner_id,
        reconciliation_id=uuid4(),
        ticket_id=first.id,
        ticket_key=first.key,
        issue_id=first.external_linear_id,
        source_state_id=CI_PENDING_STATE.id,
        target_state_id="state-review-required",
        target_status=TicketStatus.REVIEW_REQUIRED,
        created_at=NOW,
    )
    CIHandoffCoordinationRepo(db).clear_owned_fence(
        product_id=PRODUCT_ID,
        owner_id=owner_id,
        reconciliation_id=expected.reconciliation_id,
        observed_at=NOW,
    )
    lease.release(product_id=PRODUCT_ID, owner_id=owner_id)
    replacement_fence = None
    if replacement:
        replacement_owner = uuid4()
        assert lease.try_acquire(
            product_id=PRODUCT_ID,
            owner_id=replacement_owner,
            acquired_at=NOW + timedelta(seconds=1),
            ttl=timedelta(minutes=1),
        )
        assert second.external_linear_id is not None
        replacement_fence = CIHandoffCoordinationRepo(db).begin_write(
            product_id=PRODUCT_ID,
            owner_id=replacement_owner,
            reconciliation_id=uuid4(),
            ticket_id=second.id,
            ticket_key=second.key,
            issue_id=second.external_linear_id,
            source_state_id=CI_PENDING_STATE.id,
            target_state_id="state-review-required",
            target_status=TicketStatus.REVIEW_REQUIRED,
            created_at=NOW + timedelta(seconds=1),
        )
        lease.release(product_id=PRODUCT_ID, owner_id=replacement_owner)

    with pytest.raises(CIHandoffWriteFenceError):
        reconcile_ci_handoff_fence(
            db=db,
            tickets=TicketRepo(db),
            status_map=status_map(),
            initial_issues=client.fetch_project_issues(PROJECT_ID),
            product_id=PRODUCT_ID,
            now=NOW + timedelta(seconds=2),
            linear=client,
            project_id=PROJECT_ID,
            expected_reconciliation_id=expected.reconciliation_id,
            expected_ticket_id=expected.ticket_id,
        )

    assert CIHandoffCoordinationRepo(db).get_fence(PRODUCT_ID) == replacement_fence
    assert client.state_writes == []
    stored_first = TicketRepo(db).get_by_key(first.key)
    stored_second = TicketRepo(db).get_by_key(second.key)
    assert stored_first is not None and stored_first.status is TicketStatus.CI_PENDING
    assert stored_second is not None and stored_second.status is TicketStatus.CI_PENDING


def test_fenced_call_lock_blocks_replacement_until_provider_call_finishes(
    db: Database,
) -> None:
    entered_call = Event()
    release_call = Event()
    replacement_started = Event()

    class BlockingClient(RecordingClient):
        def set_state(self, issue_id: str, state_id: str) -> LinearIssue:
            entered_call.set()
            assert release_call.wait(timeout=5)
            return super().set_state(issue_id, state_id)

    client = BlockingClient()
    ticket = _seed_ci_pending(db, client)
    replacement_owner = uuid4()

    def replace_owner() -> bool:
        replacement_started.set()
        return AdmissionCoordinationRepo(db).try_acquire(
            product_id=PRODUCT_ID,
            owner_id=replacement_owner,
            acquired_at=NOW + timedelta(minutes=6),
            ttl=timedelta(minutes=5),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        handoff_future = pool.submit(_run, db, client, _github(), now=NOW)
        assert entered_call.wait(timeout=5)
        replacement_future = pool.submit(replace_owner)
        assert replacement_started.wait(timeout=5)
        with pytest.raises(FutureTimeoutError):
            replacement_future.result(timeout=0.1)
        release_call.set()
        handoff = handoff_future.result(timeout=5)
        assert replacement_future.result(timeout=5)

    assert handoff.ci_handoff_mutations == 1
    assert client.state_writes == [(ticket.external_linear_id, "state-review-required")]
    assert CIHandoffCoordinationRepo(db).get_fence(PRODUCT_ID) is None
    AdmissionCoordinationRepo(db).release(
        product_id=PRODUCT_ID, owner_id=replacement_owner
    )


def test_guarded_ordinary_call_blocks_ci_fence_creation_until_call_finishes(
    db: Database,
) -> None:
    client = RecordingClient()
    ticket = _seed_ci_pending(db, client)
    assert ticket.external_linear_id is not None
    entered_call = Event()
    release_call = Event()
    replacement_started = Event()

    def ordinary_provider_call() -> None:
        entered_call.set()
        assert release_call.wait(timeout=5)

    guard = PMWorkflowWriteGuard(db=db, observed_at=NOW)

    def install_ci_fence() -> Any:
        replacement_started.set()
        owner_id = uuid4()
        lease = AdmissionCoordinationRepo(db)
        assert lease.try_acquire(
            product_id=PRODUCT_ID,
            owner_id=owner_id,
            acquired_at=NOW + timedelta(minutes=6),
            ttl=timedelta(minutes=5),
        )
        fence = CIHandoffCoordinationRepo(db).begin_write(
            product_id=PRODUCT_ID,
            owner_id=owner_id,
            reconciliation_id=uuid4(),
            ticket_id=ticket.id,
            ticket_key=ticket.key,
            issue_id=ticket.external_linear_id or "",
            source_state_id=CI_PENDING_STATE.id,
            target_state_id="state-review-required",
            target_status=TicketStatus.REVIEW_REQUIRED,
            created_at=NOW + timedelta(minutes=6),
        )
        lease.release(product_id=PRODUCT_ID, owner_id=owner_id)
        return fence

    with ThreadPoolExecutor(max_workers=2) as pool:
        guarded_future = pool.submit(
            guard.execute,
            product_id=PRODUCT_ID,
            call=ordinary_provider_call,
        )
        assert entered_call.wait(timeout=5)
        fence_future = pool.submit(install_ci_fence)
        assert replacement_started.wait(timeout=5)
        with pytest.raises(FutureTimeoutError):
            fence_future.result(timeout=0.1)
        release_call.set()
        assert guarded_future.result(timeout=5) is None
        installed_fence = fence_future.result(timeout=5)

    assert CIHandoffCoordinationRepo(db).get_fence(PRODUCT_ID) == installed_fence


def test_guarded_ordinary_call_refuses_preexisting_ci_fence(db: Database) -> None:
    client = RecordingClient()
    ticket = _seed_ci_pending(db, client)
    assert ticket.external_linear_id is not None
    owner_id = uuid4()
    lease = AdmissionCoordinationRepo(db)
    assert lease.try_acquire(
        product_id=PRODUCT_ID,
        owner_id=owner_id,
        acquired_at=NOW,
        ttl=timedelta(minutes=1),
    )
    fence = CIHandoffCoordinationRepo(db).begin_write(
        product_id=PRODUCT_ID,
        owner_id=owner_id,
        reconciliation_id=uuid4(),
        ticket_id=ticket.id,
        ticket_key=ticket.key,
        issue_id=ticket.external_linear_id,
        source_state_id=CI_PENDING_STATE.id,
        target_state_id="state-review-required",
        target_status=TicketStatus.REVIEW_REQUIRED,
        created_at=NOW,
    )
    lease.release(product_id=PRODUCT_ID, owner_id=owner_id)
    called = False

    def forbidden_call() -> None:
        nonlocal called
        called = True

    with pytest.raises(WorkflowWriteWindowClosed):
        PMWorkflowWriteGuard(db=db, observed_at=NOW + timedelta(seconds=1)).execute(
            product_id=PRODUCT_ID, call=forbidden_call
        )

    assert called is False
    assert CIHandoffCoordinationRepo(db).get_fence(PRODUCT_ID) == fence


def test_guarded_ordinary_call_refuses_preexisting_admission_fence(
    db: Database,
) -> None:
    client = RecordingClient()
    ticket = seed_ticket(
        db,
        client,
        key="ATLAS-263",
        product_id=PRODUCT_ID,
        status=TicketStatus.PLANNED,
        acceptance_criteria=["retain the unresolved admission write"],
        linear_synced_at=NOW,
    )
    assert ticket.external_linear_id is not None
    issue = client.fetch_issue(ticket.external_linear_id)
    assert issue is not None and issue.state_id is not None
    owner_id = uuid4()
    coordination = AdmissionCoordinationRepo(db)
    assert coordination.try_acquire(
        product_id=PRODUCT_ID,
        owner_id=owner_id,
        acquired_at=NOW,
        ttl=timedelta(minutes=1),
    )
    fence = coordination.begin_write(
        product_id=PRODUCT_ID,
        owner_id=owner_id,
        admission_run_id=uuid4(),
        ticket_id=ticket.id,
        ticket_key=ticket.key,
        issue_id=ticket.external_linear_id,
        source_state_id=issue.state_id,
        target_state_id=READY.id,
        policy_revision=1,
        created_at=NOW,
    )
    coordination.release(product_id=PRODUCT_ID, owner_id=owner_id)
    called = False

    def forbidden_call() -> None:
        nonlocal called
        called = True

    with pytest.raises(WorkflowWriteWindowClosed):
        PMWorkflowWriteGuard(db=db, observed_at=NOW + timedelta(seconds=1)).execute(
            product_id=PRODUCT_ID,
            call=forbidden_call,
        )

    assert called is False
    assert AdmissionCoordinationRepo(db).get_fence(PRODUCT_ID) == fence


def test_sequence_exhaustion_fails_before_provider_or_workflow_effect(
    db: Database,
) -> None:
    client = RecordingClient()
    _seed_ci_pending(db, client)
    select_fair_ci_handoff_candidate(
        db=db,
        tickets=TicketRepo(db),
        initial_issues=client.fetch_project_issues(PROJECT_ID),
        now=NOW,
    )
    with db.engine.begin() as connection:
        connection.execute(
            sa.update(PmRecoverySequenceCounterRow)
            .where(PmRecoverySequenceCounterRow.product_id == PRODUCT_ID)
            .values(high_water=9_223_372_036_854_775_807)
        )
    github = _github()

    with pytest.raises(PmRecoveryStorageError) as exhausted:
        _run(db, client, github, now=NOW + timedelta(seconds=1))

    assert exhausted.value.code is PmRecoveryStorageCode.SEQUENCE_EXHAUSTED
    assert github.calls == []
    assert client.state_writes == []
    assert CIHandoffCoordinationRepo(db).get_fence(PRODUCT_ID) is None


def test_final_sequence_is_reserved_before_provider_effect_and_records(
    db: Database,
) -> None:
    client = RecordingClient()
    ticket = _seed_ci_pending(db, client)
    selected = select_fair_ci_handoff_candidate(
        db=db,
        tickets=TicketRepo(db),
        initial_issues=client.fetch_project_issues(PROJECT_ID),
        now=NOW,
    )
    assert selected.episode is not None
    with db.engine.begin() as connection:
        connection.execute(
            sa.update(PmRecoverySequenceCounterRow)
            .where(PmRecoverySequenceCounterRow.product_id == PRODUCT_ID)
            .values(high_water=9_223_372_036_854_775_806)
        )

    result = _run(db, client, _github(), now=NOW + timedelta(seconds=1))

    assert result.ci_handoff_mutations == 1
    assert client.state_writes == [(ticket.external_linear_id, "state-review-required")]
    assert (
        PmRecoveryRepo(db).sequence_high_water(PRODUCT_ID) == 9_223_372_036_854_775_807
    )
    recorded = PmRecoveryRepo(db).get_episode(selected.episode.id)
    assert recorded is not None
    assert recorded.last_evaluated_sequence == 9_223_372_036_854_775_807
    assert recorded.last_evaluation_id is not None
    assert recorded.last_evaluation_fingerprint is not None
    assert recorded.closed_at is not None


def test_lease_contention_defers_product_without_stealing_cursor(db: Database) -> None:
    client = RecordingClient()
    contended = _seed_ci_pending(db, client, key="ATLAS-263")
    healthy_product_id = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
    healthy = _seed_ci_pending(
        db,
        client,
        key="OTHER-263",
        product_id=healthy_product_id,
    )
    initial = select_fair_ci_handoff_candidate(
        db=db,
        tickets=TicketRepo(db),
        initial_issues=client.fetch_project_issues(PROJECT_ID),
        now=NOW,
    )
    assert initial.candidate == contended and initial.episode is not None
    initial_cursor = initial.episode.fairness_cursor
    live_owner = uuid4()
    lease = AdmissionCoordinationRepo(db)
    assert lease.try_acquire(
        product_id=PRODUCT_ID,
        owner_id=live_owner,
        acquired_at=NOW,
        ttl=timedelta(minutes=5),
    )

    held = _run(db, client, _github(), now=NOW)

    reconciliation = held.ci_handoff_decisions[0].reconciliation
    assert reconciliation is not None
    assert reconciliation.reason is CIHandoffReason.LEASE_UNAVAILABLE
    retained = PmRecoveryRepo(db).get_episode(initial.episode.id)
    assert retained is not None and retained.fairness_cursor == initial_cursor
    [blocker] = PmRecoveryRepo(db).list_blockers(
        product_id=PRODUCT_ID, active_only=True
    )
    assert blocker.code is PmBlockerCode.LEASE_UNAVAILABLE

    rebuilt = Database(str(db.engine.url))
    advanced = _run(rebuilt, client, _github(), now=NOW + timedelta(seconds=1))
    rebuilt.engine.dispose()

    assert advanced.ci_handoff_decisions[0].ticket_key == healthy.key
    advanced_reconciliation = advanced.ci_handoff_decisions[0].reconciliation
    assert advanced_reconciliation is not None
    assert advanced_reconciliation.reason is CIHandoffReason.SNAPSHOT_INCOMPLETE
    assert advanced.ci_handoff_mutations == 0
    assert client.state_writes == []
    lease.release(product_id=PRODUCT_ID, owner_id=live_owner)

    retry_client = _rebuilt_client(client)
    rebuilt = Database(str(db.engine.url))
    converged = _run(
        rebuilt,
        retry_client,
        _github(),
        now=NOW + timedelta(minutes=1, seconds=1),
    )
    rebuilt.engine.dispose()

    assert converged.ci_handoff_decisions[0].ticket_key == contended.key
    assert converged.ci_handoff_decisions[0].reconciliation is not None
    assert (
        converged.ci_handoff_decisions[0].reconciliation.reason
        is CIHandoffReason.SNAPSHOT_INCOMPLETE
    )
    assert converged.ci_handoff_mutations == 0
    stored = TicketRepo(db).get_by_key(contended.key)
    assert stored is not None and stored.status is TicketStatus.CI_PENDING
    reevaluated = PmRecoveryRepo(db).get_episode(initial.episode.id)
    assert reevaluated is not None and reevaluated.fairness_cursor > initial_cursor
    recovered_blocker = PmRecoveryRepo(db).get_blocker(blocker.id)
    assert recovered_blocker is not None
    assert recovered_blocker.superseded_at is not None
    assert retry_client.state_writes == []


def test_read_only_result_lost_before_fairness_commit_retries_then_rotates(
    db: Database,
) -> None:
    client = RecordingClient()
    first = _seed_ci_pending(db, client, key="ATLAS-263")
    second = _seed_ci_pending(db, client, key="ATLAS-264")
    assert first.external_linear_id is not None
    issue = client.fetch_issue(first.external_linear_id)
    assert issue is not None
    client._issues[first.external_linear_id] = replace(issue, github_publications=())

    def die_after_read_only_evaluation() -> None:
        raise SimulatedProcessDeath("read-only result lost before fairness commit")

    with pytest.raises(SimulatedProcessDeath):
        _run(
            db,
            client,
            _github(),
            hooks=CIHandoffHooks(
                after_candidate_evaluated=die_after_read_only_evaluation
            ),
        )
    assert client.state_writes == []
    database_url = str(db.engine.url)
    rebuilt_client = _rebuilt_client(client)
    db.engine.dispose()

    rebuilt = Database(database_url)
    retried = _run(rebuilt, rebuilt_client, _github(), now=NOW + timedelta(seconds=1))
    assert retried.ci_handoff_decisions[0].ticket_key == first.key
    assert retried.ci_handoff_mutations == 0
    next_client = _rebuilt_client(rebuilt_client)
    rebuilt.engine.dispose()

    final_db = Database(database_url)
    advanced = _run(final_db, next_client, _github(), now=NOW + timedelta(seconds=2))
    assert advanced.ci_handoff_decisions[0].ticket_key == second.key
    assert advanced.ci_handoff_mutations == 1
    final_db.engine.dispose()


def test_held_fairness_commit_survives_death_before_outer_receipt(
    db: Database,
) -> None:
    client = RecordingClient()
    first = _seed_ci_pending(db, client, key="ATLAS-263")
    second = _seed_ci_pending(db, client, key="ATLAS-264")
    assert first.external_linear_id is not None
    issue = client.fetch_issue(first.external_linear_id)
    assert issue is not None
    client._issues[first.external_linear_id] = replace(issue, github_publications=())

    def die_after_fairness_commit() -> None:
        raise SimulatedProcessDeath("fairness committed before receipt")

    with pytest.raises(SimulatedProcessDeath):
        _run(
            db,
            client,
            _github(),
            hooks=CIHandoffHooks(after_fairness_persisted=die_after_fairness_commit),
        )
    assert PmSyncReceiptRepo(db).list() == []
    [blocker] = PmRecoveryRepo(db).list_blockers(
        product_id=PRODUCT_ID, active_only=True
    )
    assert blocker.consecutive_observations == 1
    first_episode = next(
        episode
        for episode in PmRecoveryRepo(db).list_active_episodes_ordered(PRODUCT_ID)
        if episode.candidate_ticket_key == first.key
    )
    assert first_episode.candidate_ticket_key == first.key
    assert first_episode.last_evaluated_sequence is not None
    database_url = str(db.engine.url)
    rebuilt_client = _rebuilt_client(client)
    db.engine.dispose()

    rebuilt = Database(database_url)
    advanced = _run(
        rebuilt,
        rebuilt_client,
        _github(),
        now=NOW + timedelta(seconds=1),
    )
    assert advanced.ci_handoff_decisions[0].ticket_key == second.key
    retained = PmRecoveryRepo(rebuilt).get_blocker(blocker.id)
    assert retained is not None and retained.consecutive_observations == 1
    rebuilt.engine.dispose()


def test_fence_installed_after_fairness_commit_blocks_then_defers_admission(
    db: Database,
) -> None:
    client = RecordingClient()
    selected = _seed_ci_pending(db, client, key="ATLAS-263")
    planned = seed_ticket(
        db,
        client,
        key="ATLAS-264",
        product_id=PRODUCT_ID,
        status=TicketStatus.PLANNED,
        acceptance_criteria=["must not admit while CI authority is ambiguous"],
        linear_synced_at=NOW,
    )
    assert selected.external_linear_id is not None
    selected_issue = client.fetch_issue(selected.external_linear_id)
    assert selected_issue is not None
    client._issues[selected.external_linear_id] = replace(
        selected_issue, github_publications=()
    )
    installed_fence: Any = None

    def install_fence_after_fairness_commit() -> None:
        nonlocal installed_fence
        owner_id = uuid4()
        lease = AdmissionCoordinationRepo(db)
        assert lease.try_acquire(
            product_id=PRODUCT_ID,
            owner_id=owner_id,
            acquired_at=NOW,
            ttl=timedelta(minutes=1),
        )
        installed_fence = CIHandoffCoordinationRepo(db).begin_write(
            product_id=PRODUCT_ID,
            owner_id=owner_id,
            reconciliation_id=uuid4(),
            ticket_id=selected.id,
            ticket_key=selected.key,
            issue_id=selected.external_linear_id or "",
            source_state_id=CI_PENDING_STATE.id,
            target_state_id="state-review-required",
            target_status=TicketStatus.REVIEW_REQUIRED,
            created_at=NOW,
        )
        lease.release(product_id=PRODUCT_ID, owner_id=owner_id)

    result = _run(
        db,
        client,
        _github(),
        hooks=CIHandoffHooks(
            after_fairness_persisted=install_fence_after_fairness_commit
        ),
    )

    assert result.ci_handoff_evaluated == 1
    assert result.ci_handoff_held == 1
    assert result.ci_handoff_mutations == 0
    assert client.creates == []
    assert client.state_writes == []
    assert result.admission_decisions[0].reason.value == "ci_handoff_fence_present"
    assert CIHandoffCoordinationRepo(db).get_fence(PRODUCT_ID) == installed_fence
    episodes = PmRecoveryRepo(db).list_active_episodes_ordered(PRODUCT_ID)
    selected_episode = next(
        episode for episode in episodes if episode.candidate_ticket_id == selected.id
    )
    assert selected_episode.last_evaluated_sequence is not None

    database_url = str(db.engine.url)
    recovery_client = _rebuilt_client(client)
    db.engine.dispose()
    recovery_db = Database(database_url)
    recovered = _run(
        recovery_db,
        recovery_client,
        _github(),
        now=NOW + timedelta(seconds=1),
    )
    recovery_db.engine.dispose()

    assert recovered.ci_handoff_decisions[0].ticket_key == selected.key
    assert recovered.ci_handoff_decisions[0].reconciliation is not None
    assert (
        recovered.ci_handoff_decisions[0].reconciliation.reason
        is CIHandoffReason.FENCE_RECONCILED_SOURCE
    )
    assert recovery_client.state_writes == []
    assert CIHandoffCoordinationRepo(db).get_fence(PRODUCT_ID) is None

    resumed_client = _rebuilt_client(recovery_client)
    resumed_db = Database(database_url)
    resumed = _run(
        resumed_db,
        resumed_client,
        _github(),
        now=NOW + timedelta(seconds=2),
    )
    resumed_db.engine.dispose()

    assert resumed.admitted == 1
    assert resumed_client.state_writes == [(planned.external_linear_id, READY.id)]


def test_held_ci_evaluation_consumes_at_most_one_downstream_workflow_window(
    db: Database,
) -> None:
    client = RecordingClient()
    selected = _seed_ci_pending(db, client, key="ATLAS-263")
    first = seed_ticket(
        db,
        client,
        key="ATLAS-264",
        product_id=PRODUCT_ID,
        status=TicketStatus.PLANNED,
        acceptance_criteria=["first bounded definition creation"],
        with_issue=False,
        linear_synced_at=None,
    )
    assert selected.external_linear_id is not None
    selected_issue = client.fetch_issue(selected.external_linear_id)
    assert selected_issue is not None
    client._issues[selected.external_linear_id] = replace(
        selected_issue, github_publications=()
    )

    result = _run(db, client, _github())

    assert result.ci_handoff_held == 1
    assert len(client.creates) == 1
    assert len(client.state_writes) == 1
    assert result.admitted == 0
    assert result.admission_decisions == []
    stored_first = TicketRepo(db).get_by_key(first.key)
    assert stored_first is not None and stored_first.external_linear_id is not None


def test_retained_admission_fence_recovers_before_same_product_ci_candidate(
    db: Database,
) -> None:
    client = RecordingClient()
    ci_ticket = _seed_ci_pending(db, client, key="ATLAS-263")
    admission_ticket = seed_ticket(
        db,
        client,
        key="ATLAS-264",
        product_id=PRODUCT_ID,
        status=TicketStatus.PLANNED,
        acceptance_criteria=["recover prior admission ambiguity first"],
        linear_synced_at=NOW,
    )
    assert admission_ticket.external_linear_id is not None
    admission_issue = client.fetch_issue(admission_ticket.external_linear_id)
    assert admission_issue is not None
    assert admission_issue.state_id is not None
    owner_id = uuid4()
    admission_run_id = uuid4()
    coordination = AdmissionCoordinationRepo(db)
    assert coordination.try_acquire(
        product_id=PRODUCT_ID,
        owner_id=owner_id,
        acquired_at=NOW,
        ttl=timedelta(minutes=1),
    )
    fence = coordination.begin_write(
        product_id=PRODUCT_ID,
        owner_id=owner_id,
        admission_run_id=admission_run_id,
        ticket_id=admission_ticket.id,
        ticket_key=admission_ticket.key,
        issue_id=admission_ticket.external_linear_id,
        source_state_id=admission_issue.state_id,
        target_state_id=READY.id,
        policy_revision=1,
        created_at=NOW,
    )
    coordination.release(product_id=PRODUCT_ID, owner_id=owner_id)
    assert PmRecoveryRepo(db).list_active_episodes_ordered(PRODUCT_ID) == []
    assert PmRecoveryRepo(db).list_blockers(product_id=PRODUCT_ID) == []
    with db.session() as session:
        assert (
            session.scalar(
                sa.select(sa.func.count(PmRecoverySequenceCounterRow.product_id))
            )
            == 0
        )
    database_url = str(db.engine.url)
    recovery_client = _rebuilt_client(client)
    db.engine.dispose()

    recovery_db = Database(database_url)
    recovered = _run(
        recovery_db,
        recovery_client,
        _github(),
        now=NOW + timedelta(seconds=1),
    )
    recovery_db.engine.dispose()

    assert recovered.ci_handoff_evaluated == 0
    assert recovered.admission_decisions[0].reason.value == (
        "indeterminate_reconciled_no_write"
    )
    assert recovery_client.state_writes == []
    assert AdmissionCoordinationRepo(db).get_fence(PRODUCT_ID) is None
    assert PmRecoveryRepo(db).list_active_episodes_ordered(PRODUCT_ID) == []
    assert PmRecoveryRepo(db).list_blockers(product_id=PRODUCT_ID) == []
    with db.session() as session:
        assert (
            session.scalar(
                sa.select(sa.func.count(PmRecoverySequenceCounterRow.product_id))
            )
            == 0
        )

    resumed_client = _rebuilt_client(recovery_client)
    resumed_db = Database(database_url)
    resumed = _run(
        resumed_db,
        resumed_client,
        _github(),
        now=NOW + timedelta(seconds=2),
    )
    resumed_db.engine.dispose()

    assert resumed.ci_handoff_mutations == 1
    assert resumed_client.state_writes == [
        (ci_ticket.external_linear_id, "state-review-required")
    ]
    assert fence.admission_run_id == admission_run_id


def test_unresolved_admission_fence_rotates_to_independent_ci_product(
    db: Database,
) -> None:
    client = RecordingClient()
    admission_ticket = seed_ticket(
        db,
        client,
        key="ATLAS-264",
        product_id=PRODUCT_ID,
        status=TicketStatus.PLANNED,
        acceptance_criteria=["retain an unresolved admission ambiguity"],
        linear_synced_at=NOW,
    )
    healthy_product_id = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
    healthy = _seed_ci_pending(
        db,
        client,
        key="OTHER-263",
        product_id=healthy_product_id,
    )
    assert admission_ticket.external_linear_id is not None
    admission_issue = client.fetch_issue(admission_ticket.external_linear_id)
    assert admission_issue is not None and admission_issue.state_id is not None
    owner_id = uuid4()
    coordination = AdmissionCoordinationRepo(db)
    assert coordination.try_acquire(
        product_id=PRODUCT_ID,
        owner_id=owner_id,
        acquired_at=NOW,
        ttl=timedelta(minutes=1),
    )
    initial_fence = coordination.begin_write(
        product_id=PRODUCT_ID,
        owner_id=owner_id,
        admission_run_id=uuid4(),
        ticket_id=admission_ticket.id,
        ticket_key=admission_ticket.key,
        issue_id=admission_ticket.external_linear_id,
        source_state_id=admission_issue.state_id,
        target_state_id=READY.id,
        policy_revision=1,
        created_at=NOW,
    )
    coordination.release(product_id=PRODUCT_ID, owner_id=owner_id)
    client._issues.pop(admission_ticket.external_linear_id)

    first = _run(db, client, _github(), now=NOW + timedelta(seconds=1))

    assert first.ci_handoff_evaluated == 0
    assert (
        first.admission_decisions[0].reason
        is AdmissionSyncReason.INDETERMINATE_STILL_UNRESOLVED
    )
    deferred_fence = AdmissionCoordinationRepo(db).get_fence(PRODUCT_ID)
    assert deferred_fence is not None
    assert deferred_fence.admission_run_id == initial_fence.admission_run_id
    assert deferred_fence.updated_at > initial_fence.updated_at
    assert client.state_writes == []

    database_url = str(db.engine.url)
    healthy_client = _rebuilt_client(client)
    db.engine.dispose()
    healthy_db = Database(database_url)
    second = _run(
        healthy_db,
        healthy_client,
        _github(),
        now=NOW + timedelta(seconds=2),
    )
    healthy_db.engine.dispose()

    assert second.ci_handoff_decisions[0].ticket_key == healthy.key
    assert second.ci_handoff_mutations == 1
    assert healthy_client.state_writes == [
        (healthy.external_linear_id, "state-review-required")
    ]
    assert AdmissionCoordinationRepo(db).get_fence(PRODUCT_ID) == deferred_fence

    retry_client = _rebuilt_client(healthy_client)
    retry_db = Database(database_url)
    third = _run(
        retry_db,
        retry_client,
        _github(),
        now=NOW + timedelta(seconds=3),
    )
    retry_db.engine.dispose()

    assert third.ci_handoff_evaluated == 0
    assert (
        third.admission_decisions[0].reason
        is AdmissionSyncReason.INDETERMINATE_STILL_UNRESOLVED
    )
    assert retry_client.state_writes == []


@pytest.mark.parametrize("publication_available", [True, False])
def test_late_admission_fence_closes_tick_then_recovers_before_ci_candidate(
    db: Database,
    publication_available: bool,
) -> None:
    client = RecordingClient()
    ci_ticket = _seed_ci_pending(db, client, key="ATLAS-263")
    admission_ticket = seed_ticket(
        db,
        client,
        key="ATLAS-264",
        product_id=PRODUCT_ID,
        status=TicketStatus.PLANNED,
        acceptance_criteria=["recover a late admission ambiguity first"],
        linear_synced_at=NOW,
    )
    deferred_definition = seed_ticket(
        db,
        client,
        key="ATLAS-265",
        product_id=PRODUCT_ID,
        status=TicketStatus.PLANNED,
        acceptance_criteria=["defer this definition to a fresh tick"],
        with_issue=False,
        linear_synced_at=None,
    )
    assert admission_ticket.external_linear_id is not None
    admission_issue = client.fetch_issue(admission_ticket.external_linear_id)
    assert admission_issue is not None and admission_issue.state_id is not None
    if not publication_available:
        assert ci_ticket.external_linear_id is not None
        ci_issue = client.fetch_issue(ci_ticket.external_linear_id)
        assert ci_issue is not None
        client._issues[ci_ticket.external_linear_id] = replace(
            ci_issue, github_publications=()
        )
    selected = select_fair_ci_handoff_candidate(
        db=db,
        tickets=TicketRepo(db),
        initial_issues=client.fetch_project_issues(PROJECT_ID),
        now=NOW,
    )
    assert selected.episode is not None
    initial_cursor = selected.episode.fairness_cursor
    installed_fence = None

    def install_late_admission_fence() -> None:
        nonlocal installed_fence
        owner_id = uuid4()
        coordination = AdmissionCoordinationRepo(db)
        assert coordination.try_acquire(
            product_id=PRODUCT_ID,
            owner_id=owner_id,
            acquired_at=NOW,
            ttl=timedelta(minutes=1),
        )
        installed_fence = coordination.begin_write(
            product_id=PRODUCT_ID,
            owner_id=owner_id,
            admission_run_id=uuid4(),
            ticket_id=admission_ticket.id,
            ticket_key=admission_ticket.key,
            issue_id=admission_ticket.external_linear_id or "",
            source_state_id=admission_issue.state_id or "",
            target_state_id=READY.id,
            policy_revision=1,
            created_at=NOW,
        )
        coordination.release(product_id=PRODUCT_ID, owner_id=owner_id)

    deferred = _run(
        db,
        client,
        _github(),
        hooks=CIHandoffHooks(after_candidate_selected=install_late_admission_fence),
    )

    decision = deferred.ci_handoff_decisions[0]
    if publication_available:
        assert decision.reconciliation is not None
        assert decision.reconciliation.reason is CIHandoffReason.LEASE_LOST
        assert decision.ends_workflow_write_window
    else:
        assert decision.reason is CIHandoffAdapterReason.PUBLICATION_UNAVAILABLE
        assert decision.reconciliation is None
    assert deferred.ci_handoff_mutations == 0
    assert (
        deferred.admission_decisions[0].reason
        is AdmissionSyncReason.WRITE_INDETERMINATE
    )
    assert client.creates == []
    assert client.state_writes == []
    assert AdmissionCoordinationRepo(db).get_fence(PRODUCT_ID) == installed_fence
    retained_episode = PmRecoveryRepo(db).get_episode(selected.episode.id)
    assert retained_episode is not None
    assert retained_episode.fairness_cursor == initial_cursor
    assert PmRecoveryRepo(db).list_blockers(product_id=PRODUCT_ID) == []
    stored_definition = TicketRepo(db).get_by_key(deferred_definition.key)
    assert stored_definition is not None
    assert stored_definition.external_linear_id is None

    database_url = str(db.engine.url)
    recovery_client = _rebuilt_client(client)
    db.engine.dispose()
    recovery_db = Database(database_url)
    recovered = _run(
        recovery_db,
        recovery_client,
        _github(),
        now=NOW + timedelta(seconds=1),
    )
    recovery_db.engine.dispose()

    assert recovered.ci_handoff_evaluated == 0
    assert recovered.admission_decisions[0].reason.value == (
        "indeterminate_reconciled_no_write"
    )
    assert recovery_client.state_writes == []
    assert AdmissionCoordinationRepo(db).get_fence(PRODUCT_ID) is None

    resumed_client = _rebuilt_client(recovery_client)
    if not publication_available:
        assert ci_ticket.external_linear_id is not None
        resumed_client.seed_github_publication(
            ci_ticket.external_linear_id,
            owner="derekrivers",
            repo="atlas",
            pr_number=PR_NUMBER,
        )
    resumed_db = Database(database_url)
    resumed = _run(
        resumed_db,
        resumed_client,
        _github(),
        now=NOW + timedelta(seconds=2),
    )
    resumed_db.engine.dispose()

    assert resumed.ci_handoff_mutations == 1
    assert resumed_client.state_writes == [
        (ci_ticket.external_linear_id, "state-review-required")
    ]


def test_competing_reconstructed_ticks_commit_one_stale_cursor_result(
    db: Database,
) -> None:
    client = RecordingClient()
    ticket = _seed_ci_pending(db, client)
    assert ticket.external_linear_id is not None
    issue = client.fetch_issue(ticket.external_linear_id)
    assert issue is not None
    client._issues[ticket.external_linear_id] = replace(issue, github_publications=())
    select_fair_ci_handoff_candidate(
        db=db,
        tickets=TicketRepo(db),
        initial_issues=client.fetch_project_issues(PROJECT_ID),
        now=NOW,
    )
    database_url = str(db.engine.url)
    first_client = _rebuilt_client(client)
    second_client = _rebuilt_client(client)
    db.engine.dispose()
    selected = Barrier(2)

    def await_both_selections() -> None:
        selected.wait(timeout=5)

    def attempt(item: tuple[RecordingClient, int]) -> Any:
        candidate_client, offset = item
        database = Database(database_url)
        try:
            return _run(
                database,
                candidate_client,
                _github(),
                now=NOW + timedelta(seconds=offset),
                hooks=CIHandoffHooks(after_candidate_selected=await_both_selections),
            )
        finally:
            database.engine.dispose()

    outcomes: list[Any] = []
    failures: list[PmRecoveryStorageError] = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(attempt, item)
            for item in ((first_client, 1), (second_client, 2))
        ]
        for future in futures:
            try:
                outcomes.append(future.result(timeout=10))
            except PmRecoveryStorageError as error:
                failures.append(error)

    assert len(outcomes) == 1
    assert len(failures) == 1
    assert failures[0].code is PmRecoveryStorageCode.EVALUATION_CURSOR_CONFLICT
    assert first_client.state_writes == []
    assert second_client.state_writes == []
    rebuilt = Database(database_url)
    [blocker] = PmRecoveryRepo(rebuilt).list_blockers(
        product_id=PRODUCT_ID, active_only=True
    )
    assert blocker.consecutive_observations == 1
    rebuilt.engine.dispose()


def test_fence_recovery_refreshes_board_after_acquiring_lease(db: Database) -> None:
    class AdvancingBoardClient(RecordingClient):
        def __init__(self) -> None:
            super().__init__()
            self.project_pulls = 0
            self.advance_issue_id: str | None = None

        def fetch_project_issues(self, project_id: str) -> list[LinearIssue]:
            self.project_pulls += 1
            if self.project_pulls == 2:
                with db.session() as session:
                    assert (
                        session.scalar(
                            sa.select(AdmissionLeaseRow.owner_id).where(
                                AdmissionLeaseRow.product_id == PRODUCT_ID,
                                AdmissionLeaseRow.expires_at > NOW,
                            )
                        )
                        is not None
                    )
                assert self.advance_issue_id is not None
                self.simulate_linear_state(self.advance_issue_id, REVIEW_REQUIRED_STATE)
            return super().fetch_project_issues(project_id)

    client = AdvancingBoardClient()
    ticket = _seed_ci_pending(db, client)
    assert ticket.external_linear_id is not None
    client.advance_issue_id = ticket.external_linear_id
    owner_id = uuid4()
    lease = AdmissionCoordinationRepo(db)
    assert lease.try_acquire(
        product_id=PRODUCT_ID,
        owner_id=owner_id,
        acquired_at=NOW,
        ttl=timedelta(minutes=1),
    )
    CIHandoffCoordinationRepo(db).begin_write(
        product_id=PRODUCT_ID,
        owner_id=owner_id,
        reconciliation_id=uuid4(),
        ticket_id=ticket.id,
        ticket_key=ticket.key,
        issue_id=ticket.external_linear_id,
        source_state_id=CI_PENDING_STATE.id,
        target_state_id="state-review-required",
        target_status=TicketStatus.REVIEW_REQUIRED,
        created_at=NOW,
    )
    lease.release(product_id=PRODUCT_ID, owner_id=owner_id)

    recovered = _run(db, client, _github(), now=NOW)

    assert client.project_pulls == 2
    assert (
        recovered.ci_handoff_decisions[0].reconciliation.reason
        is CIHandoffReason.FENCE_RECONCILED_TARGET
    )
    assert client.state_writes == []
    assert CIHandoffCoordinationRepo(db).get_fence(PRODUCT_ID) is None


def test_target_recovery_rolls_back_local_status_when_atomic_retirement_crashes(
    db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from atlas.storage.repositories import _apply_linear_status_in_session

    client = RecordingClient()
    ticket = _seed_ci_pending(db, client)
    assert ticket.external_linear_id is not None
    owner_id = uuid4()
    lease = AdmissionCoordinationRepo(db)
    assert lease.try_acquire(
        product_id=PRODUCT_ID,
        owner_id=owner_id,
        acquired_at=NOW,
        ttl=timedelta(minutes=1),
    )
    fence = CIHandoffCoordinationRepo(db).begin_write(
        product_id=PRODUCT_ID,
        owner_id=owner_id,
        reconciliation_id=uuid4(),
        ticket_id=ticket.id,
        ticket_key=ticket.key,
        issue_id=ticket.external_linear_id,
        source_state_id=CI_PENDING_STATE.id,
        target_state_id="state-review-required",
        target_status=TicketStatus.REVIEW_REQUIRED,
        created_at=NOW,
    )
    lease.release(product_id=PRODUCT_ID, owner_id=owner_id)
    client.simulate_linear_state(ticket.external_linear_id, REVIEW_REQUIRED_STATE)

    def die_after_local_status(*args: Any, **kwargs: Any) -> Any:
        _apply_linear_status_in_session(*args, **kwargs)
        raise SimulatedProcessDeath("after local status before fence retirement")

    monkeypatch.setattr(
        "atlas.storage.ci_handoff_coordination._apply_linear_status_in_session",
        die_after_local_status,
    )

    with pytest.raises(SimulatedProcessDeath, match="after local status"):
        reconcile_ci_handoff_fence(
            db=db,
            tickets=TicketRepo(db),
            status_map=status_map(),
            initial_issues=client.fetch_project_issues(PROJECT_ID),
            product_id=PRODUCT_ID,
            now=NOW,
            linear=client,
            project_id=PROJECT_ID,
            expected_reconciliation_id=fence.reconciliation_id,
            expected_ticket_id=fence.ticket_id,
        )

    retained = TicketRepo(db).get_by_key(ticket.key)
    assert retained is not None and retained.status is TicketStatus.CI_PENDING
    assert CIHandoffCoordinationRepo(db).get_fence(PRODUCT_ID) == fence
    assert client.state_writes == []


def test_target_recovery_refuses_divergent_local_source_status(db: Database) -> None:
    client = RecordingClient()
    ticket = _seed_ci_pending(db, client)
    assert ticket.external_linear_id is not None
    owner_id = uuid4()
    lease = AdmissionCoordinationRepo(db)
    assert lease.try_acquire(
        product_id=PRODUCT_ID,
        owner_id=owner_id,
        acquired_at=NOW,
        ttl=timedelta(minutes=1),
    )
    fence = CIHandoffCoordinationRepo(db).begin_write(
        product_id=PRODUCT_ID,
        owner_id=owner_id,
        reconciliation_id=uuid4(),
        ticket_id=ticket.id,
        ticket_key=ticket.key,
        issue_id=ticket.external_linear_id,
        source_state_id=CI_PENDING_STATE.id,
        target_state_id="state-review-required",
        target_status=TicketStatus.REVIEW_REQUIRED,
        created_at=NOW,
    )
    lease.release(product_id=PRODUCT_ID, owner_id=owner_id)
    TicketRepo(db).apply_linear_status(
        ticket.key,
        TicketStatus.DONE,
        now=NOW + timedelta(seconds=1),
        created_by_id="test:divergent-local-status",
    )
    client.simulate_linear_state(ticket.external_linear_id, REVIEW_REQUIRED_STATE)

    with pytest.raises(
        CIHandoffWriteFenceError,
        match="divergent local status",
    ):
        reconcile_ci_handoff_fence(
            db=db,
            tickets=TicketRepo(db),
            status_map=status_map(),
            initial_issues=client.fetch_project_issues(PROJECT_ID),
            product_id=PRODUCT_ID,
            now=NOW + timedelta(seconds=2),
            linear=client,
            project_id=PROJECT_ID,
            expected_reconciliation_id=fence.reconciliation_id,
            expected_ticket_id=fence.ticket_id,
        )

    retained = TicketRepo(db).get_by_key(ticket.key)
    assert retained is not None and retained.status is TicketStatus.DONE
    assert CIHandoffCoordinationRepo(db).get_fence(PRODUCT_ID) == fence
    assert client.state_writes == []


def test_fence_appearing_after_initial_scan_uses_fresh_owned_recovery(
    db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = RecordingClient()
    selected_ticket = _seed_ci_pending(db, client, key="ATLAS-263")
    fenced_ticket = _seed_ci_pending(db, client, key="ATLAS-264")
    assert fenced_ticket.external_linear_id is not None
    initial = select_fair_ci_handoff_candidate(
        db=db,
        tickets=TicketRepo(db),
        initial_issues=client.fetch_project_issues(PROJECT_ID),
        now=NOW,
    )
    assert initial.candidate == selected_ticket and initial.episode is not None
    selected_cursor = initial.episode.fairness_cursor
    fenced_episode = next(
        episode
        for episode in PmRecoveryRepo(db).list_active_episodes_ordered(PRODUCT_ID)
        if episode.candidate_ticket_id == fenced_ticket.id
    )
    original_pull = drive_evidence_pull
    installed = False

    def install_fence(*args: Any, **kwargs: Any) -> Any:
        nonlocal installed
        if not installed:
            installed = True
            owner_id = uuid4()
            lease = AdmissionCoordinationRepo(db)
            assert lease.try_acquire(
                product_id=PRODUCT_ID,
                owner_id=owner_id,
                acquired_at=NOW,
                ttl=timedelta(minutes=1),
            )
            CIHandoffCoordinationRepo(db).begin_write(
                product_id=PRODUCT_ID,
                owner_id=owner_id,
                reconciliation_id=uuid4(),
                ticket_id=fenced_ticket.id,
                ticket_key=fenced_ticket.key,
                issue_id=fenced_ticket.external_linear_id or "",
                source_state_id=CI_PENDING_STATE.id,
                target_state_id="state-review-required",
                target_status=TicketStatus.REVIEW_REQUIRED,
                created_at=NOW,
            )
            lease.release(product_id=PRODUCT_ID, owner_id=owner_id)
            client.simulate_linear_state(
                fenced_ticket.external_linear_id or "", REVIEW_REQUIRED_STATE
            )
        return original_pull(*args, **kwargs)

    monkeypatch.setattr(
        "atlas.pm.ci_handoff_adapter.drive_evidence_pull", install_fence
    )

    result = _run(db, client, _github())

    reconciliation = result.ci_handoff_decisions[0].reconciliation
    assert reconciliation is not None
    assert result.ci_handoff_decisions[0].ticket_key == fenced_ticket.key
    assert reconciliation.ticket_key == fenced_ticket.key
    assert reconciliation.reason is CIHandoffReason.FENCE_RECONCILED_TARGET
    assert client.state_writes == []
    assert CIHandoffCoordinationRepo(db).get_fence(PRODUCT_ID) is None
    retained_selected = PmRecoveryRepo(db).get_episode(initial.episode.id)
    assert retained_selected is not None
    assert retained_selected.fairness_cursor == selected_cursor
    recovered_fence = PmRecoveryRepo(db).get_episode(fenced_episode.id)
    assert recovered_fence is not None and recovered_fence.closed_at == NOW
    stored = TicketRepo(db).get_by_key(fenced_ticket.key)
    assert stored is not None and stored.status is TicketStatus.REVIEW_REQUIRED


def test_late_different_ticket_fence_preempts_publication_early_return(
    db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = RecordingClient()
    selected_ticket = _seed_ci_pending(db, client, key="ATLAS-263")
    fenced_ticket = _seed_ci_pending(db, client, key="ATLAS-264")
    initial = select_fair_ci_handoff_candidate(
        db=db,
        tickets=TicketRepo(db),
        initial_issues=client.fetch_project_issues(PROJECT_ID),
        now=NOW,
    )
    assert initial.candidate == selected_ticket and initial.episode is not None
    selected_cursor = initial.episode.fairness_cursor
    fenced_episode = next(
        episode
        for episode in PmRecoveryRepo(db).list_active_episodes_ordered(PRODUCT_ID)
        if episode.candidate_ticket_id == fenced_ticket.id
    )
    assert selected_ticket.external_linear_id is not None
    selected_issue = client.fetch_issue(selected_ticket.external_linear_id)
    assert selected_issue is not None
    client._issues[selected_ticket.external_linear_id] = replace(
        selected_issue,
        github_publications=(),
    )

    def install_target_fence() -> None:
        owner_id = uuid4()
        lease = AdmissionCoordinationRepo(db)
        assert lease.try_acquire(
            product_id=PRODUCT_ID,
            owner_id=owner_id,
            acquired_at=NOW,
            ttl=timedelta(minutes=1),
        )
        assert fenced_ticket.external_linear_id is not None
        CIHandoffCoordinationRepo(db).begin_write(
            product_id=PRODUCT_ID,
            owner_id=owner_id,
            reconciliation_id=uuid4(),
            ticket_id=fenced_ticket.id,
            ticket_key=fenced_ticket.key,
            issue_id=fenced_ticket.external_linear_id,
            source_state_id=CI_PENDING_STATE.id,
            target_state_id="state-review-required",
            target_status=TicketStatus.REVIEW_REQUIRED,
            created_at=NOW,
        )
        lease.release(product_id=PRODUCT_ID, owner_id=owner_id)
        client.simulate_linear_state(
            fenced_ticket.external_linear_id,
            REVIEW_REQUIRED_STATE,
        )

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a later workflow writer ran after late-fence recovery")

    monkeypatch.setattr("atlas.pm.sync.admit_one_ready", forbidden)
    monkeypatch.setattr("atlas.pm.sync.complete_verified", forbidden)
    result = _run(
        db,
        client,
        _github(),
        hooks=CIHandoffHooks(after_candidate_selected=install_target_fence),
    )

    decision = result.ci_handoff_decisions[0]
    assert decision.ticket_key == fenced_ticket.key
    assert decision.reconciliation is not None
    assert decision.reconciliation.ticket_key == fenced_ticket.key
    assert decision.reconciliation.reason is CIHandoffReason.FENCE_RECONCILED_TARGET
    assert decision.ends_workflow_write_window
    assert client.state_writes == []
    selected_episode = PmRecoveryRepo(db).get_episode(initial.episode.id)
    assert selected_episode is not None
    assert selected_episode.fairness_cursor == selected_cursor
    recovered_episode = PmRecoveryRepo(db).get_episode(fenced_episode.id)
    assert recovered_episode is not None and recovered_episode.closed_at == NOW
    assert CIHandoffCoordinationRepo(db).get_fence(PRODUCT_ID) is None


def test_late_fence_identity_overrides_stale_true_precedence_flag(
    db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = RecordingClient()
    selected_ticket = _seed_ci_pending(db, client, key="ATLAS-263")
    fenced_ticket = _seed_ci_pending(db, client, key="ATLAS-264")
    assert selected_ticket.external_linear_id is not None
    selected_issue = client.fetch_issue(selected_ticket.external_linear_id)
    assert selected_issue is not None
    client._issues[selected_ticket.external_linear_id] = replace(
        selected_issue,
        github_publications=(),
    )
    from atlas.pm.ci_handoff_adapter import reconcile_ci_handoff_candidate as original

    installed = False

    def return_stale_flag(*args: Any, **kwargs: Any) -> Any:
        nonlocal installed
        result = original(*args, **kwargs)
        if not installed:
            installed = True
            owner_id = uuid4()
            lease = AdmissionCoordinationRepo(db)
            assert lease.try_acquire(
                product_id=PRODUCT_ID,
                owner_id=owner_id,
                acquired_at=NOW,
                ttl=timedelta(minutes=1),
            )
            assert fenced_ticket.external_linear_id is not None
            CIHandoffCoordinationRepo(db).begin_write(
                product_id=PRODUCT_ID,
                owner_id=owner_id,
                reconciliation_id=uuid4(),
                ticket_id=fenced_ticket.id,
                ticket_key=fenced_ticket.key,
                issue_id=fenced_ticket.external_linear_id,
                source_state_id=CI_PENDING_STATE.id,
                target_state_id="state-review-required",
                target_status=TicketStatus.REVIEW_REQUIRED,
                created_at=NOW,
            )
            lease.release(product_id=PRODUCT_ID, owner_id=owner_id)
            client.simulate_linear_state(
                fenced_ticket.external_linear_id,
                REVIEW_REQUIRED_STATE,
            )
        return replace(result, fence_precedence=True)

    monkeypatch.setattr(
        "atlas.pm.sync.reconcile_ci_handoff_candidate",
        return_stale_flag,
    )

    result = _run(db, client, _github())

    decision = result.ci_handoff_decisions[0]
    assert decision.ticket_key == fenced_ticket.key
    assert decision.reconciliation is not None
    assert decision.reconciliation.ticket_key == fenced_ticket.key
    assert decision.reconciliation.reason is CIHandoffReason.FENCE_RECONCILED_TARGET
    assert CIHandoffCoordinationRepo(db).get_fence(PRODUCT_ID) is None
    assert client.state_writes == []


def test_late_fence_waits_for_next_tick_after_selected_candidate_mutates(
    db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = RecordingClient()
    selected_ticket = _seed_ci_pending(db, client, key="ATLAS-263")
    fenced_ticket = _seed_ci_pending(db, client, key="ATLAS-264")
    from atlas.pm.ci_handoff_adapter import reconcile_ci_handoff_candidate as original

    def install_after_confirmed_write(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        assert result.linear_mutations == 1
        owner_id = uuid4()
        lease = AdmissionCoordinationRepo(db)
        assert lease.try_acquire(
            product_id=PRODUCT_ID,
            owner_id=owner_id,
            acquired_at=NOW,
            ttl=timedelta(minutes=1),
        )
        assert fenced_ticket.external_linear_id is not None
        CIHandoffCoordinationRepo(db).begin_write(
            product_id=PRODUCT_ID,
            owner_id=owner_id,
            reconciliation_id=uuid4(),
            ticket_id=fenced_ticket.id,
            ticket_key=fenced_ticket.key,
            issue_id=fenced_ticket.external_linear_id,
            source_state_id=CI_PENDING_STATE.id,
            target_state_id="state-review-required",
            target_status=TicketStatus.REVIEW_REQUIRED,
            created_at=NOW,
        )
        lease.release(product_id=PRODUCT_ID, owner_id=owner_id)
        return result

    monkeypatch.setattr(
        "atlas.pm.sync.reconcile_ci_handoff_candidate",
        install_after_confirmed_write,
    )

    first = _run(db, client, _github())

    first_decision = first.ci_handoff_decisions[0]
    assert first_decision.ticket_key == selected_ticket.key
    assert first_decision.reconciliation is not None
    assert first_decision.reconciliation.reason is CIHandoffReason.WRITE_CONFIRMED
    assert first.ci_handoff_mutations == 1
    assert CIHandoffCoordinationRepo(db).get_fence(PRODUCT_ID) is not None
    assert client.state_writes == [
        (selected_ticket.external_linear_id, "state-review-required")
    ]

    rebuilt_client = _rebuilt_client(client)
    rebuilt = Database(str(db.engine.url))
    second = _run(
        rebuilt,
        rebuilt_client,
        _github(),
        now=NOW + timedelta(seconds=1),
    )
    rebuilt.engine.dispose()
    second_decision = second.ci_handoff_decisions[0]
    assert second_decision.ticket_key == fenced_ticket.key
    assert second_decision.reconciliation is not None
    assert (
        second_decision.reconciliation.reason is CIHandoffReason.FENCE_RECONCILED_SOURCE
    )
    assert CIHandoffCoordinationRepo(db).get_fence(PRODUCT_ID) is None
    assert rebuilt_client.state_writes == []


def test_one_fence_reconciliation_attempt_defers_a_second_late_fence(
    db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = RecordingClient()
    _seed_ci_pending(db, client, key="ATLAS-263")
    first_fenced = _seed_ci_pending(db, client, key="ATLAS-264")
    second_fenced = _seed_ci_pending(db, client, key="ATLAS-265")
    from atlas.pm.ci_handoff_adapter import reconcile_ci_handoff_candidate as original

    def install_first_fence() -> None:
        owner_id = uuid4()
        lease = AdmissionCoordinationRepo(db)
        assert lease.try_acquire(
            product_id=PRODUCT_ID,
            owner_id=owner_id,
            acquired_at=NOW,
            ttl=timedelta(minutes=1),
        )
        assert first_fenced.external_linear_id is not None
        CIHandoffCoordinationRepo(db).begin_write(
            product_id=PRODUCT_ID,
            owner_id=owner_id,
            reconciliation_id=uuid4(),
            ticket_id=first_fenced.id,
            ticket_key=first_fenced.key,
            issue_id=first_fenced.external_linear_id,
            source_state_id=CI_PENDING_STATE.id,
            target_state_id="state-review-required",
            target_status=TicketStatus.REVIEW_REQUIRED,
            created_at=NOW,
        )
        lease.release(product_id=PRODUCT_ID, owner_id=owner_id)
        client.simulate_linear_state(
            first_fenced.external_linear_id,
            REVIEW_REQUIRED_STATE,
        )

    def install_second_after_first_result(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        assert result.reconciliation is not None
        assert result.reconciliation.reason is CIHandoffReason.FENCE_RECONCILED_TARGET
        owner_id = uuid4()
        lease = AdmissionCoordinationRepo(db)
        assert lease.try_acquire(
            product_id=PRODUCT_ID,
            owner_id=owner_id,
            acquired_at=NOW,
            ttl=timedelta(minutes=1),
        )
        assert second_fenced.external_linear_id is not None
        CIHandoffCoordinationRepo(db).begin_write(
            product_id=PRODUCT_ID,
            owner_id=owner_id,
            reconciliation_id=uuid4(),
            ticket_id=second_fenced.id,
            ticket_key=second_fenced.key,
            issue_id=second_fenced.external_linear_id,
            source_state_id=CI_PENDING_STATE.id,
            target_state_id="state-review-required",
            target_status=TicketStatus.REVIEW_REQUIRED,
            created_at=NOW,
        )
        lease.release(product_id=PRODUCT_ID, owner_id=owner_id)
        return result

    monkeypatch.setattr(
        "atlas.pm.sync.reconcile_ci_handoff_candidate",
        install_second_after_first_result,
    )
    first = _run(
        db,
        client,
        _github(),
        hooks=CIHandoffHooks(after_candidate_selected=install_first_fence),
    )

    first_decision = first.ci_handoff_decisions[0]
    assert first_decision.ticket_key == first_fenced.key
    assert first_decision.reconciliation is not None
    assert (
        first_decision.reconciliation.reason is CIHandoffReason.FENCE_RECONCILED_TARGET
    )
    retained = CIHandoffCoordinationRepo(db).get_fence(PRODUCT_ID)
    assert retained is not None and retained.ticket_id == second_fenced.id
    assert client.state_writes == []

    rebuilt_client = _rebuilt_client(client)
    rebuilt = Database(str(db.engine.url))
    second = _run(
        rebuilt,
        rebuilt_client,
        _github(),
        now=NOW + timedelta(seconds=1),
    )
    rebuilt.engine.dispose()
    second_decision = second.ci_handoff_decisions[0]
    assert second_decision.ticket_key == second_fenced.key
    assert second_decision.reconciliation is not None
    assert (
        second_decision.reconciliation.reason is CIHandoffReason.FENCE_RECONCILED_SOURCE
    )
    assert CIHandoffCoordinationRepo(db).get_fence(PRODUCT_ID) is None
    assert rebuilt_client.state_writes == []


def test_replaced_unresolved_fence_cannot_record_against_new_authority(
    db: Database,
) -> None:
    client = RecordingClient()
    _seed_ci_pending(db, client, key="ATLAS-263")
    first_fenced = _seed_ci_pending(db, client, key="ATLAS-264")
    second_fenced = _seed_ci_pending(db, client, key="ATLAS-265")
    installed_fence: Any = None

    def install_unresolved_fence() -> None:
        nonlocal installed_fence
        owner_id = uuid4()
        lease = AdmissionCoordinationRepo(db)
        assert lease.try_acquire(
            product_id=PRODUCT_ID,
            owner_id=owner_id,
            acquired_at=NOW,
            ttl=timedelta(minutes=1),
        )
        assert first_fenced.external_linear_id is not None
        installed_fence = CIHandoffCoordinationRepo(db).begin_write(
            product_id=PRODUCT_ID,
            owner_id=owner_id,
            reconciliation_id=uuid4(),
            ticket_id=first_fenced.id,
            ticket_key=first_fenced.key,
            issue_id=first_fenced.external_linear_id,
            source_state_id=CI_PENDING_STATE.id,
            target_state_id="state-review-required",
            target_status=TicketStatus.REVIEW_REQUIRED,
            created_at=NOW,
        )
        lease.release(product_id=PRODUCT_ID, owner_id=owner_id)
        client._issues.pop(first_fenced.external_linear_id)

    def replace_before_fairness_commit() -> None:
        owner_id = uuid4()
        lease = AdmissionCoordinationRepo(db)
        assert lease.try_acquire(
            product_id=PRODUCT_ID,
            owner_id=owner_id,
            acquired_at=NOW,
            ttl=timedelta(minutes=1),
        )
        CIHandoffCoordinationRepo(db).clear_owned_fence(
            product_id=PRODUCT_ID,
            owner_id=owner_id,
            reconciliation_id=installed_fence.reconciliation_id,
            observed_at=NOW,
        )
        assert second_fenced.external_linear_id is not None
        CIHandoffCoordinationRepo(db).begin_write(
            product_id=PRODUCT_ID,
            owner_id=owner_id,
            reconciliation_id=uuid4(),
            ticket_id=second_fenced.id,
            ticket_key=second_fenced.key,
            issue_id=second_fenced.external_linear_id,
            source_state_id=CI_PENDING_STATE.id,
            target_state_id="state-review-required",
            target_status=TicketStatus.REVIEW_REQUIRED,
            created_at=NOW,
        )
        lease.release(product_id=PRODUCT_ID, owner_id=owner_id)

    with pytest.raises(
        CIHandoffFairnessError,
        match="no longer matches the exact live fence",
    ):
        _run(
            db,
            client,
            _github(),
            hooks=CIHandoffHooks(
                after_candidate_selected=install_unresolved_fence,
                after_candidate_evaluated=replace_before_fairness_commit,
            ),
        )

    retained = CIHandoffCoordinationRepo(db).get_fence(PRODUCT_ID)
    assert retained is not None and retained.ticket_id == second_fenced.id
    assert (
        PmRecoveryRepo(db).list_blockers(
            product_id=PRODUCT_ID,
            active_only=True,
        )
        == []
    )
    assert client.state_writes == []


def test_fence_replacement_after_blocker_classification_fails_atomic_cas(
    db: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = RecordingClient()
    ticket = _seed_ci_pending(db, client)
    selected = select_fair_ci_handoff_candidate(
        db=db,
        tickets=TicketRepo(db),
        initial_issues=client.fetch_project_issues(PROJECT_ID),
        now=NOW,
    )
    assert selected.episode is not None
    initial_cursor = selected.episode.fairness_cursor
    lease = AdmissionCoordinationRepo(db)
    owner_id = uuid4()
    assert lease.try_acquire(
        product_id=PRODUCT_ID,
        owner_id=owner_id,
        acquired_at=NOW,
        ttl=timedelta(minutes=1),
    )
    assert ticket.external_linear_id is not None
    first_fence = CIHandoffCoordinationRepo(db).begin_write(
        product_id=PRODUCT_ID,
        owner_id=owner_id,
        reconciliation_id=uuid4(),
        ticket_id=ticket.id,
        ticket_key=ticket.key,
        issue_id=ticket.external_linear_id,
        source_state_id=CI_PENDING_STATE.id,
        target_state_id="state-review-required",
        target_status=TicketStatus.REVIEW_REQUIRED,
        created_at=NOW,
    )
    lease.release(product_id=PRODUCT_ID, owner_id=owner_id)
    client._issues.pop(ticket.external_linear_id)
    from atlas.pm.ci_handoff_fairness import _fence_authority_id as original

    replacement_fence: Any = None

    def replace_after_classification(*args: Any, **kwargs: Any) -> str:
        nonlocal replacement_fence
        authority_id = original(*args, **kwargs)
        replacement_owner = uuid4()
        assert lease.try_acquire(
            product_id=PRODUCT_ID,
            owner_id=replacement_owner,
            acquired_at=NOW,
            ttl=timedelta(minutes=1),
        )
        CIHandoffCoordinationRepo(db).clear_owned_fence(
            product_id=PRODUCT_ID,
            owner_id=replacement_owner,
            reconciliation_id=first_fence.reconciliation_id,
            observed_at=NOW,
        )
        replacement_fence = CIHandoffCoordinationRepo(db).begin_write(
            product_id=PRODUCT_ID,
            owner_id=replacement_owner,
            reconciliation_id=uuid4(),
            ticket_id=ticket.id,
            ticket_key=ticket.key,
            issue_id=ticket.external_linear_id or "",
            source_state_id=CI_PENDING_STATE.id,
            target_state_id="state-review-required",
            target_status=TicketStatus.REVIEW_REQUIRED,
            created_at=NOW,
        )
        lease.release(product_id=PRODUCT_ID, owner_id=replacement_owner)
        return authority_id

    monkeypatch.setattr(
        "atlas.pm.ci_handoff_fairness._fence_authority_id",
        replace_after_classification,
    )

    with pytest.raises(PmRecoveryStorageError) as conflict:
        _run(db, client, _github())

    assert conflict.value.code is PmRecoveryStorageCode.FENCE_IDENTITY_CONFLICT
    retained = PmRecoveryRepo(db).get_episode(selected.episode.id)
    assert retained is not None and retained.fairness_cursor == initial_cursor
    assert CIHandoffCoordinationRepo(db).get_fence(PRODUCT_ID) == replacement_fence
    assert (
        PmRecoveryRepo(db).list_blockers(
            product_id=PRODUCT_ID,
            active_only=True,
        )
        == []
    )
    assert client.state_writes == []


def test_fence_recovery_cannot_clear_after_lease_expires_during_refresh(
    db: Database,
) -> None:
    client = RecordingClient()
    ticket = _seed_ci_pending(db, client)
    assert ticket.external_linear_id is not None
    owner_id = uuid4()
    lease = AdmissionCoordinationRepo(db)
    assert lease.try_acquire(
        product_id=PRODUCT_ID,
        owner_id=owner_id,
        acquired_at=NOW,
        ttl=timedelta(minutes=1),
    )
    fence = CIHandoffCoordinationRepo(db).begin_write(
        product_id=PRODUCT_ID,
        owner_id=owner_id,
        reconciliation_id=uuid4(),
        ticket_id=ticket.id,
        ticket_key=ticket.key,
        issue_id=ticket.external_linear_id,
        source_state_id=CI_PENDING_STATE.id,
        target_state_id="state-review-required",
        target_status=TicketStatus.REVIEW_REQUIRED,
        created_at=NOW,
    )
    lease.release(product_id=PRODUCT_ID, owner_id=owner_id)
    clock = iter((0.0, 301.0))

    result = reconcile_ci_handoff_fence(
        db=db,
        tickets=TicketRepo(db),
        status_map=status_map(),
        initial_issues=client.fetch_project_issues(PROJECT_ID),
        product_id=PRODUCT_ID,
        now=NOW,
        linear=client,
        project_id=PROJECT_ID,
        monotonic_clock=lambda: next(clock),
    )

    assert result is not None and result.reason is CIHandoffReason.LEASE_LOST
    assert CIHandoffCoordinationRepo(db).get_fence(PRODUCT_ID) == fence


@pytest.mark.parametrize(
    ("moved", "expected_reason"),
    [
        (False, CIHandoffReason.FENCE_RECONCILED_SOURCE),
        (True, CIHandoffReason.FENCE_RECONCILED_MOVED),
    ],
)
def test_indeterminate_fence_recovery_preempts_all_second_workflow_writes(
    db: Database,
    monkeypatch: pytest.MonkeyPatch,
    moved: bool,
    expected_reason: CIHandoffReason,
) -> None:
    from test_ci_handoff_reconciliation import AmbiguousNoWriteClient

    client = AmbiguousNoWriteClient()
    ticket = _seed_ci_pending(db, client)
    github = _github()
    first = _run(db, client, github, now=NOW)
    assert first.ci_handoff_decisions[0].reconciliation is not None
    assert (
        first.ci_handoff_decisions[0].reconciliation.reason
        is CIHandoffReason.WRITE_INDETERMINATE
    )
    if moved:
        assert ticket.external_linear_id is not None
        client.simulate_linear_state(ticket.external_linear_id, STARTED)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a later workflow writer ran during fence recovery")

    monkeypatch.setattr("atlas.pm.sync.admit_one_ready", forbidden)
    monkeypatch.setattr("atlas.pm.sync.complete_verified", forbidden)
    second = _run(db, client, github, now=NOW + timedelta(seconds=1))

    decision = second.ci_handoff_decisions[0]
    assert decision.reconciliation is not None
    assert decision.reconciliation.reason is expected_reason
    assert decision.ends_workflow_write_window
    assert len(client.state_writes) == 1
    assert CIHandoffCoordinationRepo(db).get_fence(PRODUCT_ID) is None
    blockers = PmRecoveryRepo(db).list_blockers(product_id=PRODUCT_ID)
    assert any(blocker.superseded_at is not None for blocker in blockers)


@pytest.mark.parametrize(
    (
        "case",
        "conclusion",
        "source_event_at",
        "include_test",
        "classification",
        "mutations",
    ),
    [
        (
            "passed",
            "success",
            NOW,
            True,
            CIHandoffClassification.PASSED,
            1,
        ),
        (
            "implementation",
            "failure",
            NOW,
            True,
            CIHandoffClassification.IMPLEMENTATION_FAILURE,
            1,
        ),
        (
            "pending",
            None,
            NOW,
            True,
            CIHandoffClassification.PENDING,
            0,
        ),
        (
            "missing",
            "success",
            NOW,
            False,
            CIHandoffClassification.MISSING,
            0,
        ),
        (
            "infrastructure",
            "timed_out",
            NOW,
            True,
            CIHandoffClassification.INFRASTRUCTURE,
            0,
        ),
        (
            "unknown-conclusion",
            "provider_unknown",
            NOW,
            True,
            CIHandoffClassification.INFRASTRUCTURE,
            0,
        ),
        (
            "malformed",
            "success",
            None,
            True,
            CIHandoffClassification.MALFORMED,
            0,
        ),
        (
            "indeterminate",
            "skipped",
            NOW,
            True,
            CIHandoffClassification.INDETERMINATE,
            0,
        ),
    ],
)
def test_production_adapter_routes_or_holds_every_ci_evidence_class(
    db: Database,
    case: str,
    conclusion: str | None,
    source_event_at: Any,
    include_test: bool,
    classification: CIHandoffClassification,
    mutations: int,
) -> None:
    client = RecordingClient()
    ticket = seed_ticket(
        db,
        client,
        key="ATLAS-263",
        product_id=PRODUCT_ID,
        status=TicketStatus.CI_PENDING,
        issue_state=CI_PENDING_STATE,
        acceptance_criteria=["bounded transition"],
        linear_synced_at=NOW,
    )
    assert ticket.external_linear_id is not None
    client.seed_github_publication(
        ticket.external_linear_id,
        owner="derekrivers",
        repo="atlas",
        pr_number=PR_NUMBER,
    )
    # Add the exact dispatch/handoff episode; production-shaped evidence is
    # created only by the canonical pull path exercised inside the PM tick.
    transitions = TicketStatusTransitionRepo(db)
    transitions.record(
        _transition(
            ticket,
            TicketStatus.READY_FOR_AGENT,
            TicketStatus.IN_PROGRESS,
            -timedelta(minutes=2),
        )
    )
    transitions.record(
        _transition(
            ticket, TicketStatus.PR_OPEN, TicketStatus.CI_PENDING, -timedelta(minutes=1)
        )
    )
    github = _github(
        test_conclusion=conclusion,
        test_source_event_at=source_event_at,
        include_test=include_test,
    )

    result = _run(db, client, github)
    handoff = result.ci_handoff_decisions[0].reconciliation

    assert handoff is not None, case
    assert handoff.classification is classification
    assert handoff.linear_mutations == mutations
    assert len(client.state_writes) == mutations
    assert result.ci_handoff_held == (0 if mutations else 1)
    assert EvidenceRepo(db).list_for_ticket(ticket.id) == []


def test_unattributed_product_evidence_cannot_satisfy_current_publication(
    db: Database,
) -> None:
    client = RecordingClient()
    ticket = seed_ticket(
        db,
        client,
        key="ATLAS-263",
        product_id=PRODUCT_ID,
        status=TicketStatus.CI_PENDING,
        issue_state=CI_PENDING_STATE,
        acceptance_criteria=["bounded transition"],
        linear_synced_at=NOW,
    )
    transitions = TicketStatusTransitionRepo(db)
    transitions.record(
        _transition(
            ticket,
            TicketStatus.READY_FOR_AGENT,
            TicketStatus.IN_PROGRESS,
            -timedelta(minutes=2),
        )
    )
    transitions.record(
        _transition(
            ticket, TicketStatus.PR_OPEN, TicketStatus.CI_PENDING, -timedelta(minutes=1)
        )
    )
    assert ticket.external_linear_id is not None
    client.seed_github_publication(
        ticket.external_linear_id,
        owner="derekrivers",
        repo="atlas",
        pr_number=PR_NUMBER,
    )
    # A prior product-wide pull has a passing test at the same head. The
    # current publication pull omits that check, so it must remain missing
    # rather than borrowing the unrelated historical product record.
    drive_evidence_pull(
        _github(),
        "derekrivers",
        "atlas",
        PR_NUMBER,
        evidence_repo=EvidenceRepo(db),
        product_id=PRODUCT_ID,
        now=NOW - timedelta(minutes=5),
    )
    github = _github(include_test=False)

    result = _run(db, client, github)

    decision = result.ci_handoff_decisions[0]
    assert decision.reconciliation is not None
    assert decision.reconciliation.classification is CIHandoffClassification.MISSING
    assert result.ci_handoff_held == 1
    assert client.state_writes == []


def test_head_movement_at_final_revalidation_holds_without_linear_write(
    db: Database,
) -> None:
    client = RecordingClient()
    _seed_ci_pending(db, client)
    github = _github()

    def move_head() -> None:
        github._pull_request = _pr_payload(OTHER_HEAD)

    result = _run(
        db,
        client,
        github,
        hooks=CIHandoffHooks(after_classification=move_head),
    )

    handoff = result.ci_handoff_decisions[0].reconciliation
    assert handoff is not None
    assert handoff.classification is CIHandoffClassification.STALE
    assert handoff.linear_mutations == 0
    assert client.state_writes == []


def test_pull_scoped_evidence_change_at_final_revalidation_holds(
    db: Database,
) -> None:
    client = RecordingClient()
    _seed_ci_pending(db, client)
    github = _github()

    def change_evidence() -> None:
        drive_evidence_pull(
            _github(
                test_conclusion="failure",
                test_source_event_at=NOW + timedelta(seconds=1),
            ),
            "derekrivers",
            "atlas",
            PR_NUMBER,
            evidence_repo=EvidenceRepo(db),
            product_id=PRODUCT_ID,
            now=NOW + timedelta(seconds=1),
        )

    result = _run(
        db,
        client,
        github,
        hooks=CIHandoffHooks(after_classification=change_evidence),
    )

    handoff = result.ci_handoff_decisions[0].reconciliation
    assert handoff is not None
    assert handoff.classification is CIHandoffClassification.STALE
    assert handoff.reason is CIHandoffReason.EVIDENCE_CHANGED
    assert handoff.linear_mutations == 0
    assert client.state_writes == []
