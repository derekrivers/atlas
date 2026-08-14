"""ATLAS-249 fail-closed single-write PM admission integration.

Each acceptance criterion has a named race/failure proof.  The external
mutation spy is ``RecordingClient.state_writes``; every case asserts an exact
zero/one boundary and never uses a live Linear credential.
"""

from __future__ import annotations

import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from test_models_validation import NOW
from test_pm_sync import (
    CHANGES_REQUESTED_STATE,
    CI_PENDING_STATE,
    PACK_DOC,
    PR_OPEN_STATE,
    PROJECT_ID,
    READY,
    STARTED,
    TEAM_ID,
    RecordingClient,
    seed_ticket,
    status_map,
)

from atlas.cli import _format_sync_result
from atlas.core.models import PmSyncReceiptResult
from atlas.core.models.ticket import TicketStatus
from atlas.linear.client import (
    LinearAPIError,
    LinearIssue,
    LinearProjectIssues,
)
from atlas.pm import (
    AdmissionSyncHooks,
    AdmissionSyncOutcome,
    AdmissionSyncReason,
    AdmissionSyncResult,
    SyncResult,
    sync_tick,
)
from atlas.pm.scheduler import TickConfig, run_scheduler, run_tick
from atlas.storage import (
    AdmissionCoordinationRepo,
    AdmissionRunRepo,
    Database,
    PmSyncReceiptRepo,
    TicketRepo,
    TickFailureRepo,
)
from atlas.storage.tables import (
    AdmissionLeaseRow,
    DeliveryAdmissionPolicyActiveRow,
    DeliveryAdmissionPolicyRevisionRow,
)

PRODUCT_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{tmp_path}/atlas.db")
    database.create_all()
    return database


class CountingPullClient(RecordingClient):
    def __init__(self) -> None:
        super().__init__()
        self.project_pulls = 0

    def fetch_project_issues(self, project_id: str) -> list[LinearIssue]:
        self.project_pulls += 1
        return super().fetch_project_issues(project_id)


class CandidateMovesOnRevalidationClient(CountingPullClient):
    def fetch_project_issues(self, project_id: str) -> list[LinearIssue]:
        if self.project_pulls == 1:
            candidate_id = next(iter(self._issues))
            self.simulate_linear_state(candidate_id, STARTED)
        return super().fetch_project_issues(project_id)


class PartialRevalidationClient(CountingPullClient):
    def fetch_project_issues(self, project_id: str) -> list[LinearIssue]:
        issues = super().fetch_project_issues(project_id)
        if self.project_pulls == 2:
            return LinearProjectIssues(
                issues, complete=False, pagination_gaps=("cursor-gap",)
            )
        return issues


class FailingRevalidationClient(CountingPullClient):
    def fetch_project_issues(self, project_id: str) -> list[LinearIssue]:
        if self.project_pulls == 1:
            self.project_pulls += 1
            raise LinearAPIError("Authorization: secret; raw issue body")
        return super().fetch_project_issues(project_id)


class PartialInitialPullClient(RecordingClient):
    def fetch_project_issues(self, project_id: str) -> list[LinearIssue]:
        return LinearProjectIssues(
            super().fetch_project_issues(project_id),
            complete=False,
            pagination_gaps=("cursor-gap",),
        )


class AmbiguousSuccessClient(CountingPullClient):
    def set_state(self, issue_id: str, state_id: str) -> LinearIssue:
        super().set_state(issue_id, state_id)
        raise LinearAPIError("timeout after send; token=secret; body=raw")


class AmbiguousNoWriteClient(CountingPullClient):
    def set_state(self, issue_id: str, state_id: str) -> LinearIssue:
        self.state_writes.append((issue_id, state_id))
        raise LinearAPIError("timeout before confirmation")


def seed_candidate(
    db: Database,
    client: RecordingClient,
    *,
    key: str = "ATLAS-249",
    priority: int = 10,
) -> Any:
    return seed_ticket(
        db,
        client,
        key=key,
        product_id=PRODUCT_ID,
        status=TicketStatus.PLANNED,
        acceptance_criteria=["fail closed"],
        priority=priority,
        linear_synced_at=NOW,
    )


def run(
    db: Database,
    client: RecordingClient,
    *,
    hooks: AdmissionSyncHooks | None = None,
) -> SyncResult:
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
        admission_hooks=hooks,
    )


def _config(db: Database, client: RecordingClient) -> TickConfig:
    return TickConfig(
        tickets=TicketRepo(db),
        db=db,
        client=client,
        status_map=status_map(),
        team_id=TEAM_ID,
        project_id=PROJECT_ID,
        inbox_dir=Path(tempfile.mkdtemp()),
        documents=lambda: [PACK_DOC],
    )


