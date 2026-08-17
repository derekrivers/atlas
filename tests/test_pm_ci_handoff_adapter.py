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

from atlas.core.enums import ActorType, EvidenceStatus
from atlas.core.models import (
    CIHandoffClassification,
    CIHandoffReason,
    Evidence,
    EvidenceType,
    Ticket,
    TicketStatus,
    TicketStatusTransition,
)
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


def _evidence(
    ticket: Ticket,
    evidence_type: EvidenceType,
    *,
    status: EvidenceStatus = EvidenceStatus.PASSED,
    conclusion: str | None = "success",
    source_event_at: Any = NOW - timedelta(seconds=20),
    source_uri: str | None = None,
    suffix: str,
) -> Evidence:
    job = {
        EvidenceType.TEST_RESULT: "test-python",
        EvidenceType.LINT_RESULT: "lint-python",
    }[evidence_type]
    return Evidence(
        id=uuid4(),
        product_id=ticket.product_id,
        ticket_id=ticket.id,
        evidence_type=evidence_type,
        status=status,
        summary=f"{job}: {status.value}",
        commit_sha=HEAD,
        external_run_id=f"{job}-{suffix}",
        job_name=job,
        source_event_at=source_event_at,
        payload_hash=(suffix * 64)[:64],
        source_uri=(
            source_uri
            if source_uri is not None
            else f"https://github.com/derekrivers/atlas/actions/runs/{suffix}"
        ),
        raw_payload={
            "conclusion": conclusion,
            "pull_requests": [{"number": PR_NUMBER, "head": {"sha": HEAD}}],
        },
        created_by_type=ActorType.SYSTEM,
        created_by_id="github-actions",
        created_at=NOW - timedelta(seconds=10),
    )


