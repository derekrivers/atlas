"""The machine-check evaluator (ATLAS-75), per
docs/atlas/verification-engine.md "Evaluation semantics" (the tests/lint
bullet) and "Principle".

:func:`evaluate_machine_check` answers one question for a single required
machine check on a ticket at PR head commit ``C``: is it satisfied by
system-tier CI evidence pinned to ``C``? It is the first per-check evaluator
and the core the Phase 7 milestone gates on — agent claims alone can NEVER
satisfy a machine check (ADR-0008).

The rule (verification-engine.md): the latest system-tier evidence of the
matching type with ``commit_sha == C`` decides the check; its status passes
through unchanged. Older or different commits never satisfy a check — a new
push resets machine checks to PENDING. Agent-tier (and human-tier) evidence
is ignored for these checks entirely.

PURE and layer-faithful (D6): this module imports ``atlas.core`` only —
models, enums, and ``trust.evidence_tier`` (the ONLY place tier logic lives;
not reimplemented here). It takes pre-loaded evidence as a parameter; it does
NOT load from storage, NOT persist VerificationCheck rows, and NOT compose a
verdict. Loading is the CLI's job (ATLAS-80); the persisted row and verdict
are the composition tickets' (ATLAS-76/77). ``atlas.verification`` stays below
``atlas.pm`` in the import-linter spine.

Following the ``atlas/context/validation.py`` idiom, invalidity and absence
are DATA, not exceptions: the evaluator NEVER raises, for any input — empty
evidence, a record with ``commit_sha=None``, or a non-machine check type.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from atlas.core.enums import EvidenceStatus
from atlas.core.models import Evidence, EvidenceType, VerificationCheckType
from atlas.core.trust import evidence_tier

# The v1 machine-check set (D3): exactly TESTS and LINT, each mapped to the
# evidence type that proves it. BUILD/COVERAGE are NOT v1 gates and are not
# VerificationCheckType members — they are deliberately absent (the doc's
# "tests/lint/build/coverage" bullet is the phase-resolved scope choice). A
# check_type absent from this mapping is not a machine check.
MACHINE_CHECK_EVIDENCE: dict[VerificationCheckType, EvidenceType] = {
    VerificationCheckType.TESTS: EvidenceType.TEST_RESULT,
    VerificationCheckType.LINT: EvidenceType.LINT_RESULT,
}

# The machine-check types as a set, for membership tests by callers and the
# evaluator's own NOT_APPLICABLE guard. Derived from the mapping so the two
# can never drift.
MACHINE_CHECK_TYPES: frozenset[VerificationCheckType] = frozenset(
    MACHINE_CHECK_EVIDENCE
)

# Tier names are compared inline as string literals against
# ``evidence_tier(...)`` — the storage/repositories.py idiom — so this module
# defines no tier-named binding of its own (tier logic lives only in
# atlas.core.trust, ADR-0008 / knowledge-core.md). "system" alone satisfies a
# machine check; "agent" is named in a PENDING reason to make the crux explicit.


@dataclass(frozen=True)
class MachineCheckEvaluation:
    """The result of :func:`evaluate_machine_check` (D5).

    Frozen: an evaluation is an immutable record. ``status`` is the pinned
    evidence record's status passed through unchanged (PASSED/FAILED/WARNING/
    …) when a system-tier record at the head commit exists, ``PENDING`` when
    none does (a missing check is "not yet proven", never "proven failing"),
    or ``NOT_APPLICABLE`` for a non-machine check type. ``evidence_id`` names
    the deciding Evidence record, or ``None`` when no record decided it.
    ``reason`` is a human-readable explanation naming the pin or its absence.

    This is NOT a :class:`VerificationCheck`: it carries no id, timestamps, or
    summary — building the persisted row is the composition ticket's job
    (ATLAS-76/77).
    """

    check_type: VerificationCheckType
    status: EvidenceStatus
    evidence_id: UUID | None
    reason: str


def evaluate_machine_check(
    check_type: VerificationCheckType,
    *,
    head_commit: str,
    evidence: Sequence[Evidence],
) -> MachineCheckEvaluation:
    """Evaluate one machine check against pre-loaded evidence (never raises).

    Returns a :class:`MachineCheckEvaluation`. The rule (D4,
    verification-engine.md):

    - A non-machine ``check_type`` (SCOPE, DOCUMENTATION, ACCEPTANCE_CRITERIA,
      HUMAN_APPROVAL, SECURITY) yields ``NOT_APPLICABLE`` — this evaluator only
      decides TESTS and LINT; the others have their own evaluators.
    - Otherwise the candidates are every record whose ``evidence_type`` matches
      the check, whose ``commit_sha`` equals ``head_commit`` exactly, and whose
      tier is ``system``. The latest candidate by ``(created_at, id)`` decides:
      its ``status`` passes through unchanged and its ``id`` is the
      ``evidence_id``.
    - With no candidates the check is ``PENDING`` (never ``FAILED`` — a missing
      check is unproven, not failing). If matching agent-tier evidence exists
      at ``head_commit``, the reason states explicitly that agent claims are
      ignored for machine checks (the milestone crux).

    Args:
        check_type: the required check to evaluate.
        head_commit: the PR head commit the check is pinned to (a parameter —
            resolving it is the CLI's / Phase 8's job, not this evaluator's).
        evidence: pre-loaded Evidence records to evaluate against (loading is
            the CLI's job; this module never touches storage).
    """
    expected = MACHINE_CHECK_EVIDENCE.get(check_type)
    if expected is None:
        return MachineCheckEvaluation(
            check_type=check_type,
            status=EvidenceStatus.NOT_APPLICABLE,
            evidence_id=None,
            reason=(
                f"{_label(check_type)} is not a machine check "
                f"(machine checks: {_machine_check_names()}); not applicable."
            ),
        )

    candidates = [
        e
        for e in evidence
        if e.evidence_type == expected
        and e.commit_sha == head_commit
        and evidence_tier(e.created_by_type) == "system"
    ]

    if candidates:
        latest = max(candidates, key=lambda e: (e.created_at, e.id))
        return MachineCheckEvaluation(
            check_type=check_type,
            status=latest.status,
            evidence_id=latest.id,
            reason=(
                f"{_label(check_type)}: system-tier {expected.value} evidence "
                f"{latest.id} pinned to {head_commit} reports "
                f"{latest.status.value}."
            ),
        )

    return MachineCheckEvaluation(
        check_type=check_type,
        status=EvidenceStatus.PENDING,
        evidence_id=None,
        reason=_pending_reason(check_type, expected, head_commit, evidence),
    )


def _pending_reason(
    check_type: VerificationCheckType,
    expected: EvidenceType,
    head_commit: str,
    evidence: Sequence[Evidence],
) -> str:
    """Explain a PENDING machine check, naming ignored agent claims if any.

    The base message states no system-tier evidence of the matching type
    exists at the head commit. When matching agent-tier evidence DOES exist at
    that commit, the message says so explicitly — agent claims are ignored for
    machine checks (the milestone crux: an agent cannot manufacture a pass).
    """
    base = (
        f"{_label(check_type)}: no system-tier {expected.value} evidence "
        f"exists at {head_commit}; PENDING (a machine check is unproven, not "
        f"failing, until system-tier evidence lands)."
    )
    agent_at_head = any(
        e.evidence_type == expected
        and e.commit_sha == head_commit
        and evidence_tier(e.created_by_type) == "agent"
        for e in evidence
    )
    if agent_at_head:
        return (
            f"{base} Agent-tier {expected.value} claims at {head_commit} are "
            f"ignored for machine checks — agent evidence cannot satisfy a "
            f"machine check (ADR-0008)."
        )
    return base


def _machine_check_names() -> str:
    """The machine-check type values, comma-joined in declaration order."""
    return ", ".join(t.value for t in MACHINE_CHECK_EVIDENCE)


def _label(check_type: VerificationCheckType) -> str:
    """Render a check type for a reason string without ever raising.

    A real :class:`VerificationCheckType` renders as its value; a fabricated
    non-member value passed in (the "unknown check_type never raises" contract,
    D4) renders via ``str`` rather than crashing on a missing ``.value``.
    """
    return getattr(check_type, "value", str(check_type))
