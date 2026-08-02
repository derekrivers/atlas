"""ATLAS-238 durable exact-head acceptance-session contract."""

from __future__ import annotations

import inspect
import json
import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from github_fakes import FakeGitHubClient
from pydantic import ValidationError
from sqlalchemy.exc import DatabaseError

from atlas.core.enums import ActorType, RiskLevel
from atlas.core.models import (
    AcceptanceSessionBlockingReason as Reason,
)
from atlas.core.models import (
    AcceptanceSessionLifecycle,
    AcceptanceSessionStep,
    AcceptanceSessionStepState,
    Ticket,
    TicketStatus,
    TicketType,
)
from atlas.core.models.acceptance_session import (
    AcceptanceCriterionSnapshot,
    AcceptanceStepSummary,
)
from atlas.github import GitHubCompare, GitHubCompareStatus
from atlas.orchestration import (
    AcceptanceSessionCreationService,
    acceptance_criteria_fingerprint,
    acceptance_criteria_snapshot,
    compare_acceptance_session_freshness,
    mark_acceptance_session_stale_for_mutation,
    stored_acceptance_session_status,
)
from atlas.orchestration import (
    AcceptanceSessionCreationStatus as CreationStatus,
)
from atlas.orchestration.pr_integration import (
    PRAncestryStatus,
    PRIntegrationAssessment,
    PRIntegrationEligibility,
    PRIntegrationStatus,
    PRMergeabilityStatus,
)
from atlas.storage import AcceptanceSessionRepo, Database
from atlas.storage.tables import AcceptanceSessionRow

OWNER = "acme"
REPO = "atlas"
SLUG = f"{OWNER}/{REPO}"
PR = 418
HEAD = "2" * 40
BASE = "1" * 40
NOW = datetime(2026, 8, 2, 13, 0, tzinfo=UTC)


class FrozenClock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class TicketFake:
    def __init__(self, *tickets: Ticket) -> None:
        self.tickets = {ticket.key: ticket for ticket in tickets}
        self.calls: list[str] = []

    def get_by_key(self, key: str) -> Ticket | None:
        self.calls.append(key)
        return self.tickets.get(key)


def ticket(
    key: str,
    *criteria: str,
    status: TicketStatus = TicketStatus.REVIEW_REQUIRED,
) -> Ticket:
    return Ticket(
        id=uuid4(),
        product_id=uuid4(),
        key=key,
        title=f"Ticket {key}",
        objective="Prove the acceptance contract.",
        context="Phase 14.",
        status=status,
        ticket_type=TicketType.FEATURE,
        risk_level=RiskLevel.HIGH,
        priority=1,
        acceptance_criteria=list(criteria),
        source_anchor="docs/atlas/review-acceptance-console.md#preflight",
        created_by_type=ActorType.AGENT,
        created_by_id="planner",
        created_at=NOW,
        updated_at=NOW,
    )


def assessment(**overrides: Any) -> PRIntegrationAssessment:
    values: dict[str, Any] = {
        "owner": OWNER,
        "repo": REPO,
        "pr_number": PR,
        "pr_title": "ATLAS-2 then ATLAS-1",
        "pr_body": None,
        "pr_state": "open",
        "pr_draft": False,
        "pr_merged": False,
        "head_ref": "ATL-418-acceptance-session",
        "head_sha": HEAD,
        "head_repository": SLUG,
        "base_ref": "main",
        "base_sha": BASE,
        "base_repository": SLUG,
        "merge_base_sha": BASE,
        "ahead_by": 1,
        "behind_by": 0,
        "compare_status": GitHubCompareStatus.AHEAD,
        "mergeability": PRMergeabilityStatus.MERGEABLE,
        "ancestry": PRAncestryStatus.CURRENT,
        "eligibility": PRIntegrationEligibility.ELIGIBLE,
        "integration_status": PRIntegrationStatus.CURRENT,
    }
    values.update(overrides)
    return PRIntegrationAssessment(**values)


def github_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "number": PR,
        "title": "ATLAS-2 then ATLAS-1",
        "body": None,
        "state": "open",
        "draft": False,
        "merged": False,
        "mergeable": True,
        "head": {
            "ref": "ATL-418-acceptance-session",
            "sha": HEAD,
            "repo": {"full_name": SLUG},
        },
        "base": {
            "ref": "main",
            "sha": BASE,
            "repo": {"full_name": SLUG},
        },
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{tmp_path}/atlas.db")
    database.create_all()
    return database


