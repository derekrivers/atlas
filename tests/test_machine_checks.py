"""ATLAS-75: the machine-check evaluator decides one TESTS/LINT check against
pre-loaded evidence, using system-tier evidence pinned to the head commit and
NEVER raising.

Each behavioural assertion names the wrong answer it would catch. The
milestone crux is guarded from both sides: agent evidence can neither
manufacture a pass (AC2) nor hide a system-tier fail (T2). Per-job source
recency and fail precedence are exercised directly; UUID order is proved
irrelevant and legacy rows without source metadata fail closed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from atlas.core.enums import ActorType, EvidenceStatus
from atlas.core.models import Evidence, EvidenceType, VerificationCheckType
from atlas.evidence import ingest_checks
from atlas.github import normalise_check_run
from atlas.storage import Database, EvidenceRepo
from atlas.verification import (
    MACHINE_CHECK_EVIDENCE,
    MACHINE_CHECK_TYPES,
    MachineCheckEvaluation,
    evaluate_machine_check,
)

NOW = datetime(2026, 6, 28, tzinfo=UTC)
HEAD = "c0ffee0000000000000000000000000000000000"
OTHER = "dead000000000000000000000000000000000000"
VCT = VerificationCheckType
ES = EvidenceStatus
ET = EvidenceType


def make_evidence(
    *,
    evidence_type: EvidenceType,
    status: EvidenceStatus,
    created_by_type: ActorType,
    commit_sha: str | None = HEAD,
    created_at: datetime = NOW,
    job_name: str | None = "test",
    source_event_at: datetime | None = NOW,
    external_run_id: str | None = None,
    payload_hash: str | None = None,
    id: UUID | None = None,
) -> Evidence:
    return Evidence(
        id=id or uuid4(),
        product_id=uuid4(),
        evidence_type=evidence_type,
        status=status,
        summary="ci run",
        commit_sha=commit_sha,
        job_name=job_name,
        source_event_at=source_event_at,
        external_run_id=external_run_id,
        payload_hash=payload_hash,
        created_by_type=created_by_type,
        created_by_id="ci" if created_by_type == ActorType.SYSTEM else "claude",
        created_at=created_at,
    )


# --- AC1: system-tier PASSED at C makes the check PASSED with that record id.
def test_system_passed_at_head_evaluates_passed() -> None:
    record = make_evidence(
        evidence_type=ET.TEST_RESULT, status=ES.PASSED, created_by_type=ActorType.SYSTEM
    )
    result = evaluate_machine_check(VCT.TESTS, head_commit=HEAD, evidence=[record])

    # wrong answer: PENDING — a present, passing system-tier check is not pending.
    assert result.status == ES.PASSED
    assert result.evidence_ids == (record.id,)


# --- AC2 (crux, side 1): agent-tier PASSED alone cannot create a pass.
def test_agent_only_evidence_is_pending_and_reason_names_agent() -> None:
    agent_pass = make_evidence(
        evidence_type=ET.TEST_RESULT, status=ES.PASSED, created_by_type=ActorType.AGENT
    )
    result = evaluate_machine_check(VCT.TESTS, head_commit=HEAD, evidence=[agent_pass])

    assert result.status == ES.PENDING  # wrong answer: PASSED — agent forged a pass
    assert result.evidence_ids == ()
    assert "agent" in result.reason.lower()  # crux: reason must say claims are ignored


# --- T2 (crux, side 2): agent PASSED cannot hide a system-tier FAIL.
def test_system_fail_with_agent_pass_at_head_evaluates_failed() -> None:
    system_fail = make_evidence(
        evidence_type=ET.TEST_RESULT, status=ES.FAILED, created_by_type=ActorType.SYSTEM
    )
    agent_pass = make_evidence(
        evidence_type=ET.TEST_RESULT, status=ES.PASSED, created_by_type=ActorType.AGENT
    )
    result = evaluate_machine_check(
        VCT.TESTS, head_commit=HEAD, evidence=[agent_pass, system_fail]
    )

    assert result.status == ES.FAILED  # wrong answer: PASSED — agent hid the failure
    assert result.evidence_ids == (system_fail.id,)


# --- AC3: a system PASSED at a DIFFERENT commit never satisfies the check.
def test_system_passed_at_other_commit_is_pending() -> None:
    stale = make_evidence(
        evidence_type=ET.TEST_RESULT,
        status=ES.PASSED,
        created_by_type=ActorType.SYSTEM,
        commit_sha=OTHER,
    )
    result = evaluate_machine_check(VCT.TESTS, head_commit=HEAD, evidence=[stale])

    assert result.status == ES.PENDING  # wrong answer: PASSED — older commit leaked in
    assert result.evidence_ids == ()
    # Not an agent record, so the reason must NOT claim agent evidence was ignored.
    assert "agent" not in result.reason.lower()


# --- AC4: status passes through FAILED (not collapsed to PENDING).
def test_system_failed_lint_at_head_evaluates_failed() -> None:
    record = make_evidence(
        evidence_type=ET.LINT_RESULT, status=ES.FAILED, created_by_type=ActorType.SYSTEM
    )
    result = evaluate_machine_check(VCT.LINT, head_commit=HEAD, evidence=[record])

    assert result.status == ES.FAILED  # wrong answer: PENDING — a fail is not "missing"
    assert result.evidence_ids == (record.id,)


# --- T1: WARNING is non-passing but not a hard failure.
def test_system_warning_at_head_holds_pending() -> None:
    record = make_evidence(
        evidence_type=ET.TEST_RESULT,
        status=ES.WARNING,
        created_by_type=ActorType.SYSTEM,
    )
    result = evaluate_machine_check(VCT.TESTS, head_commit=HEAD, evidence=[record])

    assert result.status == ES.PENDING
    assert result.evidence_ids == (record.id,)


# --- AC5: latest by GitHub source time wins (newer FAILED over older PASSED).
def test_latest_by_source_event_at_wins() -> None:
    older = make_evidence(
        evidence_type=ET.TEST_RESULT,
        status=ES.PASSED,
        created_by_type=ActorType.SYSTEM,
        source_event_at=NOW,
    )
    newer = make_evidence(
        evidence_type=ET.TEST_RESULT,
        status=ES.FAILED,
        created_by_type=ActorType.SYSTEM,
        # Pull order says the opposite; GitHub lifecycle time is authoritative.
        created_at=NOW - timedelta(minutes=5),
        source_event_at=NOW + timedelta(minutes=5),
    )
    result = evaluate_machine_check(
        VCT.TESTS, head_commit=HEAD, evidence=[newer, older]
    )

    assert result.status == ES.FAILED  # wrong answer: PASSED — picked the stale record
    assert result.evidence_ids == (newer.id,)


# --- AC5b: UUID never decides equal source timestamps.
def test_equal_source_time_folds_without_uuid_recency() -> None:
    low_id = UUID(int=1)
    high_id = UUID(int=2)
    passed_low = make_evidence(
        evidence_type=ET.TEST_RESULT,
        status=ES.PASSED,
        created_by_type=ActorType.SYSTEM,
        source_event_at=NOW,
        id=low_id,
    )
    failed_high = make_evidence(
        evidence_type=ET.TEST_RESULT,
        status=ES.FAILED,
        created_by_type=ActorType.SYSTEM,
        source_event_at=NOW,
        id=high_id,
    )
    result = evaluate_machine_check(
        VCT.TESTS, head_commit=HEAD, evidence=[failed_high, passed_low]
    )

    assert result.status == ES.FAILED
    assert result.evidence_ids == (high_id, low_id)


def test_latest_execution_is_resolved_independently_per_job() -> None:
    stale_failure = make_evidence(
        evidence_type=ET.TEST_RESULT,
        status=ES.FAILED,
        created_by_type=ActorType.SYSTEM,
        job_name="test",
        source_event_at=NOW,
    )
    fixed = make_evidence(
        evidence_type=ET.TEST_RESULT,
        status=ES.PASSED,
        created_by_type=ActorType.SYSTEM,
        job_name="test",
        source_event_at=NOW + timedelta(minutes=2),
    )
    other_job = make_evidence(
        evidence_type=ET.TEST_RESULT,
        status=ES.PASSED,
        created_by_type=ActorType.SYSTEM,
        job_name="test-operator-ui",
        source_event_at=NOW + timedelta(minutes=1),
    )

    result = evaluate_machine_check(
        VCT.TESTS,
        head_commit=HEAD,
        evidence=[stale_failure, other_job, fixed],
    )

    assert result.status is ES.PASSED
    assert result.evidence_ids == (fixed.id, other_job.id)


def test_legacy_rows_without_job_metadata_fail_closed() -> None:
    legacy = make_evidence(
        evidence_type=ET.TEST_RESULT,
        status=ES.PASSED,
        created_by_type=ActorType.SYSTEM,
        job_name=None,
        source_event_at=None,
    )

    result = evaluate_machine_check(VCT.TESTS, head_commit=HEAD, evidence=[legacy])

    assert result.status is ES.PENDING
    assert result.evidence_ids == ()
    assert "re-pulled" in result.reason


def test_unmatched_legacy_row_holds_mixed_evidence_pending() -> None:
    current = make_evidence(
        evidence_type=ET.TEST_RESULT,
        status=ES.PASSED,
        created_by_type=ActorType.SYSTEM,
        job_name="test",
        source_event_at=NOW,
        external_run_id="current",
        payload_hash="current-hash",
    )
    unknown_legacy = make_evidence(
        evidence_type=ET.TEST_RESULT,
        status=ES.PASSED,
        created_by_type=ActorType.SYSTEM,
        job_name=None,
        source_event_at=None,
        external_run_id="unknown",
        payload_hash="unknown-hash",
    )

    result = evaluate_machine_check(
        VCT.TESTS, head_commit=HEAD, evidence=[current, unknown_legacy]
    )

    assert result.status is ES.PENDING
    assert result.evidence_ids == ()


def test_enriched_duplicate_supersedes_legacy_row() -> None:
    legacy = make_evidence(
        evidence_type=ET.TEST_RESULT,
        status=ES.PASSED,
        created_by_type=ActorType.SYSTEM,
        job_name=None,
        source_event_at=None,
        external_run_id="same-run",
        payload_hash="same-hash",
    )
    enriched = make_evidence(
        evidence_type=ET.TEST_RESULT,
        status=ES.PASSED,
        created_by_type=ActorType.SYSTEM,
        job_name="test",
        source_event_at=NOW,
        external_run_id="same-run",
        payload_hash="same-hash",
    )

    result = evaluate_machine_check(
        VCT.TESTS, head_commit=HEAD, evidence=[legacy, enriched]
    )

    assert result.status is ES.PASSED
    assert result.evidence_ids == (enriched.id,)


@pytest.mark.parametrize("completed_status", [ES.PASSED, ES.FAILED])
def test_ordered_lifecycle_snapshot_supersedes_queued_same_execution(
    completed_status: EvidenceStatus,
) -> None:
    queued = make_evidence(
        evidence_type=ET.TEST_RESULT,
        status=ES.PENDING,
        created_by_type=ActorType.SYSTEM,
        source_event_at=None,
        external_run_id="run-42",
        payload_hash="queued",
    )
    completed = make_evidence(
        evidence_type=ET.TEST_RESULT,
        status=completed_status,
        created_by_type=ActorType.SYSTEM,
        source_event_at=NOW,
        external_run_id="run-42",
        payload_hash="completed",
    )

    result = evaluate_machine_check(
        VCT.TESTS, head_commit=HEAD, evidence=[queued, completed]
    )

    assert result.status is completed_status
    assert result.evidence_ids == (completed.id,)


def test_unordered_different_execution_holds_job_pending() -> None:
    completed = make_evidence(
        evidence_type=ET.TEST_RESULT,
        status=ES.PASSED,
        created_by_type=ActorType.SYSTEM,
        source_event_at=NOW,
        external_run_id="completed-run",
    )
    independent_queued = make_evidence(
        evidence_type=ET.TEST_RESULT,
        status=ES.PENDING,
        created_by_type=ActorType.SYSTEM,
        source_event_at=None,
        external_run_id="queued-run",
    )

    result = evaluate_machine_check(
        VCT.TESTS, head_commit=HEAD, evidence=[completed, independent_queued]
    )

    assert result.status is ES.PENDING
    assert result.evidence_ids == (independent_queued.id,)


def test_unordered_record_without_execution_id_holds_job_pending() -> None:
    completed = make_evidence(
        evidence_type=ET.TEST_RESULT,
        status=ES.PASSED,
        created_by_type=ActorType.SYSTEM,
        source_event_at=NOW,
        external_run_id="completed-run",
    )
    uncorrelated = make_evidence(
        evidence_type=ET.TEST_RESULT,
        status=ES.PENDING,
        created_by_type=ActorType.SYSTEM,
        source_event_at=None,
        external_run_id=None,
    )

    result = evaluate_machine_check(
        VCT.TESTS, head_commit=HEAD, evidence=[uncorrelated, completed]
    )

    assert result.status is ES.PENDING
    assert result.evidence_ids == (uncorrelated.id,)


def test_lifecycle_result_is_independent_of_uuid_assignment() -> None:
    def evaluate(queued_id: UUID, completed_id: UUID) -> EvidenceStatus:
        queued = make_evidence(
            evidence_type=ET.TEST_RESULT,
            status=ES.PENDING,
            created_by_type=ActorType.SYSTEM,
            source_event_at=None,
            external_run_id="run-42",
            id=queued_id,
        )
        completed = make_evidence(
            evidence_type=ET.TEST_RESULT,
            status=ES.FAILED,
            created_by_type=ActorType.SYSTEM,
            source_event_at=NOW,
            external_run_id="run-42",
            id=completed_id,
        )
        return evaluate_machine_check(
            VCT.TESTS, head_commit=HEAD, evidence=[queued, completed]
        ).status

    assert evaluate(UUID(int=1), UUID(int=2)) is ES.FAILED
    assert evaluate(UUID(int=2), UUID(int=1)) is ES.FAILED


def test_raw_github_queued_then_completed_ingests_and_evaluates(
    tmp_path: Path,
) -> None:
    database = Database(f"sqlite:///{tmp_path}/atlas.db")
    database.create_all()
    repo = EvidenceRepo(database)
    product_id = uuid4()
    queued_payload = {
        "id": 42,
        "name": "test",
        "status": "queued",
        "conclusion": None,
        "started_at": None,
        "completed_at": None,
        "html_url": "https://github.com/acme/atlas/runs/42",
    }
    completed_payload = queued_payload | {
        "status": "completed",
        "conclusion": "success",
        "started_at": "2026-06-28T12:00:00Z",
        "completed_at": "2026-06-28T12:05:00Z",
    }

    ingest_checks(
        [normalise_check_run(queued_payload, head_sha=HEAD)],
        repo=repo,
        product_id=product_id,
        now=NOW,
    )
    ingest_checks(
        [normalise_check_run(completed_payload, head_sha=HEAD)],
        repo=repo,
        product_id=product_id,
        now=NOW + timedelta(minutes=5),
    )

    stored = repo.list()
    assert len(stored) == 2
    result = evaluate_machine_check(VCT.TESTS, head_commit=HEAD, evidence=stored)
    assert result.status is ES.PASSED
    assert result.evidence_ids == (
        next(record.id for record in stored if record.status is ES.PASSED),
    )


# --- AC6: a non-machine check type is NOT_APPLICABLE and never raises.
@pytest.mark.parametrize(
    "check_type",
    [
        VCT.SCOPE,
        VCT.DOCUMENTATION,
        VCT.ACCEPTANCE_CRITERIA,
        VCT.HUMAN_APPROVAL,
        VCT.SECURITY,
    ],
)
def test_non_machine_check_type_is_not_applicable(
    check_type: VerificationCheckType,
) -> None:
    system_pass = make_evidence(
        evidence_type=ET.TEST_RESULT, status=ES.PASSED, created_by_type=ActorType.SYSTEM
    )
    result = evaluate_machine_check(
        check_type, head_commit=HEAD, evidence=[system_pass]
    )

    # wrong answer: PASSED — this evaluator must not decide non-machine checks.
    assert result.status == ES.NOT_APPLICABLE
    assert result.evidence_ids == ()
    assert result.check_type == check_type


# --- AC7: never raises across empty evidence, commit_sha=None, unknown type.
def test_empty_evidence_is_pending_not_raise() -> None:
    result = evaluate_machine_check(VCT.TESTS, head_commit=HEAD, evidence=[])

    assert result.status == ES.PENDING  # wrong answer: an exception, or FAILED
    assert result.evidence_ids == ()


def test_commit_sha_none_record_does_not_raise_and_is_not_a_candidate() -> None:
    no_commit = make_evidence(
        evidence_type=ET.TEST_RESULT,
        status=ES.PASSED,
        created_by_type=ActorType.SYSTEM,
        commit_sha=None,
    )
    # None != HEAD, so it is not a candidate; the check is PENDING, no raise.
    result = evaluate_machine_check(VCT.TESTS, head_commit=HEAD, evidence=[no_commit])

    assert result.status == ES.PENDING
    assert result.evidence_ids == ()


def test_unknown_check_type_does_not_raise() -> None:
    # A fabricated, non-member value must yield NOT_APPLICABLE, never a raise —
    # the same defensive-branch discipline as ATLAS-71's _FakeType guard.
    fake = "totally_unknown_check"
    result = evaluate_machine_check(fake, head_commit=HEAD, evidence=[])  # type: ignore[arg-type]

    assert result.status == ES.NOT_APPLICABLE
    assert result.evidence_ids == ()


# --- Result shape and exported-surface guards.
def test_result_is_frozen_dataclass() -> None:
    result = evaluate_machine_check(VCT.TESTS, head_commit=HEAD, evidence=[])
    assert isinstance(result, MachineCheckEvaluation)
    with pytest.raises(Exception):  # noqa: B017 — FrozenInstanceError is the point
        result.status = ES.PASSED  # type: ignore[misc]


def test_machine_check_set_is_exactly_tests_and_lint() -> None:
    # wrong answer: BUILD/COVERAGE present — they are NOT v1 machine checks.
    assert set(MACHINE_CHECK_TYPES) == {VCT.TESTS, VCT.LINT}
    assert MACHINE_CHECK_EVIDENCE == {
        VCT.TESTS: ET.TEST_RESULT,
        VCT.LINT: ET.LINT_RESULT,
    }
