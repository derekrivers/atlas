"""ATLAS-263 production PM-cadence reachability for CI handoff authority."""

from __future__ import annotations

import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from github_fakes import FakeGitHubClient
from test_models_validation import NOW
from test_pm_sync import (
    CI_PENDING_STATE,
    PACK_DOC,
    PROJECT_ID,
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
from atlas.evidence.pull import drive_evidence_pull
from atlas.linear.client import LinearIssue, _github_publication_from_attachment
from atlas.pm import CIHandoffHooks, sync_tick
from atlas.pm.sync import CI_PENDING_POLL_COMPRESSION_CREATED_BY
from atlas.storage import (
    AgentRunRepo,
    CIHandoffReconciliationRepo,
    Database,
    EvidenceRepo,
    PmSyncReceiptRepo,
    TicketRepo,
    TicketStatusTransitionRepo,
)

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
) -> Ticket:
    ticket = seed_ticket(
        db,
        client,
        key=key,
        product_id=PRODUCT_ID,
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
        now=NOW,
        completion_clock=lambda: NOW + timedelta(seconds=1),
        github_client=github,
        ci_handoff_hooks=hooks,
    )


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


def test_candidate_discovery_is_ci_pending_only_stable_and_one_per_tick(
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
