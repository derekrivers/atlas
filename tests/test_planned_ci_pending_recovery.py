"""ATLAS-281 evidence-backed ``planned -> ci_pending`` mirror recovery."""

from __future__ import annotations

import tempfile
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from test_models_validation import NOW, product_kwargs
from test_pm_ci_handoff_adapter import (
    HEAD,
    OTHER_HEAD,
    PR_NUMBER,
    SequencedPRGitHub,
    _github,
)
from test_pm_sync import (
    CI_PENDING_STATE,
    PACK_DOC,
    PROJECT_ID,
    TEAM_ID,
    RecordingClient,
    debt_rows,
    seed_ticket,
    status_map,
)

from atlas.core.enums import ActorType
from atlas.core.models import (
    AdmissionRun,
    AnomalyType,
    CIHandoffClassification,
    PmSyncReceipt,
    PmSyncReceiptResult,
    Product,
    Ticket,
    TicketStatus,
    TicketStatusTransition,
)
from atlas.core.models.admission_run import (
    AdmissionCandidateDecision,
    AdmissionDecisionType,
    AdmissionHoldCode,
    AdmissionHoldReason,
    AdmissionRankInputs,
)
from atlas.core.models.planned_ci_pending_recovery import (
    PLANNED_CI_PENDING_RECOVERY_CREATED_BY,
)
from atlas.linear.client import LinearGitHubPublication
from atlas.pm import (
    LinearBoardPull,
    PlannedCIPendingRecoveryReason,
    evaluate_planned_ci_pending_recovery,
    sync_tick,
)
from atlas.pm.sync import CI_PENDING_POLL_COMPRESSION_SOURCES
from atlas.storage import (
    AdmissionRunRepo,
    AgentRunRepo,
    Database,
    DebtItemRepo,
    DeliveryAdmissionPolicyRepo,
    PlannedCIPendingRecoveryRepo,
    PmSyncReceiptRepo,
    ProductRepo,
    TicketRepo,
    TicketStatusTransitionRepo,
)
from atlas.storage.tables import (
    AdmissionWriteFenceRow,
    CIHandoffReconciliationRow,
    CIHandoffWriteFenceRow,
    PlannedCIPendingRecoveryRow,
)

PRODUCT_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
ADMITTED_AT = NOW - timedelta(minutes=10)
RECOVERED_AT = NOW


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{tmp_path}/atlas.db")
    database.create_all()
    return database


def _rank_inputs(ticket: Ticket) -> AdmissionRankInputs:
    eligible_since = ADMITTED_AT - timedelta(hours=1)
    return AdmissionRankInputs(
        unlock_count=0,
        critical_path_member=False,
        critical_path_position=None,
        priority=ticket.priority,
        risk_level=ticket.risk_level,
        risk_severity=3,
        continuously_eligible_since=eligible_since,
        continuously_eligible_age_microseconds=3_600_000_000,
    )


def _admission_run(
    db: Database,
    ticket: Ticket,
    *,
    run_id: UUID | None = None,
    product_id: UUID | None = None,
    selected_ticket_id: UUID | None = None,
    selected_ticket_key: str | None = None,
    external_linear_id: str | None = None,
    decision: AdmissionDecisionType = AdmissionDecisionType.ADMIT,
    created_by_type: ActorType = ActorType.SYSTEM,
    created_by_id: str = "atlas.pm.admission",
) -> AdmissionRun:
    policy = DeliveryAdmissionPolicyRepo(db).get_active(ticket.product_id)
    assert policy is not None
    decision_ticket_id = selected_ticket_id or ticket.id
    decision_ticket_key = selected_ticket_key or ticket.key
    reasons = (
        ()
        if decision is AdmissionDecisionType.ADMIT
        else (AdmissionHoldReason(code=AdmissionHoldCode.POLICY_PAUSED),)
    )
    return AdmissionRun(
        id=run_id or uuid4(),
        product_id=product_id or ticket.product_id,
        policy_id=policy.id,
        policy_revision=policy.revision,
        policy_fingerprint="a" * 64,
        snapshot_fingerprint="b" * 64,
        snapshot_observed_at=ADMITTED_AT,
        evaluated_at=ADMITTED_AT,
        selected_ticket_id=(
            decision_ticket_id if decision is AdmissionDecisionType.ADMIT else None
        ),
        selected_ticket_key=(
            decision_ticket_key if decision is AdmissionDecisionType.ADMIT else None
        ),
        decisions=(
            AdmissionCandidateDecision(
                ticket_id=decision_ticket_id,
                ticket_key=decision_ticket_key,
                external_linear_id=(
                    ticket.external_linear_id
                    if external_linear_id is None
                    else external_linear_id
                ),
                rank=1,
                rank_inputs=_rank_inputs(ticket),
                decision=decision,
                reasons=reasons,
            ),
        ),
        created_by_type=created_by_type,
        created_by_id=created_by_id,
    )


