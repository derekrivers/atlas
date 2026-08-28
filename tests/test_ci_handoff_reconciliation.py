"""ATLAS-256 exact-head, system-tier CI handoff reconciliation."""

from __future__ import annotations

import ast
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from github_fakes import FakeGitHubClient
from test_models_validation import NOW
from test_pm_sync import (
    CHANGES_REQUESTED_STATE,
    CI_PENDING_STATE,
    PR_OPEN_STATE,
    PROJECT_ID,
    REVIEW_REQUIRED_STATE,
    RecordingClient,
    seed_ticket,
    status_map,
)

from atlas.core.enums import ActorType, EvidenceStatus
from atlas.core.models import (
    CIHandoffClassification,
    CIHandoffDecision,
    Evidence,
    EvidenceType,
)
from atlas.core.models.ticket import TicketStatus
from atlas.linear.client import LinearAPIError, LinearIssue
from atlas.pm import CIHandoffHooks, reconcile_ci_handoff
from atlas.storage import (
    AdmissionCoordinationRepo,
    CIHandoffCoordinationRepo,
    CIHandoffReconciliationRepo,
    Database,
    EvidenceRepo,
    TicketRepo,
)
from atlas.storage.tables import (
    AdmissionLeaseRow,
    CIHandoffReconciliationRow,
    DeliveryAdmissionPolicyActiveRow,
    DeliveryAdmissionPolicyRevisionRow,
    TicketRow,
)
from atlas.verification import evaluate_ci_handoff

HEAD = "a" * 40
OTHER_HEAD = "b" * 40
PRODUCT_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{tmp_path}/atlas.db")
    database.create_all()
    return database


def pr_payload(head: str = HEAD) -> dict[str, Any]:
    return {
        "number": 434,
        "head": {"sha": head},
        "base": {"repo": {"full_name": "acme/atlas"}},
    }


def ci_evidence(
    ticket: Any,
    evidence_type: EvidenceType,
    *,
    status: EvidenceStatus = EvidenceStatus.PASSED,
    conclusion: str | None = "success",
    head: str = HEAD,
    source_event_at: Any = NOW,
    suffix: str = "1",
) -> Evidence:
    job = {
        EvidenceType.TEST_RESULT: "test-python",
        EvidenceType.LINT_RESULT: "lint-python",
    }[evidence_type]
    return Evidence(
        id=uuid4(),
        product_id=ticket.product_id,
        evidence_type=evidence_type,
        status=status,
        summary=f"{job}: {status.value}",
        commit_sha=head,
        external_run_id=f"{job}-{suffix}",
        job_name=job,
        source_event_at=source_event_at,
        payload_hash=(suffix * 64)[:64],
        raw_payload={"conclusion": conclusion},
        created_by_type=ActorType.SYSTEM,
        created_by_id="github-actions",
        created_at=NOW,
    )


def documentation_evidence(
    ticket: Any,
    *,
    docs_paths: tuple[str, ...] | None,
    raw_payload: dict[str, Any],
    suffix: str,
) -> Evidence:
    external_run_id = (
        f"docs:v2:{HEAD}" if docs_paths is not None else f"docs:{HEAD}:{suffix}"
    )
    return Evidence(
        id=uuid4(),
        product_id=ticket.product_id,
        evidence_type=EvidenceType.DOCUMENTATION_UPDATE,
        status=EvidenceStatus.PASSED,
        summary="documentation update",
        commit_sha=HEAD,
        external_run_id=external_run_id,
        payload_hash=(suffix * 64)[:64],
        raw_payload=raw_payload,
        docs_paths=docs_paths,
        created_by_type=ActorType.SYSTEM,
        created_by_id="github-actions",
        created_at=NOW,
    )


def seed_ci_pending(db: Database, client: RecordingClient) -> Any:
    return seed_ticket(
        db,
        client,
        key="ATLAS-256",
        product_id=PRODUCT_ID,
        status=TicketStatus.CI_PENDING,
        issue_state=CI_PENDING_STATE,
        acceptance_criteria=["bounded transition"],
        linear_synced_at=NOW,
    )


def add_passed_ci(db: Database, ticket: Any) -> None:
    EvidenceRepo(db).add(ci_evidence(ticket, EvidenceType.TEST_RESULT, suffix="1"))
    EvidenceRepo(db).add(ci_evidence(ticket, EvidenceType.LINT_RESULT, suffix="2"))


