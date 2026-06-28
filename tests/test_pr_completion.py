"""ATLAS-77: the PR completion validator aggregates the per-ticket verdicts of the
tickets a PR closes into one PR verdict. :func:`evaluate_pr` calls
:func:`evaluate_ticket` for each closed ticket — passing every ticket the SAME PR
files, head commit, and evidence set — and folds the verdicts: PASSED iff every
ticket PASSED; FAILED if any FAILED (fail precedence); else PENDING; an empty
close-set is PENDING. The breakdown is ordered by ``ticket.key`` (D6). It NEVER
raises.

Each behavioural assertion names the wrong answer it would catch. The keystone is
AC5 — per-ticket isolation: two same-shaped tickets sharing the one evidence set,
where ONLY ticket A's acceptance confirmations are present (scoped to A's
ticket_id), so A is genuinely fully PASSED and B is PENDING solely because the
A-scoped confirmations do not satisfy B. The named wrong answer "both PASSED" bites
only if evidence leaks across the ticket_id boundary — proving aggregation passes
each ticket its own identity.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from atlas.core.enums import ActorType, EvidenceStatus, RiskLevel
from atlas.core.models import Evidence, EvidenceType, VerificationCheckType
from atlas.core.models.ticket import Ticket, TicketStatus, TicketType
from atlas.verification import (
    PRVerification,
    TicketVerification,
    acceptance_criterion_hash,
    evaluate_pr,
)
from atlas.verification.acceptance_check import ACCEPTANCE_CRITERION_HASH_KEY
from atlas.verification.scope_check import SCOPE_DECISION_PATH_KEY

NOW = datetime(2026, 6, 28, tzinfo=UTC)
HEAD = "c0ffee0000000000000000000000000000000000"

TICKET_A = UUID(int=0xA77A)
TICKET_B = UUID(int=0xB77B)

AC1 = "Acceptance criterion one"
AC2 = "Acceptance criterion two"

ES = EvidenceStatus
ET = EvidenceType
VT = VerificationCheckType


def make_ticket(
    *,
    key: str,
    ticket_id: UUID,
    ticket_type: TicketType = TicketType.BUG,
    risk_level: RiskLevel = RiskLevel.LOW,
    acceptance_criteria: list[str] | None = None,
) -> Ticket:
    """A bug/low ticket → required checks TESTS, LINT, ACCEPTANCE, SCOPE (no
    DOCUMENTATION, no HUMAN_APPROVAL, no SECURITY): the clean shape whose only
    human-tier requirement is acceptance, so per-ticket isolation turns on exactly
    the ticket-scoped acceptance confirmations.
    """
    return Ticket(
        id=ticket_id,
        product_id=uuid4(),
        epic_id=None,
        key=key,
        title="t",
        objective="o",
        context="c",
        status=TicketStatus.REVIEW_REQUIRED,
        ticket_type=ticket_type,
        risk_level=risk_level,
        priority=1,
        relevant_docs=[],
        acceptance_criteria=[AC1, AC2]
        if acceptance_criteria is None
        else acceptance_criteria,
        documentation_requirements=[],
        source_anchor="docs/foo.md#section",
        created_by_type=ActorType.HUMAN,
        created_by_id="operator",
        created_at=NOW,
        updated_at=NOW,
    )


def _evidence(
    evidence_type: EvidenceType,
    *,
    status: EvidenceStatus,
    created_by_type: ActorType,
    ticket_id: UUID,
    commit_sha: str | None = HEAD,
    raw_payload: dict[str, Any] | None = None,
    created_at: datetime = NOW,
) -> Evidence:
    return Evidence(
        id=uuid4(),
        product_id=uuid4(),
        ticket_id=ticket_id,
        evidence_type=evidence_type,
        status=status,
        summary="e",
        commit_sha=commit_sha,
        raw_payload=raw_payload or {},
        created_by_type=created_by_type,
        created_by_id="ci" if created_by_type == ActorType.SYSTEM else "operator",
        created_at=created_at,
    )


def sys_test(*, ticket_id: UUID, status: EvidenceStatus = ES.PASSED) -> Evidence:
    return _evidence(
        ET.TEST_RESULT,
        status=status,
        created_by_type=ActorType.SYSTEM,
        ticket_id=ticket_id,
    )


def sys_lint(*, ticket_id: UUID, status: EvidenceStatus = ES.PASSED) -> Evidence:
    return _evidence(
        ET.LINT_RESULT,
        status=status,
        created_by_type=ActorType.SYSTEM,
        ticket_id=ticket_id,
    )


def human_ac(criterion: str, *, ticket_id: UUID, **kw: Any) -> Evidence:
    return _evidence(
        ET.MANUAL_APPROVAL,
        status=ES.PASSED,
        created_by_type=ActorType.HUMAN,
        ticket_id=ticket_id,
        raw_payload={
            ACCEPTANCE_CRITERION_HASH_KEY: acceptance_criterion_hash(criterion)
        },
        **kw,
    )


def passing_evidence(ticket_id: UUID) -> list[Evidence]:
    """The full evidence set that makes a bug/low ticket PASSED at HEAD: system-tier
    tests+lint and both human-tier acceptance confirmations, all scoped to the
    ticket. Scope passes with empty pr_files.
    """
    return [
        sys_test(ticket_id=ticket_id),
        sys_lint(ticket_id=ticket_id),
        human_ac(AC1, ticket_id=ticket_id, created_at=NOW),
        human_ac(AC2, ticket_id=ticket_id, created_at=NOW + timedelta(hours=1)),
    ]


def scope_decision(path: str, *, ticket_id: UUID, status: EvidenceStatus) -> Evidence:
    """A human-tier, ticket-scoped scope decision for ``path`` at HEAD: PASSED waives
    the out-of-scope file, FAILED rejects it (a scope dispute). Ticket-scoped, so it
    decides only the named ticket's scope.
    """
    return _evidence(
        ET.MANUAL_APPROVAL,
        status=status,
        created_by_type=ActorType.HUMAN,
        ticket_id=ticket_id,
        raw_payload={SCOPE_DECISION_PATH_KEY: path},
    )


def verification_for(result: PRVerification, ticket_id: UUID) -> TicketVerification:
    matches = [v for v in result.tickets if v.ticket_id == ticket_id]
    assert len(matches) == 1, f"expected exactly one verification for {ticket_id}"
    return matches[0]


# --- AC1: two tickets, both verdict PASSED → PR PASSED; breakdown carries both.
def test_all_tickets_passed_yields_pr_passed() -> None:
    a = make_ticket(key="ATLAS-1", ticket_id=TICKET_A)
    b = make_ticket(key="ATLAS-2", ticket_id=TICKET_B)
    evidence = passing_evidence(TICKET_A) + passing_evidence(TICKET_B)

    result = evaluate_pr([a, b], pr_files=[], head_commit=HEAD, evidence=evidence)

    # wrong answer: PENDING — both closed tickets are fully proven at the head commit.
    assert result.status == ES.PASSED
    assert result.head_commit == HEAD
    assert {v.ticket_id for v in result.tickets} == {TICKET_A, TICKET_B}
    assert verification_for(result, TICKET_A).status == ES.PASSED
    assert verification_for(result, TICKET_B).status == ES.PASSED


# --- AC2: one ticket PENDING, the other PASSED → PR PENDING.
def test_one_pending_ticket_yields_pr_pending() -> None:
    a = make_ticket(key="ATLAS-1", ticket_id=TICKET_A)
    b = make_ticket(key="ATLAS-2", ticket_id=TICKET_B)
    # B is missing one acceptance confirmation → B PENDING.
    evidence = [
        *passing_evidence(TICKET_A),
        sys_test(ticket_id=TICKET_B),
        sys_lint(ticket_id=TICKET_B),
        human_ac(AC1, ticket_id=TICKET_B),
    ]

    result = evaluate_pr([a, b], pr_files=[], head_commit=HEAD, evidence=evidence)

    # wrong answer: PASSED — one unproven ticket holds the whole PR at PENDING.
    assert result.status == ES.PENDING
    assert verification_for(result, TICKET_A).status == ES.PASSED
    assert verification_for(result, TICKET_B).status == ES.PENDING


# --- AC3: one ticket FAILED → PR FAILED regardless of the others (fail precedence).
# The failure must be ticket-scoped so A stays genuinely PASSED: a shared out-of-
# scope PR file, WAIVED for A but REJECTED (a scope dispute) for B. (A FAILED machine
# check would be commit-scoped and would sink both tickets, not isolate the failure.)
def test_one_failed_ticket_yields_pr_failed() -> None:
    a = make_ticket(key="ATLAS-1", ticket_id=TICKET_A)
    b = make_ticket(key="ATLAS-2", ticket_id=TICKET_B)
    out_file = "out/of/scope.py"  # out-of-scope for both (relevant_docs=[]).
    evidence = [
        *passing_evidence(TICKET_A),
        *passing_evidence(TICKET_B),
        scope_decision(out_file, ticket_id=TICKET_A, status=ES.PASSED),  # A waives
        scope_decision(out_file, ticket_id=TICKET_B, status=ES.FAILED),  # B rejects
    ]

    result = evaluate_pr(
        [a, b], pr_files=[out_file], head_commit=HEAD, evidence=evidence
    )

    # wrong answer: PASSED/PENDING — a single failing ticket sinks the PR.
    assert result.status == ES.FAILED
    assert verification_for(result, TICKET_A).status == ES.PASSED
    assert verification_for(result, TICKET_B).status == ES.FAILED
    assert {o.check_type: o.status for o in verification_for(result, TICKET_B).checks}[
        VT.SCOPE
    ] == ES.FAILED


# --- AC4: empty ticket set → PENDING (never a vacuous PASS over no tickets).
def test_empty_ticket_set_yields_pending() -> None:
    result = evaluate_pr([], pr_files=[], head_commit=HEAD, evidence=[])

    # wrong answer: PASSED — an empty close-set is unproven, not a vacuous pass.
    assert result.status == ES.PENDING
    assert result.tickets == ()
    assert result.head_commit == HEAD


# --- AC5 (KEYSTONE): per-ticket isolation. A and B are the SAME shape and share the
# one evidence set; tests/lint/scope pass for BOTH. The ONLY difference is that the
# acceptance confirmations are scoped to A's ticket_id — so A is genuinely, fully
# PASSED and B is PENDING solely because those A-scoped confirmations do not satisfy
# B. Without ticket_id isolation, B would pass on A's confirmations.
def test_per_ticket_isolation_evidence_does_not_leak_across_tickets() -> None:
    a = make_ticket(key="ATLAS-1", ticket_id=TICKET_A)
    b = make_ticket(key="ATLAS-2", ticket_id=TICKET_B)
    # System-tier machine evidence for BOTH (not ticket-scoped by the machine
    # evaluator — it gates on commit + tier), so tests/lint pass for A and B alike.
    # Acceptance confirmations exist ONLY for A. Empty pr_files → scope passes for
    # both. So the sole A↔B difference is the ticket-scoped acceptance evidence.
    evidence = [
        sys_test(ticket_id=TICKET_A),
        sys_lint(ticket_id=TICKET_A),
        sys_test(ticket_id=TICKET_B),
        sys_lint(ticket_id=TICKET_B),
        human_ac(AC1, ticket_id=TICKET_A, created_at=NOW),
        human_ac(AC2, ticket_id=TICKET_A, created_at=NOW + timedelta(hours=1)),
    ]

    result = evaluate_pr([a, b], pr_files=[], head_commit=HEAD, evidence=evidence)

    # wrong answer: both PASSED — evidence leaked across the ticket_id boundary
    # (A's confirmations satisfied B). A is genuinely fully PASSED; B is PENDING
    # solely because A's confirmations are not B's.
    assert verification_for(result, TICKET_A).status == ES.PASSED
    assert verification_for(result, TICKET_B).status == ES.PENDING
    assert result.status == ES.PENDING
    # The B↔A difference is exactly acceptance: B's acceptance check is the unproven
    # one (its machine/scope checks passed on the shared/empty inputs).
    b_checks = {
        o.check_type: o.status for o in verification_for(result, TICKET_B).checks
    }
    assert b_checks[VT.TESTS] == ES.PASSED
    assert b_checks[VT.LINT] == ES.PASSED
    assert b_checks[VT.SCOPE] == ES.PASSED
    assert b_checks[VT.ACCEPTANCE_CRITERIA] == ES.PENDING


# --- AC6 (D6): a scrambled input order yields the breakdown ordered by ticket.key.
def test_breakdown_is_ordered_by_ticket_key() -> None:
    # Keys deliberately out of order vs input; "ATLAS-10" must sort by key string,
    # consistently with however the caller scrambles the input.
    t2 = make_ticket(key="ATLAS-2", ticket_id=UUID(int=2))
    t10 = make_ticket(key="ATLAS-10", ticket_id=UUID(int=10))
    t1 = make_ticket(key="ATLAS-1", ticket_id=UUID(int=1))
    scrambled = [t10, t1, t2]
    evidence = (
        passing_evidence(t1.id) + passing_evidence(t2.id) + passing_evidence(t10.id)
    )

    result = evaluate_pr(scrambled, pr_files=[], head_commit=HEAD, evidence=evidence)

    # wrong answer: input order (ATLAS-10, ATLAS-1, ATLAS-2) — the breakdown is
    # ordered by ticket.key regardless of caller order. (String sort: "ATLAS-1" <
    # "ATLAS-10" < "ATLAS-2".)
    keys_in_order = [t.id for t in sorted(scrambled, key=lambda x: x.key)]
    assert [v.ticket_id for v in result.tickets] == keys_in_order
    assert [v.ticket_id for v in result.tickets] == [t1.id, t10.id, t2.id]


# --- AC9: never raises across an empty ticket set, empty evidence, and empty
# pr_files (singly and together).
def test_never_raises_across_empty_inputs() -> None:
    # Empty everything.
    assert evaluate_pr([], pr_files=[], head_commit=HEAD, evidence=[]).status == (
        ES.PENDING
    )
    # A real ticket but no evidence and no pr_files → PENDING, not an exception.
    a = make_ticket(key="ATLAS-1", ticket_id=TICKET_A)
    result = evaluate_pr([a], pr_files=[], head_commit=HEAD, evidence=[])
    # wrong answer: an exception, or a vacuous PASS over unproven checks.
    assert isinstance(result, PRVerification)
    assert result.status == ES.PENDING