def _pm_receipt(
    ticket: Ticket,
    *,
    receipt_id: UUID | None = None,
    product_id: UUID | None = None,
    result: PmSyncReceiptResult = PmSyncReceiptResult.SUCCESS_STATUS_ONLY,
    counters: dict[str, int] | None = None,
    created_by_type: ActorType = ActorType.SYSTEM,
    created_by_id: str = "pm-engine",
) -> PmSyncReceipt:
    return PmSyncReceipt(
        id=receipt_id or uuid4(),
        product_id=product_id or ticket.product_id,
        product_key="ATLAS",
        linear_project_id=PROJECT_ID,
        started_at=ADMITTED_AT,
        finished_at=ADMITTED_AT + timedelta(seconds=1),
        status_map_fingerprint="c" * 64,
        fetched_board_fingerprint="d" * 64,
        fetched_board_issue_count=1,
        result=result,
        counters=(
            {"admitted": 1, "promoted": 1, "stale": 0, "indeterminate": 0}
            if counters is None
            else counters
        ),
        created_by_type=created_by_type,
        created_by_id=created_by_id,
    )


def _seed_ticket(
    db: Database,
    client: RecordingClient,
    *,
    with_transition_history: bool = True,
) -> Ticket:
    planned_at = ADMITTED_AT - timedelta(hours=1)
    ticket = seed_ticket(
        db,
        client,
        key="ATLAS-280",
        product_id=PRODUCT_ID,
        status=TicketStatus.PLANNED,
        issue_state=CI_PENDING_STATE,
        acceptance_criteria=["recover exact governed publication"],
        linear_synced_at=ADMITTED_AT - timedelta(minutes=1),
        status_entered_at=planned_at if with_transition_history else None,
    )
    if with_transition_history:
        TicketStatusTransitionRepo(db).record(
            TicketStatusTransition(
                id=uuid4(),
                ticket_id=ticket.id,
                from_status=TicketStatus.BACKLOG.value,
                to_status=TicketStatus.PLANNED.value,
                occurred_at=planned_at,
                created_by_type=ActorType.SYSTEM,
                created_by_id="pm-engine",
            )
        )
    return ticket


def _seed_proof(
    db: Database,
    client: RecordingClient,
    ticket: Ticket,
    *,
    run: AdmissionRun | None = None,
    receipt: PmSyncReceipt | None = None,
) -> tuple[AdmissionRun, PmSyncReceipt]:
    admitted = run or _admission_run(db, ticket)
    confirmed = receipt or _pm_receipt(ticket)
    AdmissionRunRepo(db).record(admitted)
    PmSyncReceiptRepo(db).record(confirmed)
    assert ticket.external_linear_id is not None
    client.seed_github_publication(
        ticket.external_linear_id,
        owner="derekrivers",
        repo="atlas",
        pr_number=PR_NUMBER,
    )
    return admitted, confirmed


def _sync(
    db: Database,
    client: RecordingClient,
    *,
    now: Any = RECOVERED_AT,
    github: Any = None,
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
    )


