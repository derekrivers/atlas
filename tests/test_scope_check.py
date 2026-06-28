"""ATLAS-73: the scope evaluator decides the ``scope`` check against pre-loaded
evidence. Every changed PR file must be within the ticket's declared scope
(``relevant_docs`` + the ``source_anchor`` path) OR carry a human-tier operator
decision — waive (status PASSED) or fail (status FAILED) — pinned to the head
commit and scoped to the ticket, and the evaluator NEVER raises.

Each behavioural assertion names the wrong answer it would catch. Beyond status,
the value-level guard matters and is asserted explicitly: ``evidence_ids`` on a
waive-PASSED is the full waive set, on FAILED the failing decision(s), and on
partial-PENDING the PARTIAL waive set — all ordered by ``(created_at, id)``. The
composition ticket (ATLAS-76/77) folds these straight into
``VerificationCheck.evidence_ids``, so a refactor that returns ``()`` always, or
an unsorted set, must NOT stay green: the order tests pass evidence scrambled and
include an equal-``created_at`` id-tiebreak so ordering is a real guard.

Two crux properties are guarded directly: the human-tier filter (AC10 — a
system- or agent-tier decision with a matching path cannot decide a file), and
latest-decision-per-file (AC8 — waive-then-fail FAILS, fail-then-waive PASSES,
including an equal-``created_at`` id-tiebreak). The dedup guard (C1) proves a
path duplicated in the PR file list resolves to one latest decision and one
verdict contribution.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from atlas.core.enums import ActorType, EvidenceStatus
from atlas.core.models import Evidence, EvidenceType, VerificationCheckType
from atlas.verification import (
    SCOPE_DECISION_PATH_KEY,
    ScopeEvaluation,
    evaluate_scope,
)

NOW = datetime(2026, 6, 28, tzinfo=UTC)
HEAD = "c0ffee0000000000000000000000000000000000"
OTHER = "dead000000000000000000000000000000000000"
TICKET = UUID(int=0xA73)
OTHER_TICKET = UUID(int=0xB73)

# Declared scope: a relevant doc and the source_anchor's path part.
ANCHOR = "docs/atlas/verification-engine.md#evaluation-semantics"
ANCHOR_PATH = "docs/atlas/verification-engine.md"
DOC_A = "docs/atlas/scope-notes.md"
# Files NOT in the declared doc scope (the normal-PR code-file case).
CODE_A = "atlas/verification/scope_check.py"
CODE_B = "tests/test_scope_check.py"

ES = EvidenceStatus
ET = EvidenceType


def make_decision(
    path: str,
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
    """An operator scope decision shaped like the convention (D5).

    By default a human-tier MANUAL_APPROVAL pinned to HEAD, scoped to TICKET,
    carrying ``scope_decision_path`` for ``path``. ``status`` carries the choice:
    PASSED = waive, FAILED = fail. ``raw_payload`` overrides the payload wholesale
    for the malformed/blanket cases.
    """
    if raw_payload is None:
        raw_payload = {SCOPE_DECISION_PATH_KEY: path}
    return Evidence(
        id=id or uuid4(),
        product_id=uuid4(),
        ticket_id=ticket_id,
        evidence_type=evidence_type,
        status=status,
        summary="operator scope decision",
        commit_sha=commit_sha,
        raw_payload=raw_payload,
        created_by_type=created_by_type,
        created_by_id="operator" if created_by_type == ActorType.HUMAN else "claude",
        created_at=created_at,
    )


# --- AC1: every PR file matches relevant_docs / the source_anchor path ->
# PASSED, evidence_ids = () (no decisions needed).
def test_all_files_in_scope_passes_with_no_evidence() -> None:
    result = evaluate_scope(
        [DOC_A, ANCHOR_PATH],
        relevant_docs=[DOC_A],
        source_anchor=ANCHOR,
        ticket_id=TICKET,
        head_commit=HEAD,
        evidence=[],
    )

    # wrong answer: PENDING/FAILED — every PR file is within the declared scope.
    assert result.status == ES.PASSED
    assert result.check_type == VerificationCheckType.SCOPE
    # wrong answer: a non-empty set — nothing was out of scope, so no decision.
    assert result.evidence_ids == ()


# --- AC2: a source_anchor-path file is in-scope via the ANCHOR path alone, NOT
# via relevant_docs. relevant_docs is empty so the anchor-split is the sole guard:
# a regression dropping anchor-path derivation would (wrongly) surface the file.
def test_anchor_path_file_is_in_scope_with_empty_relevant_docs() -> None:
    result = evaluate_scope(
        [ANCHOR_PATH],
        relevant_docs=[],  # the anchor path is the ONLY thing keeping it in scope
        source_anchor=ANCHOR,
        ticket_id=TICKET,
        head_commit=HEAD,
        evidence=[],
    )

    # wrong answer: PENDING — the anchor path was not derived, so the file looked
    # out of scope and was surfaced for a waive that does not exist.
    assert result.status == ES.PASSED
    assert result.evidence_ids == ()


# --- AC3: one out-of-scope file, human-tier waive (status PASSED, matching path)
# at C -> PASSED; evidence_ids names the waive (assert value).
def test_single_out_of_scope_file_waived_passes() -> None:
    waive = make_decision(CODE_A, status=ES.PASSED)
    result = evaluate_scope(
        [CODE_A],
        relevant_docs=[DOC_A],
        source_anchor=ANCHOR,
        ticket_id=TICKET,
        head_commit=HEAD,
        evidence=[waive],
    )

    # wrong answer: PENDING — a valid waive at C was ignored.
    assert result.status == ES.PASSED
    # wrong answer: () — the deciding waive must be named for the operator/report.
    assert result.evidence_ids == (waive.id,)


# --- AC4: one out-of-scope file, NO decision -> PENDING; reason names the count;
# evidence_ids = the (empty) partial waive set. Wrong answer named: PASSED.
def test_single_out_of_scope_file_undecided_is_pending() -> None:
    result = evaluate_scope(
        [CODE_A],
        relevant_docs=[DOC_A],
        source_anchor=ANCHOR,
        ticket_id=TICKET,
        head_commit=HEAD,
        evidence=[],
    )

    # wrong answer: PASSED — an undecided out-of-scope file silently passed.
    assert result.status == ES.PENDING
    # wrong answer: a non-empty set — nothing was waived yet.
    assert result.evidence_ids == ()
    # wrong answer: a reason without the count — "1 awaiting an operator decision".
    assert "1 awaiting an operator decision" in result.reason


# --- AC5: one out-of-scope file, human-tier FAIL (status FAILED) at C -> FAILED;
# reason names the failed file; evidence_ids names the fail.
def test_single_out_of_scope_file_failed_fails() -> None:
    fail = make_decision(CODE_A, status=ES.FAILED)
    result = evaluate_scope(
        [CODE_A],
        relevant_docs=[DOC_A],
        source_anchor=ANCHOR,
        ticket_id=TICKET,
        head_commit=HEAD,
        evidence=[fail],
    )

    # wrong answer: PENDING — an operator fail (scope dispute) was not surfaced.
    assert result.status == ES.FAILED
    # wrong answer: () — the failing decision must be named (routes to a dispute).
    assert result.evidence_ids == (fail.id,)
    # wrong answer: a reason that does not name the disputed file.
    assert CODE_A in result.reason


# --- AC6: mixed — some waived + one undecided -> PENDING (not PASSED).
def test_mixed_waived_and_undecided_is_pending() -> None:
    waive = make_decision(CODE_A, status=ES.PASSED)
    # CODE_B out of scope and undecided.
    result = evaluate_scope(
        [CODE_A, CODE_B],
        relevant_docs=[DOC_A],
        source_anchor=ANCHOR,
        ticket_id=TICKET,
        head_commit=HEAD,
        evidence=[waive],
    )

    # wrong answer: PASSED — a partial waive must not pass while a file is undecided.
    assert result.status == ES.PENDING
    # evidence_ids is the partial waive set (useful to the operator).
    assert result.evidence_ids == (waive.id,)
    assert "1 of 2 out-of-scope" in result.reason


# --- AC7: mixed — some waived + one failed -> FAILED (fail precedence).
def test_mixed_waived_and_failed_fails() -> None:
    waive = make_decision(CODE_A, status=ES.PASSED)
    fail = make_decision(CODE_B, status=ES.FAILED)
    result = evaluate_scope(
        [CODE_A, CODE_B],
        relevant_docs=[DOC_A],
        source_anchor=ANCHOR,
        ticket_id=TICKET,
        head_commit=HEAD,
        evidence=[waive, fail],
    )

    # wrong answer: PASSED/PENDING — a fail must take precedence over a waive.
    assert result.status == ES.FAILED
    # wrong answer: includes the waive — only the failing decision(s) are named.
    assert result.evidence_ids == (fail.id,)


# --- AC8: latest-decision-per-file by (created_at, id). A file carrying both a
# waive and a fail is decided by the LATEST.
def test_waive_then_fail_fails() -> None:
    waive = make_decision(CODE_A, status=ES.PASSED, created_at=NOW)
    fail = make_decision(CODE_A, status=ES.FAILED, created_at=NOW + timedelta(hours=1))
    result = evaluate_scope(
        [CODE_A],
        relevant_docs=[DOC_A],
        source_anchor=ANCHOR,
        ticket_id=TICKET,
        head_commit=HEAD,
        evidence=[waive, fail],  # order scrambled below would not matter
    )

    # wrong answer: PASSED — the later fail (operator changed their mind) must win.
    assert result.status == ES.FAILED
    assert result.evidence_ids == (fail.id,)


def test_fail_then_waive_passes() -> None:
    fail = make_decision(CODE_A, status=ES.FAILED, created_at=NOW)
    waive = make_decision(CODE_A, status=ES.PASSED, created_at=NOW + timedelta(hours=1))
    result = evaluate_scope(
        [CODE_A],
        relevant_docs=[DOC_A],
        source_anchor=ANCHOR,
        ticket_id=TICKET,
        head_commit=HEAD,
        evidence=[fail, waive],
    )

    # wrong answer: FAILED — the later waive must win over the earlier fail.
    assert result.status == ES.PASSED
    assert result.evidence_ids == (waive.id,)


def test_latest_decision_per_file_equal_created_at_id_tiebreak() -> None:
    # Same created_at: the higher id is the latest by the (created_at, id) order.
    # The waive has the higher id, so waive wins -> PASSED.
    fail = make_decision(CODE_A, status=ES.FAILED, created_at=NOW, id=UUID(int=0x01))
    waive = make_decision(CODE_A, status=ES.PASSED, created_at=NOW, id=UUID(int=0x02))
    result = evaluate_scope(
        [CODE_A],
        relevant_docs=[DOC_A],
        source_anchor=ANCHOR,
        ticket_id=TICKET,
        head_commit=HEAD,
        evidence=[waive, fail],
    )

    # wrong answer: FAILED — the id-tiebreak picked the lower id (the fail).
    assert result.status == ES.PASSED
    assert result.evidence_ids == (waive.id,)


# --- AC9: a waive at a DIFFERENT commit -> that file undecided -> PENDING (the
# commit pin). A new push resets scope decisions.
def test_waive_at_other_commit_is_pending() -> None:
    stale = make_decision(CODE_A, status=ES.PASSED, commit_sha=OTHER)
    result = evaluate_scope(
        [CODE_A],
        relevant_docs=[DOC_A],
        source_anchor=ANCHOR,
        ticket_id=TICKET,
        head_commit=HEAD,
        evidence=[stale],
    )

    # wrong answer: PASSED — a waive at a stale commit leaked past the pin.
    assert result.status == ES.PENDING
    assert result.evidence_ids == ()


# --- AC10 (crux): a system- or agent-tier decision with a matching path at C is
# NOT a decision (human-tier only) -> PENDING. Wrong answer named: PASSED.
@pytest.mark.parametrize("actor", [ActorType.SYSTEM, ActorType.AGENT])
def test_non_human_decision_is_ignored_and_pending(actor: ActorType) -> None:
    forged = make_decision(CODE_A, status=ES.PASSED, created_by_type=actor)
    result = evaluate_scope(
        [CODE_A],
        relevant_docs=[DOC_A],
        source_anchor=ANCHOR,
        ticket_id=TICKET,
        head_commit=HEAD,
        evidence=[forged],
    )

    # wrong answer: PASSED — a machine/agent manufactured a scope waive.
    assert result.status == ES.PENDING
    assert result.evidence_ids == ()


# --- AC11: a decision for a DIFFERENT ticket_id -> ignored -> PENDING (ticket
# scoping).
def test_decision_for_other_ticket_is_ignored_and_pending() -> None:
    wrong_ticket = make_decision(CODE_A, status=ES.PASSED, ticket_id=OTHER_TICKET)
    result = evaluate_scope(
        [CODE_A],
        relevant_docs=[DOC_A],
        source_anchor=ANCHOR,
        ticket_id=TICKET,
        head_commit=HEAD,
        evidence=[wrong_ticket],
    )

    # wrong answer: PASSED — a waive from another ticket leaked across scope.
    assert result.status == ES.PENDING
    assert result.evidence_ids == ()


# --- AC12: malformed raw_payload contributes no decision, never raises.
@pytest.mark.parametrize(
    "raw_payload",
    [
        {},  # missing the path key entirely (a blanket human_approval)
        {SCOPE_DECISION_PATH_KEY: 123},  # non-str path
        {SCOPE_DECISION_PATH_KEY: None},  # null path
        {SCOPE_DECISION_PATH_KEY: "   "},  # blank-only path -> not a decision
        {  # an ATLAS-69 retention-cap marker payload: no path key
            "_truncated": True,
            "_original_bytes": 99999,
            "_payload_hash": "abc",
            "_source_uri": None,
        },
    ],
)
def test_malformed_payload_contributes_no_decision_and_is_pending(
    raw_payload: dict[str, Any],
) -> None:
    record = make_decision(CODE_A, status=ES.PASSED, raw_payload=raw_payload)
    result = evaluate_scope(
        [CODE_A],
        relevant_docs=[DOC_A],
        source_anchor=ANCHOR,
        ticket_id=TICKET,
        head_commit=HEAD,
        evidence=[record],
    )

    # wrong answer: PASSED (a path read from a malformed payload) or a raise.
    assert result.status == ES.PENDING
    assert result.evidence_ids == ()


# --- AC13: empty pr_files -> PASSED (nothing out of scope), never raises.
def test_empty_pr_files_passes() -> None:
    result = evaluate_scope(
        [],
        relevant_docs=[DOC_A],
        source_anchor=ANCHOR,
        ticket_id=TICKET,
        head_commit=HEAD,
        evidence=[],
    )

    # wrong answer: PENDING/FAILED — an empty PR has nothing out of scope.
    assert result.status == ES.PASSED
    assert result.evidence_ids == ()


# --- AC14: evidence_ids asserted as value AND order, with an equal-created_at
# id-tiebreak so ordering is a real guard (deterministic (created_at, id)).
def test_passed_with_waives_evidence_ids_value_and_order() -> None:
    # Two out-of-scope files, both waived. Pass scrambled; b is OLDER so it sorts
    # first by created_at regardless of input order.
    waive_a = make_decision(
        CODE_A, status=ES.PASSED, created_at=NOW + timedelta(minutes=1)
    )
    waive_b = make_decision(CODE_B, status=ES.PASSED, created_at=NOW)
    result = evaluate_scope(
        [CODE_A, CODE_B],
        relevant_docs=[DOC_A],
        source_anchor=ANCHOR,
        ticket_id=TICKET,
        head_commit=HEAD,
        evidence=[waive_a, waive_b],
    )

    # wrong answer: PENDING — both files are waived.
    assert result.status == ES.PASSED
    # wrong answer: input-order (a, b) — b is older so it sorts first.
    assert result.evidence_ids == (waive_b.id, waive_a.id)


def test_partial_pending_waives_ordered_by_id_on_equal_created_at() -> None:
    # Two out-of-scope files waived at the SAME created_at + one undecided file.
    # Ordering must fall back to id: the lower id sorts first.
    waive_lo = make_decision(
        CODE_A, status=ES.PASSED, created_at=NOW, id=UUID(int=0x10)
    )
    waive_hi = make_decision(
        CODE_B, status=ES.PASSED, created_at=NOW, id=UUID(int=0x20)
    )
    undecided = "atlas/cli.py"
    result = evaluate_scope(
        # pass hi before lo so input-order would mis-sort
        [CODE_B, CODE_A, undecided],
        relevant_docs=[DOC_A],
        source_anchor=ANCHOR,
        ticket_id=TICKET,
        head_commit=HEAD,
        evidence=[waive_hi, waive_lo],
    )

    # wrong answer: PASSED — one file is still undecided.
    assert result.status == ES.PENDING
    # wrong answer: (hi, lo) input-order — equal created_at falls back to id.
    assert result.evidence_ids == (waive_lo.id, waive_hi.id)
    assert "2 of 3 out-of-scope" in result.reason
    assert "1 awaiting an operator decision" in result.reason


# --- C1 (dedup): a path duplicated in the PR file list resolves to ONE latest
# decision and ONE verdict contribution — a duplicate cannot make a path count
# as both waived and undecided.
def test_duplicate_pr_path_resolves_once() -> None:
    waive = make_decision(CODE_A, status=ES.PASSED)
    result = evaluate_scope(
        [CODE_A, CODE_A, "  " + CODE_A + "  "],  # same path three ways
        relevant_docs=[DOC_A],
        source_anchor=ANCHOR,
        ticket_id=TICKET,
        head_commit=HEAD,
        evidence=[waive],
    )

    # wrong answer: PENDING — a duplicate entry counted the path as undecided too.
    assert result.status == ES.PASSED
    # wrong answer: (waive.id, waive.id, ...) — the waive is named exactly once.
    assert result.evidence_ids == (waive.id,)
    assert "all 1 out-of-scope" in result.reason


# --- AC16 / defensiveness: never raises across empty pr_files, empty evidence,
# empty source_anchor, and malformed payloads.
def test_empty_source_anchor_is_dropped_from_scope() -> None:
    # An "" source_anchor yields "" for its path part, which the blanks rule
    # drops — it must not match an empty/odd PR entry. CODE_A stays out of scope.
    result = evaluate_scope(
        [CODE_A],
        relevant_docs=[DOC_A],
        source_anchor="",
        ticket_id=TICKET,
        head_commit=HEAD,
        evidence=[],
    )

    # wrong answer: a raise, or PASSED via an empty-path match.
    assert result.status == ES.PENDING
    assert result.evidence_ids == ()


def test_empty_evidence_never_raises() -> None:
    result = evaluate_scope(
        [CODE_A],
        relevant_docs=[],
        source_anchor=ANCHOR,
        ticket_id=TICKET,
        head_commit=HEAD,
        evidence=[],
    )
    # wrong answer: a raise — absence is data, not an exception.
    assert result.status == ES.PENDING


def test_non_manual_approval_evidence_is_ignored() -> None:
    # A non-MANUAL_APPROVAL record carrying a matching path is not a decision.
    test_result = make_decision(CODE_A, status=ES.PASSED, evidence_type=ET.TEST_RESULT)
    result = evaluate_scope(
        [CODE_A],
        relevant_docs=[DOC_A],
        source_anchor=ANCHOR,
        ticket_id=TICKET,
        head_commit=HEAD,
        evidence=[test_result],
    )

    # wrong answer: PASSED — a non-approval evidence type decided a file.
    assert result.status == ES.PENDING
    assert result.evidence_ids == ()


# --- Result shape guard.
def test_result_is_frozen_dataclass() -> None:
    result = evaluate_scope(
        [],
        relevant_docs=[DOC_A],
        source_anchor=ANCHOR,
        ticket_id=TICKET,
        head_commit=HEAD,
        evidence=[],
    )
    assert isinstance(result, ScopeEvaluation)
    assert result.check_type == VerificationCheckType.SCOPE
    with pytest.raises(Exception):  # noqa: B017 — FrozenInstanceError is the point
        result.status = ES.PASSED  # type: ignore[misc]