def run(
    db: Database,
    client: RecordingClient,
    github: FakeGitHubClient,
    *,
    hooks: CIHandoffHooks | None = None,
) -> Any:
    return reconcile_ci_handoff(
        db=db,
        tickets=TicketRepo(db),
        github=github,
        linear=client,
        status_map=status_map(),
        project_id=PROJECT_ID,
        initial_issues=client.fetch_project_issues(PROJECT_ID),
        ticket_key="ATLAS-256",
        repository_owner="acme",
        repository_name="atlas",
        pr_number=434,
        expected_head=HEAD,
        now=NOW,
        hooks=hooks,
    )


def test_ac1_every_evidence_class_is_distinct_and_partial_failure_cannot_route(
    db: Database,
) -> None:
    client = RecordingClient()
    ticket = seed_ci_pending(db, client)

    cases = {
        "passed": (
            [
                ci_evidence(ticket, EvidenceType.TEST_RESULT, suffix="1"),
                ci_evidence(ticket, EvidenceType.LINT_RESULT, suffix="2"),
            ],
            CIHandoffClassification.PASSED,
        ),
        "implementation-failure": (
            [
                ci_evidence(
                    ticket,
                    EvidenceType.TEST_RESULT,
                    status=EvidenceStatus.FAILED,
                    conclusion="failure",
                    suffix="3",
                ),
                ci_evidence(ticket, EvidenceType.LINT_RESULT, suffix="4"),
            ],
            CIHandoffClassification.IMPLEMENTATION_FAILURE,
        ),
        "pending": (
            [
                ci_evidence(
                    ticket,
                    EvidenceType.TEST_RESULT,
                    status=EvidenceStatus.PENDING,
                    conclusion=None,
                    suffix="5",
                ),
                ci_evidence(ticket, EvidenceType.LINT_RESULT, suffix="6"),
            ],
            CIHandoffClassification.PENDING,
        ),
        "missing": (
            [ci_evidence(ticket, EvidenceType.LINT_RESULT, suffix="7")],
            CIHandoffClassification.MISSING,
        ),
        "infrastructure": (
            [
                ci_evidence(
                    ticket,
                    EvidenceType.TEST_RESULT,
                    status=EvidenceStatus.FAILED,
                    conclusion="timed_out",
                    suffix="8",
                ),
                ci_evidence(ticket, EvidenceType.LINT_RESULT, suffix="9"),
            ],
            CIHandoffClassification.INFRASTRUCTURE,
        ),
        "stale": (
            [
                ci_evidence(
                    ticket, EvidenceType.TEST_RESULT, head=OTHER_HEAD, suffix="a"
                ),
                ci_evidence(
                    ticket, EvidenceType.LINT_RESULT, head=OTHER_HEAD, suffix="b"
                ),
            ],
            CIHandoffClassification.STALE,
        ),
        "malformed": (
            [
                ci_evidence(
                    ticket,
                    EvidenceType.TEST_RESULT,
                    source_event_at=None,
                    suffix="c",
                ),
                ci_evidence(ticket, EvidenceType.LINT_RESULT, suffix="d"),
            ],
            CIHandoffClassification.MALFORMED,
        ),
        "indeterminate": (
            [
                ci_evidence(
                    ticket,
                    EvidenceType.TEST_RESULT,
                    status=EvidenceStatus.FAILED,
                    conclusion="provider_unknown",
                    suffix="e",
                ),
                ci_evidence(ticket, EvidenceType.LINT_RESULT, suffix="f"),
            ],
            CIHandoffClassification.INDETERMINATE,
        ),
        "partial-failure": (
            [
                ci_evidence(
                    ticket,
                    EvidenceType.TEST_RESULT,
                    status=EvidenceStatus.FAILED,
                    conclusion="failure",
                    suffix="0",
                )
            ],
            CIHandoffClassification.MISSING,
        ),
    }

    for evidence, expected in cases.values():
        assessment = evaluate_ci_handoff(ticket, head_commit=HEAD, evidence=evidence)
        assert assessment.classification is expected

    unknown = evaluate_ci_handoff(
        ticket, head_commit=HEAD, evidence=cases["indeterminate"][0]
    )
    assert unknown.reason.value == "indeterminate_evidence"