def _current_board(client: RecordingClient) -> LinearBoardPull:
    return LinearBoardPull.complete_project_pull(
        client.fetch_project_issues(PROJECT_ID)
    )


def test_atlas_280_shaped_recovery_reconsiders_deduped_anomaly_and_is_idempotent(
    db: Database,
) -> None:
    client = RecordingClient()
    ticket = _seed_ticket(db, client)

    first = _sync(db, client, now=RECOVERED_AT - timedelta(minutes=1))
    historical_debt = DebtItemRepo(db).list()
    assert first.anomalies_logged == 1
    assert len(historical_debt) == 1
    assert historical_debt[0].anomaly_type is AnomalyType.OUT_OF_OWNERSHIP_TRANSITION
    observed = TicketRepo(db).get_by_key(ticket.key)
    assert observed is not None
    assert observed.status is TicketStatus.PLANNED
    assert observed.last_observed_linear_state_id == CI_PENDING_STATE.id

    run, receipt = _seed_proof(db, client, ticket)
    second = _sync(db, client)
    [recorded_recovery] = PlannedCIPendingRecoveryRepo(db).list()
    storage_replay = PlannedCIPendingRecoveryRepo(db).apply(recorded_recovery)
    replay = _sync(db, client, now=RECOVERED_AT + timedelta(minutes=1))

    recovered = TicketRepo(db).get_by_key(ticket.key)
    assert recovered is not None
    assert recovered.status is TicketStatus.CI_PENDING
    assert recovered.status_entered_at == RECOVERED_AT
    assert recovered.last_observed_linear_state_id == CI_PENDING_STATE.id
    assert second.status_pulled == 1
    assert storage_replay.changed is False
    assert replay.status_pulled == 0
    assert DebtItemRepo(db).list() == historical_debt
    transitions = TicketStatusTransitionRepo(db).list_for_ticket(ticket.id)
    [transition] = [
        item
        for item in transitions
        if item.created_by_id == PLANNED_CI_PENDING_RECOVERY_CREATED_BY
    ]
    assert len(transitions) == 2
    assert (
        transition.from_status,
        transition.to_status,
        transition.created_by_id,
    ) == (
        TicketStatus.PLANNED.value,
        TicketStatus.CI_PENDING.value,
        PLANNED_CI_PENDING_RECOVERY_CREATED_BY,
    )
    [recovery] = PlannedCIPendingRecoveryRepo(db).list()
    assert recovery.admission_run_id == run.id
    assert recovery.pm_sync_receipt_id == receipt.id
    assert recovery.ticket_id == ticket.id
    assert recovery.linear_issue_id == ticket.external_linear_id
    assert recovery.observed_linear_state_id == CI_PENDING_STATE.id
    assert recovery.publication_attachment_id == "github-publication-1"
    assert recovery.publication_repository_owner == "derekrivers"
    assert recovery.publication_repository_name == "atlas"
    assert recovery.publication_pr_number == PR_NUMBER
    assert recovery.board_issue_count == 1
    assert AgentRunRepo(db).list_for_ticket(ticket.id) == []
    assert client.state_writes == []
    assert TicketStatus.PLANNED not in CI_PENDING_POLL_COMPRESSION_SOURCES


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_run",
        "duplicate_run",
        "held_run",
        "wrong_ticket_key",
        "wrong_ticket_uuid",
        "wrong_product",
        "wrong_external_identity",
        "duplicate_ticket_join",
        "agent_authored_run",
        "missing_receipt",
        "duplicate_receipt",
        "failed_receipt",
        "contradictory_receipt",
        "contradictory_success_result",
        "wrong_receipt_product",
        "agent_authored_receipt",
        "missing_publication",
        "incomplete_publication",
        "ambiguous_publication",
        "malformed_publication",
        "missing_history",
        "conflicting_history",
        "admission_fence",
        "ci_handoff_fence",
    ],
)
def test_missing_duplicate_contradictory_or_mismatched_proof_fails_closed(
    db: Database,
    mutation: str,
) -> None:
    client = RecordingClient()
    ticket = _seed_ticket(
        db,
        client,
        with_transition_history=mutation != "missing_history",
    )
    run = _admission_run(db, ticket)
    receipt = _pm_receipt(ticket)

    if mutation == "held_run":
        run = _admission_run(db, ticket, decision=AdmissionDecisionType.HOLD)
    elif mutation == "wrong_ticket_key":
        run = _admission_run(db, ticket, selected_ticket_key="ATLAS-999")
    elif mutation == "wrong_ticket_uuid":
        other = TicketRepo(db).add(
            ticket.model_copy(
                update={
                    "id": uuid4(),
                    "key": "ATLAS-999",
                    "external_linear_id": None,
                    "linear_synced_at": ticket.updated_at,
                }
            )
        )
        run = _admission_run(
            db,
            ticket,
            selected_ticket_id=other.id,
            selected_ticket_key=other.key,
            external_linear_id=ticket.external_linear_id,
        )
    elif mutation == "wrong_product":
        other_product_id = uuid4()
        ProductRepo(db).add(
            Product(**product_kwargs() | {"id": other_product_id, "key": "OTHER"})
        )
        run = _admission_run(db, ticket, product_id=other_product_id)
    elif mutation == "wrong_external_identity":
        run = _admission_run(db, ticket, external_linear_id="wrong-linear-uuid")
    elif mutation == "agent_authored_run":
        run = _admission_run(
            db,
            ticket,
            created_by_type=ActorType.AGENT,
            created_by_id="sympathy",
        )
    if mutation == "duplicate_ticket_join":
        TicketRepo(db).add(
            ticket.model_copy(
                update={
                    "id": uuid4(),
                    "key": "ATLAS-998",
                    "linear_synced_at": ticket.updated_at,
                }
            )
        )

    if mutation != "missing_run":
        AdmissionRunRepo(db).record(run)
    if mutation == "duplicate_run":
        AdmissionRunRepo(db).record(_admission_run(db, ticket))

    if mutation == "failed_receipt":
        receipt = _pm_receipt(ticket, result=PmSyncReceiptResult.FAILED)
    elif mutation == "contradictory_receipt":
        receipt = _pm_receipt(
            ticket,
            counters={"admitted": 1, "promoted": 1, "stale": 1, "indeterminate": 0},
        )
    elif mutation == "contradictory_success_result":
        receipt = _pm_receipt(ticket, result=PmSyncReceiptResult.SUCCESS_ZERO_ACTION)
    elif mutation == "wrong_receipt_product":
        other_product_id = uuid4()
        ProductRepo(db).add(
            Product(
                **product_kwargs() | {"id": other_product_id, "key": "RECEIPT-OTHER"}
            )
        )
        receipt = _pm_receipt(ticket, product_id=other_product_id)
    elif mutation == "agent_authored_receipt":
        receipt = _pm_receipt(
            ticket,
            created_by_type=ActorType.AGENT,
            created_by_id="sympathy",
        )

    if mutation != "missing_receipt":
        PmSyncReceiptRepo(db).record(receipt)
    if mutation == "duplicate_receipt":
        PmSyncReceiptRepo(db).record(_pm_receipt(ticket))

    assert ticket.external_linear_id is not None
    if mutation != "missing_publication":
        client.seed_github_publication(
            ticket.external_linear_id,
            owner="derekrivers",
            repo="atlas",
            pr_number=PR_NUMBER,
            complete=mutation != "incomplete_publication",
        )
    if mutation == "ambiguous_publication":
        client.seed_github_publication(
            ticket.external_linear_id,
            owner="other",
            repo="atlas",
            pr_number=PR_NUMBER + 1,
            attachment_id="github-publication-2",
            append=True,
        )
    if mutation == "malformed_publication":
        issue = client.fetch_issue(ticket.external_linear_id)
        assert issue is not None
        client._issues[ticket.external_linear_id] = replace(
            issue,
            github_publications=(
                LinearGitHubPublication(
                    attachment_id="github-publication-1",
                    repository_owner="bad/owner",
                    repository_name="atlas",
                    pr_number=PR_NUMBER,
                ),
            ),
        )
    if mutation == "conflicting_history":
        TicketStatusTransitionRepo(db).record(
            TicketStatusTransition(
                id=uuid4(),
                ticket_id=ticket.id,
                from_status=TicketStatus.READY_FOR_AGENT.value,
                to_status=TicketStatus.PLANNED.value,
                occurred_at=ADMITTED_AT - timedelta(minutes=1),
                created_by_type=ActorType.SYSTEM,
                created_by_id="pm-engine",
            )
        )
    if mutation == "admission_fence":
        with db.session() as session, session.begin():
            session.add(
                AdmissionWriteFenceRow(
                    product_id=ticket.product_id,
                    admission_run_id=run.id,
                    ticket_id=ticket.id,
                    ticket_key=ticket.key,
                    issue_id=ticket.external_linear_id,
                    source_state_id="planned-state",
                    target_state_id="ready-state",
                    policy_revision=run.policy_revision,
                    state="pending",
                    created_at=RECOVERED_AT,
                    updated_at=RECOVERED_AT,
                )
            )
    if mutation == "ci_handoff_fence":
        reconciliation_id = uuid4()
        with db.session() as session, session.begin():
            session.add(
                CIHandoffReconciliationRow(
                    id=reconciliation_id,
                    schema_version="ci-handoff-reconciliation-v1",
                    product_id=ticket.product_id,
                    ticket_id=ticket.id,
                    ticket_key=ticket.key,
                    linear_issue_id=ticket.external_linear_id,
                    repository_owner="derekrivers",
                    repository_name="atlas",
                    pr_number=PR_NUMBER,
                    head_commit=HEAD,
                    policy_id=None,
                    policy_revision=None,
                    policy_fingerprint=None,
                    snapshot_fingerprint=None,
                    classification="pending",
                    reason="required_checks_pending",
                    decision="hold",
                    check_results=[],
                    observed_at=RECOVERED_AT,
                    created_by_type="system",
                    created_by_id="ci-handoff-reconciler",
                )
            )
            session.add(
                CIHandoffWriteFenceRow(
                    product_id=ticket.product_id,
                    reconciliation_id=reconciliation_id,
                    ticket_id=ticket.id,
                    ticket_key=ticket.key,
                    issue_id=ticket.external_linear_id,
                    source_state_id=CI_PENDING_STATE.id,
                    target_state_id="state-review-required",
                    target_status=TicketStatus.REVIEW_REQUIRED.value,
                    state="pending",
                    created_at=RECOVERED_AT,
                    updated_at=RECOVERED_AT,
                )
            )

    result = _sync(db, client)

    stored = TicketRepo(db).get_by_key(ticket.key)
    assert stored is not None and stored.status is TicketStatus.PLANNED, mutation
    assert result.status_pulled == 0, mutation
    assert PlannedCIPendingRecoveryRepo(db).list() == [], mutation
    assert AgentRunRepo(db).list_for_ticket(ticket.id) == [], mutation
    assert client.state_writes == [], mutation
    assert (
        len(debt_rows(db, AnomalyType.OUT_OF_OWNERSHIP_TRANSITION, ticket.id)) == 1
    ), mutation