def _replace_policy(
    db: Database, *, working_budget: int = 3, integration_budget: int = 3
) -> None:
    active = DeliveryAdmissionPolicyActiveRow
    with db.session() as session, session.begin():
        session.add(
            DeliveryAdmissionPolicyRevisionRow(
                id=uuid4(),
                product_id=PRODUCT_ID,
                revision=2,
                mode="running",
                approved_symphony_ceiling=3,
                working_budget=working_budget,
                integration_budget=integration_budget,
                review_budget=3,
                changes_requested_reserve=0,
                risk_lane_limits=[],
                component_lane_limits=[],
                created_by_type="human",
                created_by_id="operator",
                created_at=NOW,
            )
        )
        session.execute(
            sa.update(active).where(active.product_id == PRODUCT_ID).values(revision=2)
        )


def test_ac1_periodic_and_once_share_database_lease_and_record_typed_hold(
    db: Database,
) -> None:
    client = CountingPullClient()
    seed_candidate(db, client)
    coordination = AdmissionCoordinationRepo(db)
    assert coordination.try_acquire(
        product_id=PRODUCT_ID,
        owner_id=uuid4(),
        acquired_at=NOW,
        ttl=timedelta(minutes=5),
    )

    periodic_results: list[SyncResult] = []
    assert (
        run_tick(
            _config(db, client),
            TickFailureRepo(db),
            now=NOW,
            completion_clock=lambda: NOW,
            result_sink=periodic_results.append,
        )
        is None
    )
    once_result = run_scheduler(
        _config(db, client), once=True, now=lambda: NOW, sleep=lambda _wait: False
    )

    assert periodic_results[0].held == 1
    assert once_result is not None and once_result.held == 1
    assert all(
        detail.reason.value == "lease_unavailable"
        for result in [periodic_results[0], once_result]
        for detail in result.admission_decisions
    )
    assert client.state_writes == []
    assert AdmissionRunRepo(db).list_for_product(PRODUCT_ID) == []


def test_ac2_success_repulls_complete_board_and_writes_exact_selected_state(
    db: Database,
) -> None:
    client = CountingPullClient()
    candidate = seed_candidate(db, client)

    result = run(db, client)

    assert client.project_pulls == 2
    assert client.state_writes == [(candidate.external_linear_id, READY.id)]
    assert client.creates == []
    assert client.updates == []
    assert result.admitted == result.promoted == 1
    assert result.admission_decisions[0].policy_revision == 1
    assert len(AdmissionRunRepo(db).list_for_product(PRODUCT_ID)) == 1
    stored = TicketRepo(db).get_by_key(candidate.key)
    assert stored is not None and stored.status is TicketStatus.PLANNED
    assert (
        PmSyncReceiptRepo(db).list()[-1].result
        is PmSyncReceiptResult.SUCCESS_STATUS_ONLY
    )


def test_ac3_revalidation_candidate_movement_writes_nothing_and_atlas_stays_writer(
    db: Database,
) -> None:
    client = CandidateMovesOnRevalidationClient()
    selected = seed_candidate(db, client, priority=20)
    fallback = seed_candidate(db, client, key="ATLAS-250", priority=10)

    result = run(db, client)

    assert result.stale == 1
    assert result.admission_decisions[0].reason.value == "candidate_moved"
    assert client.state_writes == []
    stored = TicketRepo(db).get_by_key("ATLAS-249")
    assert stored is not None and stored.status is TicketStatus.PLANNED
    [admission_run] = AdmissionRunRepo(db).list_for_product(PRODUCT_ID)
    assert admission_run.selected_ticket_key == selected.key
    assert admission_run.selected_ticket_key != fallback.key


@pytest.mark.parametrize("race", ["policy", "lease"])
def test_ac5_races_after_revalidation_fail_closed_before_set_state(
    db: Database, race: str
) -> None:
    client = CountingPullClient()
    seed_candidate(db, client)

    def race_after_revalidation() -> None:
        if race == "policy":
            _replace_policy(db)
        else:
            with db.session() as session, session.begin():
                session.execute(
                    sa.delete(AdmissionLeaseRow).where(
                        AdmissionLeaseRow.product_id == PRODUCT_ID
                    )
                )

    result = run(
        db,
        client,
        hooks=AdmissionSyncHooks(after_revalidation=race_after_revalidation),
    )

    assert result.stale == 1
    expected_reason = "policy_changed" if race == "policy" else "lease_lost"
    assert result.admission_decisions[0].reason.value == expected_reason
    assert client.state_writes == []


@pytest.mark.parametrize(
    "client_type",
    [PartialRevalidationClient, FailingRevalidationClient],
    ids=["partial-pagination", "transport-failure"],
)
def test_ac5_revalidation_pull_failures_admit_nobody(
    db: Database, client_type: type[RecordingClient]
) -> None:
    client = client_type()
    seed_candidate(db, client)

    result = run(db, client)

    assert result.stale == 1
    assert client.state_writes == []
    assert len(AdmissionRunRepo(db).list_for_product(PRODUCT_ID)) == 1


def test_ac5_partial_initial_pagination_is_malformed_and_admits_nobody(
    db: Database,
) -> None:
    client = PartialInitialPullClient()
    seed_candidate(db, client)

    with pytest.raises(RuntimeError, match="complete pagination"):
        run(db, client)

    assert client.state_writes == []
    assert PmSyncReceiptRepo(db).list()[-1].result is PmSyncReceiptResult.MALFORMED_PULL