@pytest.fixture
def tickets() -> TicketFake:
    return TicketFake(
        ticket("ATLAS-1", "first criterion", "second criterion"),
        ticket("ATLAS-2", "only criterion"),
    )


def creator(
    db: Database,
    tickets: TicketFake,
    *,
    exact_assessment: PRIntegrationAssessment | None = None,
    github: FakeGitHubClient | None = None,
    assessment_service: Any | None = None,
) -> AcceptanceSessionCreationService:
    if assessment_service is None and exact_assessment is not None:

        def static_assessment(*_args: Any) -> PRIntegrationAssessment:
            return exact_assessment

        assessment_service = static_assessment
    if github is None:
        github = FakeGitHubClient(
            pull_request=github_payload(),
            branch_head_sha=BASE,
            compare=GitHubCompare(
                status=GitHubCompareStatus.AHEAD,
                ahead_by=1,
                behind_by=0,
                merge_base_sha=BASE,
            ),
        )
    return AcceptanceSessionCreationService(
        github_client=github,
        ticket_lookup=tickets,
        repository=AcceptanceSessionRepo(db),
        clock=FrozenClock(),
        assessment_service=assessment_service,
    )


def create(
    service: AcceptanceSessionCreationService,
    *,
    key: str = "create-session-1",
) -> Any:
    return service.create(
        repository_owner=OWNER,
        repository_name=REPO,
        pr_number=PR,
        idempotency_key=key,
        created_by_type=ActorType.HUMAN,
        created_by_id="operator",
    )


def stored_session(db: Database, tickets: TicketFake) -> Any:
    result = create(creator(db, tickets, exact_assessment=assessment()))
    assert result.status is CreationStatus.CREATED
    assert result.session is not None
    return result.session


def test_ac1_model_repository_and_database_pin_complete_session_identity(
    db: Database, tickets: TicketFake
) -> None:
    session = stored_session(db, tickets)

    assert session.repository_owner == OWNER
    assert session.repository_name == REPO
    assert session.pr_number == PR
    assert session.close_set == ("ATLAS-1", "ATLAS-2")
    assert session.head_sha == HEAD
    assert session.base_sha == BASE
    assert session.initial_assessment.integration_status == "current"
    assert session.created_by_type is ActorType.HUMAN
    assert session.created_by_id == "operator"
    assert session.lifecycle is AcceptanceSessionLifecycle.PREFLIGHT_PASSED
    assert session.step_summaries[AcceptanceSessionStep.PREFLIGHT].state is (
        AcceptanceSessionStepState.COMPLETE
    )
    assert session.created_at == session.updated_at == NOW

    with pytest.raises(ValidationError, match="frozen"):
        session.head_sha = "f" * 40

    with (
        pytest.raises(DatabaseError, match="pinned identity is immutable"),
        db.session() as sql_session,
        sql_session.begin(),
    ):
        sql_session.execute(
            sa.update(AcceptanceSessionRow)
            .where(AcceptanceSessionRow.id == session.id)
            .values(head_sha="f" * 40)
        )
    assert AcceptanceSessionRepo(db).get(session.id) == session