def test_ac1_tied_current_observations_are_contradictory_not_actionable(
    db: Database,
) -> None:
    client = RecordingClient()
    ticket = seed_ci_pending(db, client)
    evidence = [
        ci_evidence(ticket, EvidenceType.TEST_RESULT, suffix="1"),
        ci_evidence(
            ticket,
            EvidenceType.TEST_RESULT,
            status=EvidenceStatus.FAILED,
            conclusion="failure",
            suffix="2",
        ),
        ci_evidence(ticket, EvidenceType.LINT_RESULT, suffix="3"),
    ]

    assessment = evaluate_ci_handoff(ticket, head_commit=HEAD, evidence=evidence)

    assert assessment.classification is CIHandoffClassification.INDETERMINATE
    assert assessment.reason.value == "contradictory_evidence"


def test_ac1_classifier_excludes_foreign_product_and_ticket_evidence(
    db: Database,
) -> None:
    client = RecordingClient()
    ticket = seed_ci_pending(db, client)
    foreign_product = uuid4()
    foreign_ticket = uuid4()
    foreign_evidence = [
        ci_evidence(ticket, EvidenceType.TEST_RESULT, suffix="1").model_copy(
            update={"product_id": foreign_product}
        ),
        ci_evidence(ticket, EvidenceType.LINT_RESULT, suffix="2").model_copy(
            update={"product_id": foreign_product}
        ),
        ci_evidence(ticket, EvidenceType.TEST_RESULT, suffix="3").model_copy(
            update={"ticket_id": foreign_ticket}
        ),
        ci_evidence(ticket, EvidenceType.LINT_RESULT, suffix="4").model_copy(
            update={"ticket_id": foreign_ticket}
        ),
    ]

    excluded = evaluate_ci_handoff(ticket, head_commit=HEAD, evidence=foreign_evidence)
    matching = evaluate_ci_handoff(
        ticket,
        head_commit=HEAD,
        evidence=[
            ci_evidence(ticket, EvidenceType.TEST_RESULT, suffix="5").model_copy(
                update={"ticket_id": ticket.id}
            ),
            ci_evidence(ticket, EvidenceType.LINT_RESULT, suffix="6").model_copy(
                update={"ticket_id": ticket.id}
            ),
        ],
    )

    assert excluded.classification is CIHandoffClassification.MISSING
    assert matching.classification is CIHandoffClassification.PASSED


def test_documentation_structured_projection_passes_beside_capped_legacy_history(
    db: Database,
) -> None:
    client = RecordingClient()
    base_ticket = seed_ci_pending(db, client)
    required = "docs/atlas/evidence-pipeline.md"
    ticket = base_ticket.model_copy(update={"documentation_requirements": [required]})
    legacy = documentation_evidence(
        ticket,
        docs_paths=None,
        raw_payload={"_truncated": True, "_original_bytes": 70000},
        suffix="3",
    )
    structured = documentation_evidence(
        ticket,
        docs_paths=(required,),
        raw_payload={"_truncated": True, "_original_bytes": 70000},
        suffix="4",
    )
    evidence = [
        ci_evidence(ticket, EvidenceType.TEST_RESULT, suffix="1"),
        ci_evidence(ticket, EvidenceType.LINT_RESULT, suffix="2"),
        legacy,
        structured,
    ]

    assessment = evaluate_ci_handoff(ticket, head_commit=HEAD, evidence=evidence)

    assert assessment.classification is CIHandoffClassification.PASSED
    documentation = next(
        check
        for check in assessment.check_results
        if check.check_type.value == "documentation"
    )
    assert documentation.classification is CIHandoffClassification.PASSED
    assert documentation.evidence_ids == (structured.id,)


@pytest.mark.parametrize("kind", ["legacy-capped", "structured-malformed"])
def test_malformed_documentation_projection_retains_typed_fail_closed_hold(
    db: Database,
    kind: str,
) -> None:
    client = RecordingClient()
    base_ticket = seed_ci_pending(db, client)
    required = "docs/atlas/evidence-pipeline.md"
    ticket = base_ticket.model_copy(update={"documentation_requirements": [required]})
    record = documentation_evidence(
        ticket,
        docs_paths=(required,) if kind == "structured-malformed" else None,
        raw_payload={"_truncated": True, "_original_bytes": 70000},
        suffix="3",
    )
    if kind == "structured-malformed":
        record = record.model_copy(update={"docs_paths": ("../docs/bad.md",)})
    evidence = [
        ci_evidence(ticket, EvidenceType.TEST_RESULT, suffix="1"),
        ci_evidence(ticket, EvidenceType.LINT_RESULT, suffix="2"),
        record,
    ]

    assessment = evaluate_ci_handoff(ticket, head_commit=HEAD, evidence=evidence)

    assert assessment.classification is CIHandoffClassification.MALFORMED
    assert assessment.reason.value == "malformed_evidence"