def test_snapshot_level_stale_stops_before_later_review_cycle_state_write(
    db: Database,
) -> None:
    client = CountingPullClient()
    review_ticket = seed_ticket(
        db,
        client,
        key="ATLAS-248",
        product_id=PRODUCT_ID,
        status=TicketStatus.PR_OPEN,
        issue_state=PR_OPEN_STATE,
        review_cycle_count=4,
        linear_synced_at=NOW,
    )
    client.create_issue(
        {"title": "Unjoined issue", "description": "outside Atlas"},
        team_id=TEAM_ID,
        project_id=PROJECT_ID,
    )

    result = run(db, client)

    assert result.stale == 1
    assert result.admission_decisions[0].reason is (
        AdmissionSyncReason.SNAPSHOT_INCOMPLETE
    )
    assert result.admission_decisions[0].ticket_key is None
    assert review_ticket.review_cycle_count > 3
    assert client.state_writes == []


def test_ac4_ambiguous_success_fences_later_admission_until_fresh_pull(
    db: Database,
) -> None:
    client = AmbiguousSuccessClient()
    candidate = seed_candidate(db, client)

    first = run(db, client)

    assert first.indeterminate == 1
    assert client.state_writes == [(candidate.external_linear_id, READY.id)]
    assert AdmissionCoordinationRepo(db).get_fence(PRODUCT_ID) is not None
    assert PmSyncReceiptRepo(db).list()[-1].result is PmSyncReceiptResult.PARTIAL

    second = run(db, client)

    assert second.admitted == 1
    assert second.admission_decisions[0].reason.value == (
        "indeterminate_reconciled_admitted"
    )
    assert client.state_writes == [(candidate.external_linear_id, READY.id)]
    assert AdmissionCoordinationRepo(db).get_fence(PRODUCT_ID) is None
    stored = TicketRepo(db).get_by_key(candidate.key)
    assert stored is not None and stored.status is TicketStatus.READY_FOR_AGENT


def test_ac5_ambiguous_no_write_reconciliation_cannot_choose_second_candidate(
    db: Database,
) -> None:
    client = AmbiguousNoWriteClient()
    first_candidate = seed_candidate(db, client, key="ATLAS-249", priority=20)
    second_candidate = seed_candidate(db, client, key="ATLAS-250", priority=10)

    first = run(db, client)
    second = run(db, client)

    assert first.indeterminate == 1
    assert second.held == 1
    assert second.admission_decisions[0].reason.value == (
        "indeterminate_reconciled_no_write"
    )
    assert client.state_writes == [(first_candidate.external_linear_id, READY.id)]
    assert second_candidate.external_linear_id != first_candidate.external_linear_id


def test_ac6_changes_requested_consumes_capacity_and_is_never_demoted(
    db: Database,
) -> None:
    client = CountingPullClient()
    rework = seed_ticket(
        db,
        client,
        key="ATLAS-248",
        product_id=PRODUCT_ID,
        status=TicketStatus.CHANGES_REQUESTED,
        issue_state=CHANGES_REQUESTED_STATE,
        linear_synced_at=NOW,
    )
    seed_candidate(db, client)
    _replace_policy(db, working_budget=1)

    result = run(db, client)

    assert result.over_capacity == 1
    assert client.state_writes == []
    issue = client.fetch_issue(rework.external_linear_id or "")
    assert issue is not None and issue.state_id == CHANGES_REQUESTED_STATE.id


def test_atlas_255_full_integration_budget_reports_over_capacity_without_demotion(
    db: Database,
) -> None:
    client = CountingPullClient()
    integrating = seed_ticket(
        db,
        client,
        key="ATLAS-255",
        product_id=PRODUCT_ID,
        status=TicketStatus.CI_PENDING,
        issue_state=CI_PENDING_STATE,
        linear_synced_at=NOW,
    )
    seed_candidate(db, client)
    _replace_policy(db, integration_budget=1)

    result = run(db, client)

    assert result.over_capacity == 1
    assert client.state_writes == []
    issue = client.fetch_issue(integrating.external_linear_id or "")
    assert issue is not None and issue.state_id == CI_PENDING_STATE.id


def test_ac7_output_names_every_outcome_and_policy_revision_without_raw_data() -> None:
    result = SyncResult(admitted=1, held=2, over_capacity=3, stale=4, indeterminate=5)
    result.admission_decisions.append(
        # A safe detail is enough to prove the formatter; no client participates.
        AdmissionSyncResult(
            outcome=AdmissionSyncOutcome.INDETERMINATE,
            reason=AdmissionSyncReason.WRITE_INDETERMINATE,
            policy_revision=7,
            ticket_key="ATLAS-249",
        )
    )

    rendered = _format_sync_result(result)

    for expected in (
        "admitted=1",
        "held=2",
        "over_capacity=3",
        "stale=4",
        "indeterminate=5",
        "policy_revision=7",
    ):
        assert expected in rendered
    for forbidden in ("Authorization", "secret", "description", "raw issue body"):
        assert forbidden not in rendered
