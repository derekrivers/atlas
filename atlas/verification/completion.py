"""The ticket completion validator (ATLAS-76) — the Phase 7 composition
keystone, per docs/atlas/verification-engine.md "Evaluation semantics" (every
bullet) and "Verdict and completion".

:func:`evaluate_ticket` answers one question for a ticket T at PR head commit
``C``: is the "no evidence = no completion" rule satisfied for EVERY required
check at ``C``? It turns the five merged per-check evaluators — machine_checks
(ATLAS-75), documentation_check (ATLAS-74), acceptance_check (ATLAS-72),
scope_check (ATLAS-73), and human_approval_check (ATLAS-76, OP-A) — into one
per-ticket gate by resolving the required-check set (:func:`required_checks`,
ATLAS-71), dispatching each required check to its evaluator with the ticket
inputs it needs, normalising each result into a uniform :class:`CheckOutcome`,
and composing the verdict.

The verdict rule (D8, verification-engine.md "Verdict and completion"), over the
GATING outcomes (``required=True``) only:

- any gating status ``FAILED`` -> ``FAILED`` (fail precedence — a single failing
  required check sinks the ticket, regardless of the others);
- else every gating status ``PASSED`` -> ``PASSED`` (only PASSED passes);
- else -> ``PENDING`` (a required check that is PENDING, WARNING, or
  NOT_APPLICABLE is non-passing but not failing — the ticket is unproven).

Non-required checks (``required=False`` — the deferred SECURITY surface, OP-4)
appear in the breakdown as NOT_APPLICABLE but do NOT gate. A required check whose
type has no dispatch arm cannot silently pass: the fallthrough synthesises a
non-passing PENDING outcome that holds the verdict (D7). A behavioural test
asserts every required check the resolver can actually emit routes to a real
evaluator, so a future resolver-emitted required check without an evaluator fails
a test rather than slipping through.

PURE and layer-faithful (D9): this module imports ``atlas.core`` and its
``atlas.verification`` siblings ONLY — never ``atlas.evidence``, ``atlas.storage``,
``atlas.pm``, or ``atlas.github``. It READS pre-loaded evidence and returns a
verdict; it does NOT persist VerificationCheck rows, NOT generate ids or
timestamps, NOT capture operator input or write evidence (ATLAS-80 / OP-3), NOT
resolve ``pr_files`` or the head commit, and NOT aggregate across tickets
(ATLAS-77). ``atlas.verification`` stays below ``atlas.pm`` in the import-linter
spine. Following the sibling evaluators, invalidity and absence are DATA, not
exceptions: :func:`evaluate_ticket` NEVER raises, for any input.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from uuid import UUID

from atlas.core.enums import EvidenceStatus
from atlas.core.models import Evidence, Ticket, VerificationCheck, VerificationCheckType
from atlas.verification.acceptance_check import evaluate_acceptance_criteria
from atlas.verification.documentation_check import evaluate_documentation_check
from atlas.verification.human_approval_check import evaluate_human_approval
from atlas.verification.machine_checks import evaluate_machine_check
from atlas.verification.rules import required_checks
from atlas.verification.scope_check import evaluate_scope


@dataclass(frozen=True)
class CheckOutcome:
    """One normalised, pre-persistence per-check row (D4).

    Frozen: a composed outcome is an immutable record. It flattens the differing
    evaluator result shapes into one: the single-evidence evaluators' ``evidence_id``
    becomes ``(id,)`` or ``()``; the multi-evidence evaluators' ``evidence_ids``
    tuple passes through unchanged. ``required`` distinguishes a gating check
    (``True``) from the non-gating SECURITY surface (``False``), so the verdict can
    fold over the gating subset. This maps cleanly onto
    ``VerificationCheck.evidence_ids`` for the persistence ticket (ATLAS-80).

    This is NOT a :class:`VerificationCheck`: it carries no id, timestamps, or
    summary — building the persisted row is the persistence ticket's job.
    """

    check_type: VerificationCheckType
    required: bool
    status: EvidenceStatus
    evidence_ids: tuple[UUID, ...]
    reason: str


@dataclass(frozen=True)
class TicketVerification:
    """The composed per-ticket verdict (D4).

    Frozen: an immutable record. ``status`` is the composed verdict over the
    gating outcomes (D8); ``checks`` is the full per-check breakdown in resolver
    order (gating and non-gating alike), each a :class:`CheckOutcome`. There is no
    top-level reason — the per-:class:`CheckOutcome` reasons carry the detail and
    the persistence ticket (ATLAS-80) derives a summary from the failing/pending
    checks.
    """

    ticket_id: UUID
    status: EvidenceStatus
    checks: tuple[CheckOutcome, ...]


# An adapter has a uniform signature so the dispatch table is homogeneous despite
# the evaluators' differing signatures: it pulls the ticket fields each evaluator
# needs, calls it, and normalises the result into a gating CheckOutcome. The
# normalisation (single evidence_id -> (id,) or (); multi evidence_ids -> tuple)
# lives here so CheckOutcome is uniform across checks (D4).
_Adapter = Callable[
    [VerificationCheckType, Ticket, Sequence[str], str, Sequence[Evidence]],
    CheckOutcome,
]


def _single(evidence_id: UUID | None) -> tuple[UUID, ...]:
    """Normalise a single-evidence evaluator's ``evidence_id`` to a tuple (D4)."""
    return () if evidence_id is None else (evidence_id,)