@pytest.mark.parametrize("case", ["incomplete", "gap", "duplicate_id"])
def test_incomplete_or_duplicate_board_observation_is_never_eligible(
    db: Database,
    case: str,
) -> None:
    client = RecordingClient()
    ticket = _seed_ticket(db, client)
    _seed_proof(db, client, ticket)
    issues = client.fetch_project_issues(PROJECT_ID)
    board = LinearBoardPull(
        issues=tuple(issues if case != "duplicate_id" else [issues[0], issues[0]]),
        complete=case != "incomplete",
        pagination_gaps=("cursor-gap",) if case == "gap" else (),
    )

    result = evaluate_planned_ci_pending_recovery(
        db=db,
        ticket=ticket,
        status_map=status_map(),
        board=board,
        project_id=PROJECT_ID,
        now=RECOVERED_AT,
    )

    assert result.reason is PlannedCIPendingRecoveryReason.BOARD_INCOMPLETE
    assert result.recovery is None
    assert TicketRepo(db).get_by_key(ticket.key) == ticket


def test_recovery_state_transition_and_evidence_insert_are_atomic(
    db: Database,
) -> None:
    client = RecordingClient()
    ticket = _seed_ticket(db, client)
    _seed_proof(db, client, ticket)
    evaluation = evaluate_planned_ci_pending_recovery(
        db=db,
        ticket=ticket,
        status_map=status_map(),
        board=_current_board(client),
        project_id=PROJECT_ID,
        now=RECOVERED_AT,
    )
    assert evaluation.recovery is not None
    with db.engine.begin() as connection:
        connection.execute(
            sa.text(
                "CREATE TRIGGER reject_recovery_insert "
                "BEFORE INSERT ON planned_ci_pending_recoveries "
                "BEGIN SELECT RAISE(ABORT, 'seeded insert failure'); END"
            )
        )

    with pytest.raises(sa.exc.IntegrityError, match="seeded insert failure"):
        PlannedCIPendingRecoveryRepo(db).apply(evaluation.recovery)

    stored = TicketRepo(db).get_by_key(ticket.key)
    assert stored is not None and stored.status is TicketStatus.PLANNED
    [historical_transition] = TicketStatusTransitionRepo(db).list_for_ticket(ticket.id)
    assert historical_transition.from_status == TicketStatus.BACKLOG.value
    assert historical_transition.to_status == TicketStatus.PLANNED.value
    assert PlannedCIPendingRecoveryRepo(db).list() == []


