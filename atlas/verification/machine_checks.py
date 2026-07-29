"""The machine-check evaluator (ATLAS-75), per
docs/atlas/verification-engine.md "Evaluation semantics" (the tests/lint
bullet) and "Principle".

:func:`evaluate_machine_check` answers one question for a single required
machine check on a ticket at PR head commit ``C``: is it satisfied by
system-tier CI evidence pinned to ``C``? It is the first per-check evaluator
and the core the Phase 7 milestone gates on — agent claims alone can NEVER
satisfy a machine check (ADR-0008).

The rule (verification-engine.md): for each CI job name, the latest execution
by GitHub-supplied lifecycle time decides that job, then fail precedence folds
the current job outcomes. Older or different commits never satisfy a check —
a new push resets machine checks to PENDING. Agent-tier (and human-tier)
evidence is ignored for these checks entirely. UUIDs never decide recency.

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

from collections.abc import Iterable, Sequence
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

    Frozen: an evaluation is an immutable record. ``status`` is the fold over
    the current execution of each matching CI job. ``evidence_ids`` names all
    records participating in that fold. Missing or unorderable source metadata
    yields ``PENDING`` rather than an identifier-based guess.

    This is NOT a :class:`VerificationCheck`: it carries no id, timestamps, or
    summary — building the persisted row is the composition ticket's job
    (ATLAS-76/77).
    """

    check_type: VerificationCheckType
    status: EvidenceStatus
    evidence_ids: tuple[UUID, ...]
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
      tier is ``system``. Candidates are grouped by ``job_name``; within each
      group the greatest ``source_event_at`` selects the current execution.
      FAILED has precedence across current jobs; all-current-PASSED passes;
      every other combination is PENDING.
    - Historical candidates without ``job_name`` or source ordering metadata
      never fall back to UUID order. They hold the check PENDING until re-pulled.
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
            evidence_ids=(),
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
        return _resolve_jobs(check_type, expected, head_commit, candidates)

    return MachineCheckEvaluation(
        check_type=check_type,
        status=EvidenceStatus.PENDING,
        evidence_ids=(),
        reason=_pending_reason(check_type, expected, head_commit, evidence),
    )


def _resolve_jobs(
    check_type: VerificationCheckType,
    expected: EvidenceType,
    head_commit: str,
    candidates: Sequence[Evidence],
) -> MachineCheckEvaluation:
    """Resolve current execution per job without identifier-based recency."""

    named = [record for record in candidates if record.job_name]
    if not named:
        return MachineCheckEvaluation(
            check_type=check_type,
            status=EvidenceStatus.PENDING,
            evidence_ids=(),
            reason=(
                f"{_label(check_type)}: {len(candidates)} system-tier "
                f"{expected.value} record(s) exist at {head_commit}, but they "
                "predate per-job source ordering metadata; PENDING until "
                "evidence is re-pulled."
            ),
        )
    named_source_keys = {
        (record.external_run_id, record.payload_hash)
        for record in named
        if record.external_run_id is not None and record.payload_hash is not None
    }
    unmatched_legacy = [
        record
        for record in candidates
        if not record.job_name
        and (record.external_run_id, record.payload_hash) not in named_source_keys
    ]
    if unmatched_legacy:
        return MachineCheckEvaluation(
            check_type=check_type,
            status=EvidenceStatus.PENDING,
            evidence_ids=(),
            reason=(
                f"{_label(check_type)}: {len(unmatched_legacy)} system-tier "
                f"{expected.value} record(s) at {head_commit} have no per-job "
                "metadata and no enriched duplicate; PENDING until all evidence "
                "is re-pulled."
            ),
        )

    by_job: dict[str, list[Evidence]] = {}
    for record in named:
        assert record.job_name is not None
        by_job.setdefault(record.job_name, []).append(record)

    job_statuses: list[EvidenceStatus] = []
    deciding: list[Evidence] = []
    job_parts: list[str] = []
    for job_name in sorted(by_job):
        records = by_job[job_name]
        unordered = [record for record in records if record.source_event_at is None]
        if unordered:
            # A queued execution has no lifecycle timestamp yet; a malformed
            # timestamp is equally unorderable. Either holds the gate rather
            # than allowing an older pass to win.
            selected = sorted(unordered, key=lambda record: str(record.id))
            job_status = EvidenceStatus.PENDING
            job_parts.append(f"{job_name}=pending (source time unavailable)")
        else:
            latest_at = max(
                record.source_event_at
                for record in records
                if record.source_event_at is not None
            )
            selected = sorted(
                (record for record in records if record.source_event_at == latest_at),
                key=lambda record: str(record.id),
            )
            job_status = _fold_machine_statuses(record.status for record in selected)
            job_parts.append(
                f"{job_name}={job_status.value} at {latest_at.isoformat()}"
            )
        deciding.extend(selected)
        job_statuses.append(job_status)

    status = _fold_machine_statuses(job_statuses)
    return MachineCheckEvaluation(
        check_type=check_type,
        status=status,
        evidence_ids=tuple(record.id for record in deciding),
        reason=(
            f"{_label(check_type)}: resolved {len(by_job)} current CI job(s) "
            f"at {head_commit}; {', '.join(job_parts)}; folded status "
            f"{status.value}."
        ),
    )


def _fold_machine_statuses(statuses: Iterable[EvidenceStatus]) -> EvidenceStatus:
    """Fold current job outcomes with the ticket-verdict precedence rule."""

    materialised = tuple(statuses)
    if any(status is EvidenceStatus.FAILED for status in materialised):
        return EvidenceStatus.FAILED
    if materialised and all(status is EvidenceStatus.PASSED for status in materialised):
        return EvidenceStatus.PASSED
    return EvidenceStatus.PENDING


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
