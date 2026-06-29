"""ATLAS-132 (OP-3.1): the pure operator-confirmation foundation.

The builders construct exactly the human-tier MANUAL_APPROVAL Evidence the three
human-tier evaluators match, and :func:`pending_capture` is their inverse view.
The spine of this suite is the ROUND-TRIP: every confirmation a builder produces
is fed into the REAL evaluator (``evaluate_acceptance_criteria``,
``evaluate_scope``, ``evaluate_human_approval``) at the same commit/ticket and
must flip it to the expected verdict — the builders cannot drift from the
evaluators because the tests run the evaluators, not a re-statement of their
rules. Each assertion names the wrong answer it would catch; the seeded-defect
sweep (runbook §7) proves AC-1 and AC-6 go red on the two planted defects.

``out_of_scope_paths`` is the single source the evaluator and the capture share;
AC-5 guards it against an independent expected set (so a regression that folds
the anchor SLUG into the in-scope key is caught even though both callers
delegate to it). The scope-capture is GATED on the ticket actually requiring the
``scope`` check, mirroring ``human_approval`` — a documentation ticket surfaces no
undecided scope files even with out-of-scope PR paths.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from atlas.core.enums import ActorType, EvidenceStatus, RiskLevel
from atlas.core.models import Evidence, EvidenceType
from atlas.core.models.ticket import Ticket, TicketStatus, TicketType
from atlas.core.trust import evidence_tier
from atlas.verification import (
    CriterionPrompt,
    PendingCapture,
    build_acceptance_confirmation,
    build_blanket_approval,
    build_scope_decision,
    evaluate_acceptance_criteria,
    evaluate_human_approval,
    evaluate_scope,
    out_of_scope_paths,
    pending_capture,
)
from atlas.verification.acceptance_check import ACCEPTANCE_CRITERION_HASH_KEY
from atlas.verification.scope_check import SCOPE_DECISION_PATH_KEY

NOW = datetime(2026, 6, 28, tzinfo=UTC)
HEAD = "c0ffee0000000000000000000000000000000000"
OTHER = "dead000000000000000000000000000000000000"
TICKET = UUID(int=0x132)
PRODUCT = UUID(int=0x9000)
OPERATOR = "operator"

# Declared scope: a relevant doc and the source_anchor's path part.
ANCHOR = "docs/atlas/verification-engine.md#evaluation-semantics"
ANCHOR_PATH = "docs/atlas/verification-engine.md"
DOC = "docs/atlas/scope-notes.md"
# Files NOT in the declared doc scope (the normal-PR code-file case). CODE_A
# sorts before CODE_B, so out_of_scope_paths returns them in this order.
CODE_A = "atlas/verification/scope_check.py"
CODE_B = "tests/test_scope_check.py"

AC1 = "Acceptance criterion one"
AC2 = "Acceptance criterion two"
AC3 = "Acceptance criterion three"

ES = EvidenceStatus
ET = EvidenceType


def make_ticket(
    *,
    ticket_type: TicketType = TicketType.FEATURE,
    risk_level: RiskLevel = RiskLevel.LOW,
    acceptance_criteria: list[str] | None = None,
    relevant_docs: list[str] | None = None,
    source_anchor: str = ANCHOR,
    ticket_id: UUID = TICKET,
) -> Ticket:
    """A Ticket carrying the fields ``pending_capture`` reads."""
    return Ticket(
        id=ticket_id,
        product_id=PRODUCT,
        key="ATLAS-1",
        title="t",
        objective="o",
        context="c",
        status=TicketStatus.REVIEW_REQUIRED,
        ticket_type=ticket_type,
        risk_level=risk_level,
        priority=1,
        relevant_docs=[DOC] if relevant_docs is None else relevant_docs,
        acceptance_criteria=(
            [AC1, AC2] if acceptance_criteria is None else acceptance_criteria
        ),
        documentation_requirements=[DOC],
        source_anchor=source_anchor,
        created_by_type=ActorType.HUMAN,
        created_by_id=OPERATOR,
        created_at=NOW,
        updated_at=NOW,
    )


def _ac(
    criterion: str, *, ticket_id: UUID = TICKET, head_commit: str = HEAD
) -> Evidence:
    return build_acceptance_confirmation(
        criterion,
        ticket_id=ticket_id,
        head_commit=head_commit,
        product_id=PRODUCT,
        operator_id=OPERATOR,
        evidence_id=uuid4(),
        now=NOW,
    )


def _scope(path: str, *, waive: bool, head_commit: str = HEAD) -> Evidence:
    return build_scope_decision(
        path,
        waive=waive,
        ticket_id=TICKET,
        head_commit=head_commit,
        product_id=PRODUCT,
        operator_id=OPERATOR,
        evidence_id=uuid4(),
        now=NOW,
    )


def _blanket(*, approved: bool, head_commit: str = HEAD) -> Evidence:
    return build_blanket_approval(
        approved=approved,
        ticket_id=TICKET,
        head_commit=head_commit,
        product_id=PRODUCT,
        operator_id=OPERATOR,
        evidence_id=uuid4(),
        now=NOW,
    )


# ── AC-1: acceptance round-trip against the real evaluator ──────────────────
def test_acceptance_round_trip_passes_real_evaluator() -> None:
    criteria = [AC1, AC2, AC3]
    built = [_ac(c) for c in criteria]
    result = evaluate_acceptance_criteria(
        criteria, ticket_id=TICKET, head_commit=HEAD, evidence=built
    )

    # wrong answer: PENDING — a built confirmation did not match its criterion.
    assert result.status == ES.PASSED
    # wrong answer: a partial/empty set — every built record must be named.
    assert set(result.evidence_ids) == {e.id for e in built}


# ── AC-2: scope waive/fail round-trip against the real evaluator ────────────
def test_scope_waive_round_trip_passes() -> None:
    waive = _scope(CODE_A, waive=True)
    result = evaluate_scope(
        [CODE_A],
        relevant_docs=[DOC],
        source_anchor=ANCHOR,
        ticket_id=TICKET,
        head_commit=HEAD,
        evidence=[waive],
    )

    # wrong answer: PENDING — a built waive must satisfy the out-of-scope file.
    assert result.status == ES.PASSED
    assert result.evidence_ids == (waive.id,)


def test_scope_fail_round_trip_fails() -> None:
    fail = _scope(CODE_A, waive=False)
    result = evaluate_scope(
        [CODE_A],
        relevant_docs=[DOC],
        source_anchor=ANCHOR,
        ticket_id=TICKET,
        head_commit=HEAD,
        evidence=[fail],
    )

    # wrong answer: PASSED/PENDING — waive=False is a scope dispute (FAILED).
    assert result.status == ES.FAILED
    assert result.evidence_ids == (fail.id,)


# ── AC-3: blanket approval round-trip; carries NEITHER discriminator key ────
def test_blanket_approval_round_trip_approved() -> None:
    approval = _blanket(approved=True)
    result = evaluate_human_approval(
        ticket_id=TICKET, head_commit=HEAD, evidence=[approval]
    )

    # wrong answer: PENDING — a built approval must satisfy the blanket check.
    assert result.status == ES.PASSED
    assert result.evidence_id == approval.id
    # wrong answer: a payload with a discriminator key — then the human_approval
    # evaluator would stop treating it as a blanket approval.
    assert ACCEPTANCE_CRITERION_HASH_KEY not in approval.raw_payload
    assert SCOPE_DECISION_PATH_KEY not in approval.raw_payload


def test_blanket_approval_round_trip_rejected() -> None:
    rejection = _blanket(approved=False)
    result = evaluate_human_approval(
        ticket_id=TICKET, head_commit=HEAD, evidence=[rejection]
    )

    # wrong answer: PASSED/PENDING — approved=False is a rejection (FAILED).
    assert result.status == ES.FAILED
    assert result.evidence_id == rejection.id


# ── AC-4: tier is human and the head pin is load-bearing ────────────────────
def test_builders_are_human_tier_and_pinned() -> None:
    built = [_ac(AC1), _scope(CODE_A, waive=True), _blanket(approved=True)]
    for record in built:
        # wrong answer: AGENT/SYSTEM — only a human record satisfies these checks.
        assert record.created_by_type == ActorType.HUMAN
        assert evidence_tier(record.created_by_type) == "human"
        # wrong answer: commit_sha=None — silently ignored by every evaluator.
        assert record.commit_sha == HEAD
        assert record.evidence_type == ET.MANUAL_APPROVAL
        assert record.created_by_id == OPERATOR


def test_acceptance_confirmation_at_other_commit_is_ignored() -> None:
    # Built pinned to OTHER, evaluated at HEAD: the pin must exclude it.
    stale = _ac(AC1, head_commit=OTHER)
    result = evaluate_acceptance_criteria(
        [AC1], ticket_id=TICKET, head_commit=HEAD, evidence=[stale]
    )

    # wrong answer: PASSED — a confirmation at a stale commit leaked past the pin.
    assert result.status != ES.PASSED


def test_agent_tier_copy_breaks_the_round_trip() -> None:
    # The same payload at AGENT tier is not a human confirmation (assert the tier,
    # not persistence — EvidenceRepo separately caps agent-tier at PENDING).
    human = _ac(AC1)
    agent_copy = human.model_copy(update={"created_by_type": ActorType.AGENT})
    assert evidence_tier(agent_copy.created_by_type) == "agent"
    result = evaluate_acceptance_criteria(
        [AC1], ticket_id=TICKET, head_commit=HEAD, evidence=[agent_copy]
    )

    # wrong answer: PASSED — an agent manufactured an acceptance confirmation.
    assert result.status != ES.PASSED


# ── AC-5: out_of_scope_paths is the single source; guard vs an expected set ──
@pytest.mark.parametrize(
    ("pr_files", "relevant_docs", "source_anchor", "expected"),
    [
        # all in scope via relevant_docs + the anchor path part
        ([DOC, ANCHOR_PATH], [DOC], ANCHOR, []),
        # the anchor PATH alone keeps the file in scope (the slug-regression catcher)
        ([ANCHOR_PATH], [], ANCHOR, []),
        # one out-of-scope code file
        ([CODE_A], [DOC], ANCHOR, [CODE_A]),
        # mixed; distinct + sorted
        ([DOC, CODE_B, CODE_A], [DOC], ANCHOR, [CODE_A, CODE_B]),
        # blank/whitespace entries dropped, duplicates collapsed
        (["  ", CODE_A, "  " + CODE_A + " "], [DOC], ANCHOR, [CODE_A]),
        # an empty source_anchor drops from scope (cannot match a PR entry)
        ([CODE_A], [DOC], "", [CODE_A]),
    ],
)
def test_out_of_scope_paths_matches_evaluator(
    pr_files: list[str],
    relevant_docs: list[str],
    source_anchor: str,
    expected: list[str],
) -> None:
    paths = out_of_scope_paths(
        pr_files, relevant_docs=relevant_docs, source_anchor=source_anchor
    )
    # wrong answer: the anchor slug folded into scope would surface ANCHOR_PATH.
    assert paths == expected

    # And the evaluator (which delegates to the same derivation) agrees: with no
    # evidence, an empty out-of-scope set PASSES and a non-empty set is PENDING.
    result = evaluate_scope(
        pr_files,
        relevant_docs=relevant_docs,
        source_anchor=source_anchor,
        ticket_id=TICKET,
        head_commit=HEAD,
        evidence=[],
    )
    assert result.status == (ES.PASSED if not expected else ES.PENDING)


# ── AC-6: the pending detector excludes already-satisfied work ──────────────
def test_pending_capture_excludes_satisfied() -> None:
    ticket = make_ticket(
        ticket_type=TicketType.FEATURE,
        risk_level=RiskLevel.HIGH,  # high-risk feature requires human_approval
        acceptance_criteria=[AC1, AC2],
        relevant_docs=[DOC],
        source_anchor=ANCHOR,
    )
    pr_files = [DOC, CODE_A, CODE_B]  # DOC in scope; CODE_A/CODE_B out

    before = pending_capture(ticket, head_commit=HEAD, pr_files=pr_files, evidence=[])
    assert {p.criterion for p in before.unconfirmed_criteria} == {AC1, AC2}
    assert before.undecided_scope_files == (CODE_A, CODE_B)
    assert before.human_approval_required_and_missing is True

    evidence = [
        _ac(AC1),
        _ac(AC2),
        _scope(CODE_A, waive=True),
        _scope(CODE_B, waive=True),
        _blanket(approved=True),
    ]
    after = pending_capture(
        ticket, head_commit=HEAD, pr_files=pr_files, evidence=evidence
    )

    # wrong answer: non-empty — the detector ignored the matching built records.
    assert after.unconfirmed_criteria == ()
    assert after.undecided_scope_files == ()
    assert after.human_approval_required_and_missing is False


# ── Amendment: undecided_scope_files is gated on the ticket requiring SCOPE ──
def test_documentation_ticket_does_not_gate_scope() -> None:
    # documentation tickets require acceptance + documentation + lint, NOT scope.
    ticket = make_ticket(
        ticket_type=TicketType.DOCUMENTATION,
        acceptance_criteria=[AC1],
        relevant_docs=[DOC],
        source_anchor=ANCHOR,
    )
    result = pending_capture(
        ticket, head_commit=HEAD, pr_files=[CODE_A, CODE_B], evidence=[]
    )

    # wrong answer: (CODE_A, CODE_B) — scope must not be surfaced for a ticket
    # type the matrix does not require the scope check for.
    assert result.undecided_scope_files == ()
    # acceptance is always required, so it still surfaces.
    assert {p.criterion for p in result.unconfirmed_criteria} == {AC1}
    # documentation tickets do not require human_approval.
    assert result.human_approval_required_and_missing is False


def test_low_risk_feature_does_not_require_human_approval() -> None:
    ticket = make_ticket(ticket_type=TicketType.FEATURE, risk_level=RiskLevel.LOW)
    result = pending_capture(ticket, head_commit=HEAD, pr_files=[], evidence=[])

    # wrong answer: True — human_approval is if_risk_high for features.
    assert result.human_approval_required_and_missing is False


def test_spike_requires_human_approval_and_does_not_gate_scope() -> None:
    ticket = make_ticket(
        ticket_type=TicketType.SPIKE, acceptance_criteria=[AC1], relevant_docs=[DOC]
    )
    result = pending_capture(ticket, head_commit=HEAD, pr_files=[CODE_A], evidence=[])

    # spike/research require human_approval always and have no scope column.
    assert result.human_approval_required_and_missing is True
    assert result.undecided_scope_files == ()


def test_blanket_rejection_counts_as_decided_not_missing() -> None:
    # A FAILED blanket approval is a decision, so it is not "missing".
    ticket = make_ticket(ticket_type=TicketType.SPIKE, acceptance_criteria=[AC1])
    result = pending_capture(
        ticket, head_commit=HEAD, pr_files=[], evidence=[_blanket(approved=False)]
    )

    # wrong answer: True — a rejection is decided, not absent.
    assert result.human_approval_required_and_missing is False


def test_pending_capture_dedupes_criteria_by_hash() -> None:
    # Duplicate criterion text (and a whitespace variant) prompts once.
    ticket = make_ticket(acceptance_criteria=[AC1, AC1, "  " + AC1 + "  "])
    result = pending_capture(ticket, head_commit=HEAD, pr_files=[], evidence=[])

    # wrong answer: 2 or 3 prompts — the hash set collapses duplicates.
    assert len(result.unconfirmed_criteria) == 1
    assert result.unconfirmed_criteria[0].criterion == AC1


# ── AC-7: builders and the detector never raise on degenerate input ─────────
@pytest.mark.parametrize("acceptance_criteria", [[], [AC1]])
@pytest.mark.parametrize("pr_files", [[], [CODE_A]])
@pytest.mark.parametrize("source_anchor", ["", ANCHOR])
@pytest.mark.parametrize("with_null_commit_evidence", [False, True])
def test_pending_capture_never_raises(
    acceptance_criteria: list[str],
    pr_files: list[str],
    source_anchor: str,
    with_null_commit_evidence: bool,
) -> None:
    ticket = make_ticket(
        ticket_type=TicketType.FEATURE,
        risk_level=RiskLevel.HIGH,
        acceptance_criteria=acceptance_criteria,
        source_anchor=source_anchor,
    )
    evidence: list[Evidence] = []
    if with_null_commit_evidence:
        # An unpinned human MANUAL_APPROVAL carrying each discriminator: the
        # detector must not raise reading it (it simply decides nothing).
        evidence = [
            _null_commit_record({ACCEPTANCE_CRITERION_HASH_KEY: "x"}),
            _null_commit_record({SCOPE_DECISION_PATH_KEY: CODE_A}),
            _null_commit_record({}),
        ]

    result = pending_capture(
        ticket, head_commit=HEAD, pr_files=pr_files, evidence=evidence
    )
    assert isinstance(result, PendingCapture)


def test_builders_never_raise_on_degenerate_input() -> None:
    # Empty criterion, whitespace-only path: construct a record, never raise.
    assert isinstance(_ac(""), Evidence)
    assert isinstance(_scope("   ", waive=True), Evidence)
    assert isinstance(_blanket(approved=True), Evidence)


def _null_commit_record(raw_payload: dict[str, Any]) -> Evidence:
    return Evidence(
        id=uuid4(),
        product_id=PRODUCT,
        ticket_id=TICKET,
        evidence_type=ET.MANUAL_APPROVAL,
        status=ES.PASSED,
        summary="unpinned human record",
        commit_sha=None,
        raw_payload=raw_payload,
        created_by_type=ActorType.HUMAN,
        created_by_id=OPERATOR,
        created_at=NOW,
    )


# ── Result-shape guards ─────────────────────────────────────────────────────
def test_pending_capture_returns_frozen_dataclass() -> None:
    result = pending_capture(make_ticket(), head_commit=HEAD, pr_files=[], evidence=[])
    assert isinstance(result, PendingCapture)
    with pytest.raises(Exception):  # noqa: B017 — FrozenInstanceError is the point
        result.undecided_scope_files = ()  # type: ignore[misc]


def test_criterion_prompt_carries_text_and_hash() -> None:
    ticket = make_ticket(acceptance_criteria=[AC1])
    [prompt] = pending_capture(
        ticket, head_commit=HEAD, pr_files=[], evidence=[]
    ).unconfirmed_criteria
    assert isinstance(prompt, CriterionPrompt)
    # the hash matches what the builder writes for the same criterion.
    built = _ac(AC1)
    assert prompt.criterion_hash == built.raw_payload[ACCEPTANCE_CRITERION_HASH_KEY]