def test_recovery_evidence_is_bounded_and_database_immutable(db: Database) -> None:
    client = RecordingClient()
    ticket = _seed_ticket(db, client)
    _seed_proof(db, client, ticket)
    _sync(db, client)
    [recovery] = PlannedCIPendingRecoveryRepo(db).list()

    assert set(PlannedCIPendingRecoveryRow.__table__.columns.keys()) == {
        "id",
        "schema_version",
        "product_id",
        "ticket_id",
        "ticket_key",
        "linear_issue_id",
        "linear_project_id",
        "observed_linear_state_id",
        "source_local_status",
        "recovered_local_status",
        "admission_run_id",
        "pm_sync_receipt_id",
        "publication_attachment_id",
        "publication_repository_owner",
        "publication_repository_name",
        "publication_pr_number",
        "board_fingerprint",
        "board_issue_count",
        "observed_at",
        "created_by_type",
        "created_by_id",
    }
    dumped = recovery.model_dump(mode="json")
    assert not {
        "provider_payload",
        "issue_body",
        "pr_body",
        "credential",
        "token",
        "secret",
    } & set(dumped)
    with (
        pytest.raises(sa.exc.IntegrityError, match="append-only"),
        db.engine.begin() as connection,
    ):
        connection.execute(
            sa.update(PlannedCIPendingRecoveryRow)
            .where(PlannedCIPendingRecoveryRow.id == recovery.id)
            .values(ticket_key="ATLAS-999")
        )
    with (
        pytest.raises(sa.exc.IntegrityError, match="append-only"),
        db.engine.begin() as connection,
    ):
        connection.execute(
            sa.delete(PlannedCIPendingRecoveryRow).where(
                PlannedCIPendingRecoveryRow.id == recovery.id
            )
        )