def _adapt_machine(
    check_type: VerificationCheckType,
    ticket: Ticket,
    pr_files: Sequence[str],
    head_commit: str,
    evidence: Sequence[Evidence],
) -> CheckOutcome:
    """TESTS / LINT -> the machine evaluator (passes its own ``check_type``)."""
    result = evaluate_machine_check(
        check_type, head_commit=head_commit, evidence=evidence
    )
    return CheckOutcome(
        check_type=check_type,
        required=True,
        status=result.status,
        evidence_ids=_single(result.evidence_id),
        reason=result.reason,
    )


def _adapt_documentation(
    check_type: VerificationCheckType,
    ticket: Ticket,
    pr_files: Sequence[str],
    head_commit: str,
    evidence: Sequence[Evidence],
) -> CheckOutcome:
    """DOCUMENTATION -> the documentation evaluator over the ticket's requirements."""
    result = evaluate_documentation_check(
        ticket.documentation_requirements,
        head_commit=head_commit,
        evidence=evidence,
    )
    return CheckOutcome(
        check_type=check_type,
        required=True,
        status=result.status,
        evidence_ids=_single(result.evidence_id),
        reason=result.reason,
    )


def _adapt_acceptance(
    check_type: VerificationCheckType,
    ticket: Ticket,
    pr_files: Sequence[str],
    head_commit: str,
    evidence: Sequence[Evidence],
) -> CheckOutcome:
    """ACCEPTANCE_CRITERIA -> the acceptance evaluator (multi-evidence)."""
    result = evaluate_acceptance_criteria(
        ticket.acceptance_criteria,
        ticket_id=ticket.id,
        head_commit=head_commit,
        evidence=evidence,
    )
    return CheckOutcome(
        check_type=check_type,
        required=True,
        status=result.status,
        evidence_ids=result.evidence_ids,
        reason=result.reason,
    )


def _adapt_scope(
    check_type: VerificationCheckType,
    ticket: Ticket,
    pr_files: Sequence[str],
    head_commit: str,
    evidence: Sequence[Evidence],
) -> CheckOutcome:
    """SCOPE -> the scope evaluator over the PR files vs the ticket's scope."""
    result = evaluate_scope(
        pr_files,
        relevant_docs=ticket.relevant_docs,
        source_anchor=ticket.source_anchor,
        ticket_id=ticket.id,
        head_commit=head_commit,
        evidence=evidence,
    )
    return CheckOutcome(
        check_type=check_type,
        required=True,
        status=result.status,
        evidence_ids=result.evidence_ids,
        reason=result.reason,
    )


