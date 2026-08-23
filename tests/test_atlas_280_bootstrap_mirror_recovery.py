"""Focused proof for the one-time ATLAS-280/ATLAS-281 bootstrap exception."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from github_fakes import FakeGitHubClient
from pydantic import ValidationError
from test_models_validation import dependency_kwargs, product_kwargs, ticket_kwargs

from atlas.core.enums import ActorType, RiskLevel
from atlas.core.models import (
    AdmissionRun,
    Atlas280BootstrapRecoveryReceipt,
    DebtItem,
    DeliveryAdmissionPolicyRevision,
    PmSyncReceipt,
    PmSyncReceiptResult,
    Product,
    Ticket,
    TicketDependency,
)
from atlas.core.models.admission_run import (
    AdmissionCandidateDecision,
    AdmissionDecisionType,
    AdmissionRankInputs,
)
from atlas.core.models.atlas_280_bootstrap_recovery import (
    ATLAS_280_ADMISSION_RUN_ID,
    ATLAS_280_DEBT_ITEM_ID,
    ATLAS_280_LINEAR_ID,
    ATLAS_280_PM_RECEIPT_ID,
    ATLAS_280_POLICY_FINGERPRINT,
    ATLAS_280_PUBLICATION_HEAD,
    ATLAS_280_TICKET_ID,
    ATLAS_281_LINEAR_ID,
    ATLAS_281_TICKET_ID,
)
from atlas.core.models.debt_item import AnomalyType
from atlas.core.models.delivery_admission_policy import (
    ComponentLaneLimit,
    DeliveryAdmissionMode,
)
from atlas.core.models.ticket import TicketStatus
from atlas.linear.client import (
    LinearGitHubPublication,
    LinearIssue,
    LinearProjectIssues,
    WorkflowState,
)
from atlas.linear.ownership import LinearStatusMap
from atlas.pm.atlas_280_bootstrap_recovery import (
    Atlas280BootstrapCheckCode,
    Atlas280BootstrapRecoveryService,
)
from atlas.pm.delivery_snapshot import delivery_policy_fingerprint
from atlas.pm.sync import sync_tick
from atlas.storage import (
    AdmissionRunRepo,
    AgentRunRepo,
    Atlas280BootstrapRecoveryRepo,
    CIHandoffReconciliationRepo,
    Database,
    DebtItemRepo,
    DeliveryAdmissionPolicyRepo,
    PmSyncReceiptRepo,
    ProductRepo,
    TicketDependencyRepo,
    TicketRepo,
    TicketStatusTransitionRepo,
)
from atlas.storage.atlas_280_bootstrap_recovery import EXPECTED_DEPENDENCIES
from atlas.storage.tables import (
    AdmissionRunRow,
    AdmissionWriteFenceRow,
    Atlas280BootstrapRecoveryReceiptRow,
    CIHandoffWriteFenceRow,
    DebtItemRow,
    DeliveryAdmissionPolicyActiveRow,
    DeliveryAdmissionPolicyRevisionRow,
    PmSyncReceiptRow,
    TicketRow,
    TicketStatusTransitionRow,
)
from scripts.bootstrap_atlas_280_ci_pending_mirror_recovery import _parser

NOW = datetime(2026, 8, 22, 15, 0, tzinfo=UTC)
PRODUCT_ID = UUID("6363ff3f-22db-40b3-9a47-aeba5d0a7586")
POLICY_16_ID = UUID("8adf559f-525e-44e3-a14d-f5bc308b1082")
POLICY_17_ID = UUID("b3d4b499-bc05-4e42-9309-4c2a7429ccae")
PROJECT_ID = "linear-project-atlas"
TEAM_ID = "linear-team-atlas"
PLANNED_STATE_ID = "state-planned"
CI_PENDING_STATE_ID = "state-ci-pending"
DONE_STATE_ID = "state-done"
REVIEW_STATE_ID = "state-review-required"
READY_STATE_ID = "state-ready"
IN_PROGRESS_STATE_ID = "state-in-progress"
PR_OPEN_STATE_ID = "state-pr-open"
CHANGES_STATE_ID = "state-changes-requested"
NEEDS_HUMAN_STATE_ID = "state-needs-human"
REJECTED_STATE_ID = "state-rejected"
ACCEPTED_MAIN = "7" * 40

COMPONENTS = (
    "runtime-event-contract",
    "runtime-trace-contract",
    "execution-topology-contract",
    "role-capability-contract",
    "interface-contract",
    "runtime-handoff-contract",
    "execution-outcome-contract",
    "trajectory-alert-contract",
    "steering-contract",
    "chaos-evidence-contract",
    "effect-request-contract",
    "effect-authority-contract",
    "pr-interaction-contract",
    "reviewer-evidence-contract",
)


class RecordingLinearClient:
    def __init__(self, issues: list[LinearIssue]) -> None:
        self.issues: list[LinearIssue] = LinearProjectIssues(issues)
        self.reads: list[str] = []
        self.writes: list[str] = []
        self.state_writes: list[tuple[str, str]] = []
        self.allow_state_write = False

    def fetch_workflow_states(self, team_id: str) -> list[WorkflowState]:
        self.reads.append("workflow_states")
        return [
            WorkflowState(PLANNED_STATE_ID, "Planned", "unstarted"),
            WorkflowState(READY_STATE_ID, "Ready for Agent", "unstarted"),
            WorkflowState(IN_PROGRESS_STATE_ID, "In Progress", "started"),
            WorkflowState(PR_OPEN_STATE_ID, "PR Open", "started"),
            WorkflowState(CI_PENDING_STATE_ID, "CI Pending", "started"),
            WorkflowState(REVIEW_STATE_ID, "Review Required", "started"),
            WorkflowState(CHANGES_STATE_ID, "Changes Requested", "started"),
            WorkflowState(NEEDS_HUMAN_STATE_ID, "Needs Human", "started"),
            WorkflowState(DONE_STATE_ID, "Done", "completed"),
            WorkflowState(REJECTED_STATE_ID, "Rejected", "canceled"),
        ]

    def fetch_project_issues(self, project_id: str) -> list[LinearIssue]:
        self.reads.append("project_issues")
        return self.issues

    def create_issue(self, *args: Any, **kwargs: Any) -> LinearIssue:
        self.writes.append("create_issue")
        raise AssertionError("bootstrap recovery may not create Linear issues")

    def update_issue(self, *args: Any, **kwargs: Any) -> LinearIssue:
        self.writes.append("update_issue")
        raise AssertionError("bootstrap recovery may not update Linear issues")

    def set_state(self, issue_id: str, state_id: str) -> LinearIssue:
        self.writes.append("set_state")
        if not self.allow_state_write:
            raise AssertionError("bootstrap recovery may not write Linear state")
        state = next(
            item for item in self.fetch_workflow_states(TEAM_ID) if item.id == state_id
        )
        current = next(item for item in self.issues if item.id == issue_id)
        updated = replace(
            current,
            state_id=state.id,
            state_name=state.name,
            state_type=state.type,
        )
        self.issues[self.issues.index(current)] = updated
        self.state_writes.append((issue_id, state_id))
        return updated

    def fetch_issue(self, issue_id: str) -> LinearIssue | None:
        self.reads.append("fetch_issue")
        return next((issue for issue in self.issues if issue.id == issue_id), None)

    def fetch_project(self, project_id: str) -> None:
        self.reads.append("project")
        return None

    def fetch_comments(self, issue_id: str) -> list[Any]:
        self.reads.append("comments")
        return []


class RecordingGitHubClient:
    def __init__(self, *, head: str = ATLAS_280_PUBLICATION_HEAD) -> None:
        self.head = head
        self.reads: list[str] = []

    def fetch_pull_request(
        self, owner: str, repo: str, pr_number: int
    ) -> dict[str, Any]:
        self.reads.append("pull_request")
        return {
            "number": 350,
            "state": "open",
            "head": {
                "sha": self.head,
            },
            "base": {
                "ref": "main",
                "repo": {"full_name": "derekrivers/atlas"},
            },
        }

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"unexpected GitHub capability {name}")


@dataclass
class Seeded:
    db: Database
    linear: RecordingLinearClient
    github: RecordingGitHubClient
    service: Atlas280BootstrapRecoveryService
    blocker: Ticket
    repair: Ticket


def _ticket(
    *, key: str, ticket_id: UUID, status: TicketStatus, linear_id: str | None
) -> Ticket:
    return Ticket(
        **(
            ticket_kwargs()
            | {
                "id": ticket_id,
                "product_id": PRODUCT_ID,
                "key": key,
                "status": status,
                "external_linear_id": linear_id,
                "component": "delivery-control",
                "acceptance_criteria": ["bounded proof"],
            }
        )
    )


def _policy_17() -> DeliveryAdmissionPolicyRevision:
    policy = DeliveryAdmissionPolicyRevision(
        id=POLICY_17_ID,
        product_id=PRODUCT_ID,
        revision=17,
        mode=DeliveryAdmissionMode.PAUSED,
        approved_symphony_ceiling=1,
        working_budget=1,
        integration_budget=1,
        review_budget=2,
        changes_requested_reserve=0,
        risk_lane_limits=(),
        component_lane_limits=tuple(
            ComponentLaneLimit(
                component=component,
                limit=1 if component == "runtime-event-contract" else 0,
            )
            for component in COMPONENTS
        ),
        created_by_type=ActorType.HUMAN,
        created_by_id="operator",
        created_at=datetime(2026, 8, 22, 13, 1, 2, 700775, tzinfo=UTC),
    )
    assert delivery_policy_fingerprint(policy) == ATLAS_280_POLICY_FINGERPRINT
    return policy


def _store_policy(db: Database) -> None:
    historical = DeliveryAdmissionPolicyRevision(
        id=POLICY_16_ID,
        product_id=PRODUCT_ID,
        revision=16,
        mode=DeliveryAdmissionMode.RUNNING,
        approved_symphony_ceiling=1,
        working_budget=1,
        integration_budget=1,
        review_budget=3,
        changes_requested_reserve=0,
        risk_lane_limits=(),
        component_lane_limits=(),
        created_by_type=ActorType.HUMAN,
        created_by_id="operator",
        created_at=NOW - timedelta(hours=2),
    )
    current = _policy_17()
    with db.session() as session, session.begin():
        session.add(
            DeliveryAdmissionPolicyRevisionRow(**historical.model_dump(mode="python"))
        )
        session.add(
            DeliveryAdmissionPolicyRevisionRow(**current.model_dump(mode="python"))
        )
        session.add(
            DeliveryAdmissionPolicyActiveRow(product_id=PRODUCT_ID, revision=17)
        )


def _admission_run(*, run_id: UUID = ATLAS_280_ADMISSION_RUN_ID) -> AdmissionRun:
    return AdmissionRun(
        id=run_id,
        product_id=PRODUCT_ID,
        policy_id=POLICY_16_ID,
        policy_revision=16,
        policy_fingerprint="1" * 64,
        snapshot_fingerprint="2" * 64,
        snapshot_observed_at=NOW - timedelta(hours=1),
        evaluated_at=NOW - timedelta(hours=1),
        selected_ticket_id=ATLAS_280_TICKET_ID,
        selected_ticket_key="ATLAS-280",
        decisions=(
            AdmissionCandidateDecision(
                ticket_id=ATLAS_280_TICKET_ID,
                ticket_key="ATLAS-280",
                external_linear_id=ATLAS_280_LINEAR_ID,
                rank=1,
                rank_inputs=AdmissionRankInputs(
                    unlock_count=1,
                    critical_path_member=False,
                    critical_path_position=None,
                    priority=10,
                    risk_level=RiskLevel.MEDIUM,
                    risk_severity=2,
                    continuously_eligible_since=NOW - timedelta(hours=2),
                    continuously_eligible_age_microseconds=3_600_000_000,
                ),
                decision=AdmissionDecisionType.ADMIT,
                protected_lanes=("operator-admission-hotspot",),
            ),
        ),
        created_by_type=ActorType.SYSTEM,
        created_by_id="atlas.pm.admission",
    )


def _pm_receipt(**counter_overrides: int) -> PmSyncReceipt:
    counters = {
        "admitted": 1,
        "promoted": 1,
        "stale": 0,
        "indeterminate": 0,
    } | counter_overrides
    return PmSyncReceipt(
        id=ATLAS_280_PM_RECEIPT_ID,
        product_id=PRODUCT_ID,
        product_key="ATLAS",
        linear_project_id=PROJECT_ID,
        started_at=NOW - timedelta(hours=1),
        finished_at=NOW - timedelta(hours=1) + timedelta(seconds=4),
        status_map_fingerprint="3" * 64,
        fetched_board_fingerprint="4" * 64,
        fetched_board_issue_count=5,
        result=PmSyncReceiptResult.SUCCESS_STATUS_ONLY,
        counters=counters,
        created_by_type=ActorType.SYSTEM,
        created_by_id="atlas.pm.sync",
    )


def _linear_issue(
    ticket: Ticket,
    *,
    identifier: str,
    state_id: str,
    state_name: str,
    state_type: str,
    publications: tuple[LinearGitHubPublication, ...] = (),
) -> LinearIssue:
    return LinearIssue(
        id=ticket.external_linear_id or "missing",
        identifier=identifier,
        title=ticket.title,
        state_id=state_id,
        state_name=state_name,
        state_type=state_type,
        github_publications=publications,
    )


def _status_map() -> LinearStatusMap:
    return LinearStatusMap(
        {
            PLANNED_STATE_ID: TicketStatus.PLANNED,
            READY_STATE_ID: TicketStatus.READY_FOR_AGENT,
            IN_PROGRESS_STATE_ID: TicketStatus.IN_PROGRESS,
            PR_OPEN_STATE_ID: TicketStatus.PR_OPEN,
            CI_PENDING_STATE_ID: TicketStatus.CI_PENDING,
            REVIEW_STATE_ID: TicketStatus.REVIEW_REQUIRED,
            CHANGES_STATE_ID: TicketStatus.CHANGES_REQUESTED,
            NEEDS_HUMAN_STATE_ID: TicketStatus.NEEDS_HUMAN_DECISION,
            DONE_STATE_ID: TicketStatus.DONE,
            REJECTED_STATE_ID: TicketStatus.REJECTED,
        }
    )


def seed(tmp_path: Path) -> Seeded:
    db = Database(f"sqlite:///{tmp_path}/atlas.db")
    db.create_all()
    ProductRepo(db).add(
        Product(**(product_kwargs() | {"id": PRODUCT_ID, "key": "ATLAS"}))
    )
    _store_policy(db)

    blocker = _ticket(
        key="ATLAS-280",
        ticket_id=ATLAS_280_TICKET_ID,
        status=TicketStatus.PLANNED,
        linear_id=ATLAS_280_LINEAR_ID,
    )
    repair = _ticket(
        key="ATLAS-281",
        ticket_id=ATLAS_281_TICKET_ID,
        status=TicketStatus.PLANNED,
        linear_id=ATLAS_281_LINEAR_ID,
    )
    tickets = TicketRepo(db)
    tickets.add(blocker)
    tickets.add(repair)
    for key in sorted(EXPECTED_DEPENDENCIES):
        dependency_ticket = _ticket(
            key=key,
            ticket_id=uuid4(),
            status=TicketStatus.DONE,
            linear_id=None,
        )
        tickets.add(dependency_ticket)
        TicketDependencyRepo(db).add(
            TicketDependency(
                **(
                    dependency_kwargs()
                    | {
                        "source_ticket_id": repair.id,
                        "target_entity_id": dependency_ticket.id,
                    }
                )
            )
        )

    AdmissionRunRepo(db).record(_admission_run())
    PmSyncReceiptRepo(db).record(_pm_receipt())
    DebtItemRepo(db).record(
        DebtItem(
            id=ATLAS_280_DEBT_ITEM_ID,
            product_id=PRODUCT_ID,
            ticket_id=ATLAS_280_TICKET_ID,
            anomaly_type=AnomalyType.OUT_OF_OWNERSHIP_TRANSITION,
            summary="bounded historical anomaly",
            observed_at=NOW - timedelta(minutes=30),
            created_by_type=ActorType.SYSTEM,
            created_by_id="pm-engine",
            created_at=NOW - timedelta(minutes=30),
        )
    )

    publication = LinearGitHubPublication(
        attachment_id="publication-350",
        repository_owner="derekrivers",
        repository_name="atlas",
        pr_number=350,
    )
    linear = RecordingLinearClient(
        [
            _linear_issue(
                blocker,
                identifier="ATL-456",
                state_id=CI_PENDING_STATE_ID,
                state_name="CI Pending",
                state_type="started",
                publications=(publication,),
            ),
            _linear_issue(
                repair,
                identifier="ATL-457",
                state_id=PLANNED_STATE_ID,
                state_name="Planned",
                state_type="unstarted",
            ),
        ]
    )
    github = RecordingGitHubClient()
    service = Atlas280BootstrapRecoveryService(
        db=db,
        linear=linear,
        github=github,
        status_map=_status_map(),
        team_id=TEAM_ID,
        project_id=PROJECT_ID,
        accepted_main_commit=ACCEPTED_MAIN,
        clock=lambda: NOW,
    )
    return Seeded(db, linear, github, service, blocker, repair)


def test_check_is_eligible_bounded_and_mutation_free(tmp_path: Path) -> None:
    seeded = seed(tmp_path)
    before_tickets = TicketRepo(seeded.db).list()
    before_debt = DebtItemRepo(seeded.db).list()

    result = seeded.service.check()

    assert result.eligible is True
    assert result.changed is False
    assert result.reason_codes == (Atlas280BootstrapCheckCode.ELIGIBLE,)
    assert result.proof is not None
    assert result.proof.publication == "derekrivers/atlas#350"
    assert TicketRepo(seeded.db).list() == before_tickets
    assert DebtItemRepo(seeded.db).list() == before_debt
    assert (
        TicketStatusTransitionRepo(seeded.db).list_for_ticket(seeded.blocker.id) == []
    )
    assert Atlas280BootstrapRecoveryRepo(seeded.db).list() == []
    assert seeded.linear.writes == []


def test_apply_is_one_atomic_direct_edge_and_idempotent(tmp_path: Path) -> None:
    seeded = seed(tmp_path)
    before_debt = DebtItemRepo(seeded.db).get(ATLAS_280_DEBT_ITEM_ID)
    before_policy = DeliveryAdmissionPolicyRepo(seeded.db).get_active(PRODUCT_ID)

    first = seeded.service.apply(operator_id="authorized-operator")

    assert first.eligible is True
    assert first.changed is True
    assert first.recovery_id is not None
    blocker = TicketRepo(seeded.db).get_by_key("ATLAS-280")
    repair = TicketRepo(seeded.db).get_by_key("ATLAS-281")
    assert blocker is not None and blocker.status is TicketStatus.CI_PENDING
    assert repair is not None and repair.status is TicketStatus.PLANNED
    transitions = TicketStatusTransitionRepo(seeded.db).list_for_ticket(
        ATLAS_280_TICKET_ID
    )
    observed_transitions = [
        (row.from_status, row.to_status, row.created_by_id) for row in transitions
    ]
    assert observed_transitions == [
        ("planned", "ci_pending", "bootstrap:atlas-280-mirror-recovery")
    ]
    receipts = Atlas280BootstrapRecoveryRepo(seeded.db).list()
    assert len(receipts) == 1
    assert receipts[0].id == first.recovery_id
    assert receipts[0].accepted_main_commit == ACCEPTED_MAIN
    assert DebtItemRepo(seeded.db).get(ATLAS_280_DEBT_ITEM_ID) == before_debt
    assert (
        DeliveryAdmissionPolicyRepo(seeded.db).get_active(PRODUCT_ID) == before_policy
    )
    assert AgentRunRepo(seeded.db).list() == []
    assert CIHandoffReconciliationRepo(seeded.db).list_for_ticket(blocker.id) == []
    assert seeded.linear.writes == []
    assert seeded.github.reads == ["pull_request", "pull_request"]

    second = seeded.service.apply(operator_id="authorized-operator")
    assert second.changed is False
    assert second.already_recovered is True
    assert len(Atlas280BootstrapRecoveryRepo(seeded.db).list()) == 1
    assert (
        len(TicketStatusTransitionRepo(seeded.db).list_for_ticket(ATLAS_280_TICKET_ID))
        == 1
    )


def test_atomic_failure_leaves_neither_transition_nor_receipt(tmp_path: Path) -> None:
    seeded = seed(tmp_path)
    with seeded.db.engine.begin() as connection:
        connection.execute(
            sa.text(
                "CREATE TRIGGER fail_bootstrap_receipt BEFORE INSERT ON "
                "atlas_280_bootstrap_recovery_receipts BEGIN "
                "SELECT RAISE(ABORT, 'injected receipt failure'); END"
            )
        )

    result = seeded.service.apply(operator_id="authorized-operator")

    assert result.changed is False
    assert result.reason_codes == (Atlas280BootstrapCheckCode.STORAGE_REFUSED,)
    blocker = TicketRepo(seeded.db).get_by_key("ATLAS-280")
    assert blocker is not None and blocker.status is TicketStatus.PLANNED
    assert Atlas280BootstrapRecoveryRepo(seeded.db).list() == []
    assert TicketStatusTransitionRepo(seeded.db).list_for_ticket(blocker.id) == []


def test_only_subsequent_normal_pm_cadence_owns_ci_pending_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = seed(tmp_path)
    bootstrap = seeded.service.apply(operator_id="authorized-operator")
    assert bootstrap.changed is True
    assert seeded.linear.state_writes == []
    assert (
        CIHandoffReconciliationRepo(seeded.db).list_for_ticket(ATLAS_280_TICKET_ID)
        == []
    )

    github = FakeGitHubClient(
        check_runs=[
            {
                "id": 1,
                "name": "lint-python",
                "status": "completed",
                "conclusion": "success",
                "completed_at": NOW.isoformat().replace("+00:00", "Z"),
                "html_url": "https://github.com/derekrivers/atlas/runs/1",
                "repository": {"full_name": "derekrivers/atlas"},
                "pull_requests": [
                    {"number": 350, "head": {"sha": ATLAS_280_PUBLICATION_HEAD}}
                ],
            },
            {
                "id": 2,
                "name": "test-python",
                "status": "completed",
                "conclusion": "success",
                "completed_at": NOW.isoformat().replace("+00:00", "Z"),
                "html_url": "https://github.com/derekrivers/atlas/runs/2",
                "repository": {"full_name": "derekrivers/atlas"},
                "pull_requests": [
                    {"number": 350, "head": {"sha": ATLAS_280_PUBLICATION_HEAD}}
                ],
            },
        ],
        pull_request={
            "number": 350,
            "state": "open",
            "draft": False,
            "head": {"sha": ATLAS_280_PUBLICATION_HEAD},
            "base": {
                "ref": "main",
                "repo": {"full_name": "derekrivers/atlas"},
            },
        },
    )
    seeded.linear.writes.clear()
    seeded.linear.allow_state_write = True

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a later workflow writer ran after CI handoff")

    monkeypatch.setattr("atlas.pm.sync.admit_one_ready", forbidden)
    monkeypatch.setattr("atlas.pm.sync.complete_verified", forbidden)
    result = sync_tick(
        tickets=TicketRepo(seeded.db),
        db=seeded.db,
        client=seeded.linear,
        status_map=_status_map(),
        team_id=TEAM_ID,
        project_id=PROJECT_ID,
        inbox_dir=tmp_path / "inbox",
        documents=lambda: [],
        now=NOW + timedelta(minutes=1),
        completion_clock=lambda: NOW + timedelta(minutes=1, seconds=1),
        github_client=github,
    )

    assert result.ci_handoff_mutations == 1
    assert seeded.linear.state_writes == [(ATLAS_280_LINEAR_ID, REVIEW_STATE_ID)]
    blocker = TicketRepo(seeded.db).get_by_key("ATLAS-280")
    assert blocker is not None and blocker.status is TicketStatus.REVIEW_REQUIRED
    reconciliations = CIHandoffReconciliationRepo(seeded.db).list_for_ticket(
        ATLAS_280_TICKET_ID
    )
    assert len(reconciliations) == 1


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("blocker_uuid", Atlas280BootstrapCheckCode.BLOCKER_IDENTITY),
        ("repair_uuid", Atlas280BootstrapCheckCode.REPAIR_IDENTITY),
        ("blocker_key", Atlas280BootstrapCheckCode.BLOCKER_IDENTITY),
        ("repair_key", Atlas280BootstrapCheckCode.REPAIR_IDENTITY),
        ("blocker_linear", Atlas280BootstrapCheckCode.BLOCKER_IDENTITY),
        ("repair_linear", Atlas280BootstrapCheckCode.REPAIR_IDENTITY),
        ("dependency", Atlas280BootstrapCheckCode.DEPENDENCIES),
        ("missing_admission", Atlas280BootstrapCheckCode.ADMISSION_EVIDENCE),
        ("wrong_selected_ticket", Atlas280BootstrapCheckCode.STORAGE_REFUSED),
        ("duplicate_admission", Atlas280BootstrapCheckCode.ADMISSION_EVIDENCE),
        ("wrong_pm_receipt", Atlas280BootstrapCheckCode.PM_RECEIPT),
        ("failed_pm_receipt", Atlas280BootstrapCheckCode.PM_RECEIPT),
        ("partial_pm_receipt", Atlas280BootstrapCheckCode.PM_RECEIPT),
        ("bad_pm_receipt", Atlas280BootstrapCheckCode.PM_RECEIPT),
        ("indeterminate_pm_receipt", Atlas280BootstrapCheckCode.PM_RECEIPT),
        ("missing_debt", Atlas280BootstrapCheckCode.DEBT_ITEM),
        ("blocker_local_state", Atlas280BootstrapCheckCode.LOCAL_STATE),
        ("repair_local_state", Atlas280BootstrapCheckCode.LOCAL_STATE),
        ("transition_history", Atlas280BootstrapCheckCode.TRANSITION_HISTORY),
        ("policy", Atlas280BootstrapCheckCode.POLICY),
        ("admission_fence", Atlas280BootstrapCheckCode.ADMISSION_FENCE),
        ("ci_fence", Atlas280BootstrapCheckCode.CI_HANDOFF_FENCE),
    ],
)
def test_local_proof_defects_fail_closed(
    tmp_path: Path, mutation: str, reason: Atlas280BootstrapCheckCode
) -> None:
    seeded = seed(tmp_path)
    with seeded.db.session() as session, session.begin():
        row: Any
        if mutation == "blocker_uuid":
            row = session.get(TicketRow, ATLAS_280_TICKET_ID)
            assert row is not None
            row.id = uuid4()
        elif mutation == "repair_uuid":
            row = session.get(TicketRow, ATLAS_281_TICKET_ID)
            assert row is not None
            row.id = uuid4()
        elif mutation == "blocker_key":
            row = session.get(TicketRow, ATLAS_280_TICKET_ID)
            assert row is not None
            row.key = "ATLAS-999"
        elif mutation == "repair_key":
            row = session.get(TicketRow, ATLAS_281_TICKET_ID)
            assert row is not None
            row.key = "ATLAS-998"
        elif mutation == "blocker_linear":
            row = session.get(TicketRow, ATLAS_280_TICKET_ID)
            assert row is not None
            row.external_linear_id = "0" * 36
        elif mutation == "repair_linear":
            row = session.get(TicketRow, ATLAS_281_TICKET_ID)
            assert row is not None
            row.external_linear_id = "0" * 36
        elif mutation == "dependency":
            row = session.scalars(
                sa.select(TicketRow).where(TicketRow.key == "ATLAS-249")
            ).one()
            row.status = TicketStatus.PLANNED.value
        elif mutation == "missing_admission":
            session.execute(sa.text("DROP TRIGGER admission_runs_no_delete"))
            row = session.get(AdmissionRunRow, ATLAS_280_ADMISSION_RUN_ID)
            assert row is not None
            session.delete(row)
        elif mutation == "wrong_selected_ticket":
            session.execute(sa.text("DROP TRIGGER admission_runs_no_update"))
            row = session.get(AdmissionRunRow, ATLAS_280_ADMISSION_RUN_ID)
            assert row is not None
            row.selected_ticket_id = ATLAS_281_TICKET_ID
            row.selected_ticket_key = "ATLAS-281"
        elif mutation == "duplicate_admission":
            duplicate = _admission_run(run_id=uuid4())
            payload = duplicate.model_dump(mode="python")
            payload["decisions"] = duplicate.model_dump(mode="json")["decisions"]
            session.add(AdmissionRunRow(**payload))
        elif mutation == "wrong_pm_receipt":
            row = session.get(PmSyncReceiptRow, ATLAS_280_PM_RECEIPT_ID)
            assert row is not None
            row.id = uuid4()
        elif mutation == "failed_pm_receipt":
            row = session.get(PmSyncReceiptRow, ATLAS_280_PM_RECEIPT_ID)
            assert row is not None
            row.result = PmSyncReceiptResult.FAILED.value
        elif mutation == "partial_pm_receipt":
            row = session.get(PmSyncReceiptRow, ATLAS_280_PM_RECEIPT_ID)
            assert row is not None
            row.result = PmSyncReceiptResult.PARTIAL.value
        elif mutation == "bad_pm_receipt":
            row = session.get(PmSyncReceiptRow, ATLAS_280_PM_RECEIPT_ID)
            assert row is not None
            row.counters = dict(row.counters) | {"stale": 1}
        elif mutation == "indeterminate_pm_receipt":
            row = session.get(PmSyncReceiptRow, ATLAS_280_PM_RECEIPT_ID)
            assert row is not None
            row.counters = dict(row.counters) | {"indeterminate": 1}
        elif mutation == "missing_debt":
            row = session.get(DebtItemRow, ATLAS_280_DEBT_ITEM_ID)
            assert row is not None
            session.delete(row)
        elif mutation == "blocker_local_state":
            row = session.get(TicketRow, ATLAS_280_TICKET_ID)
            assert row is not None
            row.status = TicketStatus.BACKLOG.value
        elif mutation == "repair_local_state":
            row = session.get(TicketRow, ATLAS_281_TICKET_ID)
            assert row is not None
            row.status = TicketStatus.BACKLOG.value
        elif mutation == "transition_history":
            session.add(
                TicketStatusTransitionRow(
                    id=uuid4(),
                    ticket_id=ATLAS_280_TICKET_ID,
                    from_status="backlog",
                    to_status="planned",
                    occurred_at=NOW,
                    created_by_type="system",
                    created_by_id="test",
                )
            )
        elif mutation == "policy":
            active = session.get(DeliveryAdmissionPolicyActiveRow, PRODUCT_ID)
            assert active is not None
            active.revision = 16
        elif mutation == "admission_fence":
            session.add(
                AdmissionWriteFenceRow(
                    product_id=PRODUCT_ID,
                    admission_run_id=ATLAS_280_ADMISSION_RUN_ID,
                    ticket_id=ATLAS_280_TICKET_ID,
                    ticket_key="ATLAS-280",
                    issue_id=ATLAS_280_LINEAR_ID,
                    source_state_id=PLANNED_STATE_ID,
                    target_state_id=CI_PENDING_STATE_ID,
                    policy_revision=16,
                    state="indeterminate",
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        elif mutation == "ci_fence":
            session.add(
                CIHandoffWriteFenceRow(
                    product_id=PRODUCT_ID,
                    reconciliation_id=uuid4(),
                    ticket_id=ATLAS_280_TICKET_ID,
                    ticket_key="ATLAS-280",
                    issue_id=ATLAS_280_LINEAR_ID,
                    source_state_id=CI_PENDING_STATE_ID,
                    target_state_id="state-review",
                    target_status="review_required",
                    state="indeterminate",
                    created_at=NOW,
                    updated_at=NOW,
                )
            )

    result = seeded.service.check()
    assert result.eligible is False
    assert result.changed is False
    assert reason in result.reason_codes
    assert seeded.linear.writes == []


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("incomplete", Atlas280BootstrapCheckCode.BOARD_PULL),
        ("duplicate_issue", Atlas280BootstrapCheckCode.BOARD_IDENTITY),
        ("wrong_issue_identity", Atlas280BootstrapCheckCode.BOARD_IDENTITY),
        ("blocker_state", Atlas280BootstrapCheckCode.BOARD_STATE),
        ("repair_state", Atlas280BootstrapCheckCode.BOARD_STATE),
        ("missing_publication", Atlas280BootstrapCheckCode.PUBLICATION),
        ("incomplete_publication", Atlas280BootstrapCheckCode.PUBLICATION),
        ("ambiguous_publication", Atlas280BootstrapCheckCode.PUBLICATION),
        ("wrong_pr", Atlas280BootstrapCheckCode.PUBLICATION),
        ("wrong_head", Atlas280BootstrapCheckCode.PUBLICATION),
    ],
)
def test_external_proof_defects_fail_closed(
    tmp_path: Path, mutation: str, reason: Atlas280BootstrapCheckCode
) -> None:
    seeded = seed(tmp_path)
    blocker = seeded.linear.issues[0]
    repair = seeded.linear.issues[1]
    if mutation == "incomplete":
        seeded.linear.issues = LinearProjectIssues(
            list(seeded.linear.issues), complete=False, pagination_gaps=("gap",)
        )
    elif mutation == "duplicate_issue":
        seeded.linear.issues = LinearProjectIssues([*seeded.linear.issues, blocker])
    elif mutation == "wrong_issue_identity":
        seeded.linear.issues[0] = replace(blocker, identifier="ATL-999")
    elif mutation == "blocker_state":
        seeded.linear.issues[0] = replace(
            blocker,
            state_id=PLANNED_STATE_ID,
            state_name="Planned",
            state_type="unstarted",
        )
    elif mutation == "repair_state":
        seeded.linear.issues[1] = replace(
            repair,
            state_id=CI_PENDING_STATE_ID,
            state_name="CI Pending",
            state_type="started",
        )
    elif mutation == "missing_publication":
        seeded.linear.issues[0] = replace(blocker, github_publications=())
    elif mutation == "incomplete_publication":
        seeded.linear.issues[0] = replace(blocker, github_publications_complete=False)
    elif mutation == "ambiguous_publication":
        other = LinearGitHubPublication(
            attachment_id="other",
            repository_owner="derekrivers",
            repository_name="atlas",
            pr_number=349,
        )
        seeded.linear.issues[0] = replace(
            blocker,
            github_publications=(*blocker.github_publications, other),
        )
    elif mutation == "wrong_pr":
        wrong = LinearGitHubPublication(
            attachment_id="wrong",
            repository_owner="derekrivers",
            repository_name="atlas",
            pr_number=349,
        )
        seeded.linear.issues[0] = replace(blocker, github_publications=(wrong,))
    elif mutation == "wrong_head":
        seeded.github.head = "8" * 40

    result = seeded.service.check()
    assert result.eligible is False
    assert result.changed is False
    assert reason in result.reason_codes
    assert seeded.linear.writes == []


def test_existing_receipt_with_different_proof_fails_closed(tmp_path: Path) -> None:
    seeded = seed(tmp_path)
    eligible, proof = seeded.service._evaluate(operator_id="operator")
    assert eligible.eligible and proof is not None
    altered = proof.receipt.model_copy(update={"board_fingerprint": "9" * 64})
    with seeded.db.session() as session, session.begin():
        session.add(
            Atlas280BootstrapRecoveryReceiptRow(**altered.model_dump(mode="python"))
        )

    result = seeded.service.check()
    assert result.eligible is False
    assert result.reason_codes == (
        Atlas280BootstrapCheckCode.EXISTING_RECOVERY_CONFLICT,
    )


def test_fixed_receipt_model_rejects_arbitrary_ticket_use() -> None:
    with pytest.raises(ValidationError, match="blocker_ticket_id"):
        Atlas280BootstrapRecoveryReceipt(
            id=uuid4(),
            product_id=PRODUCT_ID,
            blocker_ticket_id=uuid4(),
            blocker_linear_issue_id=ATLAS_280_LINEAR_ID,
            blocker_linear_state_id=CI_PENDING_STATE_ID,
            repair_ticket_id=ATLAS_281_TICKET_ID,
            repair_linear_issue_id=ATLAS_281_LINEAR_ID,
            repair_linear_state_id=PLANNED_STATE_ID,
            admission_run_id=ATLAS_280_ADMISSION_RUN_ID,
            pm_sync_receipt_id=ATLAS_280_PM_RECEIPT_ID,
            publication_head=ATLAS_280_PUBLICATION_HEAD,
            historical_debt_item_id=ATLAS_280_DEBT_ITEM_ID,
            board_fingerprint="1" * 64,
            policy_id=POLICY_17_ID,
            policy_fingerprint=ATLAS_280_POLICY_FINGERPRINT,
            accepted_main_commit=ACCEPTED_MAIN,
            created_at=NOW,
            created_by_id="operator",
        )


def test_storage_boundary_revalidates_fixed_pair(tmp_path: Path) -> None:
    seeded = seed(tmp_path)
    eligible, proof = seeded.service._evaluate(operator_id="operator")
    assert eligible.eligible and proof is not None
    bypassed = proof.receipt.model_copy(update={"blocker_ticket_id": uuid4()})

    with pytest.raises(ValidationError, match="blocker_ticket_id"):
        Atlas280BootstrapRecoveryRepo(seeded.db).apply(bypassed)

    blocker = TicketRepo(seeded.db).get_by_key("ATLAS-280")
    assert blocker is not None and blocker.status is TicketStatus.PLANNED
    assert Atlas280BootstrapRecoveryRepo(seeded.db).list() == []


def test_command_surface_rejects_arbitrary_ticket_arguments() -> None:
    with pytest.raises(SystemExit):
        _parser().parse_args(
            ["check", "--db", "sqlite:////tmp/disposable.db", "--ticket", "ATLAS-999"]
        )