@pytest.mark.parametrize(
    ("case", "conclusion", "source_event_at", "include_test", "classification"),
    [
        ("passed", "success", NOW, True, CIHandoffClassification.PASSED),
        ("pending", None, NOW, True, CIHandoffClassification.PENDING),
        ("missing", "success", NOW, False, CIHandoffClassification.MISSING),
        (
            "infrastructure",
            "timed_out",
            NOW,
            True,
            CIHandoffClassification.INFRASTRUCTURE,
        ),
        ("malformed", "success", None, True, CIHandoffClassification.MALFORMED),
        (
            "indeterminate",
            "skipped",
            NOW,
            True,
            CIHandoffClassification.INDETERMINATE,
        ),
    ],
)
def test_only_existing_ci_handoff_authority_can_exit_after_recovery(
    db: Database,
    case: str,
    conclusion: str | None,
    source_event_at: Any,
    include_test: bool,
    classification: CIHandoffClassification,
) -> None:
    client = RecordingClient()
    ticket = _seed_ticket(db, client)
    _seed_proof(db, client, ticket)
    _sync(db, client)
    client.state_writes.clear()
    github = _github(
        test_conclusion=conclusion,
        test_source_event_at=source_event_at,
        include_test=include_test,
    )

    result = _sync(db, client, now=NOW + timedelta(minutes=1), github=github)

    handoff = result.ci_handoff_decisions[0].reconciliation
    assert handoff is not None, case
    assert handoff.classification is classification, case
    stored = TicketRepo(db).get_by_key(ticket.key)
    assert stored is not None
    expected_mutations = 1 if classification is CIHandoffClassification.PASSED else 0
    assert len(client.state_writes) == expected_mutations, case
    assert handoff.linear_mutations == expected_mutations, case
    assert stored.status is (
        TicketStatus.REVIEW_REQUIRED if expected_mutations else TicketStatus.CI_PENDING
    )
    transitions = TicketStatusTransitionRepo(db).list_for_ticket(ticket.id)
    [recovery_transition] = [
        transition
        for transition in transitions
        if transition.created_by_id == PLANNED_CI_PENDING_RECOVERY_CREATED_BY
    ]
    assert recovery_transition.from_status == TicketStatus.PLANNED.value
    assert recovery_transition.to_status == TicketStatus.CI_PENDING.value
    if expected_mutations:
        assert transitions[-1].created_by_id == "ci-handoff-reconciler"
        assert transitions[-1].from_status == TicketStatus.CI_PENDING.value
        assert transitions[-1].to_status == TicketStatus.REVIEW_REQUIRED.value


