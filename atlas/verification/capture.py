"""Operator-confirmation capture — the pure foundation (ATLAS-132, OP-3.1), per
docs/atlas/verification-engine.md "Evaluation semantics" and "Verdict and
completion".

The three human-tier evaluators — :func:`evaluate_acceptance_criteria`
(acceptance_check.py), :func:`evaluate_scope` (scope_check.py), and
:func:`evaluate_human_approval` (human_approval_check.py) — read pre-loaded
``MANUAL_APPROVAL`` Evidence and pass only when the operator's confirmations
exist at the PR head commit ``C``. Nothing writes those confirmations yet, so
every real ticket reads PENDING. This module is the pure foundation that
PRODUCES exactly the Evidence records those evaluators match — the confirmation
*builders* — plus :func:`pending_capture`, the inverse view that reports what the
operator still has to decide at ``C``. The interactive ``atlas confirm`` CLI that
wraps these (operator identity, the clock, the uuid factory, GitHub resolution,
persistence) is OP-3.2 — this leg touches NO I/O and calls NO ``EvidenceRepo``.

The contract (the single source of truth is the evaluators, not this module). A
confirmation is a ``MANUAL_APPROVAL`` Evidence record that ALL three evaluators
agree on the shape of:

- ``evidence_type == EvidenceType.MANUAL_APPROVAL``,
- ``created_by_type == ActorType.HUMAN`` (so ``evidence_tier(...) == "human"``;
  an agent- or system-tier record can NEVER satisfy these checks — ADR-0008),
- ``commit_sha == head_commit`` (the PR head ``C``) — ALWAYS set: a human
  confirmation with ``commit_sha=None`` is accepted by ``EvidenceRepo`` (it only
  *forces* the pin for system-tier) but silently IGNORED by every evaluator, so
  pinning ``C`` is a correctness requirement, not a storage one,
- ``ticket_id == ticket.id``,
- discriminated by ``raw_payload``:

  * acceptance confirmation → ``raw_payload[ACCEPTANCE_CRITERION_HASH_KEY] =
    acceptance_criterion_hash(criterion)``; ``status = PASSED``;
  * scope decision → ``raw_payload[SCOPE_DECISION_PATH_KEY] = <normalised path>``;
    ``status = PASSED`` (waive) or ``FAILED`` (fail/dispute);
  * blanket human approval → ``raw_payload`` carries NEITHER key; ``status =
    PASSED`` (approve) or ``FAILED`` (reject).

The discriminating key constants and :func:`acceptance_criterion_hash` are
imported from their home evaluator modules — one source — so the builders and the
evaluators can never drift. Status mapping is fixed (D4): confirm / waive /
approve → ``PASSED``; scope-fail / reject → ``FAILED``; there is no other status
a builder emits (the scope evaluator treats any other status as undecided).

Re-confirmation is by head pin, not mutation (D5): every builder pins the CURRENT
``C``, so a new push (new ``C``) auto-invalidates prior confirmations (different
``commit_sha``). Append-only persistence is OP-3.2's concern; this leg only
constructs records.

PURE and layer-faithful: this module imports ``atlas.core`` and its
``atlas.verification`` siblings only — no storage, no CLI, no upward import. The
builders take the uuid (``evidence_id``), the clock (``now``), and the operator
identity (``operator_id``) as PARAMETERS; the caller (OP-3.2) supplies them.
Following the evaluators, invalidity and absence are DATA, not exceptions: the
builders and :func:`pending_capture` never raise, for any input.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from atlas.core.enums import ActorType, EvidenceStatus
from atlas.core.models import Evidence, EvidenceType, Ticket, VerificationCheckType
from atlas.verification.acceptance_check import (
    ACCEPTANCE_CRITERION_HASH_KEY,
    acceptance_criterion_hash,
    evaluate_acceptance_criteria,
)
from atlas.verification.human_approval_check import evaluate_human_approval
from atlas.verification.rules import required_checks
from atlas.verification.scope_check import (
    SCOPE_DECISION_PATH_KEY,
    _latest_decision,
    out_of_scope_paths,
)


@dataclass(frozen=True)
class CriterionPrompt:
    """One acceptance criterion the operator has yet to confirm at ``C``.

    Frozen. ``criterion`` is the ``.strip()``ed criterion text the operator reads;
    ``criterion_hash`` is :func:`acceptance_criterion_hash` of that text — the same
    digest :func:`build_acceptance_confirmation` writes and the acceptance
    evaluator matches — so OP-3.2 can prompt with the text and build with the hash
    from one record.
    """

    criterion: str
    criterion_hash: str


@dataclass(frozen=True)
class PendingCapture:
    """What the operator still has to decide for a ticket at head commit ``C``.

    Frozen — the inverse view of the three human-tier evaluators, built ONLY from
    their public/shared pieces (never a second copy of the matching logic):

    - ``unconfirmed_criteria``: each criterion with no human confirmation at ``C``
      (deduped by hash, ticket order preserved), carrying its text and hash;
    - ``undecided_scope_files``: out-of-scope PR paths with no latest human
      decision at ``C`` — EMPTY unless the ticket actually requires the ``scope``
      check (mirrors how ``human_approval`` is gated on its requirement);
    - ``human_approval_required_and_missing``: ``True`` iff the ticket requires the
      ``human_approval`` check AND no blanket human approval exists at ``C``.
    """

    unconfirmed_criteria: tuple[CriterionPrompt, ...]
    undecided_scope_files: tuple[str, ...]
    human_approval_required_and_missing: bool


def build_acceptance_confirmation(
    criterion: str,
    *,
    ticket_id: UUID,
    head_commit: str,
    product_id: UUID,
    operator_id: str,
    evidence_id: UUID,
    now: datetime,
) -> Evidence:
    """Build the human-tier confirmation that satisfies one acceptance criterion.

    The record carries ``raw_payload[ACCEPTANCE_CRITERION_HASH_KEY] =
    acceptance_criterion_hash(criterion)`` and ``status = PASSED`` — the exact
    shape :func:`evaluate_acceptance_criteria` matches at ``C``. Pure; never
    persists, never raises.
    """
    return _manual_approval(
        status=EvidenceStatus.PASSED,
        raw_payload={
            ACCEPTANCE_CRITERION_HASH_KEY: acceptance_criterion_hash(criterion)
        },
        summary=f"operator confirmed acceptance criterion at {head_commit}",
        ticket_id=ticket_id,
        head_commit=head_commit,
        product_id=product_id,
        operator_id=operator_id,
        evidence_id=evidence_id,
        now=now,
    )


def build_scope_decision(
    path: str,
    *,
    waive: bool,
    ticket_id: UUID,
    head_commit: str,
    product_id: UUID,
    operator_id: str,
    evidence_id: UUID,
    now: datetime,
) -> Evidence:
    """Build the human-tier scope decision for one out-of-scope file at ``C``.

    The record carries ``raw_payload[SCOPE_DECISION_PATH_KEY] = path.strip()`` (so
    it compares equal to the evaluator's normalised out-of-scope path) and
    ``status = PASSED`` when ``waive`` else ``FAILED`` (D4) — the shape
    :func:`evaluate_scope` matches at ``C``. Pure; never persists, never raises.
    """
    return _manual_approval(
        status=EvidenceStatus.PASSED if waive else EvidenceStatus.FAILED,
        raw_payload={SCOPE_DECISION_PATH_KEY: path.strip()},
        summary=(
            f"operator {'waived' if waive else 'failed'} out-of-scope file "
            f"{path.strip()} at {head_commit}"
        ),
        ticket_id=ticket_id,
        head_commit=head_commit,
        product_id=product_id,
        operator_id=operator_id,
        evidence_id=evidence_id,
        now=now,
    )


def build_blanket_approval(
    *,
    approved: bool,
    ticket_id: UUID,
    head_commit: str,
    product_id: UUID,
    operator_id: str,
    evidence_id: UUID,
    now: datetime,
) -> Evidence:
    """Build the human-tier blanket PR approval at ``C``.

    The record carries NEITHER discriminator key in ``raw_payload`` (that double
    absence is what distinguishes a blanket approval from an acceptance
    confirmation or a scope decision) and ``status = PASSED`` when ``approved``
    else ``FAILED`` (D4) — the shape :func:`evaluate_human_approval` matches at
    ``C``. Pure; never persists, never raises.
    """
    return _manual_approval(
        status=EvidenceStatus.PASSED if approved else EvidenceStatus.FAILED,
        raw_payload={},
        summary=(
            f"operator {'approved' if approved else 'rejected'} the PR at {head_commit}"
        ),
        ticket_id=ticket_id,
        head_commit=head_commit,
        product_id=product_id,
        operator_id=operator_id,
        evidence_id=evidence_id,
        now=now,
    )


def pending_capture(
    ticket: Ticket,
    *,
    head_commit: str,
    pr_files: Sequence[str],
    evidence: Sequence[Evidence],
) -> PendingCapture:
    """Report what the operator still has to decide for ``ticket`` at ``C``.

    The inverse view of the three human-tier evaluators, reusing THEM as the
    matching predicate rather than re-implementing it (never raises):

    - ``unconfirmed_criteria``: each ``.strip()``ed criterion (deduped by hash,
      ticket order preserved) for which the REAL acceptance evaluator, called for
      that one criterion, does not return ``PASSED`` at ``C``.
    - ``undecided_scope_files``: empty unless the ticket requires the ``scope``
      check; otherwise :func:`out_of_scope_paths` (the single-source derivation)
      minus every path already carrying a latest human decision at ``C`` (reusing
      ``scope_check._latest_decision``).
    - ``human_approval_required_and_missing``: ``True`` iff ``required_checks``
      requires ``human_approval`` AND :func:`evaluate_human_approval` is ``PENDING``
      at ``C`` (a PASSED/FAILED blanket means decided, not missing).

    Args:
        ticket: the ticket whose criteria, declared scope, and required checks
            drive the capture (a parameter — this module resolves nothing).
        head_commit: the PR head commit confirmations must be pinned to.
        pr_files: the PR's changed-file paths (resolving them is OP-3.2's job).
        evidence: pre-loaded Evidence to test against (loading is OP-3.2's job).
    """
    required = {check.check_type for check in required_checks(ticket) if check.required}

    unconfirmed: list[CriterionPrompt] = []
    seen: set[str] = set()
    for raw in ticket.acceptance_criteria:
        criterion = raw.strip()
        if not criterion:
            continue
        digest = acceptance_criterion_hash(criterion)
        if digest in seen:
            continue
        seen.add(digest)
        confirmed = evaluate_acceptance_criteria(
            [criterion],
            ticket_id=ticket.id,
            head_commit=head_commit,
            evidence=evidence,
        )
        if confirmed.status != EvidenceStatus.PASSED:
            unconfirmed.append(
                CriterionPrompt(criterion=criterion, criterion_hash=digest)
            )

    if VerificationCheckType.SCOPE in required:
        undecided_scope_files = tuple(
            path
            for path in out_of_scope_paths(
                pr_files,
                relevant_docs=ticket.relevant_docs,
                source_anchor=ticket.source_anchor,
            )
            if _latest_decision(path, ticket.id, head_commit, evidence) is None
        )
    else:
        undecided_scope_files = ()

    human_approval_required_and_missing = (
        VerificationCheckType.HUMAN_APPROVAL in required
        and evaluate_human_approval(
            ticket_id=ticket.id,
            head_commit=head_commit,
            evidence=evidence,
        ).status
        == EvidenceStatus.PENDING
    )

    return PendingCapture(
        unconfirmed_criteria=tuple(unconfirmed),
        undecided_scope_files=undecided_scope_files,
        human_approval_required_and_missing=human_approval_required_and_missing,
    )


def _manual_approval(
    *,
    status: EvidenceStatus,
    raw_payload: dict[str, str],
    summary: str,
    ticket_id: UUID,
    head_commit: str,
    product_id: UUID,
    operator_id: str,
    evidence_id: UUID,
    now: datetime,
) -> Evidence:
    """Construct a human-tier MANUAL_APPROVAL pinned to ``C`` — the shared shape.

    The one place the cross-evaluator contract is materialised: MANUAL_APPROVAL,
    HUMAN tier (``created_by_id = operator_id``), ``commit_sha = head_commit`` (the
    pin is ALWAYS set — an unpinned human record is silently ignored by every
    evaluator), and ``ticket_id``/``product_id`` scoping. The discriminating
    ``raw_payload`` and ``status`` are the caller's; everything else is fixed here
    so the three builders cannot drift from one another.
    """
    return Evidence(
        id=evidence_id,
        product_id=product_id,
        ticket_id=ticket_id,
        evidence_type=EvidenceType.MANUAL_APPROVAL,
        status=status,
        summary=summary,
        commit_sha=head_commit,
        raw_payload=raw_payload,
        created_by_type=ActorType.HUMAN,
        created_by_id=operator_id,
        created_at=now,
    )
