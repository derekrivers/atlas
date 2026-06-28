"""ATLAS-75: the machine-check evaluator decides one TESTS/LINT check against
pre-loaded evidence, using system-tier evidence pinned to the head commit and
NEVER raising.

Each behavioural assertion names the wrong answer it would catch. The
milestone crux is guarded from both sides: agent evidence can neither
manufacture a pass (AC2) nor hide a system-tier fail (T2). Status pass-through
is guarded across PASSED (AC1), FAILED (AC4), and WARNING (T1) so a later
``status in (PASSED,) else FAILED`` collapse refactor is caught. The
``(created_at, id)`` tiebreak is exercised both by time (AC5) and by a shared
timestamp (AC5b) so the id tiebreak is a real falsifiable guard, not dead code.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from atlas.core.enums import ActorType, EvidenceStatus
from atlas.core.models import Evidence, EvidenceType, VerificationCheckType
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
    id: UUID | None = None,
) -> Evidence:
    return Evidence(
        id=id or uuid4(),
        product_id=uuid4(),
        evidence_type=evidence_type,
        status=status,
        summary="ci run",
        commit_sha=commit_sha,
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
    assert result.evidence_id == record.id  # wrong answer: None, or a different record


# --- AC2 (crux, side 1): agent-tier PASSED alone cannot create a pass.
def test_agent_only_evidence_is_pending_and_reason_names_agent() -> None:
    agent_pass = make_evidence(
        evidence_type=ET.TEST_RESULT, status=ES.PASSED, created_by_type=ActorType.AGENT
    )
    result = evaluate_machine_check(VCT.TESTS, head_commit=HEAD, evidence=[agent_pass])

    assert result.status == ES.PENDING  # wrong answer: PASSED — agent forged a pass
    assert result.evidence_id is None
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
    assert result.evidence_id == system_fail.id


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
    assert result.evidence_id is None
    # Not an agent record, so the reason must NOT claim agent evidence was ignored.
    assert "agent" not in result.reason.lower()


# --- AC4: status passes through FAILED (not collapsed to PENDING).
def test_system_failed_lint_at_head_evaluates_failed() -> None:
    record = make_evidence(
        evidence_type=ET.LINT_RESULT, status=ES.FAILED, created_by_type=ActorType.SYSTEM
    )
    result = evaluate_machine_check(VCT.LINT, head_commit=HEAD, evidence=[record])

    assert result.status == ES.FAILED  # wrong answer: PENDING — a fail is not "missing"
    assert result.evidence_id == record.id


# --- T1 (no-collapse guard): WARNING passes through, not promoted/demoted.
def test_system_warning_at_head_evaluates_warning() -> None:
    record = make_evidence(
        evidence_type=ET.TEST_RESULT,
        status=ES.WARNING,
        created_by_type=ActorType.SYSTEM,
    )
    result = evaluate_machine_check(VCT.TESTS, head_commit=HEAD, evidence=[record])

    # wrong answers: PASSED (silently promoted) or FAILED (silently demoted) — a
    # `status in (PASSED,) else FAILED` collapse refactor fails exactly here.
    assert result.status == ES.WARNING
    assert result.evidence_id == record.id


# --- AC5: latest by created_at wins (newer FAILED over older PASSED).
def test_latest_by_created_at_wins() -> None:
    older = make_evidence(
        evidence_type=ET.TEST_RESULT,
        status=ES.PASSED,
        created_by_type=ActorType.SYSTEM,
        created_at=NOW,
    )
    newer = make_evidence(
        evidence_type=ET.TEST_RESULT,
        status=ES.FAILED,
        created_by_type=ActorType.SYSTEM,
        created_at=NOW + timedelta(minutes=5),
    )
    result = evaluate_machine_check(
        VCT.TESTS, head_commit=HEAD, evidence=[newer, older]
    )

    assert result.status == ES.FAILED  # wrong answer: PASSED — picked the stale record
    assert result.evidence_id == newer.id


# --- AC5b: id tiebreak when created_at ties (real guard, not dead code).
def test_id_tiebreaks_equal_created_at() -> None:
    low_id = UUID(int=1)
    high_id = UUID(int=2)
    # Same timestamp; the higher id must win under max((created_at, id)).
    passed_low = make_evidence(
        evidence_type=ET.TEST_RESULT,
        status=ES.PASSED,
        created_by_type=ActorType.SYSTEM,
        created_at=NOW,
        id=low_id,
    )
    failed_high = make_evidence(
        evidence_type=ET.TEST_RESULT,
        status=ES.FAILED,
        created_by_type=ActorType.SYSTEM,
        created_at=NOW,
        id=high_id,
    )
    # Pass in reverse order so a no-op tiebreak (first-wins) would pick PASSED.
    result = evaluate_machine_check(
        VCT.TESTS, head_commit=HEAD, evidence=[failed_high, passed_low]
    )

    assert result.evidence_id == high_id  # wrong answer: low_id — tiebreak is dead code
    assert result.status == ES.FAILED


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
    assert result.evidence_id is None
    assert result.check_type == check_type


# --- AC7: never raises across empty evidence, commit_sha=None, unknown type.
def test_empty_evidence_is_pending_not_raise() -> None:
    result = evaluate_machine_check(VCT.TESTS, head_commit=HEAD, evidence=[])

    assert result.status == ES.PENDING  # wrong answer: an exception, or FAILED
    assert result.evidence_id is None


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
    assert result.evidence_id is None


def test_unknown_check_type_does_not_raise() -> None:
    # A fabricated, non-member value must yield NOT_APPLICABLE, never a raise —
    # the same defensive-branch discipline as ATLAS-71's _FakeType guard.
    fake = "totally_unknown_check"
    result = evaluate_machine_check(fake, head_commit=HEAD, evidence=[])  # type: ignore[arg-type]

    assert result.status == ES.NOT_APPLICABLE
    assert result.evidence_id is None


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