def test_stale_exact_head_after_recovery_remains_ci_pending(db: Database) -> None:
    client = RecordingClient()
    ticket = _seed_ticket(db, client)
    _seed_proof(db, client, ticket)
    _sync(db, client)
    client.state_writes.clear()

    result = _sync(
        db,
        client,
        now=NOW + timedelta(minutes=1),
        github=SequencedPRGitHub([HEAD, OTHER_HEAD]),
    )

    handoff = result.ci_handoff_decisions[0].reconciliation
    assert handoff is not None
    assert handoff.classification is CIHandoffClassification.STALE
    stored = TicketRepo(db).get_by_key(ticket.key)
    assert stored is not None and stored.status is TicketStatus.CI_PENDING
    assert client.state_writes == []


def test_missing_publication_after_recovery_holds_before_github(db: Database) -> None:
    client = RecordingClient()
    ticket = _seed_ticket(db, client)
    _seed_proof(db, client, ticket)
    _sync(db, client)
    assert ticket.external_linear_id is not None
    issue = client.fetch_issue(ticket.external_linear_id)
    assert issue is not None
    client._issues[ticket.external_linear_id] = replace(
        issue,
        github_publications=(),
        github_publications_complete=True,
    )
    github = _github()

    result = _sync(db, client, now=NOW + timedelta(minutes=1), github=github)

    decision = result.ci_handoff_decisions[0]
    assert decision.reason.value == "trusted_publication_unavailable"
    assert decision.reconciliation is None
    assert github.calls == []
    stored = TicketRepo(db).get_by_key(ticket.key)
    assert stored is not None and stored.status is TicketStatus.CI_PENDING
    assert client.state_writes == []
