"""ATLAS-76: the ticket completion validator composes the five per-check
evaluators into one per-ticket verdict. :func:`evaluate_ticket` resolves a
ticket's required checks, dispatches each to its evaluator with the right ticket
inputs, normalises each result into a uniform :class:`CheckOutcome`, and folds
the gating outcomes into the verdict: PASSED iff every required check PASSED;
FAILED if any required FAILED (fail precedence); else PENDING. The deferred
SECURITY surface (required=False) appears but does not gate. It NEVER raises.

Each behavioural assertion names the wrong answer it would catch. The two safety
properties are guarded directly: the dispatch routes the RIGHT evaluator with the
RIGHT inputs (spied), and the dispatch-completeness guard (R1) is BEHAVIOURAL —
driven from the resolver's actual output across TicketType x RiskLevel, asserting
every required type it emits routes to a real arm — paired with a forced-unhandled
required check proving the fallthrough HOLDS the verdict at PENDING rather than
silently passing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from atlas.core.enums import ActorType, EvidenceStatus, RiskLevel
from atlas.core.models import (
    Evidence,
    EvidenceType,
    VerificationCheck,
    VerificationCheckType,
)
from atlas.core.models.ticket import Ticket, TicketStatus, TicketType
from atlas.verification import (
    CheckOutcome,
    MachineCheckEvaluation,
    RequiredCheck,
    ScopeEvaluation,
    TicketVerification,
    acceptance_criterion_hash,
    evaluate_ticket,
    merge_confirmed,
    proof_evidence_ids,
    required_checks,
    ticket_verdict_from_checks,
)
from atlas.verification import completion as completion_mod
from atlas.verification.acceptance_check import ACCEPTANCE_CRITERION_HASH_KEY
from atlas.verification.completion import _DISPATCH, fold_statuses

NOW = datetime(2026, 6, 28, tzinfo=UTC)
HEAD = "c0ffee0000000000000000000000000000000000"
OTHER = "dead000000000000000000000000000000000000"
TICKET = UUID(int=0xA76C)
OTHER_TICKET = UUID(int=0xB76C)

DOC = "docs/foo.md"
ANCHOR = f"{DOC}#section"
AC1 = "Acceptance criterion one"
AC2 = "Acceptance criterion two"

ES = EvidenceStatus
ET = EvidenceType
VT = VerificationCheckType


def make_ticket(
    *,
    ticket_type: TicketType = TicketType.FEATURE,
    risk_level: RiskLevel = RiskLevel.LOW,
    acceptance_criteria: list[str] | None = None,
    documentation_requirements: list[str] | None = None,
    relevant_docs: list[str] | None = None,
    source_anchor: str = ANCHOR,
    ticket_id: UUID = TICKET,
) -> Ticket:
    """A Ticket with the fields the validator and evaluators read.

    Defaults: a feature ticket with two acceptance criteria, one documentation
    requirement, and the anchor's path as its sole relevant doc — enough to fire
    every column the matrix can require.
    """
    return Ticket(
        id=ticket_id,
        product_id=uuid4(),
        key="ATLAS-1",
        title="t",
        objective="o",
        context="c",
        status=TicketStatus.REVIEW_REQUIRED,
        ticket_type=ticket_type,
        risk_level=risk_level,
        priority=1,
        relevant_docs=[DOC] if relevant_docs is None else relevant_docs,
        acceptance_criteria=[AC1, AC2]
        if acceptance_criteria is None
        else acceptance_criteria,
        documentation_requirements=(
            [DOC] if documentation_requirements is None else documentation_requirements
        ),
        source_anchor=source_anchor,
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
    commit_sha: str | None = HEAD,
    ticket_id: UUID = TICKET,
    raw_payload: dict[str, Any] | None = None,
    created_at: datetime = NOW,
    job_name: str | None = None,
    source_event_at: datetime | None = None,
    id: UUID | None = None,
) -> Evidence:
    return Evidence(
        id=id or uuid4(),
        product_id=uuid4(),
        ticket_id=ticket_id,
        evidence_type=evidence_type,
        status=status,
        summary="e",
        commit_sha=commit_sha,
        job_name=job_name,
        source_event_at=source_event_at,
        raw_payload=raw_payload or {},
        created_by_type=created_by_type,
        created_by_id="ci" if created_by_type == ActorType.SYSTEM else "operator",
        created_at=created_at,
    )


def sys_test(status: EvidenceStatus = ES.PASSED, **kw: Any) -> Evidence:
    return _evidence(
        ET.TEST_RESULT,
        status=status,
        created_by_type=ActorType.SYSTEM,
        job_name="test",
        source_event_at=NOW,
        **kw,
    )


def sys_lint(status: EvidenceStatus = ES.PASSED, **kw: Any) -> Evidence:
    return _evidence(
        ET.LINT_RESULT,
        status=status,
        created_by_type=ActorType.SYSTEM,
        job_name="lint",
        source_event_at=NOW,
        **kw,
    )


def sys_doc(path: str = DOC, **kw: Any) -> Evidence:
    return _evidence(
        ET.DOCUMENTATION_UPDATE,
        status=ES.PASSED,
        created_by_type=ActorType.SYSTEM,
        raw_payload={"files": [{"filename": path}]},
        **kw,
    )


def human_ac(criterion: str, **kw: Any) -> Evidence:
    return _evidence(
        ET.MANUAL_APPROVAL,
        status=ES.PASSED,
        created_by_type=ActorType.HUMAN,
        raw_payload={
            ACCEPTANCE_CRITERION_HASH_KEY: acceptance_criterion_hash(criterion)
        },
        **kw,
    )


def human_blanket(status: EvidenceStatus = ES.PASSED, **kw: Any) -> Evidence:
    return _evidence(
        ET.MANUAL_APPROVAL,
        status=status,
        created_by_type=ActorType.HUMAN,
        raw_payload={},
        **kw,
    )


def outcome_for(
    result: TicketVerification, check_type: VerificationCheckType
) -> CheckOutcome:
    matches = [o for o in result.checks if o.check_type == check_type]
    assert len(matches) == 1, f"expected exactly one {check_type} outcome"
    return matches[0]


# --- AC1: every required check PASSED → ticket PASSED, one CheckOutcome per
# resolved check, with normalised evidence_ids (single → (id,); multi → tuple).
def test_all_required_passed_yields_passed() -> None:
    ticket = (
        make_ticket()
    )  # feature/low/doc → TESTS, LINT, ACCEPTANCE, DOCUMENTATION, SCOPE
    test_ev = sys_test()
    ac1 = human_ac(AC1, created_at=NOW)
    ac2 = human_ac(AC2, created_at=NOW + timedelta(hours=1))
    evidence = [test_ev, sys_lint(), sys_doc(), ac1, ac2]

    result = evaluate_ticket(
        ticket, pr_files=[DOC], head_commit=HEAD, evidence=evidence
    )

    # wrong answer: PENDING — every required check is satisfied at the head commit.
    assert result.status == ES.PASSED
    assert result.ticket_id == ticket.id
    # one CheckOutcome per resolved check.
    assert {o.check_type for o in result.checks} == {
        VT.TESTS,
        VT.LINT,
        VT.ACCEPTANCE_CRITERIA,
        VT.DOCUMENTATION,
        VT.SCOPE,
    }
    assert len(result.checks) == len(required_checks(ticket))
    # single-evidence check → (id,); multi-evidence check → the sorted tuple.
    assert outcome_for(result, VT.TESTS).evidence_ids == (test_ev.id,)
    assert outcome_for(result, VT.ACCEPTANCE_CRITERIA).evidence_ids == (ac1.id, ac2.id)
    # scope passed with no decisions → ().
    assert outcome_for(result, VT.SCOPE).evidence_ids == ()


# --- AC2: one required check PENDING (rest PASSED) → PENDING.
def test_one_pending_required_check_yields_pending() -> None:
    ticket = make_ticket()
    # Drop one acceptance confirmation → ACCEPTANCE_CRITERIA is PENDING.
    evidence = [sys_test(), sys_lint(), sys_doc(), human_ac(AC1)]

    result = evaluate_ticket(
        ticket, pr_files=[DOC], head_commit=HEAD, evidence=evidence
    )

    # wrong answer: PASSED — one criterion is unconfirmed, so the ticket is unproven.
    assert result.status == ES.PENDING
    assert outcome_for(result, VT.ACCEPTANCE_CRITERIA).status == ES.PENDING


# --- AC3: one required check FAILED → FAILED regardless of the others (precedence).
def test_one_failed_required_check_yields_failed() -> None:
    ticket = make_ticket()
    # Everything else passes; LINT fails. Fail precedence must dominate.
    evidence = [
        sys_test(),
        sys_lint(status=ES.FAILED),
        sys_doc(),
        human_ac(AC1),
        human_ac(AC2),
    ]

    result = evaluate_ticket(
        ticket, pr_files=[DOC], head_commit=HEAD, evidence=evidence
    )

    # wrong answer: PASSED/PENDING — a single failing required check sinks the ticket.
    assert result.status == ES.FAILED
    assert outcome_for(result, VT.LINT).status == ES.FAILED


# --- AC4: the deferred SECURITY check (critical risk) is NOT_APPLICABLE, non-gating,
# and does not change an otherwise-PASSED verdict.
def test_deferred_security_is_non_gating_not_applicable() -> None:
    # bug/critical → TESTS, LINT, ACCEPTANCE, SCOPE, + non-gating SECURITY.
    ticket = make_ticket(ticket_type=TicketType.BUG, risk_level=RiskLevel.CRITICAL)
    evidence = [sys_test(), sys_lint(), human_ac(AC1), human_ac(AC2)]

    result = evaluate_ticket(
        ticket, pr_files=[DOC], head_commit=HEAD, evidence=evidence
    )

    security = outcome_for(result, VT.SECURITY)
    # wrong answer: required=True / a gating status — SECURITY is deferred, non-gating.
    assert security.required is False
    assert security.status == ES.NOT_APPLICABLE
    assert security.evidence_ids == ()
    # wrong answer: PENDING — a non-gating NOT_APPLICABLE check must not hold a PASS.
    assert result.status == ES.PASSED


# --- AC5 (composed): a discriminated record does not satisfy human_approval, so a
# ticket requiring it is not PASSED on an acceptance confirmation alone.
def test_human_approval_not_satisfied_by_discriminated_record() -> None:
    # feature/high (no doc reqs) → TESTS, LINT, ACCEPTANCE, SCOPE, HUMAN_APPROVAL.
    ticket = make_ticket(risk_level=RiskLevel.HIGH, documentation_requirements=[])
    # All gating pass EXCEPT human_approval, for which only an acceptance
    # confirmation (carrying the hash) exists — that is NOT a blanket approval.
    evidence = [sys_test(), sys_lint(), human_ac(AC1), human_ac(AC2)]

    result = evaluate_ticket(
        ticket, pr_files=[DOC], head_commit=HEAD, evidence=evidence
    )

    # wrong answer: PASSED — a hash-carrying confirmation cannot approve the PR.
    assert outcome_for(result, VT.HUMAN_APPROVAL).status == ES.PENDING
    assert result.status == ES.PENDING


# --- AC6: human_approval status pass-through — a FAILED blanket approval → the
# human_approval check FAILED → the ticket FAILED (OP-B).
def test_human_approval_failed_sinks_the_ticket() -> None:
    ticket = make_ticket(risk_level=RiskLevel.HIGH, documentation_requirements=[])
    evidence = [
        sys_test(),
        sys_lint(),
        human_ac(AC1),
        human_ac(AC2),
        human_blanket(status=ES.FAILED),  # operator rejected the PR
    ]

    result = evaluate_ticket(
        ticket, pr_files=[DOC], head_commit=HEAD, evidence=evidence
    )

    # wrong answer: PENDING/PASSED — a rejected PR fails the ticket (OP-B), routing
    # to needs_human_decision.
    assert outcome_for(result, VT.HUMAN_APPROVAL).status == ES.FAILED
    assert result.status == ES.FAILED


# --- AC7 (composed spot-check): human-tier checks ignore a different commit, a
# different ticket, and non-human tiers.
def test_human_approval_ignores_wrong_commit_ticket_and_tier() -> None:
    ticket = make_ticket(risk_level=RiskLevel.HIGH, documentation_requirements=[])
    evidence = [
        sys_test(),
        sys_lint(),
        human_ac(AC1),
        human_ac(AC2),
        human_blanket(commit_sha=OTHER),  # wrong commit
        human_blanket(ticket_id=OTHER_TICKET),  # wrong ticket
        _evidence(  # agent-tier blanket approval — cannot approve
            ET.MANUAL_APPROVAL, status=ES.PASSED, created_by_type=ActorType.AGENT
        ),
    ]

    result = evaluate_ticket(
        ticket, pr_files=[DOC], head_commit=HEAD, evidence=evidence
    )

    # wrong answer: PASSED — none of those is a valid approval at (HEAD, TICKET).
    assert outcome_for(result, VT.HUMAN_APPROVAL).status == ES.PENDING
    assert result.status == ES.PENDING


# --- AC8: dispatch routes the RIGHT evaluator with the RIGHT inputs. The scope
# adapter must call evaluate_scope with the ticket's relevant_docs/source_anchor
# and the PR file list — spied to assert the exact wiring.
def test_scope_routes_to_evaluate_scope_with_ticket_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticket = make_ticket()
    captured: dict[str, Any] = {}

    def spy_scope(pr_files: Any, **kwargs: Any) -> ScopeEvaluation:
        captured["pr_files"] = pr_files
        captured.update(kwargs)
        return ScopeEvaluation(
            check_type=VT.SCOPE, status=ES.PASSED, evidence_ids=(), reason="spy"
        )

    monkeypatch.setattr(completion_mod, "evaluate_scope", spy_scope)

    pr_files = ["atlas/code.py", "tests/test_code.py"]
    evaluate_ticket(ticket, pr_files=pr_files, head_commit=HEAD, evidence=[])

    # wrong answer: some other evaluator's inputs — the scope adapter must forward
    # exactly the ticket's scope fields and the PR file list.
    assert captured["pr_files"] == pr_files
    assert captured["relevant_docs"] == ticket.relevant_docs
    assert captured["source_anchor"] == ticket.source_anchor
    assert captured["ticket_id"] == ticket.id
    assert captured["head_commit"] == HEAD


# --- AC8 (machine): the machine adapter passes its OWN check_type, so TESTS and
# LINT each reach evaluate_machine_check with the right type.
def test_machine_dispatch_passes_its_own_check_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticket = make_ticket()
    seen: list[VerificationCheckType] = []

    def spy_machine(
        check_type: VerificationCheckType, *, head_commit: str, evidence: Any
    ) -> MachineCheckEvaluation:
        seen.append(check_type)
        return MachineCheckEvaluation(
            check_type=check_type, status=ES.PASSED, evidence_ids=(), reason="spy"
        )

    monkeypatch.setattr(completion_mod, "evaluate_machine_check", spy_machine)
    evaluate_ticket(ticket, pr_files=[DOC], head_commit=HEAD, evidence=[])

    # wrong answer: the same type twice, or a swapped type — each machine arm must
    # forward its own check_type.
    assert set(seen) == {VT.TESTS, VT.LINT}


# --- AC9 (R1, BEHAVIOURAL): every required check the resolver actually emits, over
# the full TicketType x RiskLevel space (with conditional columns fired), routes to
# a real dispatch arm — NOT the fallthrough. Driven from required_checks output, not
# a re-imported resolver constant, so it covers BOTH the matrix loop and any
# cross-cutting emission path.
def test_every_emittable_required_check_routes_to_a_real_arm() -> None:
    emitted: set[VerificationCheckType] = set()
    for ticket_type in TicketType:
        for risk_level in RiskLevel:
            for has_docs in (True, False):
                ticket = make_ticket(
                    ticket_type=ticket_type,
                    risk_level=risk_level,
                    documentation_requirements=[DOC] if has_docs else [],
                )
                for rc in required_checks(ticket):
                    if rc.required:
                        emitted.add(rc.check_type)

    # Sanity: the sweep actually exercised the conditional/cross-cutting columns.
    assert {VT.DOCUMENTATION, VT.HUMAN_APPROVAL} <= emitted
    # wrong answer: a required type with no arm (it would silently hit the
    # fallthrough) — every emitted required type must route to a real evaluator.
    unrouted = emitted - set(_DISPATCH)
    assert unrouted == set(), f"required types with no dispatch arm: {unrouted}"


# --- AC9 (fallthrough holds the verdict): a required check whose type has no arm
# must yield a non-passing PENDING outcome that holds the verdict — never a silent
# pass. Force the resolver to emit a required check with no dispatch arm.
def test_unhandled_required_check_holds_verdict_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticket = make_ticket()
    # SECURITY has no dispatch arm; forcing it required=True drives the fallthrough.
    phantom = RequiredCheck(VT.SECURITY, required=True)
    monkeypatch.setattr(completion_mod, "required_checks", lambda t: (phantom,))

    result = evaluate_ticket(ticket, pr_files=[DOC], head_commit=HEAD, evidence=[])

    held = outcome_for(result, VT.SECURITY)
    # wrong answer: PASSED/NOT_APPLICABLE — an unhandled REQUIRED check must hold the
    # verdict, not silently pass.
    assert held.required is True
    assert held.status == ES.PENDING
    assert "no evaluator wired" in held.reason
    assert result.status == ES.PENDING


# --- AC10: evidence_ids are the evaluator's ids, normalised, asserted by value.
def test_evidence_ids_are_normalised_by_value() -> None:
    ticket = make_ticket(risk_level=RiskLevel.HIGH)  # also requires HUMAN_APPROVAL
    test_ev = sys_test()
    doc_ev = sys_doc()
    blanket = human_blanket()
    ac1 = human_ac(AC1, created_at=NOW)
    ac2 = human_ac(AC2, created_at=NOW + timedelta(hours=1))
    evidence = [test_ev, sys_lint(), doc_ev, ac1, ac2, blanket]

    result = evaluate_ticket(
        ticket, pr_files=[DOC], head_commit=HEAD, evidence=evidence
    )

    # single-evidence evaluators normalise to (id,).
    assert outcome_for(result, VT.TESTS).evidence_ids == (test_ev.id,)
    assert outcome_for(result, VT.DOCUMENTATION).evidence_ids == (doc_ev.id,)
    assert outcome_for(result, VT.HUMAN_APPROVAL).evidence_ids == (blanket.id,)
    # multi-evidence evaluator passes its tuple through, sorted by (created_at, id).
    assert outcome_for(result, VT.ACCEPTANCE_CRITERIA).evidence_ids == (ac1.id, ac2.id)
    assert result.status == ES.PASSED


# --- AC12: never raises across empty evidence, every ticket_type x risk_level, and
# empty pr_files.
def test_never_raises_across_cross_product() -> None:
    for ticket_type in TicketType:
        for risk_level in RiskLevel:
            ticket = make_ticket(ticket_type=ticket_type, risk_level=risk_level)
            result = evaluate_ticket(ticket, pr_files=[], head_commit=HEAD, evidence=[])
            # wrong answer: an exception, or a vacuous PASS over unproven checks.
            assert isinstance(result, TicketVerification)
            assert result.status in {ES.PENDING, ES.FAILED, ES.NOT_APPLICABLE}


# --- D5: the shared fold rule (fold_statuses) is unit-tested directly. It is the
# single source of truth for BOTH the per-ticket verdict (_compose_verdict, above)
# and the PR verdict (ATLAS-77 evaluate_pr) — so its rule is pinned here once.
def test_fold_statuses_any_failed_yields_failed() -> None:
    # wrong answer: PENDING — fail precedence dominates any mix.
    assert fold_statuses([ES.PASSED, ES.FAILED, ES.PASSED]) == ES.FAILED
    assert fold_statuses([ES.FAILED]) == ES.FAILED
    assert fold_statuses([ES.PENDING, ES.FAILED]) == ES.FAILED


def test_fold_statuses_empty_yields_pending() -> None:
    # wrong answer: PASSED — an empty set is never a vacuous PASS.
    assert fold_statuses([]) == ES.PENDING


def test_fold_statuses_all_passed_yields_passed() -> None:
    # wrong answer: PENDING — a non-empty set of only PASSED passes.
    assert fold_statuses([ES.PASSED]) == ES.PASSED
    assert fold_statuses([ES.PASSED, ES.PASSED]) == ES.PASSED


def test_fold_statuses_non_passing_in_mix_yields_pending() -> None:
    # wrong answer: PASSED — a PENDING/WARNING/NOT_APPLICABLE is non-passing but not
    # failing, so it holds the verdict at PENDING (only PASSED passes).
    assert fold_statuses([ES.PASSED, ES.PENDING]) == ES.PENDING
    assert fold_statuses([ES.PASSED, ES.WARNING]) == ES.PENDING
    assert fold_statuses([ES.PASSED, ES.NOT_APPLICABLE]) == ES.PENDING


# --- ticket_verdict_from_checks: verdict from PERSISTED rows (ATLAS-131) ----
#
# The PM-side consumer reads the same verdict evaluate_ticket computes, but from
# the append-only VerificationCheck rows atlas verify wrote rather than by
# re-running the evaluators. Each case names the wrong answer it catches.

VERDICT_EARLIER = NOW
VERDICT_LATER = NOW + timedelta(hours=1)


def _row(
    check_type: VerificationCheckType,
    status: EvidenceStatus,
    *,
    created_at: datetime = VERDICT_EARLIER,
    ticket_id: UUID = TICKET,
) -> VerificationCheck:
    """One persisted VerificationCheck row for the verdict-from-checks tests."""
    return VerificationCheck(
        id=uuid4(),
        ticket_id=ticket_id,
        check_type=check_type,
        status=status,
        summary="seeded row",
        required=True,
        created_at=created_at,
    )


def _passed_rows_for(ticket: Ticket) -> list[VerificationCheck]:
    """A latest-PASSED row for every GATING required type of ``ticket`` — robust
    to the matrix (drives off required_checks, not a hard-coded set)."""
    return [
        _row(rc.check_type, EvidenceStatus.PASSED)
        for rc in required_checks(ticket)
        if rc.required
    ]


def test_verdict_all_required_passed_yields_passed() -> None:
    ticket = make_ticket()
    # wrong answer: PENDING — a latest-PASSED row for every required type passes.
    assert ticket_verdict_from_checks(ticket, _passed_rows_for(ticket)) == ES.PASSED


def test_verdict_missing_required_row_holds_pending() -> None:
    # A required type with NO persisted row must hold the verdict at PENDING — a
    # missing required check can never produce a false PASS.
    ticket = make_ticket()
    rows = _passed_rows_for(ticket)
    rows.pop()  # drop one required type's only row
    # wrong answer: PASSED — treating a missing required type as satisfied.
    assert ticket_verdict_from_checks(ticket, rows) == ES.PENDING


def test_verdict_any_required_failed_yields_failed() -> None:
    ticket = make_ticket()
    rows = _passed_rows_for(ticket)
    rows[0] = _row(rows[0].check_type, EvidenceStatus.FAILED)
    # wrong answer: PENDING/PASSED — one FAILED required check sinks the verdict.
    assert ticket_verdict_from_checks(ticket, rows) == ES.FAILED


def test_verdict_latest_row_per_type_wins() -> None:
    # AC-4: an older PASSED superseded by a newer FAILED for the SAME required type
    # composes to FAILED. wrong answer: select the oldest row, yielding PASSED.
    ticket = make_ticket()
    rows = _passed_rows_for(ticket)
    gating_type = rows[0].check_type
    rows[0] = _row(gating_type, EvidenceStatus.PASSED, created_at=VERDICT_EARLIER)
    rows.append(_row(gating_type, EvidenceStatus.FAILED, created_at=VERDICT_LATER))
    assert ticket_verdict_from_checks(ticket, rows) == ES.FAILED
    # And the reverse order (newer PASSED over older FAILED) passes that type.
    ticket2 = make_ticket()
    rows2 = _passed_rows_for(ticket2)
    g2 = rows2[0].check_type
    rows2[0] = _row(g2, EvidenceStatus.FAILED, created_at=VERDICT_EARLIER)
    rows2.append(_row(g2, EvidenceStatus.PASSED, created_at=VERDICT_LATER))
    assert ticket_verdict_from_checks(ticket2, rows2) == ES.PASSED


def test_verdict_ignores_non_gating_security_row() -> None:
    # The non-gating SECURITY surface (required=False on critical tickets) never
    # participates in the fold: a FAILED security row does NOT sink an otherwise
    # all-PASSED verdict. wrong answer: FAILED — folding the non-gating row in.
    ticket = make_ticket(risk_level=RiskLevel.CRITICAL)
    rows = _passed_rows_for(ticket)  # gating types only
    rows.append(_row(VerificationCheckType.SECURITY, EvidenceStatus.FAILED))
    assert ticket_verdict_from_checks(ticket, rows) == ES.PASSED


def test_verdict_never_raises_on_empty_or_unknown_rows() -> None:
    # Pure and total, like its siblings: no rows at all -> PENDING (required types
    # all missing), and an unrelated row type is simply ignored.
    ticket = make_ticket()
    assert ticket_verdict_from_checks(ticket, []) == ES.PENDING
    stray = [_row(VerificationCheckType.SECURITY, EvidenceStatus.PASSED)]
    assert ticket_verdict_from_checks(ticket, stray) == ES.PENDING


# === ATLAS-134: the merge-gate pure helpers =================================
# proof_evidence_ids collects the evidence backing the latest REQUIRED checks
# (the verdict's proof); merge_confirmed asks whether a system-tier PR_MERGED is
# pinned to one of those proof commits. Both pure, both never raise.


def _row_with(
    check_type: VerificationCheckType,
    status: EvidenceStatus,
    evidence_ids: tuple[UUID, ...],
    *,
    required: bool = True,
    created_at: datetime = VERDICT_EARLIER,
) -> VerificationCheck:
    return VerificationCheck(
        id=uuid4(),
        ticket_id=TICKET,
        check_type=check_type,
        status=status,
        summary="seeded row",
        required=required,
        evidence_ids=list(evidence_ids),
        created_at=created_at,
    )


def pr_merged(commit: str = HEAD, **kw: Any) -> Evidence:
    return _evidence(
        ET.PR_MERGED,
        status=ES.PASSED,
        created_by_type=ActorType.SYSTEM,
        commit_sha=commit,
        **kw,
    )


# --- proof_evidence_ids -----------------------------------------------------


def test_proof_evidence_ids_collects_latest_required_checks_evidence() -> None:
    ticket = make_ticket()
    gating = [rc.check_type for rc in required_checks(ticket) if rc.required]
    ids = {ct: uuid4() for ct in gating}
    rows = [_row_with(ct, ES.PASSED, (ids[ct],)) for ct in gating]

    proof = proof_evidence_ids(ticket, rows)

    # wrong answer: a subset (a check's ids dropped) or empty (helper not reading ids)
    assert proof == frozenset(ids.values())


def test_proof_evidence_ids_uses_only_the_latest_row_per_type() -> None:
    ticket = make_ticket()
    gating = next(rc.check_type for rc in required_checks(ticket) if rc.required)
    stale, fresh = uuid4(), uuid4()
    rows = [
        _row_with(gating, ES.PASSED, (stale,), created_at=VERDICT_EARLIER),
        _row_with(gating, ES.PASSED, (fresh,), created_at=VERDICT_LATER),
    ]

    proof = proof_evidence_ids(ticket, rows)

    # the latest row's ids win; the superseded row's ids are excluded.
    assert fresh in proof
    assert stale not in proof  # wrong answer: union across all rows ignores latest-wins


def test_proof_evidence_ids_excludes_non_required_checks() -> None:
    ticket = make_ticket()
    gating = next(rc.check_type for rc in required_checks(ticket) if rc.required)
    req_id, non_req_id = uuid4(), uuid4()
    rows = [
        _row_with(gating, ES.PASSED, (req_id,)),
        _row_with(VT.SECURITY, ES.NOT_APPLICABLE, (non_req_id,), required=False),
    ]

    proof = proof_evidence_ids(ticket, rows)

    assert req_id in proof
    assert non_req_id not in proof  # a non-gating check never contributes proof


# --- merge_confirmed --------------------------------------------------------


def test_merge_confirmed_true_for_system_pr_merged_at_a_proof_commit() -> None:
    assert merge_confirmed([pr_merged(HEAD)], commit_shas={HEAD}) is True


def test_merge_confirmed_false_for_empty_commit_set() -> None:
    # no proven commit to match -> never a vacuous Done.
    assert merge_confirmed([pr_merged(HEAD)], commit_shas=set()) is False


def test_merge_confirmed_false_when_no_pr_merged_record() -> None:
    # a passing machine-check evidence is not a merge record.
    assert merge_confirmed([sys_test()], commit_shas={HEAD}) is False


def test_merge_confirmed_false_for_merge_at_a_different_commit() -> None:
    # the stale-merge guard: a PR_MERGED at OTHER does not satisfy a verdict at HEAD.
    assert merge_confirmed([pr_merged(OTHER)], commit_shas={HEAD}) is False


def test_merge_confirmed_false_for_non_system_tier_merge_claim() -> None:
    human_merge = _evidence(
        ET.PR_MERGED, status=ES.PASSED, created_by_type=ActorType.HUMAN, commit_sha=HEAD
    )
    # wrong answer: True (tier check dropped — a non-system merge claim slips through)
    assert merge_confirmed([human_merge], commit_shas={HEAD}) is False


def test_merge_confirmed_never_raises_on_empty_evidence() -> None:
    assert merge_confirmed([], commit_shas={HEAD}) is False