def test_ac2_creation_calls_shared_exact_head_assessment_and_writes_last(
    db: Database, tickets: TicketFake, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ATLAS_LIVE_TESTS", "0")
    github = FakeGitHubClient(
        pull_request=github_payload(),
        branch_head_sha=BASE,
        compare=GitHubCompare(
            status=GitHubCompareStatus.AHEAD,
            ahead_by=1,
            behind_by=0,
            merge_base_sha=BASE,
        ),
    )

    result = create(creator(db, tickets, github=github))

    assert result.status is CreationStatus.CREATED
    assert github.calls == [
        ("pull_request", OWNER, REPO, PR),
        ("branch_head", OWNER, REPO, "main"),
        ("compare", OWNER, REPO, f"{BASE}...{HEAD}"),
    ]
    assert tickets.calls == ["ATLAS-1", "ATLAS-2"]
    assert len(AcceptanceSessionRepo(db).list_for_pr(OWNER, REPO, PR)) == 1


@pytest.mark.parametrize(
    ("ineligible", "reason"),
    [
        (PRIntegrationEligibility.MERGED, Reason.PR_MERGED),
        (PRIntegrationEligibility.CLOSED, Reason.PR_CLOSED),
        (PRIntegrationEligibility.DRAFT, Reason.PR_DRAFT),
        (PRIntegrationEligibility.FORK, Reason.PR_FORK_HEAD),
        (PRIntegrationEligibility.NON_MAIN, Reason.PR_NON_MAIN),
    ],
)
def test_ac2_every_ineligible_class_refuses_before_acceptance_write(
    db: Database,
    tickets: TicketFake,
    ineligible: PRIntegrationEligibility,
    reason: Reason,
) -> None:
    state = (
        "closed"
        if ineligible
        in {
            PRIntegrationEligibility.MERGED,
            PRIntegrationEligibility.CLOSED,
        }
        else "open"
    )
    result = create(
        creator(
            db,
            tickets,
            exact_assessment=assessment(
                pr_state=state,
                pr_draft=ineligible is PRIntegrationEligibility.DRAFT,
                pr_merged=ineligible is PRIntegrationEligibility.MERGED,
                eligibility=ineligible,
                ancestry=PRAncestryStatus.INDETERMINATE,
                integration_status=PRIntegrationStatus.INELIGIBLE,
                merge_base_sha=None,
                ahead_by=None,
                behind_by=None,
                compare_status=None,
            ),
        )
    )

    assert result.status is CreationStatus.REFUSED
    assert result.reasons == (reason,)
    assert AcceptanceSessionRepo(db).list_for_pr(OWNER, REPO, PR) == []
    assert tickets.calls == []


def test_ac3_live_criteria_snapshot_and_fingerprint_are_canonical(
    db: Database, tickets: TicketFake
) -> None:
    result = create(creator(db, tickets, exact_assessment=assessment()))
    assert result.session is not None
    session = result.session

    assert [
        (item.ticket_key, item.criterion_index, item.text)
        for item in session.criteria_snapshot
    ] == [
        ("ATLAS-1", 0, "first criterion"),
        ("ATLAS-1", 1, "second criterion"),
        ("ATLAS-2", 0, "only criterion"),
    ]
    assert session.criteria_fingerprint == (
        "sha256:66825b8749b1788abf069a03ff48e0279494e7fd0fa09afc4688d589b8402a15"
    )
    parameters = inspect.signature(AcceptanceSessionCreationService.create).parameters
    assert "criteria" not in parameters
    assert "criterion_text" not in parameters

    tickets.tickets["ATLAS-1"].acceptance_criteria[0] = "changed later"
    assert session.criteria_snapshot[0].text == "first criterion"


def test_ac4_idempotent_and_concurrent_creation_yield_one_record(
    db: Database, tickets: TicketFake
) -> None:
    first = create(creator(db, tickets, exact_assessment=assessment()))
    replay = create(
        creator(db, tickets, exact_assessment=assessment()),
        key="create-session-1",
    )
    assert first.session is not None
    assert replay.status is CreationStatus.REPLAYED
    assert replay.session == first.session

    other_db_url = str(db.engine.url)
    barrier = threading.Barrier(2)

    def gated_assessment(*_args: Any) -> PRIntegrationAssessment:
        barrier.wait(timeout=5)
        return assessment(pr_number=PR + 1, pr_title="ATLAS-1")

    other_tickets = TicketFake(ticket("ATLAS-1", "criterion"))

    def concurrent_create(key: str, results: list[Any]) -> None:
        database = Database(other_db_url)
        service = AcceptanceSessionCreationService(
            github_client=FakeGitHubClient(),
            ticket_lookup=other_tickets,
            repository=AcceptanceSessionRepo(database),
            clock=FrozenClock(NOW + timedelta(seconds=1)),
            assessment_service=gated_assessment,
        )
        results.append(
            service.create(
                repository_owner=OWNER,
                repository_name=REPO,
                pr_number=PR + 1,
                idempotency_key=key,
                created_by_type=ActorType.HUMAN,
                created_by_id="operator",
            )
        )

    results: list[Any] = []
    threads = [
        threading.Thread(target=concurrent_create, args=(f"race-{index}", results))
        for index in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert len(results) == 2
    assert {result.status for result in results} == {
        CreationStatus.CREATED,
        CreationStatus.REPLAYED,
    }
    assert len(AcceptanceSessionRepo(db).list_for_pr(OWNER, REPO, PR + 1)) == 1


def test_ac4_different_head_waits_for_old_session_to_be_stale(
    db: Database, tickets: TicketFake
) -> None:
    first = stored_session(db, tickets)
    moved = assessment(head_sha="3" * 40)

    blocked = create(creator(db, tickets, exact_assessment=moved), key="different-head")
    assert blocked.status is CreationStatus.CONFLICT
    assert blocked.reasons == (Reason.ACTIVE_SESSION_EXISTS,)

    AcceptanceSessionRepo(db).mark_stale(
        first.id,
        (Reason.HEAD_SHA_MISMATCH,),
        staled_at=NOW + timedelta(minutes=1),
    )
    created = create(creator(db, tickets, exact_assessment=moved), key="different-head")
    assert created.status is CreationStatus.CREATED
    assert created.session is not None
    assert created.session.head_sha == "3" * 40
    assert len(AcceptanceSessionRepo(db).list_for_pr(OWNER, REPO, PR)) == 2


def test_ac5_freshness_comparator_returns_every_typed_mismatch(
    db: Database, tickets: TicketFake
) -> None:
    session = stored_session(db, tickets)
    live = replace(
        assessment(),
        owner="other",
        repo="other",
        pr_number=PR + 1,
        head_ref="moved-head",
        head_sha="3" * 40,
        head_repository="other/other",
        base_ref="develop",
        base_sha="4" * 40,
        base_repository="other/other",
        eligibility=PRIntegrationEligibility.DRAFT,
        integration_status=PRIntegrationStatus.INDETERMINATE,
        ancestry=PRAncestryStatus.INDETERMINATE,
        mergeability=PRMergeabilityStatus.INDETERMINATE,
    )
    live_criteria = list(session.criteria_snapshot)
    live_criteria[0] = live_criteria[0].model_copy(update={"text": "drift"})

    reasons = compare_acceptance_session_freshness(session, live, live_criteria)

    assert set(reasons) == {
        Reason.REPOSITORY_MISMATCH,
        Reason.PR_NUMBER_MISMATCH,
        Reason.HEAD_REF_MISMATCH,
        Reason.HEAD_SHA_MISMATCH,
        Reason.HEAD_REPOSITORY_MISMATCH,
        Reason.BASE_REF_MISMATCH,
        Reason.BASE_SHA_MISMATCH,
        Reason.BASE_REPOSITORY_MISMATCH,
        Reason.ELIGIBILITY_MISMATCH,
        Reason.INTEGRATION_STATUS_MISMATCH,
        Reason.EXTERNAL_STATE_INDETERMINATE,
        Reason.CRITERIA_MISMATCH,
    }
    assert AcceptanceSessionRepo(db).get(session.id) == session


def test_ac5_mutation_freshness_atomically_marks_terminal_stale(
    db: Database, tickets: TicketFake
) -> None:
    session = stored_session(db, tickets)
    live = replace(assessment(), head_sha="3" * 40, base_sha="4" * 40)

    stale = mark_acceptance_session_stale_for_mutation(
        AcceptanceSessionRepo(db),
        session,
        live,
        session.criteria_snapshot,
        observed_at=NOW + timedelta(minutes=1),
    )

    assert stale.lifecycle is AcceptanceSessionLifecycle.STALE
    assert stale.blocking_reasons == (
        Reason.HEAD_SHA_MISMATCH,
        Reason.BASE_SHA_MISMATCH,
    )
    assert stale.staled_at == NOW + timedelta(minutes=1)
    assert stale.head_sha == HEAD
    assert stale.base_sha == BASE
    assert AcceptanceSessionRepo(db).get_non_terminal_for_pr(OWNER, REPO, PR) is None


def test_ac5_indeterminate_external_state_never_counts_as_fresh(
    db: Database, tickets: TicketFake
) -> None:
    session = stored_session(db, tickets)
    assert compare_acceptance_session_freshness(
        session, None, session.criteria_snapshot
    ) == (Reason.EXTERNAL_STATE_INDETERMINATE,)
    assert compare_acceptance_session_freshness(session, assessment(), None) == (
        Reason.EXTERNAL_STATE_INDETERMINATE,
    )


def test_ac6_stored_status_is_pure_history_and_never_current_authority(
    db: Database, tickets: TicketFake
) -> None:
    session = stored_session(db, tickets)
    receipt_id = UUID("00000000-0000-0000-0000-000000000418")
    summaries = dict(session.step_summaries)
    summaries[AcceptanceSessionStep.VERIFICATION] = AcceptanceStepSummary(
        state=AcceptanceSessionStepState.COMPLETE,
        receipt_ids=(receipt_id,),
        occurred_at=NOW,
    )
    historical = session.model_copy(
        update={
            "step_summaries": summaries,
            "stored_merge_ready": True,
            "historical_readiness_reasons": (),
        }
    )
    sql_calls: list[str] = []

    def record_sql(
        _conn: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        sql_calls.append(statement)

    sa.event.listen(db.engine, "before_cursor_execute", record_sql)
    try:
        projection = stored_acceptance_session_status(historical)
    finally:
        sa.event.remove(db.engine, "before_cursor_execute", record_sql)

    assert sql_calls == []
    assert projection["pinned_identity"] == {
        "repository": {"owner": OWNER, "name": REPO},
        "pr_number": PR,
        "head": {
            "ref": session.head_ref,
            "sha": HEAD,
            "repository": SLUG,
        },
        "base": {"ref": "main", "sha": BASE, "repository": SLUG},
    }
    assert projection["receipts"] == [str(receipt_id)]
    assert "merge_ready" not in projection
    readiness = projection["historical_readiness"]
    assert isinstance(readiness, dict)
    assert readiness["stored_merge_ready"] is True
    assert readiness["authority"] == "historical_only"
    assert readiness["is_current_merge_authority"] is False


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (PRIntegrationStatus.BEHIND, Reason.INTEGRATION_BEHIND),
        (PRIntegrationStatus.DIVERGED, Reason.INTEGRATION_DIVERGED),
        (PRIntegrationStatus.CONFLICTED, Reason.INTEGRATION_CONFLICTED),
    ],
)
def test_ac7_rebase_eligible_preflight_returns_exact_bounded_recovery(
    db: Database,
    tickets: TicketFake,
    status: PRIntegrationStatus,
    reason: Reason,
) -> None:
    result = create(
        creator(
            db,
            tickets,
            exact_assessment=assessment(integration_status=status),
        )
    )

    assert result.status is CreationStatus.REFUSED
    assert result.reasons == (reason,)
    assert result.recovery_command == (
        "atlas pr rebase prepare --pr 418 --repo acme/atlas"
    )
    assert len(result.recovery_command) < 256
    assert AcceptanceSessionRepo(db).list_for_pr(OWNER, REPO, PR) == []


def test_ac7_unknown_and_indeterminate_failures_are_distinct_and_secret_free(
    db: Database, tickets: TicketFake
) -> None:
    unknown = create(creator(db, tickets, github=FakeGitHubClient()), key="unknown-pr")

    def fail_assessment(*_args: Any) -> PRIntegrationAssessment:
        from atlas.github import GitHubAPIError

        raise GitHubAPIError("transport failed with token canary-secret")

    indeterminate = create(
        creator(db, tickets, assessment_service=fail_assessment),
        key="indeterminate-pr",
    )

    assert unknown.reasons == (Reason.PR_UNKNOWN,)
    assert indeterminate.reasons == (Reason.EXTERNAL_STATE_INDETERMINATE,)
    rendered = json.dumps(
        {
            "status": indeterminate.status,
            "reasons": indeterminate.reasons,
            "recovery": indeterminate.recovery_command,
        },
        default=str,
    )
    assert "canary-secret" not in rendered
    assert AcceptanceSessionRepo(db).list_for_pr(OWNER, REPO, PR) == []


def test_criteria_helpers_reject_incomplete_or_duplicate_live_ticket_sets() -> None:
    one = ticket("ATLAS-1", "criterion")
    with pytest.raises(ValueError, match="equal the close-set"):
        acceptance_criteria_snapshot(("ATLAS-1", "ATLAS-2"), [one])
    with pytest.raises(ValueError, match="duplicate"):
        acceptance_criteria_snapshot(("ATLAS-1",), [one, one])
    assert acceptance_criteria_fingerprint(
        (
            AcceptanceCriterionSnapshot(
                ticket_key="ATLAS-1", criterion_index=0, text="criterion"
            ),
        )
    ).startswith("sha256:")