def test_absent_documentation_projection_remains_missing_and_fail_closed(
    db: Database,
) -> None:
    client = RecordingClient()
    ticket = seed_ci_pending(db, client).model_copy(
        update={"documentation_requirements": ["docs/atlas/evidence-pipeline.md"]}
    )
    assessment = evaluate_ci_handoff(
        ticket,
        head_commit=HEAD,
        evidence=[
            ci_evidence(ticket, EvidenceType.TEST_RESULT, suffix="1"),
            ci_evidence(ticket, EvidenceType.LINT_RESULT, suffix="2"),
        ],
    )

    assert assessment.classification is CIHandoffClassification.MISSING
    assert assessment.reason.value == "required_checks_missing"


def test_ac1_reconciler_holds_when_only_another_product_passed_same_head(
    db: Database,
) -> None:
    client = RecordingClient()
    ticket = seed_ci_pending(db, client)
    other_ticket = seed_ticket(
        db,
        client,
        key="ATLAS-OTHER",
        product_id=uuid4(),
        status=TicketStatus.CI_PENDING,
        with_issue=False,
    )
    add_passed_ci(db, other_ticket)

    result = run(db, client, FakeGitHubClient(pull_request=pr_payload()))

    assert result.classification is CIHandoffClassification.MISSING
    assert result.reason.value == "required_checks_missing"
    assert result.decision is CIHandoffDecision.HOLD
    assert result.linear_mutations == 0
    assert client.state_writes == []
    [recorded] = CIHandoffReconciliationRepo(db).list_for_ticket(ticket.id)
    assert recorded.check_results
    assert all(not check.evidence_ids for check in recorded.check_results)


@pytest.mark.parametrize(
    ("failed", "expected_state", "expected_decision"),
    [
        (False, REVIEW_REQUIRED_STATE.id, CIHandoffDecision.REVIEW_REQUIRED),
        (True, CHANGES_REQUESTED_STATE.id, CIHandoffDecision.CHANGES_REQUESTED),
    ],
    ids=["complete-pass-to-review", "definite-failure-to-rework"],
)
def test_ac2_ac3_complete_current_evidence_performs_one_exact_transition(
    db: Database,
    failed: bool,
    expected_state: str,
    expected_decision: CIHandoffDecision,
) -> None:
    client = RecordingClient()
    ticket = seed_ci_pending(db, client)
    EvidenceRepo(db).add(
        ci_evidence(
            ticket,
            EvidenceType.TEST_RESULT,
            status=EvidenceStatus.FAILED if failed else EvidenceStatus.PASSED,
            conclusion="failure" if failed else "success",
            suffix="1",
        )
    )
    EvidenceRepo(db).add(ci_evidence(ticket, EvidenceType.LINT_RESULT, suffix="2"))
    github = FakeGitHubClient(pull_request=pr_payload())

    result = run(db, client, github)

    assert result.linear_mutations == 1
    assert result.decision is expected_decision
    assert client.state_writes == [(ticket.external_linear_id, expected_state)]
    [recorded] = CIHandoffReconciliationRepo(db).list_for_ticket(ticket.id)
    assert recorded.decision is expected_decision
    assert recorded.head_commit == HEAD
    assert recorded.policy_revision == 1
    assert len(recorded.check_results) == 2
    stored = TicketRepo(db).get_by_key(ticket.key)
    expected_status = TicketStatus(expected_decision.value)
    assert stored is not None and stored.status is expected_status
    assert github.calls and {call[0] for call in github.calls} == {"pull_request"}
    assert client.creates == [] and client.updates == []