def _adapt_human_approval(
    check_type: VerificationCheckType,
    ticket: Ticket,
    pr_files: Sequence[str],
    head_commit: str,
    evidence: Sequence[Evidence],
) -> CheckOutcome:
    """HUMAN_APPROVAL -> the human-approval evaluator (single-evidence)."""
    result = evaluate_human_approval(
        ticket_id=ticket.id,
        head_commit=head_commit,
        evidence=evidence,
    )
    return CheckOutcome(
        check_type=check_type,
        required=True,
        status=result.status,
        evidence_ids=_single(result.evidence_id),
        reason=result.reason,
    )


# The dispatch table (D5): every required VerificationCheckType the resolver can
# emit maps to the adapter that evaluates it. TESTS and LINT both point at the
# machine adapter, which passes its own check_type through. A required type ABSENT
# from this table hits the fallthrough in evaluate_ticket (D7) — it never silently
# passes. The completeness test (R1) asserts this table covers every required type
# the resolver actually emits, driven from required_checks output, not a constant.
_DISPATCH: dict[VerificationCheckType, _Adapter] = {
    VerificationCheckType.TESTS: _adapt_machine,
    VerificationCheckType.LINT: _adapt_machine,
    VerificationCheckType.DOCUMENTATION: _adapt_documentation,
    VerificationCheckType.ACCEPTANCE_CRITERIA: _adapt_acceptance,
    VerificationCheckType.SCOPE: _adapt_scope,
    VerificationCheckType.HUMAN_APPROVAL: _adapt_human_approval,
}


def evaluate_ticket(
    ticket: Ticket,
    *,
    pr_files: Sequence[str],
    head_commit: str,
    evidence: Sequence[Evidence],
) -> TicketVerification:
    """Compose the per-ticket verdict over its required checks (never raises).

    Resolves ``required_checks(ticket)`` and, for each entry:

    - a non-required check (``required=False`` — the deferred SECURITY surface,
      OP-4 / D6) becomes a NOT_APPLICABLE non-gating :class:`CheckOutcome` that
      appears in the breakdown but does NOT gate;
    - a required check with a dispatch arm is evaluated by that arm (D5) and
      normalised into a gating :class:`CheckOutcome`;
    - a required check with NO dispatch arm hits the fallthrough (D7): a
      non-passing PENDING outcome that holds the verdict — a required type without
      an evaluator must never silently pass.

    The verdict (D8) folds over the GATING outcomes only: any FAILED -> FAILED;
    else all PASSED -> PASSED; else PENDING. With no gating checks at all (should
    not happen — acceptance is always required) the verdict is PENDING, never a
    vacuous PASS.

    Args:
        ticket: the ticket to verify (feeds required_checks and the evaluators;
            this validator resolves nothing from it beyond reading its fields).
        pr_files: the PR's changed-file paths (resolving them is the CLI's /
            Phase 8's job — this validator does not).
        head_commit: the PR head commit every check is pinned to (a parameter —
            resolving it is the CLI's / Phase 8's job).
        evidence: pre-loaded Evidence records to evaluate against (loading is the
            CLI's job; this module never touches storage).
    """
    outcomes: list[CheckOutcome] = []
    for rc in required_checks(ticket):
        if not rc.required:
            outcomes.append(
                CheckOutcome(
                    check_type=rc.check_type,
                    required=False,
                    status=EvidenceStatus.NOT_APPLICABLE,
                    evidence_ids=(),
                    reason=rc.note or "deferred; not a v1 gate.",
                )
            )
            continue
        adapter = _DISPATCH.get(rc.check_type)
        if adapter is None:
            outcomes.append(
                CheckOutcome(
                    check_type=rc.check_type,
                    required=True,
                    status=EvidenceStatus.PENDING,
                    evidence_ids=(),
                    reason=(
                        f"no evaluator wired for {rc.check_type.value}; cannot "
                        f"confirm (PENDING — a required check without an evaluator "
                        f"holds the verdict, never silently passes)."
                    ),
                )
            )
            continue
        outcomes.append(adapter(rc.check_type, ticket, pr_files, head_commit, evidence))

    return TicketVerification(
        ticket_id=ticket.id,
        status=_compose_verdict(outcomes),
        checks=tuple(outcomes),
    )


