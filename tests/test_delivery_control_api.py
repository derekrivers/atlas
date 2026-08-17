"""ATL-423: authenticated delivery-control policy and status API."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_delivery_admission_policy_model import policy_spec
from test_models_validation import ticket_kwargs
from test_plan_pipeline import fresh_db

from atlas.api.app import create_app
from atlas.api.dependencies import get_delivery_admission_policy_service
from atlas.api.security import CSRF_HEADER_NAME
from atlas.core.enums import ActorType, EvidenceStatus, RiskLevel
from atlas.core.models import (
    AcceptanceSession,
    AcceptanceSessionBlockingReason,
    AcceptanceSessionLifecycle,
    AcceptanceSessionStep,
    AcceptanceSessionStepState,
    AdmissionRun,
    CIHandoffClassification,
    CIHandoffDecision,
    CIHandoffReason,
    CIHandoffReconciliation,
    Evidence,
    EvidenceType,
    PmSyncReceipt,
    PmSyncReceiptResult,
    Ticket,
    TicketStatus,
    VerificationCheckType,
)
from atlas.core.models.acceptance_session import (
    AcceptanceAssessmentSnapshot,
    AcceptanceCriterionSnapshot,
    AcceptanceStepSummary,
)
from atlas.core.models.admission_run import (
    AdmissionCandidateDecision,
    AdmissionDecisionType,
    AdmissionHoldCode,
    AdmissionHoldReason,
    AdmissionRankInputs,
)
from atlas.core.models.ci_handoff_reconciliation import CIHandoffCheckResult
from atlas.core.models.delivery_admission_policy import DeliveryAdmissionPolicySpec
from atlas.github import GitHubClient
from atlas.linear.client import LinearClient
from atlas.orchestration import (
    DeliveryAdmissionPolicyChangeResult,
    DeliveryAdmissionPolicyChangeStatus,
    DeliveryAdmissionPolicyConflictCode,
    DeliveryAdmissionPolicyService,
)
from atlas.pm import SnapshotIncompletenessCode, delivery_policy_fingerprint
from atlas.storage import (
    AcceptanceSessionRepo,
    AdmissionCoordinationRepo,
    AdmissionRunRepo,
    CIHandoffReconciliationRepo,
    Database,
    DeliveryAdmissionPolicyRepo,
    EvidenceRepo,
    OperatorActionReceiptRepo,
    PmSyncReceiptRepo,
    ProductRepo,
    TicketRepo,
)
from atlas.verification import validation_plan as validation_plan_module

GOOD_TOKEN = "atlas-operator-token-0123456789ABCDEFGHJKLMNPQRSTxyz!@#"
LOOPBACK_HOST = "127.0.0.1:4173"
LOOPBACK_ORIGIN = f"http://{LOOPBACK_HOST}"
NOW = datetime(2026, 8, 10, 10, tzinfo=UTC)


@pytest.fixture
def database(tmp_path: Path) -> Database:
    return fresh_db(tmp_path)


def _product_id(database: Database) -> UUID:
    [product] = ProductRepo(database).list()
    return product.id


def _seed_policy(
    database: Database,
    *,
    spec: DeliveryAdmissionPolicySpec | None = None,
) -> DeliveryAdmissionPolicyChangeResult:
    result = DeliveryAdmissionPolicyService(database, clock=lambda: NOW).revise(
        product_id=_product_id(database),
        expected_revision=0,
        idempotency_key="seed-policy-revision-one",
        policy=spec or policy_spec(),
    )
    assert result.status is DeliveryAdmissionPolicyChangeStatus.APPLIED
    assert result.policy is not None
    assert result.receipt is not None
    return result


def _writable_app(
    database: Database,
    service: RecordingPolicyService | None = None,
) -> FastAPI:
    app = create_app(
        database=database,
        enable_writes=True,
        operator_token=GOOD_TOKEN,
        bind_host="127.0.0.1",
        clock=lambda: NOW,
    )
    if service is not None:
        app.dependency_overrides[get_delivery_admission_policy_service] = lambda: cast(
            DeliveryAdmissionPolicyService,
            service,
        )
    return app


def _login(client: TestClient) -> str:
    response = client.post(
        "/api/v1/session",
        json={"token": GOOD_TOKEN},
        headers={"host": LOOPBACK_HOST},
    )
    assert response.status_code == 200
    return str(response.json()["csrf_token"])


def _policy_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "expected_revision": 1,
        "mode": "paused",
        "approved_symphony_ceiling": 3,
        "working_budget": 2,
        "integration_budget": 2,
        "review_budget": 2,
        "changes_requested_reserve": 0,
        "risk_lane_limits": [],
        "component_lane_limits": [],
    }
    return body | overrides


def _mutation_headers(
    csrf_token: str,
    *,
    idempotency_key: str | None = "replace-policy",
    host: str = LOOPBACK_HOST,
    origin: str = LOOPBACK_ORIGIN,
    content_type: str = "application/json",
) -> dict[str, str]:
    headers = {
        "host": host,
        "origin": origin,
        "content-type": content_type,
        CSRF_HEADER_NAME: csrf_token,
    }
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return headers


@dataclass
class RecordingPolicyService:
    result: DeliveryAdmissionPolicyChangeResult
    calls: list[dict[str, object]] = field(default_factory=list)

    def revise_current(
        self,
        *,
        expected_revision: int,
        idempotency_key: str,
        policy: DeliveryAdmissionPolicySpec,
    ) -> DeliveryAdmissionPolicyChangeResult:
        self.calls.append(
            {
                "expected_revision": expected_revision,
                "idempotency_key": idempotency_key,
                "policy": policy,
            }
        )
        return self.result


def _ticket(
    database: Database,
    *,
    key: str,
    status: str,
    risk_level: str = "low",
    component: str | None = None,
    **overrides: Any,
) -> Ticket:
    return TicketRepo(database).add(
        Ticket(
            **ticket_kwargs()
            | {
                "id": uuid4(),
                "product_id": _product_id(database),
                "key": key,
                "status": status,
                "risk_level": risk_level,
                "component": component,
                "external_linear_id": f"linear-{key}",
                "status_entered_at": NOW,
                "created_at": NOW,
                "updated_at": NOW,
            }
            | overrides
        )
    )


def _seed_ci_reconciliation(
    database: Database,
    *,
    ticket: Ticket,
    classification: CIHandoffClassification,
    reason: CIHandoffReason,
    decision: CIHandoffDecision,
    status: EvidenceStatus,
    evidence_ids: tuple[UUID, ...] = (),
    head_sha: str = "2" * 40,
    pr_number: int = 438,
    observed_at: datetime = NOW + timedelta(minutes=2),
) -> CIHandoffReconciliation:
    policy = DeliveryAdmissionPolicyRepo(database).get_active(_product_id(database))
    assert policy is not None
    return CIHandoffReconciliationRepo(database).record(
        CIHandoffReconciliation(
            id=uuid4(),
            product_id=policy.product_id,
            ticket_id=ticket.id,
            ticket_key=ticket.key,
            linear_issue_id=ticket.external_linear_id,
            repository_owner="acme",
            repository_name="atlas",
            pr_number=pr_number,
            head_commit=head_sha,
            policy_id=policy.id,
            policy_revision=policy.revision,
            policy_fingerprint=delivery_policy_fingerprint(policy),
            snapshot_fingerprint="a" * 64,
            classification=classification,
            reason=reason,
            decision=decision,
            check_results=(
                CIHandoffCheckResult(
                    check_type=VerificationCheckType.TESTS,
                    status=status,
                    classification=classification,
                    evidence_ids=evidence_ids,
                ),
            ),
            observed_at=observed_at,
            created_by_type=ActorType.SYSTEM,
            created_by_id="atlas.pm.ci_handoff",
        )
    )


def _seed_acceptance_assessment(
    database: Database,
    *,
    ticket_key: str,
    head_sha: str,
    pr_number: int,
    observed_at: datetime = NOW,
    identity_seed: str = "d",
) -> AcceptanceSession:
    step_summaries = {
        step: AcceptanceStepSummary(
            state=(
                AcceptanceSessionStepState.COMPLETE
                if step is AcceptanceSessionStep.PREFLIGHT
                else AcceptanceSessionStepState.PENDING
            ),
            occurred_at=(NOW if step is AcceptanceSessionStep.PREFLIGHT else None),
        )
        for step in AcceptanceSessionStep
    }
    session = AcceptanceSession(
        id=uuid4(),
        repository_owner="acme",
        repository_name="atlas",
        pr_number=pr_number,
        close_set=(ticket_key,),
        head_ref="agent/candidate",
        head_sha=head_sha,
        head_repository="acme/atlas",
        base_ref="main",
        base_sha="1" * 40,
        base_repository="acme/atlas",
        initial_assessment=AcceptanceAssessmentSnapshot(
            pr_state="open",
            pr_draft=False,
            pr_merged=False,
            base_sha_source="live_branch",
            merge_base_sha="1" * 40,
            ahead_by=1,
            behind_by=0,
            compare_status="ahead",
            mergeability="mergeable",
            ancestry="current",
            eligibility="eligible",
            integration_status="current",
        ),
        criteria_snapshot=(
            AcceptanceCriterionSnapshot(
                ticket_key=ticket_key,
                criterion_index=0,
                text="Prove the delivery-control projection.",
            ),
        ),
        criteria_fingerprint=f"sha256:{'c' * 64}",
        creation_idempotency_key_identity=f"sha256:{identity_seed * 64}",
        created_by_type=ActorType.HUMAN,
        created_by_id="operator",
        lifecycle=AcceptanceSessionLifecycle.PREFLIGHT_PASSED,
        step_summaries=step_summaries,
        blocking_reasons=(),
        stored_merge_ready=False,
        historical_readiness_reasons=(
            AcceptanceSessionBlockingReason.EVIDENCE_NOT_READY,
            AcceptanceSessionBlockingReason.CONFIRMATIONS_NOT_READY,
            AcceptanceSessionBlockingReason.VERIFICATION_NOT_PASSED,
        ),
        created_at=observed_at,
        updated_at=observed_at,
    )
    result = AcceptanceSessionRepo(database).create(session)
    assert result.created is True
    return result.session


def _receipt(
    database: Database,
    *,
    result: PmSyncReceiptResult,
    finished_at: datetime,
    error_summary: str | None = None,
) -> PmSyncReceipt:
    return PmSyncReceiptRepo(database).record(
        PmSyncReceipt(
            id=uuid4(),
            product_id=_product_id(database),
            product_key="ATLAS",
            linear_project_id="linear-project",
            started_at=finished_at - timedelta(seconds=1),
            finished_at=finished_at,
            status_map_fingerprint="status-map-fingerprint",
            fetched_board_fingerprint="board-fingerprint",
            fetched_board_issue_count=4,
            result=result,
            counters={"admitted": 0, "held": 1, "indeterminate": 0},
            error_summary=error_summary,
            created_by_type=ActorType.SYSTEM,
            created_by_id="atlas.pm.sync",
        )
    )


def _seed_latest_run_and_fence(
    database: Database,
    *,
    ticket: Ticket,
) -> AdmissionRun:
    policy = DeliveryAdmissionPolicyRepo(database).get_active(_product_id(database))
    assert policy is not None
    duplicate_snapshot_reasons = (
        AdmissionHoldReason(
            code=AdmissionHoldCode.SNAPSHOT_INCOMPLETE,
            source_code=SnapshotIncompletenessCode.MISSING_JOINED_ISSUE.value,
            issue_id="raw-linear-issue-id-must-not-project",
            ticket_key=ticket.key,
        ),
        AdmissionHoldReason(
            code=AdmissionHoldCode.SNAPSHOT_INCOMPLETE,
            source_code=SnapshotIncompletenessCode.MISSING_JOINED_ISSUE.value,
            issue_id="second-raw-linear-issue-id-must-not-project",
            ticket_key="ATLAS-OTHER",
        ),
        AdmissionHoldReason(
            code=AdmissionHoldCode.RISK_LANE,
            selector="critical",
            observed=1,
            limit=0,
        ),
        AdmissionHoldReason(
            code=AdmissionHoldCode.PROTECTED_LANE,
            selector="database-migrations",
            observed=2,
            limit=1,
            owner_ticket_keys=("ATLAS-OWNER-2", "ATLAS-OWNER-1"),
        ),
    )
    run = AdmissionRun(
        id=uuid4(),
        product_id=_product_id(database),
        policy_id=policy.id,
        policy_revision=policy.revision,
        policy_fingerprint=delivery_policy_fingerprint(policy),
        snapshot_fingerprint="a" * 64,
        snapshot_observed_at=NOW,
        evaluated_at=NOW,
        decisions=(
            AdmissionCandidateDecision(
                ticket_id=ticket.id,
                ticket_key=ticket.key,
                external_linear_id=ticket.external_linear_id,
                rank=1,
                rank_inputs=AdmissionRankInputs(
                    unlock_count=4,
                    critical_path_member=True,
                    critical_path_position=2,
                    priority=ticket.priority,
                    risk_level=ticket.risk_level,
                    risk_severity=3,
                    continuously_eligible_since=NOW - timedelta(minutes=5),
                    continuously_eligible_age_microseconds=300_000_000,
                ),
                decision=AdmissionDecisionType.HOLD,
                reasons=duplicate_snapshot_reasons,
                protected_lanes=("database-migrations",),
                protected_lane_registry_version=("protected-integration-lanes/v1"),
                protected_lane_registry_fingerprint="f" * 64,
            ),
        ),
        created_by_type=ActorType.SYSTEM,
        created_by_id="atlas.pm.admission",
    )
    AdmissionRunRepo(database).record(run)

    owner_id = uuid4()
    coordination = AdmissionCoordinationRepo(database)
    assert coordination.try_acquire(
        product_id=run.product_id,
        owner_id=owner_id,
        acquired_at=NOW,
        ttl=timedelta(minutes=5),
    )
    coordination.begin_write(
        product_id=run.product_id,
        owner_id=owner_id,
        admission_run_id=run.id,
        ticket_id=ticket.id,
        ticket_key=ticket.key,
        issue_id="raw-linear-issue-id-must-not-project",
        source_state_id="planned-state",
        target_state_id="ready-state",
        policy_revision=run.policy_revision,
        created_at=NOW,
    )
    coordination.mark_indeterminate(
        product_id=run.product_id,
        admission_run_id=run.id,
        observed_at=NOW + timedelta(seconds=1),
    )
    return run


def test_ac1_ac5_get_returns_policy_sync_occupancy_and_secret_free_typed_reasons(
    database: Database,
) -> None:
    _seed_policy(
        database,
        spec=policy_spec(
            working_budget=1,
            review_budget=1,
            changes_requested_reserve=1,
            risk_lane_limits=[{"risk_level": "critical", "limit": 0}],
            component_lane_limits=[{"component": "atlas.api", "limit": 0}],
        ),
    )
    working = _ticket(
        database,
        key="ATLAS-STATUS-1",
        status="in_progress",
        risk_level="critical",
        component=" Atlas.API ",
    )
    _ticket(database, key="ATLAS-STATUS-2", status="changes_requested")
    _ticket(database, key="ATLAS-STATUS-3", status="review_required")
    _ticket(database, key="ATLAS-STATUS-4", status="needs_human_decision")
    _seed_latest_run_and_fence(database, ticket=working)
    successful_at = NOW + timedelta(minutes=1)
    _receipt(
        database,
        result=PmSyncReceiptResult.SUCCESS_ZERO_ACTION,
        finished_at=successful_at,
    )
    secret_exception = "token=linear-secret raw exception " + "x" * 4_000
    _receipt(
        database,
        result=PmSyncReceiptResult.PARTIAL,
        finished_at=successful_at + timedelta(minutes=1),
        error_summary=secret_exception,
    )

    with TestClient(_writable_app(database)) as client:
        unauthenticated = client.get("/api/v1/delivery-control")
        assert unauthenticated.status_code == 401
        csrf_token = _login(client)
        response = client.get("/api/v1/delivery-control")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert payload["policy"]["revision"] == 1
    assert payload["policy"]["approved_symphony_ceiling"] == 3
    assert payload["policy"]["integration_budget"] == 2
    assert payload["last_linear_sync_at"] == successful_at.isoformat().replace(
        "+00:00", "Z"
    )
    assert payload["occupancy"]["source"] == "materialized_atlas_statuses"
    assert payload["occupancy"]["working_occupancy"] == 2
    assert payload["occupancy"]["review_occupancy"] == 2
    assert payload["occupancy"]["changes_requested_occupancy"] == 1
    assert payload["occupancy"]["changes_requested_reserve_remaining"] == 0
    assert {
        (reason["dimension"], reason["selector"])
        for reason in payload["occupancy"]["over_capacity_reasons"]
    } == {
        ("working", None),
        ("review", None),
        ("risk_lane", "critical"),
        ("component_lane", "atlas.api"),
    }
    latest = payload["latest_admission"]
    assert latest["decision_count"] == 1
    assert latest["decisions_truncated"] is False
    assert latest["decisions"][0]["rank_inputs"] == {
        "unlock_count": 4,
        "critical_path_member": True,
        "critical_path_position": 2,
        "priority": working.priority,
        "risk_level": "critical",
        "risk_severity": 3,
        "continuously_eligible_since": (NOW - timedelta(minutes=5))
        .isoformat()
        .replace("+00:00", "Z"),
        "continuously_eligible_age_microseconds": 300_000_000,
    }
    reasons = latest["decisions"][0]["reasons"]
    assert [reason["code"] for reason in reasons] == [
        "protected_lane",
        "risk_lane",
        "snapshot_incomplete",
    ]
    assert reasons[0]["owner_ticket_keys"] == [
        "ATLAS-OWNER-1",
        "ATLAS-OWNER-2",
    ]
    assert reasons[2]["source_code"] == "missing_joined_issue"
    assert latest["decisions"][0]["protected_lanes"] == ["database-migrations"]
    assert latest["decisions"][0]["protected_lane_registry_version"] == (
        "protected-integration-lanes/v1"
    )
    assert latest["decisions"][0]["protected_lane_registry_fingerprint"] == ("f" * 64)
    assert payload["indeterminate_reasons"] == [
        {
            "reason": "write_indeterminate",
            "state": "indeterminate",
            "admission_run_id": str(latest["run_id"]),
            "ticket_key": "ATLAS-STATUS-1",
            "policy_revision": 1,
            "observed_at": (NOW + timedelta(seconds=1))
            .isoformat()
            .replace("+00:00", "Z"),
        }
    ]
    assert secret_exception not in response.text
    assert "raw-linear-issue-id" not in response.text
    assert GOOD_TOKEN not in response.text
    assert csrf_token not in response.text


def test_ac5_latest_decisions_are_bounded_without_dropping_typed_hold_codes(
    database: Database,
) -> None:
    seeded = _seed_policy(database)
    assert seeded.policy is not None
    policy = seeded.policy
    decisions = tuple(
        AdmissionCandidateDecision(
            ticket_id=uuid4(),
            ticket_key=f"ATLAS-BOUND-{rank}",
            external_linear_id=f"linear-bound-{rank}",
            rank=rank,
            rank_inputs=AdmissionRankInputs(
                unlock_count=0,
                critical_path_member=False,
                critical_path_position=None,
                priority=0,
                risk_level=RiskLevel.LOW,
                risk_severity=0,
                continuously_eligible_since=NOW,
                continuously_eligible_age_microseconds=0,
            ),
            decision=AdmissionDecisionType.HOLD,
            reasons=(
                AdmissionHoldReason(
                    code=AdmissionHoldCode.WORKING_BUDGET,
                    observed=4,
                    limit=3,
                ),
            ),
        )
        for rank in range(1, 102)
    )
    AdmissionRunRepo(database).record(
        AdmissionRun(
            id=uuid4(),
            product_id=policy.product_id,
            policy_id=policy.id,
            policy_revision=policy.revision,
            policy_fingerprint=delivery_policy_fingerprint(policy),
            snapshot_fingerprint="b" * 64,
            snapshot_observed_at=NOW,
            evaluated_at=NOW,
            decisions=decisions,
            created_by_type=ActorType.SYSTEM,
            created_by_id="atlas.pm.admission",
        )
    )

    with TestClient(_writable_app(database)) as client:
        _login(client)
        response = client.get("/api/v1/delivery-control")

    assert response.status_code == 200
    latest = response.json()["latest_admission"]
    assert latest["decision_count"] == 101
    assert latest["decisions_truncated"] is True
    assert len(latest["decisions"]) == 100
    assert {
        reason["code"]
        for decision in latest["decisions"]
        for reason in decision["reasons"]
    } == {"working_budget"}


def test_ac4_get_is_observational_and_never_uses_mutating_boundaries(
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_policy(database)
    before_receipts = OperatorActionReceiptRepo(database).list()
    before_revisions = DeliveryAdmissionPolicyRepo(database).list_revisions(
        _product_id(database)
    )

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("observational GET crossed a mutation boundary")

    monkeypatch.setattr(AdmissionCoordinationRepo, "try_acquire", forbidden)
    monkeypatch.setattr(LinearClient, "fetch_project_issues", forbidden)
    monkeypatch.setattr(GitHubClient, "fetch_pull_request", forbidden)
    monkeypatch.setattr(GitHubClient, "fetch_branch_head", forbidden)
    monkeypatch.setattr(GitHubClient, "compare_commits", forbidden)
    monkeypatch.setattr(validation_plan_module, "calculate_validation_plan", forbidden)
    monkeypatch.setattr(CIHandoffReconciliationRepo, "record", forbidden)
    monkeypatch.setattr(AcceptanceSessionRepo, "mark_stale", forbidden)
    monkeypatch.setattr(TicketRepo, "apply_linear_status", forbidden)
    monkeypatch.setattr(
        DeliveryAdmissionPolicyService,
        "revise_current",
        forbidden,
    )

    with TestClient(_writable_app(database)) as client:
        _login(client)
        first = client.get("/api/v1/delivery-control")
        second = client.get("/api/v1/delivery-control")

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert OperatorActionReceiptRepo(database).list() == before_receipts
    assert (
        DeliveryAdmissionPolicyRepo(database).list_revisions(_product_id(database))
        == before_revisions
    )


def test_operator_amendment_get_reports_policy_without_reading_workflow(
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seeded = _seed_policy(database)
    assert seeded.policy is not None
    assert seeded.policy.approved_symphony_ceiling == 3
    original_read_text = Path.read_text

    def forbid_workflow_read(
        path: Path,
        encoding: str | None = None,
        errors: str | None = None,
    ) -> str:
        if path.name == "WORKFLOW.md":
            raise AssertionError("delivery-control API must not read WORKFLOW.md")
        return original_read_text(path, encoding=encoding, errors=errors)

    monkeypatch.setattr(Path, "read_text", forbid_workflow_read)

    with TestClient(_writable_app(database)) as client:
        _login(client)
        response = client.get("/api/v1/delivery-control")

    assert response.status_code == 200
    assert response.json()["policy"]["approved_symphony_ceiling"] == 3


def test_atlas_261_ac1_ac2_coherent_snapshot_exposes_exact_source_identities(
    database: Database,
) -> None:
    seeded = _seed_policy(database, spec=policy_spec(integration_budget=3))
    assert seeded.policy is not None
    board = _receipt(
        database,
        result=PmSyncReceiptResult.SUCCESS_STATUS_ONLY,
        finished_at=NOW + timedelta(minutes=1),
    )

    with TestClient(_writable_app(database)) as client:
        _login(client)
        response = client.get("/api/v1/delivery-control")

    assert response.status_code == 200
    payload = response.json()
    snapshot = payload["snapshot"]
    assert snapshot["status"] == "coherent"
    assert snapshot["reasons"] == []
    assert snapshot["policy_id"] == str(seeded.policy.id)
    assert snapshot["policy_revision"] == seeded.policy.revision
    assert snapshot["board"] == {
        "status": "coherent",
        "reasons": [],
        "receipt_id": str(board.id),
        "status_map_fingerprint": board.status_map_fingerprint,
        "fetched_board_fingerprint": board.fetched_board_fingerprint,
        "fetched_board_issue_count": board.fetched_board_issue_count,
        "observed_at": board.finished_at.isoformat().replace("+00:00", "Z"),
        "latest_attempt_receipt_id": str(board.id),
        "latest_attempt_result": board.result.value,
        "latest_attempt_finished_at": board.finished_at.isoformat().replace(
            "+00:00", "Z"
        ),
        "materialized_ticket_fingerprint": snapshot["board"][
            "materialized_ticket_fingerprint"
        ],
    }
    assert len(snapshot["fingerprint"]) == 64
    assert len(snapshot["policy_fingerprint"]) == 64
    assert len(snapshot["board"]["materialized_ticket_fingerprint"]) == 64
    assert snapshot["evidence"]["evidence_count"] == 0
    assert snapshot["integration"]["validation_registry_version"] == (
        "validation-registry/v1"
    )
    assert payload["occupancy"]["integration_occupancy"] == 0
    assert payload["occupancy"]["new_admission_integration_capacity"] == 3
    assert payload["ci_pending_ticket_count"] == 0


def test_atlas_261_ac2_stale_board_is_visible_and_never_available_capacity(
    database: Database,
) -> None:
    _seed_policy(database, spec=policy_spec(integration_budget=3))
    successful = _receipt(
        database,
        result=PmSyncReceiptResult.SUCCESS_ZERO_ACTION,
        finished_at=NOW + timedelta(minutes=1),
    )
    failed = _receipt(
        database,
        result=PmSyncReceiptResult.FAILED,
        finished_at=NOW + timedelta(minutes=2),
        error_summary="credential=must-not-project /workspace/private traceback",
    )

    with TestClient(_writable_app(database)) as client:
        _login(client)
        response = client.get("/api/v1/delivery-control")

    payload = response.json()
    assert payload["snapshot"]["status"] == "stale"
    assert payload["snapshot"]["reasons"] == ["newer_board_refresh_unsuccessful"]
    board = payload["snapshot"]["board"]
    assert board["receipt_id"] == str(successful.id)
    assert board["latest_attempt_receipt_id"] == str(failed.id)
    assert board["latest_attempt_result"] == "failed"
    assert payload["occupancy"]["new_admission_integration_capacity"] == 0
    assert payload["occupancy"]["new_admission_working_capacity"] == 0
    assert "must-not-project" not in response.text
    assert "/workspace/private" not in response.text


def test_atlas_261_ac1_over_capacity_and_protected_lane_owners_are_explicit(
    database: Database,
) -> None:
    _seed_policy(database, spec=policy_spec(integration_budget=1))
    _receipt(
        database,
        result=PmSyncReceiptResult.SUCCESS_ZERO_ACTION,
        finished_at=NOW + timedelta(minutes=1),
    )
    for number in (901, 902):
        _ticket(
            database,
            key=f"ATLAS-{number}",
            status="ci_pending",
            component="delivery-control",
        )

    with TestClient(_writable_app(database)) as client:
        _login(client)
        response = client.get("/api/v1/delivery-control")

    payload = response.json()
    occupancy = payload["occupancy"]
    assert occupancy["integration_occupancy"] == 2
    assert occupancy["new_admission_integration_capacity"] == 0
    protected = {lane["lane"]: lane for lane in occupancy["protected_lane_occupancy"]}
    assert protected["operator-admission-hotspot"] == {
        "lane": "operator-admission-hotspot",
        "count": 2,
        "limit": 1,
        "ticket_keys": ["ATLAS-901", "ATLAS-902"],
        "operator_declared": True,
    }
    assert {
        (reason["dimension"], reason["selector"])
        for reason in occupancy["over_capacity_reasons"]
    } >= {
        ("integration", None),
        ("protected_lane", "operator-admission-hotspot"),
    }
    assert payload["snapshot"]["status"] == "indeterminate"
    assert set(payload["snapshot"]["reasons"]) >= {
        "ci_reconciliation_unavailable",
        "validation_plan_provenance_unavailable",
        "exact_base_assessment_unavailable",
    }


def test_atlas_261_ac1_ac5_ci_failures_waits_and_evidence_are_typed_and_secret_free(
    database: Database,
) -> None:
    _seed_policy(database, spec=policy_spec(integration_budget=3))
    _receipt(
        database,
        result=PmSyncReceiptResult.SUCCESS_ZERO_ACTION,
        finished_at=NOW + timedelta(minutes=1),
    )
    failed_ticket = _ticket(database, key="ATLAS-910", status="ci_pending")
    pending_ticket = _ticket(database, key="ATLAS-911", status="ci_pending")
    secret = "provider-token=ci-secret /workspace/agent raw-command-output"
    evidence = EvidenceRepo(database).add(
        Evidence(
            id=uuid4(),
            product_id=_product_id(database),
            ticket_id=failed_ticket.id,
            evidence_type=EvidenceType.TEST_RESULT,
            status=EvidenceStatus.FAILED,
            summary=secret,
            commit_sha="2" * 40,
            external_run_id=f"run-{secret}",
            job_name="tests",
            source_event_at=NOW + timedelta(minutes=1),
            payload_hash="e" * 64,
            source_uri=f"https://example.invalid/{secret}",
            raw_payload={"secret": secret, "logs": [secret]},
            created_by_type=ActorType.SYSTEM,
            created_by_id="github",
            created_at=NOW + timedelta(minutes=1),
        )
    )
    failed = _seed_ci_reconciliation(
        database,
        ticket=failed_ticket,
        classification=CIHandoffClassification.IMPLEMENTATION_FAILURE,
        reason=CIHandoffReason.COMPLETE_IMPLEMENTATION_FAILURE,
        decision=CIHandoffDecision.CHANGES_REQUESTED,
        status=EvidenceStatus.FAILED,
        evidence_ids=(evidence.id,),
        pr_number=410,
    )
    pending = _seed_ci_reconciliation(
        database,
        ticket=pending_ticket,
        classification=CIHandoffClassification.PENDING,
        reason=CIHandoffReason.REQUIRED_CHECKS_PENDING,
        decision=CIHandoffDecision.HOLD,
        status=EvidenceStatus.PENDING,
        pr_number=411,
    )

    with TestClient(_writable_app(database)) as client:
        _login(client)
        response = client.get("/api/v1/delivery-control")

    payload = response.json()
    assert payload["ci_pending_ticket_count"] == 2
    tickets = {item["ticket_key"]: item for item in payload["ci_pending_tickets"]}
    failed_item = tickets[failed_ticket.key]
    assert failed_item["outcome"]["reconciliation_id"] == str(failed.id)
    assert failed_item["outcome"]["classification"] == "implementation_failure"
    assert failed_item["outcome"]["decision"] == "changes_requested"
    assert failed_item["outcome"]["reason"] == "complete_implementation_failure"
    assert failed_item["outcome"]["check_results"] == [
        {
            "check_type": "tests",
            "status": "failed",
            "classification": "implementation_failure",
            "evidence_count": 1,
            "evidence_ids": [str(evidence.id)],
            "evidence_ids_truncated": False,
        }
    ]
    assert failed_item["validation_plan"]["status"] == "indeterminate"
    assert failed_item["validation_plan"]["head_sha"] == "2" * 40
    assert failed_item["validation_plan"]["reasons"] == [
        "validation_plan_provenance_unavailable"
    ]
    assert failed_item["exact_base"]["status"] == "indeterminate"
    pending_item = tickets[pending_ticket.key]
    assert pending_item["outcome"]["reconciliation_id"] == str(pending.id)
    assert pending_item["outcome"]["classification"] == "pending"
    assert pending_item["outcome"]["decision"] == "hold"
    assert pending_item["outcome"]["reason"] == "required_checks_pending"
    assert payload["snapshot"]["evidence"]["evidence_ids"] == [str(evidence.id)]
    assert secret not in response.text
    assert "raw_payload" not in response.text
    assert "command-output" not in response.text


def test_atlas_261_ac2_ci_outcome_is_pinned_to_the_current_ci_pending_episode(
    database: Database,
) -> None:
    _seed_policy(database, spec=policy_spec(integration_budget=2))
    _receipt(
        database,
        result=PmSyncReceiptResult.SUCCESS_ZERO_ACTION,
        finished_at=NOW + timedelta(minutes=1),
    )
    ticket = _ticket(database, key="ATLAS-912", status="ci_pending")
    old_head = "4" * 40
    new_head = "5" * 40
    old_evidence = EvidenceRepo(database).add(
        Evidence(
            id=uuid4(),
            product_id=_product_id(database),
            ticket_id=ticket.id,
            evidence_type=EvidenceType.TEST_RESULT,
            status=EvidenceStatus.FAILED,
            summary="historical failure",
            commit_sha=old_head,
            external_run_id="historical-run",
            job_name="tests",
            source_event_at=NOW + timedelta(minutes=1),
            payload_hash="6" * 64,
            source_uri="https://example.invalid/historical-run",
            raw_payload={"historical": True},
            created_by_type=ActorType.SYSTEM,
            created_by_id="github",
            created_at=NOW + timedelta(minutes=1),
        )
    )
    old_reconciliation = _seed_ci_reconciliation(
        database,
        ticket=ticket,
        classification=CIHandoffClassification.IMPLEMENTATION_FAILURE,
        reason=CIHandoffReason.COMPLETE_IMPLEMENTATION_FAILURE,
        decision=CIHandoffDecision.CHANGES_REQUESTED,
        status=EvidenceStatus.FAILED,
        evidence_ids=(old_evidence.id,),
        head_sha=old_head,
        pr_number=412,
        observed_at=NOW + timedelta(minutes=1),
    )
    old_assessment = _seed_acceptance_assessment(
        database,
        ticket_key=ticket.key,
        head_sha=old_head,
        pr_number=old_reconciliation.pr_number,
        observed_at=NOW + timedelta(minutes=1),
    )

    tickets = TicketRepo(database)
    tickets.apply_linear_status(
        ticket.key,
        TicketStatus.CHANGES_REQUESTED,
        now=NOW + timedelta(minutes=2),
        created_by_id="atlas.pm.ci_handoff",
    )
    AcceptanceSessionRepo(database).mark_stale(
        old_assessment.id,
        (AcceptanceSessionBlockingReason.HEAD_SHA_MISMATCH,),
        staled_at=NOW + timedelta(minutes=2),
    )
    for status, minute in (
        (TicketStatus.IN_PROGRESS, 3),
        (TicketStatus.PR_OPEN, 4),
        (TicketStatus.CI_PENDING, 5),
    ):
        tickets.apply_linear_status(
            ticket.key,
            status,
            now=NOW + timedelta(minutes=minute),
            created_by_id="atlas.pm.sync",
        )

    with TestClient(_writable_app(database)) as client:
        _login(client)
        before_new_reconciliation = client.get("/api/v1/delivery-control")

    stale_payload = before_new_reconciliation.json()
    [stale_item] = stale_payload["ci_pending_tickets"]
    assert stale_item["ticket_key"] == ticket.key
    assert {
        "repository_owner": stale_item["repository_owner"],
        "repository_name": stale_item["repository_name"],
        "pr_number": stale_item["pr_number"],
        "head_sha": stale_item["head_sha"],
    } == {
        "repository_owner": None,
        "repository_name": None,
        "pr_number": None,
        "head_sha": None,
    }
    assert stale_item["outcome"] == {
        "reconciliation_id": None,
        "classification": "indeterminate",
        "decision": "hold",
        "reason": None,
        "observed_at": None,
        "check_results": [],
        "projection_reasons": ["ci_reconciliation_unavailable"],
    }
    assert stale_item["validation_plan"]["head_sha"] is None
    assert stale_item["exact_base"]["assessment_id"] is None
    assert stale_item["exact_base"]["head_sha"] is None
    integration = stale_payload["snapshot"]["integration"]
    assert integration["reconciliation_count"] == 0
    assert integration["reconciliation_ids"] == []
    assert integration["acceptance_session_count"] == 0
    assert integration["acceptance_session_ids"] == []
    assert stale_payload["snapshot"]["evidence"]["evidence_count"] == 0
    assert stale_payload["snapshot"]["evidence"]["evidence_ids"] == []

    new_reconciliation = _seed_ci_reconciliation(
        database,
        ticket=ticket,
        classification=CIHandoffClassification.PENDING,
        reason=CIHandoffReason.REQUIRED_CHECKS_PENDING,
        decision=CIHandoffDecision.HOLD,
        status=EvidenceStatus.PENDING,
        head_sha=new_head,
        pr_number=old_reconciliation.pr_number,
        observed_at=NOW + timedelta(minutes=6),
    )
    new_assessment = _seed_acceptance_assessment(
        database,
        ticket_key=ticket.key,
        head_sha=new_head,
        pr_number=new_reconciliation.pr_number,
        observed_at=NOW + timedelta(minutes=6),
        identity_seed="e",
    )

    with TestClient(_writable_app(database)) as client:
        _login(client)
        after_new_reconciliation = client.get("/api/v1/delivery-control")

    current_payload = after_new_reconciliation.json()
    [current_item] = current_payload["ci_pending_tickets"]
    assert current_item["head_sha"] == new_head
    assert current_item["outcome"]["reconciliation_id"] == str(new_reconciliation.id)
    assert current_item["outcome"]["classification"] == "pending"
    assert current_item["exact_base"]["status"] == "exact_branch"
    assert current_item["exact_base"]["assessment_id"] == str(new_assessment.id)
    assert current_payload["snapshot"]["integration"]["reconciliation_ids"] == [
        str(new_reconciliation.id)
    ]
    assert current_payload["snapshot"]["integration"]["acceptance_session_ids"] == [
        str(new_assessment.id)
    ]


def test_atlas_261_ac2_unknown_ci_episode_boundary_rejects_reconciliation(
    database: Database,
) -> None:
    _seed_policy(database, spec=policy_spec(integration_budget=1))
    _receipt(
        database,
        result=PmSyncReceiptResult.SUCCESS_ZERO_ACTION,
        finished_at=NOW + timedelta(minutes=1),
    )
    ticket = _ticket(
        database,
        key="ATLAS-913",
        status="ci_pending",
        status_entered_at=None,
    )
    _seed_ci_reconciliation(
        database,
        ticket=ticket,
        classification=CIHandoffClassification.PASSED,
        reason=CIHandoffReason.COMPLETE_REQUIRED_CHECKS_PASSED,
        decision=CIHandoffDecision.REVIEW_REQUIRED,
        status=EvidenceStatus.PASSED,
    )

    with TestClient(_writable_app(database)) as client:
        _login(client)
        response = client.get("/api/v1/delivery-control")

    [item] = response.json()["ci_pending_tickets"]
    assert item["outcome"]["reconciliation_id"] is None
    assert item["outcome"]["classification"] == "indeterminate"
    assert item["outcome"]["projection_reasons"] == ["ci_reconciliation_unavailable"]
    assert item["head_sha"] is None
    assert item["exact_base"]["assessment_id"] is None


@pytest.mark.parametrize(
    ("blocking_reason", "expected_status", "expected_reason"),
    [
        (
            AcceptanceSessionBlockingReason.INTEGRATION_BEHIND,
            "rebase_required",
            "integration_behind",
        ),
        (
            AcceptanceSessionBlockingReason.INTEGRATION_DIVERGED,
            "rebase_required",
            "integration_diverged",
        ),
        (
            AcceptanceSessionBlockingReason.INTEGRATION_CONFLICTED,
            "rebase_required",
            "integration_conflicted",
        ),
        (
            AcceptanceSessionBlockingReason.INTEGRATION_INDETERMINATE,
            "indeterminate",
            "integration_indeterminate",
        ),
    ],
)
def test_atlas_261_ac1_stored_exact_base_states_are_not_actions(
    database: Database,
    blocking_reason: AcceptanceSessionBlockingReason,
    expected_status: str,
    expected_reason: str,
) -> None:
    _seed_policy(database, spec=policy_spec(integration_budget=2))
    _receipt(
        database,
        result=PmSyncReceiptResult.SUCCESS_ZERO_ACTION,
        finished_at=NOW + timedelta(minutes=1),
    )
    ticket = _ticket(database, key="ATLAS-920", status="ci_pending")
    reconciliation = _seed_ci_reconciliation(
        database,
        ticket=ticket,
        classification=CIHandoffClassification.PENDING,
        reason=CIHandoffReason.REQUIRED_CHECKS_PENDING,
        decision=CIHandoffDecision.HOLD,
        status=EvidenceStatus.PENDING,
        head_sha="3" * 40,
        pr_number=420,
    )
    session = _seed_acceptance_assessment(
        database,
        ticket_key=ticket.key,
        head_sha=reconciliation.head_commit,
        pr_number=reconciliation.pr_number,
    )

    with TestClient(_writable_app(database)) as client:
        _login(client)
        current = client.get("/api/v1/delivery-control")

    exact = current.json()["ci_pending_tickets"][0]["exact_base"]
    assert exact == {
        "status": "exact_branch",
        "assessment_id": str(session.id),
        "head_sha": reconciliation.head_commit,
        "base_sha": "1" * 40,
        "observed_at": NOW.isoformat().replace("+00:00", "Z"),
        "reasons": [],
    }

    AcceptanceSessionRepo(database).mark_stale(
        session.id,
        (blocking_reason,),
        staled_at=NOW + timedelta(minutes=3),
    )
    with TestClient(_writable_app(database)) as client:
        _login(client)
        stale = client.get("/api/v1/delivery-control")

    rebase = stale.json()["ci_pending_tickets"][0]["exact_base"]
    assert rebase["status"] == expected_status
    assert rebase["reasons"] == [expected_reason]
    assert rebase["assessment_id"] == str(session.id)


@pytest.mark.parametrize(
    ("case", "changes", "expected_status"),
    [
        ("missing-session", {"session": False}, 401),
        ("missing-csrf", {"csrf": ""}, 403),
        ("hostile-origin", {"origin": "http://evil.test"}, 403),
        ("host-confusion", {"host": "evil.test"}, 403),
        ("missing-idempotency", {"idempotency_key": None}, 422),
        ("blank-idempotency", {"idempotency_key": "   "}, 422),
        ("non-strict-json", {"content_type": "application/json; charset=utf-8"}, 415),
    ],
)
def test_ac3_policy_security_fails_before_the_one_service_call(
    database: Database,
    case: str,
    changes: dict[str, object],
    expected_status: int,
) -> None:
    del case
    seeded = _seed_policy(database)
    service = RecordingPolicyService(seeded)
    with TestClient(_writable_app(database, service)) as client:
        csrf_token = _login(client)
        if changes.get("session") is False:
            client.cookies.clear()
        response = client.post(
            "/api/v1/delivery-control/policy",
            json=_policy_body(),
            headers=_mutation_headers(
                str(changes.get("csrf", csrf_token)),
                idempotency_key=cast(
                    str | None,
                    changes.get("idempotency_key", "security-policy-command"),
                ),
                host=str(changes.get("host", LOOPBACK_HOST)),
                origin=str(changes.get("origin", LOOPBACK_ORIGIN)),
                content_type=str(changes.get("content_type", "application/json")),
            ),
        )

    assert response.status_code == expected_status
    assert service.calls == []


@pytest.mark.parametrize(
    "body",
    [
        _policy_body(actor="attacker"),
        _policy_body(product_id=str(uuid4())),
        _policy_body(action="dispatch"),
        _policy_body(current_policy={}),
        {
            key: value
            for key, value in _policy_body().items()
            if key != "risk_lane_limits"
        },
        _policy_body(working_budget=True),
        _policy_body(integration_budget=True),
        _policy_body(integration_budget=0),
        _policy_body(integration_budget=11),
        _policy_body(
            component_lane_limits=[
                {"component": "Atlas.API", "limit": 1},
                {"component": " atlas.api ", "limit": 1},
            ]
        ),
    ],
)
def test_ac2_complete_strict_policy_rejects_client_owned_or_invalid_fields(
    database: Database,
    body: dict[str, object],
) -> None:
    secret = "client-owned-secret-value"
    seeded = _seed_policy(database)
    service = RecordingPolicyService(seeded)
    if "actor" in body:
        body["actor"] = secret
    with TestClient(_writable_app(database, service)) as client:
        csrf_token = _login(client)
        response = client.post(
            "/api/v1/delivery-control/policy",
            json=body,
            headers=_mutation_headers(csrf_token),
        )

    assert response.status_code == 422
    assert service.calls == []
    assert secret not in response.text


@pytest.mark.parametrize(
    "body",
    [
        _policy_body(
            risk_lane_limits=[
                {
                    "risk_level": "high",
                    "limit": 1,
                    "actor": "nested-client-owned-secret",
                }
            ]
        ),
        _policy_body(
            component_lane_limits=[
                {
                    "component": "atlas.api",
                    "limit": 1,
                    "current_state": "nested-client-owned-secret",
                }
            ]
        ),
    ],
)
def test_ac2_complete_policy_rejects_unknown_nested_lane_fields_before_service(
    database: Database,
    body: dict[str, object],
) -> None:
    seeded = _seed_policy(database)
    service = RecordingPolicyService(seeded)
    with TestClient(_writable_app(database, service)) as client:
        csrf_token = _login(client)
        response = client.post(
            "/api/v1/delivery-control/policy",
            json=body,
            headers=_mutation_headers(csrf_token),
        )

    assert response.status_code == 422
    assert service.calls == []
    assert "nested-client-owned-secret" not in response.text


def test_ac2_ac3_post_calls_policy_service_once_with_only_validated_policy(
    database: Database,
) -> None:
    seeded = _seed_policy(database)
    service = RecordingPolicyService(seeded)
    raw_key = "one-call-raw-idempotency-key"
    with TestClient(_writable_app(database, service)) as client:
        csrf_token = _login(client)
        response = client.post(
            "/api/v1/delivery-control/policy",
            json=_policy_body(),
            headers=_mutation_headers(csrf_token, idempotency_key=raw_key),
        )

    assert response.status_code == 200
    assert len(service.calls) == 1
    assert set(service.calls[0]) == {
        "expected_revision",
        "idempotency_key",
        "policy",
    }
    assert service.calls[0]["expected_revision"] == 1
    assert service.calls[0]["idempotency_key"] == raw_key
    assert isinstance(service.calls[0]["policy"], DeliveryAdmissionPolicySpec)
    assert response.json()["receipt"]["actor"] == {
        "type": "human",
        "id": "operator",
    }
    assert raw_key not in response.text
    assert csrf_token not in response.text


def test_ac3_policy_success_replay_stale_and_altered_replay_are_fail_closed(
    database: Database,
) -> None:
    _seed_policy(database)
    product_id = _product_id(database)
    with TestClient(_writable_app(database)) as client:
        csrf_token = _login(client)
        success = client.post(
            "/api/v1/delivery-control/policy",
            json=_policy_body(),
            headers=_mutation_headers(
                csrf_token,
                idempotency_key="successful-policy-replacement",
            ),
        )
        replay = client.post(
            "/api/v1/delivery-control/policy",
            json=_policy_body(),
            headers=_mutation_headers(
                csrf_token,
                idempotency_key="successful-policy-replacement",
            ),
        )
        altered_replay = client.post(
            "/api/v1/delivery-control/policy",
            json=_policy_body(expected_revision=2, mode="draining"),
            headers=_mutation_headers(
                csrf_token,
                idempotency_key="successful-policy-replacement",
            ),
        )
        stale = client.post(
            "/api/v1/delivery-control/policy",
            json=_policy_body(expected_revision=1, mode="draining"),
            headers=_mutation_headers(
                csrf_token,
                idempotency_key="stale-policy-replacement",
            ),
        )

    assert success.status_code == replay.status_code == 200
    assert success.json() == replay.json()
    payload = success.json()
    assert payload["policy"]["revision"] == 2
    assert payload["receipt"]["action"] == "delivery_admission_policy.revise"
    assert payload["receipt"]["target"] == {
        "type": "product",
        "id": str(product_id),
    }
    assert payload["receipt"]["actor"] == {"type": "human", "id": "operator"}
    assert altered_replay.status_code == 409
    assert altered_replay.json()["conflict_code"] == "idempotency_key_reused"
    assert stale.status_code == 409
    assert stale.json()["conflict_code"] == "stale_revision"
    assert stale.json()["current_policy"]["revision"] == 2
    assert len(DeliveryAdmissionPolicyRepo(database).list_revisions(product_id)) == 2
    assert len(OperatorActionReceiptRepo(database).list()) == 3


@pytest.mark.parametrize(
    ("code", "expected_detail"),
    [
        (
            DeliveryAdmissionPolicyConflictCode.IN_PROGRESS,
            "idempotent command is still in progress",
        ),
        (
            DeliveryAdmissionPolicyConflictCode.IDEMPOTENCY_KEY_REUSED,
            "idempotency key conflicts with an existing command",
        ),
        (
            DeliveryAdmissionPolicyConflictCode.STALE_REVISION,
            "expected policy revision is stale",
        ),
    ],
)
def test_ac3_presenter_maps_every_policy_conflict_without_internal_detail(
    database: Database,
    code: DeliveryAdmissionPolicyConflictCode,
    expected_detail: str,
) -> None:
    seeded = _seed_policy(database)
    service = RecordingPolicyService(
        DeliveryAdmissionPolicyChangeResult(
            status=DeliveryAdmissionPolicyChangeStatus.CONFLICT,
            current_policy=seeded.policy,
            conflict_code=code,
        )
    )
    with TestClient(_writable_app(database, service)) as client:
        csrf_token = _login(client)
        response = client.post(
            "/api/v1/delivery-control/policy",
            json=_policy_body(),
            headers=_mutation_headers(csrf_token),
        )

    assert response.status_code == 409
    assert response.json()["detail"] == expected_detail
    assert response.json()["current_policy"]["revision"] == 1


@pytest.mark.parametrize(
    ("result_status", "expected_status", "expected_detail"),
    [
        (
            DeliveryAdmissionPolicyChangeStatus.REFUSED,
            409,
            "policy replacement was refused",
        ),
        (
            DeliveryAdmissionPolicyChangeStatus.FAILED,
            500,
            "policy replacement failed",
        ),
    ],
)
def test_ac3_presenter_maps_refusal_and_failure_without_internal_material(
    database: Database,
    result_status: DeliveryAdmissionPolicyChangeStatus,
    expected_status: int,
    expected_detail: str,
) -> None:
    _seed_policy(database)
    service = RecordingPolicyService(
        DeliveryAdmissionPolicyChangeResult(status=result_status)
    )
    with TestClient(_writable_app(database, service)) as client:
        csrf_token = _login(client)
        response = client.post(
            "/api/v1/delivery-control/policy",
            json=_policy_body(),
            headers=_mutation_headers(csrf_token),
        )

    assert response.status_code == expected_status
    assert response.json()["detail"] == expected_detail
    assert "exception" not in response.text.lower()


def test_ac6_route_inventory_rejects_worker_ticket_and_ceiling_control(
    database: Database,
) -> None:
    app = _writable_app(database)
    document = app.openapi()
    delivery_operations = {
        (method.upper(), path)
        for path, operations in document["paths"].items()
        if path.startswith("/api/v1/delivery-control")
        for method in operations
    }
    assert delivery_operations == {
        ("GET", "/api/v1/delivery-control"),
        ("POST", "/api/v1/delivery-control/policy"),
    }

    forbidden_fragments = {
        "branch-update",
        "ci-retry",
        "ticket-status",
        "dispatch",
        "cancel",
        "merge",
        "rebase",
        "automatic-ceiling",
        "agent-session",
    }
    assert not any(
        fragment in path
        for path in document["paths"]
        for fragment in forbidden_fragments
    )
    assert all(
        method not in {"patch", "put"}
        for operations in document["paths"].values()
        for method in operations
    )

    with TestClient(app) as client:
        for method, path in (
            ("PATCH", "/api/v1/delivery-control/policy"),
            ("PUT", "/api/v1/delivery-control/policy"),
            ("POST", "/api/v1/delivery-control/automatic-ceiling"),
            ("POST", "/api/v1/delivery-control/dispatch"),
            ("POST", "/api/v1/delivery-control/ci/410/retry"),
            ("POST", "/api/v1/delivery-control/ci/410/cancel"),
            ("POST", "/api/v1/delivery-control/branches/main/update"),
            ("POST", "/api/v1/delivery-control/branches/main/rebase"),
            ("POST", "/api/v1/delivery-control/merge"),
            ("POST", "/api/v1/delivery-control/workers/1/cancel"),
            ("POST", "/api/v1/tickets/ATL-423/status"),
            ("POST", "/api/v1/tickets/ATL-423/transition"),
            ("POST", "/api/v1/reviews/1/merge"),
            ("POST", "/api/v1/reviews/1/rebase"),
        ):
            assert client.request(method, path).status_code in {404, 405}


def test_ac7_openapi_pins_authentication_strict_policy_and_bounded_enums(
    database: Database,
) -> None:
    document = _writable_app(database).openapi()
    read = document["paths"]["/api/v1/delivery-control"]["get"]
    write = document["paths"]["/api/v1/delivery-control/policy"]["post"]
    schemas = document["components"]["schemas"]

    assert read["security"] == [{"AtlasSessionCookie": []}]
    assert write["security"] == [{"AtlasSessionCookie": [], "AtlasCSRFToken": []}]
    idempotency = [
        parameter
        for parameter in write["parameters"]
        if parameter["name"] == "Idempotency-Key"
    ]
    assert len(idempotency) == 1
    assert idempotency[0]["required"] is True
    request = schemas["DeliveryAdmissionPolicyRequest"]
    assert request["additionalProperties"] is False
    assert request["properties"]["risk_lane_limits"]["items"] == {
        "$ref": "#/components/schemas/DeliveryAdmissionRiskLaneLimitRequest"
    }
    assert request["properties"]["component_lane_limits"]["items"] == {
        "$ref": "#/components/schemas/DeliveryAdmissionComponentLaneLimitRequest"
    }
    assert (
        schemas["DeliveryAdmissionRiskLaneLimitRequest"]["additionalProperties"]
        is False
    )
    assert (
        schemas["DeliveryAdmissionComponentLaneLimitRequest"]["additionalProperties"]
        is False
    )
    assert set(request["required"]) == {
        "expected_revision",
        "mode",
        "approved_symphony_ceiling",
        "working_budget",
        "integration_budget",
        "review_budget",
        "changes_requested_reserve",
        "risk_lane_limits",
        "component_lane_limits",
    }
    policy_ceiling_description = request["properties"]["approved_symphony_ceiling"][
        "description"
    ]
    assert "not an independently observed Symphony" in policy_ceiling_description
    assert (
        schemas["DeliveryAdmissionPolicySchema"]["properties"][
            "approved_symphony_ceiling"
        ]["description"]
        == policy_ceiling_description
    )
    assert set(schemas["AdmissionHoldCode"]["enum"]) >= {
        "policy_paused",
        "working_budget",
        "integration_budget",
        "review_budget",
        "single_write_limit",
    }
    assert set(schemas["AdmissionSyncReason"]["enum"]) >= {
        "write_indeterminate",
        "indeterminate_still_unresolved",
    }
    assert set(schemas["DeliveryControlSnapshotStatus"]["enum"]) == {
        "coherent",
        "stale",
        "indeterminate",
    }
    assert set(schemas["DeliveryControlExactBaseStatus"]["enum"]) == {
        "exact_branch",
        "rebase_required",
        "stale",
        "indeterminate",
    }
    response = schemas["DeliveryControlResponse"]
    assert {
        "snapshot",
        "ci_pending_ticket_count",
        "ci_pending_tickets_truncated",
        "ci_pending_tickets",
        "protected_lane_holds",
    } <= set(response["required"])
    assert response["properties"]["ci_pending_tickets"]["maxItems"] == 100
    assert response["properties"]["protected_lane_holds"]["maxItems"] == 3_200
    assert (
        schemas["DeliveryControlOccupancySchema"]["properties"][
            "protected_lane_occupancy"
        ]["maxItems"]
        == 32
    )
    assert (
        schemas["DeliveryControlCICheckSchema"]["properties"]["evidence_ids"][
            "maxItems"
        ]
        == 32
    )
    rank_inputs = schemas["DeliveryControlRankInputsSchema"]
    assert rank_inputs["additionalProperties"] is False
    assert set(rank_inputs["required"]) == {
        "unlock_count",
        "critical_path_member",
        "critical_path_position",
        "priority",
        "risk_level",
        "risk_severity",
        "continuously_eligible_since",
        "continuously_eligible_age_microseconds",
    }
    assert GOOD_TOKEN not in str(document)


def test_read_only_app_does_not_mount_authenticated_delivery_control(
    database: Database,
) -> None:
    document = create_app(database=database).openapi()

    assert not any("delivery-control" in path for path in document["paths"])