def _seed_ci_pending(
    db: Database,
    client: RecordingClient,
    *,
    key: str = "ATLAS-263",
    evidence: list[Evidence] | None = None,
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
    records = (
        [
            _evidence(ticket, EvidenceType.TEST_RESULT, suffix="1"),
            _evidence(ticket, EvidenceType.LINT_RESULT, suffix="2"),
        ]
        if evidence is None
        else evidence
    )
    for record in records:
        EvidenceRepo(db).add(record)
    return ticket


def _seed_compressed_ci_pending(
    db: Database,
    client: RecordingClient,
    *,
    source: TicketStatus,
    evidence: list[Evidence] | None = None,
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
    records = (
        [
            _evidence(ticket, EvidenceType.TEST_RESULT, suffix="1"),
            _evidence(ticket, EvidenceType.LINT_RESULT, suffix="2"),
        ]
        if evidence is None
        else evidence
    )
    for record in records:
        EvidenceRepo(db).add(record)
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
    github = FakeGitHubClient(pull_request=_pr_payload())

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
    assert all(
        call[1:4] == ("derekrivers", "atlas", PR_NUMBER) for call in github.calls
    )
    reconciliations = CIHandoffReconciliationRepo(db).list()
    assert len(reconciliations) == 1
    assert reconciliations[0].head_commit == HEAD
    receipt = PmSyncReceiptRepo(db).list()[-1]
    assert receipt.counters["ci_handoff_evaluated"] == 1
    assert receipt.counters["ci_handoff_held"] == 0
    assert receipt.counters["ci_handoff_mutations"] == 1


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
    github = FakeGitHubClient(pull_request=_pr_payload())

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


def test_compressed_observation_with_missing_identity_holds_before_provider_calls(
    db: Database,
) -> None:
    client = RecordingClient()
    ticket = _seed_compressed_ci_pending(
        db,
        client,
        source=TicketStatus.READY_FOR_AGENT,
        evidence=[],
    )
    github = FakeGitHubClient(pull_request=_pr_payload())

    result = _run(db, client, github)

    decision = result.ci_handoff_decisions[0]
    assert decision.reason.value == "trusted_identity_unavailable"
    assert decision.reconciliation is None
    assert result.ci_handoff_held == 1
    stored = TicketRepo(db).get_by_key(ticket.key)
    assert stored is not None and stored.status is TicketStatus.CI_PENDING
    assert client.state_writes == []
    assert github.calls == []
    assert CIHandoffReconciliationRepo(db).list_for_ticket(ticket.id) == []


def test_compressed_identity_ambiguity_holds_before_provider_calls(
    db: Database,
) -> None:
    client = RecordingClient()
    ticket = _seed_compressed_ci_pending(
        db,
        client,
        source=TicketStatus.IN_PROGRESS,
        evidence=[],
    )
    EvidenceRepo(db).add(_evidence(ticket, EvidenceType.TEST_RESULT, suffix="1"))
    EvidenceRepo(db).add(
        _evidence(
            ticket,
            EvidenceType.LINT_RESULT,
            source_uri="https://github.com/other/atlas/actions/runs/2",
            suffix="2",
        )
    )
    github = FakeGitHubClient(pull_request=_pr_payload())

    result = _run(db, client, github)

    decision = result.ci_handoff_decisions[0]
    assert decision.reason.value == "trusted_identity_ambiguous"
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
    github = FakeGitHubClient(pull_request=_pr_payload(OTHER_HEAD))

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
    github = FakeGitHubClient(pull_request=_pr_payload())

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
    github = FakeGitHubClient(pull_request=_pr_payload())

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
    github = FakeGitHubClient(pull_request=_pr_payload())

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
    github = FakeGitHubClient(pull_request=_pr_payload())

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
        "test_status",
        "conclusion",
        "source_event_at",
        "include_test",
        "live_head",
        "classification",
        "mutations",
    ),
    [
        (
            "passed",
            EvidenceStatus.PASSED,
            "success",
            NOW,
            True,
            HEAD,
            CIHandoffClassification.PASSED,
            1,
        ),
        (
            "implementation",
            EvidenceStatus.FAILED,
            "failure",
            NOW,
            True,
            HEAD,
            CIHandoffClassification.IMPLEMENTATION_FAILURE,
            1,
        ),
        (
            "pending",
            EvidenceStatus.PENDING,
            None,
            NOW,
            True,
            HEAD,
            CIHandoffClassification.PENDING,
            0,
        ),
        (
            "missing",
            EvidenceStatus.PASSED,
            "success",
            NOW,
            False,
            HEAD,
            CIHandoffClassification.MISSING,
            0,
        ),
        (
            "infrastructure",
            EvidenceStatus.FAILED,
            "timed_out",
            NOW,
            True,
            HEAD,
            CIHandoffClassification.INFRASTRUCTURE,
            0,
        ),
        (
            "malformed",
            EvidenceStatus.PASSED,
            "success",
            None,
            True,
            HEAD,
            CIHandoffClassification.MALFORMED,
            0,
        ),
        (
            "indeterminate",
            EvidenceStatus.FAILED,
            "provider_unknown",
            NOW,
            True,
            HEAD,
            CIHandoffClassification.INDETERMINATE,
            0,
        ),
        (
            "stale",
            EvidenceStatus.PASSED,
            "success",
            NOW,
            True,
            OTHER_HEAD,
            CIHandoffClassification.STALE,
            0,
        ),
    ],
)
def test_production_adapter_routes_or_holds_every_ci_evidence_class(
    db: Database,
    case: str,
    test_status: EvidenceStatus,
    conclusion: str | None,
    source_event_at: Any,
    include_test: bool,
    live_head: str,
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
    records = [_evidence(ticket, EvidenceType.LINT_RESULT, suffix="2")]
    if include_test:
        records.append(
            _evidence(
                ticket,
                EvidenceType.TEST_RESULT,
                status=test_status,
                conclusion=conclusion,
                source_event_at=source_event_at,
                suffix="1",
            )
        )
    # Add the exact dispatch/handoff episode and its selected evidence.
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
    for record in records:
        EvidenceRepo(db).add(record)
    github = FakeGitHubClient(pull_request=_pr_payload(live_head))

    result = _run(db, client, github)
    handoff = result.ci_handoff_decisions[0].reconciliation

    assert handoff is not None, case
    assert handoff.classification is classification
    assert handoff.linear_mutations == mutations
    assert len(client.state_writes) == mutations
    assert result.ci_handoff_held == (0 if mutations else 1)


def test_missing_or_conflicting_trusted_repository_identity_holds_without_github(
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
    first = _evidence(ticket, EvidenceType.TEST_RESULT, suffix="1")
    second = _evidence(
        ticket,
        EvidenceType.LINT_RESULT,
        source_uri="https://github.com/other/atlas/actions/runs/2",
        suffix="2",
    )
    EvidenceRepo(db).add(first)
    EvidenceRepo(db).add(second)
    github = FakeGitHubClient(pull_request=_pr_payload())

    result = _run(db, client, github)

    decision = result.ci_handoff_decisions[0]
    assert decision.reason.value == "trusted_identity_ambiguous"
    assert decision.reconciliation is None
    assert result.ci_handoff_held == 1
    assert client.state_writes == []
    assert github.calls == []


def test_head_movement_at_final_revalidation_holds_without_linear_write(
    db: Database,
) -> None:
    client = RecordingClient()
    _seed_ci_pending(db, client)
    github = FakeGitHubClient(pull_request=_pr_payload())

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