def fold_statuses(statuses: Iterable[EvidenceStatus]) -> EvidenceStatus:
    """Fold a set of statuses into one verdict (D8/D5; never raises).

    The single source of truth for the verification fold rule — shared by the
    per-ticket verdict (:func:`_compose_verdict`, over a ticket's gating check
    statuses) and the PR-level verdict (``evaluate_pr``, over its closed tickets'
    verdicts; ATLAS-77). The same rule, applied one level apart:

    - any ``FAILED`` -> ``FAILED`` (fail precedence — one failure sinks the whole);
    - else non-empty and every ``PASSED`` -> ``PASSED`` (only PASSED passes);
    - else ``PENDING`` (an empty set, or any non-passing/non-failing status in the
      mix, is unproven — never a vacuous PASS over nothing).

    One fold, not two copies: the ticket and PR verdicts are the same rule, and two
    independent implementations would drift (the Atlas single-source-of-truth
    principle).
    """
    materialised = list(statuses)
    if any(s == EvidenceStatus.FAILED for s in materialised):
        return EvidenceStatus.FAILED
    if materialised and all(s == EvidenceStatus.PASSED for s in materialised):
        return EvidenceStatus.PASSED
    return EvidenceStatus.PENDING


def ticket_verdict_from_checks(
    ticket: Ticket, checks: Iterable[VerificationCheck]
) -> EvidenceStatus:
    """Compose a ticket's verdict from PERSISTED VerificationCheck rows (never raises).

    The PM-side consumer's read of the same verdict :func:`evaluate_ticket` computes,
    but sourced from the append-only rows ``atlas verify`` already persisted rather
    than by re-running the evaluators — so the PM Engine never couples to evidence or
    GitHub and never redoes the evaluation work. The two compose the SAME fold one
    level apart, via the single-source-of-truth :func:`fold_statuses`.

    Resolves ``required_checks(ticket)`` and folds over the GATING subset only
    (``rc.required`` — the non-gating SECURITY surface never participates). For each
    required check type it takes the LATEST persisted row (the greatest ``created_at``);
    a required type with NO row contributes ``PENDING``, so a missing required check
    holds the verdict and can never produce a false PASS. The resulting statuses fold
    through :func:`fold_statuses`: any FAILED -> FAILED; else non-empty all-PASSED ->
    PASSED; else PENDING.

    Pure and layer-faithful, exactly like its siblings: it reads ``atlas.core`` models
    and resolver output only, persists nothing, and NEVER raises for any input.
    """
    latest: dict[VerificationCheckType, VerificationCheck] = {}
    for check in checks:
        current = latest.get(check.check_type)
        if current is None or check.created_at > current.created_at:
            latest[check.check_type] = check
    statuses = [
        latest[rc.check_type].status
        if rc.check_type in latest
        else EvidenceStatus.PENDING
        for rc in required_checks(ticket)
        if rc.required
    ]
    return fold_statuses(statuses)


def _compose_verdict(outcomes: Sequence[CheckOutcome]) -> EvidenceStatus:
    """Fold the gating outcomes into the ticket verdict (D8; never raises).

    Over the GATING outcomes (``required=True``) only, via the shared
    :func:`fold_statuses` rule: any FAILED -> FAILED; else every PASSED -> PASSED;
    else PENDING. With no gating outcomes at all (a defensive case — acceptance is
    always required) the verdict is PENDING, never a vacuous PASS over an empty set.
    """
    return fold_statuses(o.status for o in outcomes if o.required)
