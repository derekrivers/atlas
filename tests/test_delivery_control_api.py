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
from atlas.core.enums import ActorType, RiskLevel
from atlas.core.models import (
    AdmissionRun,
    PmSyncReceipt,
    PmSyncReceiptResult,
    Ticket,
)
from atlas.core.models.admission_run import (
    AdmissionCandidateDecision,
    AdmissionDecisionType,
    AdmissionHoldCode,
    AdmissionHoldReason,
    AdmissionRankInputs,
)
from atlas.core.models.delivery_admission_policy import DeliveryAdmissionPolicySpec
from atlas.linear.client import LinearClient
from atlas.orchestration import (
    DeliveryAdmissionPolicyChangeResult,
    DeliveryAdmissionPolicyChangeStatus,
    DeliveryAdmissionPolicyConflictCode,
    DeliveryAdmissionPolicyService,
)
from atlas.pm import SnapshotIncompletenessCode, delivery_policy_fingerprint
from atlas.storage import (
    AdmissionCoordinationRepo,
    AdmissionRunRepo,
    Database,
    DeliveryAdmissionPolicyRepo,
    OperatorActionReceiptRepo,
    PmSyncReceiptRepo,
    ProductRepo,
    TicketRepo,
)

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
                "created_at": NOW,
                "updated_at": NOW,
            }
        )
    )


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
        "risk_lane",
        "snapshot_incomplete",
    ]
    assert reasons[1]["source_code"] == "missing_joined_issue"
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
            ("POST", "/api/v1/tickets/ATL-423/status"),
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
