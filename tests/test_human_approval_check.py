"""ATLAS-76 (OP-A): the human_approval evaluator decides the ``human_approval``
check against pre-loaded evidence. A blanket human-tier MANUAL_APPROVAL pinned to
the head commit and scoped to the ticket — carrying NEITHER the acceptance
criterion hash NOR the scope decision path — approves the PR; its status passes
through (OP-B: PASSED = approved, FAILED = rejected); the latest by
``(created_at, id)`` decides; none → PENDING; and the evaluator NEVER raises.

Each behavioural assertion names the wrong answer it would catch. The crux
property is discrimination-by-ABSENCE (a record carrying either discriminator is
NOT a blanket approval — it is an acceptance confirmation or a scope decision),
guarded directly against a record that carries each key. The human-tier filter
(a system- or agent-tier MANUAL_APPROVAL does not approve a PR) and the
commit/ticket pins are guarded too.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from atlas.core.enums import ActorType, EvidenceStatus
from atlas.core.models import Evidence, EvidenceType, VerificationCheckType
from atlas.verification import HumanApprovalEvaluation, evaluate_human_approval
from atlas.verification.acceptance_check import ACCEPTANCE_CRITERION_HASH_KEY
from atlas.verification.scope_check import SCOPE_DECISION_PATH_KEY

NOW = datetime(2026, 6, 28, tzinfo=UTC)
HEAD = "c0ffee0000000000000000000000000000000000"
OTHER = "dead000000000000000000000000000000000000"
TICKET = UUID(int=0xA76)
OTHER_TICKET = UUID(int=0xB76)

ES = EvidenceStatus
ET = EvidenceType


def make_approval(
    *,
    status: EvidenceStatus = ES.PASSED,
    ticket_id: UUID = TICKET,
    commit_sha: str | None = HEAD,
    created_by_type: ActorType = ActorType.HUMAN,
    created_at: datetime = NOW,
    id: UUID | None = None,
    raw_payload: dict[str, Any] | None = None,
    evidence_type: EvidenceType = ET.MANUAL_APPROVAL,
) -> Evidence:
    """A blanket PR approval shaped like the convention.

    By default a human-tier MANUAL_APPROVAL pinned to HEAD, scoped to TICKET,
    carrying NEITHER discriminator (an empty payload). ``raw_payload`` overrides
    the payload wholesale for the discriminated/malformed cases.
    """
    if raw_payload is None:
        raw_payload = {}
    return Evidence(
        id=id or uuid4(),
        product_id=uuid4(),
        ticket_id=ticket_id,
        evidence_type=evidence_type,
        status=status,
        summary="operator PR approval",
        commit_sha=commit_sha,
        raw_payload=raw_payload,
        created_by_type=created_by_type,
        created_by_id="operator" if created_by_type == ActorType.HUMAN else "claude",
        created_at=created_at,
    )


# --- A blanket human-tier approval at C → PASSED, naming the deciding record.
def test_blanket_human_approval_at_head_passes() -> None:
    approval = make_approval()
    result = evaluate_human_approval(
        ticket_id=TICKET, head_commit=HEAD, evidence=[approval]
    )

    # wrong answer: PENDING — a blanket human approval at the head commit approves.
    assert result.status == ES.PASSED
    assert result.check_type == VerificationCheckType.HUMAN_APPROVAL
    assert result.evidence_id == approval.id


# --- An acceptance confirmation (carries the hash) does NOT approve the PR.
def test_acceptance_confirmation_does_not_satisfy_human_approval() -> None:
    confirmation = make_approval(raw_payload={ACCEPTANCE_CRITERION_HASH_KEY: "abc"})
    result = evaluate_human_approval(
        ticket_id=TICKET, head_commit=HEAD, evidence=[confirmation]
    )

    # wrong answer: PASSED on a discriminated record — a record carrying the
    # acceptance hash is a criterion confirmation, not a blanket PR approval.
    assert result.status == ES.PENDING
    assert result.evidence_id is None


# --- A scope decision (carries the path) does NOT approve the PR.
def test_scope_decision_does_not_satisfy_human_approval() -> None:
    decision = make_approval(raw_payload={SCOPE_DECISION_PATH_KEY: "atlas/x.py"})
    result = evaluate_human_approval(
        ticket_id=TICKET, head_commit=HEAD, evidence=[decision]
    )

    # wrong answer: PASSED on a discriminated record — a record carrying the
    # scope path is a scope decision, not a blanket PR approval.
    assert result.status == ES.PENDING
    assert result.evidence_id is None


# --- OP-B: a blanket approval with status FAILED at C → FAILED (pass-through).
def test_blanket_approval_failed_passes_through_as_failed() -> None:
    rejection = make_approval(status=ES.FAILED)
    result = evaluate_human_approval(
        ticket_id=TICKET, head_commit=HEAD, evidence=[rejection]
    )

    # wrong answer: PENDING/PASSED — the operator rejected the PR; FAILED passes
    # through (OP-B), routing to needs_human_decision.
    assert result.status == ES.FAILED
    assert result.evidence_id == rejection.id


# --- The latest by (created_at, id) decides when an approve and a reject coexist.
def test_latest_decision_wins_reject_after_approve() -> None:
    approve = make_approval(status=ES.PASSED, created_at=NOW)
    reject = make_approval(status=ES.FAILED, created_at=NOW + timedelta(hours=1))
    # Order scrambled so the result cannot depend on input order.
    result = evaluate_human_approval(
        ticket_id=TICKET, head_commit=HEAD, evidence=[reject, approve]
    )

    # wrong answer: PASSED — the later reject overrides the earlier approve.
    assert result.status == ES.FAILED
    assert result.evidence_id == reject.id


def test_latest_decision_wins_approve_after_reject_id_tiebreak() -> None:
    # Equal created_at → the id tiebreak decides; pick ids so approve > reject.
    reject = make_approval(status=ES.FAILED, created_at=NOW, id=UUID(int=1))
    approve = make_approval(status=ES.PASSED, created_at=NOW, id=UUID(int=2))
    result = evaluate_human_approval(
        ticket_id=TICKET, head_commit=HEAD, evidence=[approve, reject]
    )

    # wrong answer: FAILED — equal timestamps, the larger id (approve) wins.
    assert result.status == ES.PASSED
    assert result.evidence_id == approve.id


# --- A different commit, a different ticket, and non-human tiers are all ignored.
def test_approval_at_other_commit_is_ignored() -> None:
    result = evaluate_human_approval(
        ticket_id=TICKET, head_commit=HEAD, evidence=[make_approval(commit_sha=OTHER)]
    )
    # wrong answer: PASSED — an approval pinned to a different commit cannot decide.
    assert result.status == ES.PENDING
    assert result.evidence_id is None


def test_approval_for_other_ticket_is_ignored() -> None:
    result = evaluate_human_approval(
        ticket_id=TICKET,
        head_commit=HEAD,
        evidence=[make_approval(ticket_id=OTHER_TICKET)],
    )
    # wrong answer: PASSED — an approval scoped to a different ticket cannot decide.
    assert result.status == ES.PENDING
    assert result.evidence_id is None


def test_agent_and_system_approvals_are_ignored() -> None:
    agent = make_approval(created_by_type=ActorType.AGENT)
    system = make_approval(created_by_type=ActorType.SYSTEM)
    result = evaluate_human_approval(
        ticket_id=TICKET, head_commit=HEAD, evidence=[agent, system]
    )
    # wrong answer: PASSED — only the operator (human tier) approves a PR; an
    # agent's or a machine's MANUAL_APPROVAL cannot (ADR-0008).
    assert result.status == ES.PENDING
    assert result.evidence_id is None


# --- Absence and never-raises.
def test_no_approval_is_pending_never_raises() -> None:
    result = evaluate_human_approval(ticket_id=TICKET, head_commit=HEAD, evidence=[])
    # wrong answer: FAILED — an unapproved PR is unproven, not failing.
    assert isinstance(result, HumanApprovalEvaluation)
    assert result.status == ES.PENDING
    assert result.evidence_id is None
