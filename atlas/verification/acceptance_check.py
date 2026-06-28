"""The acceptance-criteria evaluator (ATLAS-72), per
docs/atlas/verification-engine.md "Evaluation semantics" (the
acceptance_criteria bullet) and "Principle".

:func:`evaluate_acceptance_criteria` answers one question for a ticket's
required ``acceptance_criteria`` check at PR head commit ``C``: has EVERY
criterion been confirmed by the operator at ``C``? It is the fourth per-check
evaluator, a sibling of :func:`atlas.verification.evaluate_machine_check`
(machine_checks.py) and :func:`atlas.verification.evaluate_documentation_check`
(documentation_check.py). Unlike those system-tier checks, acceptance is
HUMAN-tier: it records the operator's judgement, so an agent's or a machine's
claim that a criterion is met can NEVER satisfy it (D6; ADR-0008).

The confirmation convention (D3 — the single source of truth ATLAS-73 and
ATLAS-80 inherit). A per-criterion confirmation is a human-tier
:attr:`EvidenceType.MANUAL_APPROVAL` record that:

- is pinned to ``C`` (``commit_sha == head_commit``),
- is scoped to the ticket (``ticket_id == ticket_id``),
- is human-tier (``evidence_tier(created_by_type) == "human"``), and
- carries ``raw_payload["acceptance_criterion_hash"]`` equal to the criterion's
  hash, where :func:`acceptance_criterion_hash` is the SHA-256 hex digest of the
  criterion text after ``.strip()``.

Two layers separate WHICH criterion from WHICH ticket: the hash identifies the
criterion (independent of ordering or ticket); the ``ticket_id`` filter scopes
it to this ticket. The hash is the discriminator that distinguishes an
acceptance confirmation from a blanket ``human_approval`` MANUAL_APPROVAL (which
carries no such key and is therefore ignored here). Because the hash is taken
over the criterion's text, rewording a criterion yields a new hash, so a
confirmation of the OLD text no longer matches — re-confirmation is correctly
required (auto-invalidation).

PURE and layer-faithful (D8): this module imports ``atlas.core`` only — models,
enums, and ``trust.evidence_tier`` (the ONLY place tier logic lives; not
reimplemented here) — plus ``hashlib`` for the one hash convention. It takes
pre-loaded evidence as a parameter; it does NOT load from storage, NOT capture
or write operator confirmations (that is the verify CLI's job, ATLAS-80 / OP-3),
NOT persist VerificationCheck rows, and NOT compose a verdict (ATLAS-76/77).
``atlas.verification`` stays below ``atlas.pm`` in the import-linter spine.

Following the sibling evaluators, invalidity and absence are DATA, not
exceptions: the evaluator NEVER raises, for any input — empty evidence, empty
criteria, a record with ``commit_sha=None``, or a malformed/again-capped
``raw_payload`` (the ATLAS-69 retention-cap marker carries no
``acceptance_criterion_hash`` key, so it confirms nothing). A record that cannot
prove a confirmation simply does not.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from atlas.core.enums import EvidenceStatus
from atlas.core.models import Evidence, EvidenceType, VerificationCheckType
from atlas.core.trust import evidence_tier

# The raw_payload key that discriminates an acceptance confirmation from a
# blanket human_approval MANUAL_APPROVAL (D3). Defined once here, the home of
# the convention, so ATLAS-80 (writes) and ATLAS-73 (mirrors) reference one name.
ACCEPTANCE_CRITERION_HASH_KEY = "acceptance_criterion_hash"

# Tier names are compared inline as string literals against
# ``evidence_tier(...)`` — the storage/repositories.py and sibling-evaluator
# idiom — so this module defines no tier-named binding of its own (tier logic
# lives only in atlas.core.trust, ADR-0008 / knowledge-core.md). "human" alone
# confirms a criterion; a non-human MANUAL_APPROVAL with a matching hash is named
# in the PENDING reason to make the crux explicit (acceptance is
# operator-confirmed, not machine- or agent-claimed).


def acceptance_criterion_hash(criterion: str) -> str:
    """The canonical hash of one acceptance criterion (D3).

    The SHA-256 hex digest of the criterion's text after ``.strip()``. Pure,
    deterministic, and strip-stable: surrounding whitespace does not change the
    hash, but any change to the criterion's words does. There is NO ticket
    scoping inside the hash — the hash says WHICH criterion; the ``ticket_id``
    filter in :func:`evaluate_acceptance_criteria` says WHICH ticket.

    This is the single source of truth for the confirmation convention: ATLAS-80
    writes confirmations carrying this hash, and ATLAS-73 mirrors its shape.
    Never reimplement the digest ad hoc elsewhere — call this.
    """
    return hashlib.sha256(criterion.strip().encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AcceptanceEvaluation:
    """The result of :func:`evaluate_acceptance_criteria` (D4).

    Frozen: an evaluation is an immutable record. ``check_type`` is always
    :attr:`VerificationCheckType.ACCEPTANCE_CRITERIA` — this evaluator decides a
    single fixed check, so the type is a constant, not an argument. ``status`` is
    ``PASSED`` when every criterion has a human-tier confirmation pinned to the
    head commit, ``PENDING`` when one or more remain unconfirmed (NEVER
    ``FAILED`` — an unconfirmed criterion is unproven, not failing), or
    ``NOT_APPLICABLE`` when there are no criteria to confirm (a defensive guard:
    the model guarantees 1 to 7 criteria, so this is not a normal path).

    ``evidence_ids`` is PLURAL — acceptance is a MULTI-evidence check (one
    confirmation per criterion), unlike the single-``evidence_id`` machine/doc
    evaluators. On PASSED it names every confirming record; on PENDING it names
    the PARTIAL set actually confirmed so far (useful to the operator). It is
    deterministically ordered by ``(created_at, id)`` so the composition ticket
    (ATLAS-76/77) can fold it straight into ``VerificationCheck.evidence_ids``
    without re-sorting. ``reason`` is a human-readable explanation.

    This is NOT a :class:`VerificationCheck`: it carries no id, timestamps, or
    summary — building the persisted row is the composition ticket's job
    (ATLAS-76/77).
    """

    check_type: VerificationCheckType
    status: EvidenceStatus
    evidence_ids: tuple[UUID, ...]
    reason: str


def evaluate_acceptance_criteria(
    acceptance_criteria: Sequence[str],
    *,
    ticket_id: UUID,
    head_commit: str,
    evidence: Sequence[Evidence],
) -> AcceptanceEvaluation:
    """Evaluate the acceptance-criteria check vs pre-loaded evidence (never raises).

    Returns an :class:`AcceptanceEvaluation`. The rule (D5,
    verification-engine.md):

    - The criteria are normalised (``.strip()``, blank entries dropped). If NONE
      remain, the result is ``NOT_APPLICABLE`` with empty ``evidence_ids`` (D5
      operator decision: a defensive guard, never a silent PASS — the ticket
      model guarantees 1 to 7 criteria, so this path means a caller passed
      nothing to confirm).
    - A confirmation is every record where ALL hold: ``evidence_type`` is
      MANUAL_APPROVAL; ``ticket_id`` equals this ticket; ``commit_sha`` equals
      ``head_commit`` exactly; tier is ``human`` (D6); and its
      ``acceptance_criterion_hash`` is one of the criteria's hashes.
    - ``PASSED`` iff EVERY criterion's hash is confirmed; ``evidence_ids`` names
      all confirming records, ordered by ``(created_at, id)``.
    - Otherwise ``PENDING`` (never ``FAILED``): the reason states how many of how
      many criteria are confirmed at the head commit, and ``evidence_ids`` names
      the PARTIAL confirmed set, same ordering. When a non-human MANUAL_APPROVAL
      carrying a matching hash exists at the head commit, the reason states
      explicitly that it is ignored — acceptance is operator-confirmed (the
      milestone crux: a machine or agent cannot manufacture an acceptance pass).

    Args:
        acceptance_criteria: the ticket's acceptance criteria (the composition
            ticket passes ``ticket.acceptance_criteria``).
        ticket_id: the ticket the confirmations must be scoped to (a parameter —
            this evaluator does not resolve it).
        head_commit: the PR head commit the confirmations must be pinned to (a
            parameter — resolving it is the CLI's / Phase 8's job).
        evidence: pre-loaded Evidence records to evaluate against (loading is the
            CLI's job; this module never touches storage).
    """
    normalised = [c.strip() for c in acceptance_criteria if c.strip()]
    if not normalised:
        return AcceptanceEvaluation(
            check_type=VerificationCheckType.ACCEPTANCE_CRITERIA,
            status=EvidenceStatus.NOT_APPLICABLE,
            evidence_ids=(),
            reason=(
                "acceptance_criteria: no criteria to confirm; not applicable "
                "(defensive guard — a ticket is expected to carry 1 to 7 criteria)."
            ),
        )

    criterion_hashes = {acceptance_criterion_hash(c) for c in normalised}
    confirmations = [
        e
        for e in evidence
        if e.evidence_type == EvidenceType.MANUAL_APPROVAL
        and e.ticket_id == ticket_id
        and e.commit_sha == head_commit
        and evidence_tier(e.created_by_type) == "human"
        and _confirmed_hash(e) in criterion_hashes
    ]
    confirmed_hashes = {_confirmed_hash(e) for e in confirmations}
    evidence_ids = tuple(
        e.id for e in sorted(confirmations, key=lambda e: (e.created_at, e.id))
    )

    total = len(criterion_hashes)
    confirmed_count = len(criterion_hashes & confirmed_hashes)

    if criterion_hashes <= confirmed_hashes:
        return AcceptanceEvaluation(
            check_type=VerificationCheckType.ACCEPTANCE_CRITERIA,
            status=EvidenceStatus.PASSED,
            evidence_ids=evidence_ids,
            reason=(
                f"acceptance_criteria: all {total} criteria confirmed by "
                f"human-tier manual_approval evidence pinned to {head_commit}; "
                f"PASSED."
            ),
        )

    return AcceptanceEvaluation(
        check_type=VerificationCheckType.ACCEPTANCE_CRITERIA,
        status=EvidenceStatus.PENDING,
        evidence_ids=evidence_ids,
        reason=_pending_reason(
            confirmed_count, total, head_commit, ticket_id, criterion_hashes, evidence
        ),
    )


def _confirmed_hash(evidence: Evidence) -> str | None:
    """The criterion hash a record confirms, read from ``raw_payload`` (D7).

    Reads ``raw_payload[ACCEPTANCE_CRITERION_HASH_KEY]`` defensively and never
    raises: a missing key (a blanket human_approval, or the ATLAS-69
    retention-cap marker payload, both of which lack it) or a non-str value
    yields ``None`` — that record confirms nothing.
    """
    value = evidence.raw_payload.get(ACCEPTANCE_CRITERION_HASH_KEY)
    return value if isinstance(value, str) else None


def _pending_reason(
    confirmed_count: int,
    total: int,
    head_commit: str,
    ticket_id: UUID,
    criterion_hashes: set[str],
    evidence: Sequence[Evidence],
) -> str:
    """Explain a PENDING acceptance check, naming an ignored non-human claim if any.

    The base message states how many of how many criteria are confirmed at the
    head commit. When a non-human MANUAL_APPROVAL carrying a matching criterion
    hash exists at the head commit for this ticket, the message says so
    explicitly — acceptance is operator-confirmed, so a system- or agent-tier
    claim is ignored (the milestone crux).
    """
    base = (
        f"acceptance_criteria: {confirmed_count} of {total} criteria confirmed "
        f"at {head_commit}; {total - confirmed_count} unconfirmed; PENDING (an "
        f"unconfirmed criterion is unproven, not failing)."
    )
    nonhuman_match = any(
        e.evidence_type == EvidenceType.MANUAL_APPROVAL
        and e.ticket_id == ticket_id
        and e.commit_sha == head_commit
        and evidence_tier(e.created_by_type) != "human"
        and _confirmed_hash(e) in criterion_hashes
        for e in evidence
    )
    if nonhuman_match:
        return (
            f"{base} Non-human manual_approval claims carrying a matching "
            f"criterion hash at {head_commit} are ignored — acceptance is "
            f"operator-confirmed, not machine- or agent-claimed (ADR-0008)."
        )
    return base
