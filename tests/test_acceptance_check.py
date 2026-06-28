"""ATLAS-72: the acceptance-criteria evaluator decides the ``acceptance_criteria``
check against pre-loaded evidence, using human-tier MANUAL_APPROVAL confirmations
pinned to the head commit and scoped to the ticket, one per criterion, and NEVER
raising.

Each behavioural assertion names the wrong answer it would catch. Beyond status,
two value-level guards matter and are asserted explicitly (T1): ``evidence_ids``
on PASSED is the full confirming set, and on partial-PENDING is the PARTIAL
confirmed set — both ordered by ``(created_at, id)``. The composition ticket
(ATLAS-76/77) folds these straight into ``VerificationCheck.evidence_ids``, so a
refactor that returns ``()`` always, or an unsorted set, must NOT stay green:
the order assertions pass evidence in scrambled order so input-order would fail.

The milestone crux is guarded explicitly (AC4): a system- or agent-tier
MANUAL_APPROVAL carrying a matching hash at C cannot manufacture a pass, AND the
PENDING reason must actually say the non-human claim is ignored — a status-only
assertion would stay green if that message silently vanished (the dead-guard
trap). The hash convention's properties are guarded directly (AC10): strip
stability ("  do X  " == "do X") and distinctness ("do X" != "do Y").
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from atlas.core.enums import ActorType, EvidenceStatus
from atlas.core.models import Evidence, EvidenceType, VerificationCheckType
from atlas.verification import (
    AcceptanceEvaluation,
    acceptance_criterion_hash,
    evaluate_acceptance_criteria,
)

NOW = datetime(2026, 6, 28, tzinfo=UTC)
HEAD = "c0ffee0000000000000000000000000000000000"
OTHER = "dead000000000000000000000000000000000000"
TICKET = UUID(int=0xA72)
OTHER_TICKET = UUID(int=0xB73)
CRIT_A = "The CLI exits non-zero on an invalid graph."
CRIT_B = "The report names the failing dependency."
CRIT_C = "A new push resets the check to pending."
ES = EvidenceStatus
ET = EvidenceType


def make_confirmation(
    criterion: str,
    *,
    ticket_id: UUID = TICKET,
    commit_sha: str | None = HEAD,
    created_by_type: ActorType = ActorType.HUMAN,
    created_at: datetime = NOW,
    id: UUID | None = None,
    raw_payload: dict[str, Any] | None = None,
    evidence_type: EvidenceType = ET.MANUAL_APPROVAL,
) -> Evidence:
    """A per-criterion confirmation shaped like the convention (D3).

    By default a human-tier MANUAL_APPROVAL pinned to HEAD, scoped to TICKET,
    carrying ``acceptance_criterion_hash`` for ``criterion``. ``raw_payload``
    overrides the payload wholesale for the malformed/blanket cases.
    """
    if raw_payload is None:
        raw_payload = {
            "acceptance_criterion_hash": acceptance_criterion_hash(criterion)
        }
    return Evidence(
        id=id or uuid4(),
        product_id=uuid4(),
        ticket_id=ticket_id,
        evidence_type=evidence_type,
        status=ES.PASSED,
        summary="operator confirmed criterion",
        commit_sha=commit_sha,
        raw_payload=raw_payload,
        created_by_type=created_by_type,
        created_by_id="operator" if created_by_type == ActorType.HUMAN else "claude",
        created_at=created_at,
    )


# --- AC1: every criterion confirmed at C -> PASSED; evidence_ids = full set,
# ordered by (created_at, id) (T1: assert contents AND order, not mere presence).
def test_all_criteria_confirmed_passes_with_ordered_evidence_ids() -> None:
    conf_a = make_confirmation(CRIT_A, created_at=NOW + timedelta(minutes=1))
    conf_b = make_confirmation(CRIT_B, created_at=NOW)
    # Pass scrambled (a before b) though b is older: input-order would mis-sort.
    result = evaluate_acceptance_criteria(
        [CRIT_A, CRIT_B], ticket_id=TICKET, head_commit=HEAD, evidence=[conf_a, conf_b]
    )

    # wrong answer: PENDING — every criterion is confirmed at C.
    assert result.status == ES.PASSED
    assert result.check_type == VerificationCheckType.ACCEPTANCE_CRITERIA
    # wrong answer: () always, a partial set, or input-order (conf_a, conf_b).
    # b is older so it sorts first regardless of input order.
    assert result.evidence_ids == (conf_b.id, conf_a.id)


# --- AC2: one of N unconfirmed -> PENDING; reason names the count; evidence_ids
# = the PARTIAL confirmed set, ordered (T1: assert the partial set's value+order).
def test_one_unconfirmed_is_pending_with_partial_ordered_evidence_ids() -> None:
    conf_a = make_confirmation(CRIT_A, created_at=NOW + timedelta(minutes=2))
    conf_b = make_confirmation(CRIT_B, created_at=NOW + timedelta(minutes=1))
    # CRIT_C deliberately unconfirmed. Pass scrambled (a before b, b is newer).
    result = evaluate_acceptance_criteria(
        [CRIT_A, CRIT_B, CRIT_C],
        ticket_id=TICKET,
        head_commit=HEAD,
        evidence=[conf_a, conf_b],
    )

    # wrong answer: PASSED — one criterion (CRIT_C) is unconfirmed.
    assert result.status == ES.PENDING
    # wrong answer: () or the full set — the partial confirmed set is the useful
    # operator output, ordered by (created_at, id): b (older) before a.
    assert result.evidence_ids == (conf_b.id, conf_a.id)
    # wrong answer: a reason without the count — "2 of 3" / "1 unconfirmed".
    assert "2 of 3" in result.reason
    assert "1 unconfirmed" in result.reason


# --- AC3: a confirmation at a DIFFERENT commit -> that criterion unconfirmed
# -> PENDING (commit pin / OP-3).
def test_confirmation_at_other_commit_is_pending() -> None:
    stale = make_confirmation(CRIT_A, commit_sha=OTHER)
    result = evaluate_acceptance_criteria(
        [CRIT_A], ticket_id=TICKET, head_commit=HEAD, evidence=[stale]
    )

    # wrong answer: PASSED — a confirmation at a stale commit leaked past the pin.
    assert result.status == ES.PENDING
    assert result.evidence_ids == ()


# --- AC4 (crux): a system- or agent-tier MANUAL_APPROVAL with a matching hash at
# C does NOT confirm -> PENDING, and the reason MUST name the ignored claim.
@pytest.mark.parametrize("actor", [ActorType.SYSTEM, ActorType.AGENT])
def test_non_human_confirmation_is_pending_and_reason_names_it(
    actor: ActorType,
) -> None:
    forged = make_confirmation(CRIT_A, created_by_type=actor)
    result = evaluate_acceptance_criteria(
        [CRIT_A], ticket_id=TICKET, head_commit=HEAD, evidence=[forged]
    )

    # wrong answer: PASSED — a machine/agent forged an acceptance pass.
    assert result.status == ES.PENDING
    assert result.evidence_ids == ()
    # The guard must bite: a status-only check would stay green if this message
    # silently vanished. Assert both the ignore language and the rationale.
    reason = result.reason.lower()
    assert "ignored" in reason
    assert "operator-confirmed" in reason


# --- AC5: a confirmation for a DIFFERENT ticket (same criterion text) -> does
# NOT confirm -> PENDING (ticket scoping).
def test_confirmation_for_other_ticket_is_pending() -> None:
    wrong_ticket = make_confirmation(CRIT_A, ticket_id=OTHER_TICKET)
    result = evaluate_acceptance_criteria(
        [CRIT_A], ticket_id=TICKET, head_commit=HEAD, evidence=[wrong_ticket]
    )

    # wrong answer: PASSED — a confirmation from another ticket leaked across scope.
    assert result.status == ES.PENDING
    assert result.evidence_ids == ()


# --- AC6: a criterion reworded so its hash changes, with a confirmation for the
# OLD text -> that criterion unconfirmed -> PENDING (auto-invalidation).
def test_reworded_criterion_invalidates_old_confirmation() -> None:
    old_text = "The report lists out-of-scope files."
    reworded = "The report lists every out-of-scope file with its path."
    confirm_old = make_confirmation(old_text)  # hash of the OLD wording
    result = evaluate_acceptance_criteria(
        [reworded], ticket_id=TICKET, head_commit=HEAD, evidence=[confirm_old]
    )

    # wrong answer: PASSED — a stale confirmation matched the reworded criterion.
    assert result.status == ES.PENDING
    assert result.evidence_ids == ()


# --- AC7: a blanket human_approval MANUAL_APPROVAL with no
# acceptance_criterion_hash -> ignored -> PENDING (the discriminator works).
def test_blanket_human_approval_without_hash_is_ignored() -> None:
    blanket = make_confirmation(CRIT_A, raw_payload={"note": "looks good to me"})
    result = evaluate_acceptance_criteria(
        [CRIT_A], ticket_id=TICKET, head_commit=HEAD, evidence=[blanket]
    )

    # wrong answer: PASSED — a hash-less blanket approval was treated as a confirmation.
    assert result.status == ES.PENDING
    assert result.evidence_ids == ()
    # A human MANUAL_APPROVAL with no matching hash is not a "non-human claim".
    assert "ignored" not in result.reason.lower()


# --- AC8: malformed raw_payload contributes no confirmation, never raises.
@pytest.mark.parametrize(
    "raw_payload",
    [
        {},  # missing the hash key entirely
        {"acceptance_criterion_hash": 123},  # non-str hash
        {"acceptance_criterion_hash": None},  # null hash
        {  # an ATLAS-69 retention-cap marker payload: no hash key
            "_truncated": True,
            "_original_bytes": 99999,
            "_payload_hash": "abc",
            "_source_uri": None,
        },
    ],
)
def test_malformed_payload_contributes_no_confirmation_and_is_pending(
    raw_payload: dict[str, Any],
) -> None:
    record = make_confirmation(CRIT_A, raw_payload=raw_payload)
    result = evaluate_acceptance_criteria(
        [CRIT_A], ticket_id=TICKET, head_commit=HEAD, evidence=[record]
    )

    # wrong answer: PASSED (hash read from a malformed payload) or a raise.
    assert result.status == ES.PENDING
    assert result.evidence_ids == ()


# --- AC9: empty acceptance_criteria -> NOT_APPLICABLE, never raises.
def test_empty_criteria_is_not_applicable() -> None:
    result = evaluate_acceptance_criteria(
        [], ticket_id=TICKET, head_commit=HEAD, evidence=[]
    )

    # wrong answer: PASSED (a silent vacuous pass) or PENDING — empty is a guard.
    assert result.status == ES.NOT_APPLICABLE
    assert result.evidence_ids == ()


def test_blank_only_criteria_is_not_applicable() -> None:
    # Whitespace-only criteria normalise away to nothing -> NOT_APPLICABLE, not a
    # spurious criterion that could never be confirmed.
    conf = make_confirmation(CRIT_A)
    result = evaluate_acceptance_criteria(
        ["   ", ""], ticket_id=TICKET, head_commit=HEAD, evidence=[conf]
    )

    # wrong answer: PASSED — blank criteria must not vacuously pass.
    assert result.status == ES.NOT_APPLICABLE
    assert result.evidence_ids == ()


# --- AC10: the hash is deterministic, strip-stable, and distinct per text.
def test_hash_is_strip_stable_and_distinct() -> None:
    # strip-stability: surrounding whitespace does not change the hash.
    # wrong answer: the two differ — .strip() normalisation is not applied.
    assert acceptance_criterion_hash("  do X  ") == acceptance_criterion_hash("do X")
    # determinism: same text, same hash on repeat.
    assert acceptance_criterion_hash("do X") == acceptance_criterion_hash("do X")
    # distinctness: different words, different hash.
    # wrong answer: equal — the hash ignores the criterion's content.
    assert acceptance_criterion_hash("do X") != acceptance_criterion_hash("do Y")


def test_confirmation_matches_after_strip_normalisation() -> None:
    # The criterion carries surrounding whitespace; the confirmation was written
    # against the clean text. .strip() on both sides makes the hashes equal.
    conf = make_confirmation(CRIT_A)
    result = evaluate_acceptance_criteria(
        [f"  {CRIT_A}  "], ticket_id=TICKET, head_commit=HEAD, evidence=[conf]
    )

    # wrong answer: PENDING — whitespace defeated the match.
    assert result.status == ES.PASSED
    assert result.evidence_ids == (conf.id,)


# --- AC12: never raises across empty evidence and malformed payloads.
def test_empty_evidence_is_pending_not_raise() -> None:
    result = evaluate_acceptance_criteria(
        [CRIT_A], ticket_id=TICKET, head_commit=HEAD, evidence=[]
    )

    # wrong answer: an exception, or FAILED — a missing confirmation is unproven.
    assert result.status == ES.PENDING
    assert result.evidence_ids == ()


def test_non_manual_approval_evidence_is_ignored() -> None:
    # A TEST_RESULT carrying a matching hash is not a MANUAL_APPROVAL -> ignored.
    test_result = Evidence(
        id=uuid4(),
        product_id=uuid4(),
        ticket_id=TICKET,
        evidence_type=ET.TEST_RESULT,
        status=ES.PASSED,
        summary="ci",
        commit_sha=HEAD,
        raw_payload={"acceptance_criterion_hash": acceptance_criterion_hash(CRIT_A)},
        created_by_type=ActorType.SYSTEM,
        created_by_id="github-actions",
        created_at=NOW,
    )
    result = evaluate_acceptance_criteria(
        [CRIT_A], ticket_id=TICKET, head_commit=HEAD, evidence=[test_result]
    )

    # wrong answer: PASSED — a non-approval evidence type confirmed a criterion.
    assert result.status == ES.PENDING
    assert result.evidence_ids == ()


# --- Result shape guard.
def test_result_is_frozen_dataclass() -> None:
    result = evaluate_acceptance_criteria(
        [CRIT_A], ticket_id=TICKET, head_commit=HEAD, evidence=[]
    )
    assert isinstance(result, AcceptanceEvaluation)
    assert result.check_type == VerificationCheckType.ACCEPTANCE_CRITERIA
    with pytest.raises(Exception):  # noqa: B017 — FrozenInstanceError is the point
        result.status = ES.PASSED  # type: ignore[misc]