def test_ac2_reconciliation_outcome_is_database_append_only(db: Database) -> None:
    client = RecordingClient()
    ticket = seed_ci_pending(db, client)
    add_passed_ci(db, ticket)
    result = run(db, client, FakeGitHubClient(pull_request=pr_payload()))
    assert result.reconciliation_id is not None
    assert not any(
        hasattr(CIHandoffReconciliationRepo, name)
        for name in ("update", "delete", "finalize")
    )

    for statement in (
        sa.update(CIHandoffReconciliationRow)
        .where(CIHandoffReconciliationRow.id == result.reconciliation_id)
        .values(reason="required_checks_missing"),
        sa.delete(CIHandoffReconciliationRow).where(
            CIHandoffReconciliationRow.id == result.reconciliation_id
        ),
    ):
        with (
            pytest.raises(sa.exc.IntegrityError, match="append-only"),
            db.session() as session,
            session.begin(),
        ):
            session.execute(statement)


def _replace_policy(db: Database) -> None:
    with db.session() as session, session.begin():
        session.add(
            DeliveryAdmissionPolicyRevisionRow(
                id=uuid4(),
                product_id=PRODUCT_ID,
                revision=2,
                mode="running",
                approved_symphony_ceiling=3,
                working_budget=3,
                integration_budget=3,
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
            sa.update(DeliveryAdmissionPolicyActiveRow)
            .where(DeliveryAdmissionPolicyActiveRow.product_id == PRODUCT_ID)
            .values(revision=2)
        )


@pytest.mark.parametrize("race", ["head", "board", "policy", "snapshot"])
def test_ac4_head_board_policy_and_snapshot_races_write_nothing(
    db: Database, race: str
) -> None:
    client = RecordingClient()
    ticket = seed_ci_pending(db, client)
    add_passed_ci(db, ticket)
    github = FakeGitHubClient(pull_request=pr_payload())

    def move() -> None:
        if race == "head":
            github._pull_request = pr_payload(OTHER_HEAD)
        elif race == "board":
            client.simulate_linear_state(ticket.external_linear_id or "", PR_OPEN_STATE)
        elif race == "policy":
            _replace_policy(db)
        else:
            with db.session() as session, session.begin():
                session.execute(
                    sa.update(TicketRow)
                    .where(TicketRow.id == ticket.id)
                    .values(priority=ticket.priority + 1)
                )

    result = run(
        db,
        client,
        github,
        hooks=CIHandoffHooks(after_classification=move),
    )

    assert result.decision is CIHandoffDecision.HOLD
    assert result.linear_mutations == 0
    assert client.state_writes == []
    assert len(CIHandoffReconciliationRepo(db).list_for_ticket(ticket.id)) == 1


def test_ac4_lease_loss_after_revalidation_writes_nothing(db: Database) -> None:
    client = RecordingClient()
    ticket = seed_ci_pending(db, client)
    add_passed_ci(db, ticket)
    github = FakeGitHubClient(pull_request=pr_payload())

    def lose_lease() -> None:
        with db.session() as session, session.begin():
            session.execute(
                sa.delete(AdmissionLeaseRow).where(
                    AdmissionLeaseRow.product_id == PRODUCT_ID
                )
            )

    result = run(
        db,
        client,
        github,
        hooks=CIHandoffHooks(after_revalidation=lose_lease),
    )

    assert result.reason.value == "lease_lost"
    assert result.linear_mutations == 0
    assert client.state_writes == []


@pytest.mark.parametrize(
    ("status", "conclusion", "next_classification", "next_state"),
    [
        (
            EvidenceStatus.FAILED,
            "failure",
            CIHandoffClassification.IMPLEMENTATION_FAILURE,
            CHANGES_REQUESTED_STATE.id,
        ),
        (
            EvidenceStatus.PENDING,
            None,
            CIHandoffClassification.PENDING,
            None,
        ),
    ],
    ids=["newer-failed", "newer-pending"],
)
def test_ac4_newer_same_head_evidence_requires_a_fresh_tick(
    db: Database,
    status: EvidenceStatus,
    conclusion: str | None,
    next_classification: CIHandoffClassification,
    next_state: str | None,
) -> None:
    client = RecordingClient()
    ticket = seed_ci_pending(db, client)
    add_passed_ci(db, ticket)
    github = FakeGitHubClient(pull_request=pr_payload())

    def append_newer_observation() -> None:
        EvidenceRepo(db).add(
            ci_evidence(
                ticket,
                EvidenceType.TEST_RESULT,
                status=status,
                conclusion=conclusion,
                source_event_at=NOW + timedelta(seconds=1),
                suffix="3",
            )
        )

    first = run(
        db,
        client,
        github,
        hooks=CIHandoffHooks(after_classification=append_newer_observation),
    )

    assert first.classification is CIHandoffClassification.STALE
    assert first.reason.value == "evidence_changed"
    assert first.decision is CIHandoffDecision.HOLD
    assert first.linear_mutations == 0
    assert client.state_writes == []

    second = run(db, client, github)

    assert second.classification is next_classification
    if next_state is None:
        assert second.decision is CIHandoffDecision.HOLD
        assert second.linear_mutations == 0
        assert client.state_writes == []
    else:
        assert second.decision is CIHandoffDecision.CHANGES_REQUESTED
        assert second.linear_mutations == 1
        assert client.state_writes == [(ticket.external_linear_id, next_state)]


def test_ac5_duplicate_owner_is_fenced_before_evaluation(db: Database) -> None:
    client = RecordingClient()
    ticket = seed_ci_pending(db, client)
    add_passed_ci(db, ticket)
    lease = AdmissionCoordinationRepo(db)
    assert lease.try_acquire(
        product_id=PRODUCT_ID,
        owner_id=uuid4(),
        acquired_at=NOW,
        ttl=timedelta(minutes=5),
    )

    result = run(db, client, FakeGitHubClient(pull_request=pr_payload()))

    assert result.reason.value == "lease_unavailable"
    assert result.linear_mutations == 0
    assert client.state_writes == []
    assert CIHandoffReconciliationRepo(db).list_for_ticket(ticket.id) == []


class AmbiguousSuccessClient(RecordingClient):
    def set_state(self, issue_id: str, state_id: str) -> LinearIssue:
        super().set_state(issue_id, state_id)
        raise LinearAPIError("timeout after send; token=secret")


class AmbiguousNoWriteClient(RecordingClient):
    def set_state(self, issue_id: str, state_id: str) -> LinearIssue:
        self.state_writes.append((issue_id, state_id))
        raise LinearAPIError("timeout before confirmation; body=private")


@pytest.mark.parametrize(
    ("client_type", "landed", "second_reason"),
    [
        (AmbiguousSuccessClient, True, "fence_reconciled_target"),
        (AmbiguousNoWriteClient, False, "fence_reconciled_source"),
    ],
    ids=["ambiguous-landed", "ambiguous-no-write"],
)
def test_ac5_ambiguous_write_requires_fresh_reconciliation_before_another_attempt(
    db: Database,
    client_type: type[RecordingClient],
    landed: bool,
    second_reason: str,
) -> None:
    client = client_type()
    ticket = seed_ci_pending(db, client)
    add_passed_ci(db, ticket)
    github = FakeGitHubClient(pull_request=pr_payload())

    first = run(db, client, github)
    second = run(db, client, github)

    assert first.reason.value == "write_indeterminate"
    assert second.reason.value == second_reason
    assert client.state_writes == [
        (ticket.external_linear_id, REVIEW_REQUIRED_STATE.id)
    ]
    assert CIHandoffCoordinationRepo(db).get_fence(PRODUCT_ID) is None
    assert len(CIHandoffReconciliationRepo(db).list_for_ticket(ticket.id)) == 1
    stored = TicketRepo(db).get_by_key(ticket.key)
    expected = TicketStatus.REVIEW_REQUIRED if landed else TicketStatus.CI_PENDING
    assert stored is not None and stored.status is expected


def test_ac6_architecture_has_no_github_git_symphony_or_done_authority() -> None:
    root = Path(__file__).resolve().parents[1]
    reconciler = root / "atlas/pm/ci_handoff.py"
    writer = root / "atlas/linear/ci_handoff.py"
    sources = [
        reconciler.read_text(encoding="utf-8"),
        writer.read_text(encoding="utf-8"),
    ]
    forbidden_calls = {
        "merge",
        "update_branch",
        "rebase",
        "force_push",
        "cancel_agent",
        "confirm",
        "waive",
    }
    calls = {
        ast.unparse(node.func).split(".")[-1]
        for source in sources
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
    }
    assert calls.isdisjoint(forbidden_calls)
    assert "set_state" not in {
        ast.unparse(node.func).split(".")[-1]
        for node in ast.walk(ast.parse(sources[0]))
        if isinstance(node, ast.Call)
    }
    assert "set_state" in calls  # one strict adapter, never the PM orchestration
    assert TicketStatus.DONE not in {
        TicketStatus.REVIEW_REQUIRED,
        TicketStatus.CHANGES_REQUESTED,
    }
