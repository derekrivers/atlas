"""The human-approval evaluator (ATLAS-76, folded per OP-A), per
docs/atlas/verification-engine.md "Evaluation semantics" (the human_approval
bullet) and "Principle".

:func:`evaluate_human_approval` answers one question for a ticket's required
``human_approval`` check at PR head commit ``C``: has the operator given a
blanket approval of this PR at ``C``? It is the sixth per-check evaluator, a
sibling of :func:`atlas.verification.evaluate_acceptance_criteria`
(acceptance_check.py) and :func:`atlas.verification.evaluate_scope`
(scope_check.py). Like those it is HUMAN-tier: only the operator approves a PR,
so an agent's or a machine's MANUAL_APPROVAL can NEVER satisfy it (ADR-0008).

The resolver (ATLAS-71) surfaces ``human_approval`` as required for high/critical
feature and infrastructure tickets and for spike/research; ATLAS-71 left it
without an evaluator (the OP-A gap). This module closes that gap.

The approval convention (the discriminator is ABSENCE). A blanket approval is a
human-tier :attr:`EvidenceType.MANUAL_APPROVAL` record that:

- is pinned to ``C`` (``commit_sha == head_commit``),
- is scoped to the ticket (``ticket_id == ticket_id``),
- is human-tier (``evidence_tier(created_by_type) == "human"``), and
- carries in ``raw_payload`` NEITHER ``ACCEPTANCE_CRITERION_HASH_KEY`` NOR
  ``SCOPE_DECISION_PATH_KEY``.

That double absence is what distinguishes a blanket PR approval from an
acceptance-criterion confirmation (which carries the criterion hash) or a scope
decision (which carries the literal path). Both discriminator keys are imported
from their home modules — acceptance_check and scope_check — so this evaluator
and those it must NOT collide with read the convention from one source and can
never drift.

The LATEST such record by ``(created_at, id)`` decides, and its
:attr:`Evidence.status` passes through unchanged (OP-B), mirroring scope's
waive/fail: ``PASSED`` == approved, ``FAILED`` == the operator rejected the PR (a
dispute the PM Engine routes to ``needs_human_decision``). None → ``PENDING`` (an
unapproved PR is unproven, not failing).

PURE and layer-faithful (D9): this module imports ``atlas.core`` only — models,
enums, and ``trust.evidence_tier`` (the ONLY place tier logic lives; not
reimplemented here) — plus its two ``atlas.verification`` siblings for the
discriminator keys. It takes pre-loaded evidence as a parameter; it does NOT load
from storage, NOT capture or write the operator's approval (that is the verify
CLI's job, ATLAS-80 / OP-3), NOT persist VerificationCheck rows, and NOT compose
a verdict (that is :func:`atlas.verification.evaluate_ticket`).
``atlas.verification`` stays below ``atlas.pm`` in the import-linter spine.

Following the sibling evaluators, invalidity and absence are DATA, not
exceptions: the evaluator NEVER raises, for any input — empty evidence, a record
with ``commit_sha=None``, or a malformed/again-capped ``raw_payload``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from atlas.core.enums import EvidenceStatus
from atlas.core.models import Evidence, EvidenceType, VerificationCheckType
from atlas.core.trust import evidence_tier
from atlas.verification.acceptance_check import ACCEPTANCE_CRITERION_HASH_KEY
from atlas.verification.scope_check import SCOPE_DECISION_PATH_KEY

# Tier names are compared inline as string literals against ``evidence_tier(...)``
# — the sibling-evaluator idiom — so this module defines no tier-named binding of
# its own (tier logic lives only in atlas.core.trust, ADR-0008). "human" alone
# approves a PR; a non-human MANUAL_APPROVAL carrying neither discriminator does
# NOT approve it.


@dataclass(frozen=True)
class HumanApprovalEvaluation:
    """The result of :func:`evaluate_human_approval`.

    Frozen: an evaluation is an immutable record. ``check_type`` is always
    :attr:`VerificationCheckType.HUMAN_APPROVAL` — this evaluator decides a single
    fixed check, so the type is a constant, not an argument. ``status`` is the
    latest blanket approval's status passed through unchanged (OP-B): ``PASSED`` ==
    approved, ``FAILED`` == the operator rejected the PR; or ``PENDING`` when no
    blanket approval exists at the head commit (an unapproved PR is unproven, not
    failing). ``evidence_id`` names the deciding Evidence record, or ``None`` when
    none decided it. ``reason`` is a human-readable explanation.

    This is NOT a :class:`VerificationCheck`: it carries no id, timestamps, or
    summary — building the persisted row is the composition's job (ATLAS-77/80).
    """

    check_type: VerificationCheckType
    status: EvidenceStatus
    evidence_id: UUID | None
    reason: str


def evaluate_human_approval(
    *,
    ticket_id: UUID,
    head_commit: str,
    evidence: Sequence[Evidence],
) -> HumanApprovalEvaluation:
    """Evaluate the human_approval check vs pre-loaded evidence (never raises).

    Returns a :class:`HumanApprovalEvaluation`. The rule:

    - The candidates are every record where ALL hold: ``evidence_type`` is
      MANUAL_APPROVAL; ``ticket_id`` equals this ticket; ``commit_sha`` equals
      ``head_commit`` exactly; tier is ``human``; and ``raw_payload`` carries
      NEITHER discriminator key (a blanket approval, not an acceptance confirmation
      or a scope decision).
    - With candidates, the LATEST by ``(created_at, id)`` decides: its ``status``
      passes through unchanged (OP-B) and its ``id`` is the ``evidence_id``.
    - With no candidates the check is ``PENDING`` (never ``FAILED`` — an
      unapproved PR is unproven, not failing).

    Args:
        ticket_id: the ticket the approval must be scoped to (a parameter — this
            evaluator does not resolve it).
        head_commit: the PR head commit the approval must be pinned to (a
            parameter — resolving it is the CLI's / Phase 8's job).
        evidence: pre-loaded Evidence records to evaluate against (loading is the
            CLI's job; this module never touches storage).
    """
    candidates = [
        e
        for e in evidence
        if e.evidence_type == EvidenceType.MANUAL_APPROVAL
        and e.ticket_id == ticket_id
        and e.commit_sha == head_commit
        and evidence_tier(e.created_by_type) == "human"
        and _is_blanket_approval(e)
    ]

    if candidates:
        latest = max(candidates, key=lambda e: (e.created_at, e.id))
        return HumanApprovalEvaluation(
            check_type=VerificationCheckType.HUMAN_APPROVAL,
            status=latest.status,
            evidence_id=latest.id,
            reason=(
                f"human_approval: blanket human-tier manual_approval {latest.id} "
                f"pinned to {head_commit} reports {latest.status.value}."
            ),
        )

    return HumanApprovalEvaluation(
        check_type=VerificationCheckType.HUMAN_APPROVAL,
        status=EvidenceStatus.PENDING,
        evidence_id=None,
        reason=(
            f"human_approval: no blanket human-tier manual_approval exists at "
            f"{head_commit}; PENDING (an unapproved PR is unproven, not failing)."
        ),
    )


def _is_blanket_approval(evidence: Evidence) -> bool:
    """Whether a record is a blanket approval — carries NEITHER discriminator.

    Reads ``raw_payload`` defensively and never raises: a record whose payload
    carries the acceptance criterion hash is an acceptance confirmation, and one
    carrying the scope decision path is a scope decision; either presence means
    this is NOT a blanket approval. The keys are imported from their home modules
    so the discrimination convention has a single source of truth.
    """
    payload = evidence.raw_payload
    return (
        ACCEPTANCE_CRITERION_HASH_KEY not in payload
        and SCOPE_DECISION_PATH_KEY not in payload
    )
