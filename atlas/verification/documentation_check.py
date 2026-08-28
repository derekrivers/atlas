"""The documentation-check evaluator (ATLAS-74), per
docs/atlas/verification-engine.md "Evaluation semantics" (the documentation
bullet) and "Principle".

:func:`evaluate_documentation_check` answers one question for a ticket's
required ``documentation`` check at PR head commit ``C``: does a
DOCUMENTATION_UPDATE record pinned to ``C`` cover the documentation the ticket
requires? It is the third per-check evaluator, a sibling of
:func:`atlas.verification.evaluate_machine_check` (machine_checks.py) and the
pure resolver :func:`atlas.verification.required_checks` (ATLAS-71). Like the
machine evaluator it gates on system-tier, commit-pinned evidence — an agent's
claim that it "updated the docs" can NEVER satisfy the check (ADR-0008).

The rule (verification-engine.md): a DOCUMENTATION_UPDATE record for ``C``
covering at least one path named in ``documentation_requirements``. New-format
``docs:v2:<head>`` evidence uses the bounded structured ``docs_paths`` field as
authority. Legacy evidence may fall back to a valid retained
``raw_payload["files"]`` projection; a capped legacy row has neither form and
cannot prove coverage. Two modes:

- ``documentation_requirements`` NON-EMPTY: PASSED iff some covered filename
  equals (after str/strip normalisation) some required path; the deciding
  ``evidence_id`` is the latest such record by ``(created_at, id)``.
- ``documentation_requirements`` EMPTY (reachable only for documentation and
  spike/research ticket types, whose matrix cell is unconditionally required):
  PASSED iff ANY system-tier DOCUMENTATION_UPDATE exists at ``C`` (findings
  documented = some doc touched); ``evidence_id`` is the latest such record.

Matching is EXACT normalised path equality in v1 — directory/prefix/glob
matching is deferred. Because ``documentation_requirements`` is free-form
planner text while touched filenames are full repo paths, the check resolves
PENDING whenever they are not verbatim equal: the conservative default for a
completion gate (it never false-PASSES), at the cost of under-asserting until
glob/prefix matching lands.

PURE and layer-faithful (D8): this module imports ``atlas.core`` only — models,
enums, and ``trust.evidence_tier`` (the ONLY place tier logic lives; not
reimplemented here). It takes pre-loaded evidence as a parameter; it does NOT
load from storage, NOT import ``atlas.evidence``/``atlas.github`` to reach the
normaliser, NOT persist VerificationCheck rows, and NOT compose a verdict.
Loading is the CLI's job (ATLAS-80); the persisted row and verdict are the
composition tickets' (ATLAS-76/77). ``atlas.verification`` stays below
``atlas.pm`` in the import-linter spine.

Following the machine evaluator, invalidity and absence are DATA, not
exceptions: the evaluator NEVER raises, for any input — empty evidence, empty
requirements, a record with ``commit_sha=None``, malformed structured paths,
or a capped legacy ``raw_payload``. A record that cannot prove coverage simply
does not.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID

from atlas.core.enums import EvidenceStatus
from atlas.core.models import Evidence, EvidenceType, VerificationCheckType
from atlas.core.models.evidence import canonical_documentation_paths
from atlas.core.trust import evidence_tier

# Tier names are compared inline as string literals against
# ``evidence_tier(...)`` — the storage/repositories.py and machine_checks.py
# idiom — so this module defines no tier-named binding of its own (tier logic
# lives only in atlas.core.trust, ADR-0008 / knowledge-core.md). "system" alone
# satisfies the check; "agent" is named in a PENDING reason to make the crux
# explicit (the milestone: an agent cannot manufacture a doc pass).


@dataclass(frozen=True)
class DocumentationEvaluation:
    """The result of :func:`evaluate_documentation_check` (D3).

    Frozen: an evaluation is an immutable record. ``check_type`` is always
    :attr:`VerificationCheckType.DOCUMENTATION` — unlike the machine evaluator
    this one decides a single fixed check, so the type is a constant, not an
    argument. ``status`` is ``PASSED`` when a system-tier DOCUMENTATION_UPDATE
    pinned to the head commit covers the requirement (or, in empty-requirements
    mode, simply exists), otherwise ``PENDING`` — NEVER ``FAILED`` (a missing or
    uncovered doc is unproven, not failing; and DOCUMENTATION_UPDATE itself is
    always PASSED-by-presence, so there is no FAILED status to pass through).
    ``evidence_id`` names the deciding Evidence record, or ``None`` when none
    decided it. ``reason`` is a human-readable explanation.

    This is NOT a :class:`VerificationCheck`: it carries no id, timestamps, or
    summary — building the persisted row is the composition ticket's job
    (ATLAS-76/77).
    """

    check_type: VerificationCheckType
    status: EvidenceStatus
    evidence_id: UUID | None
    reason: str


@dataclass(frozen=True)
class DocumentationPathAuthority:
    """Validated records selected by the versioned projection precedence."""

    records: tuple[tuple[Evidence, frozenset[str]], ...]
    malformed: bool
    structured: bool


def documentation_path_authority(
    records: Sequence[Evidence],
) -> DocumentationPathAuthority:
    """Resolve one fail-closed docs-path authority set without raising.

    The presence of any structured/v2 record makes structured evidence
    authoritative and excludes legacy rows at that head.  This is what permits
    append-only recovery beside an already-capped legacy observation without
    letting the unavailable historical payload poison the fresh record.  Any
    malformed record in the selected authority set invalidates that set.
    """

    candidates = tuple(
        record
        for record in records
        if record.evidence_type is EvidenceType.DOCUMENTATION_UPDATE
    )
    structured = tuple(record for record in candidates if _is_structured(record))
    selected = structured or candidates
    projected = tuple((record, _project_paths(record)) for record in selected)
    malformed = any(paths is None for _, paths in projected)
    valid = (
        ()
        if malformed
        else tuple((record, paths) for record, paths in projected if paths is not None)
    )
    return DocumentationPathAuthority(
        records=valid,
        malformed=malformed,
        structured=bool(structured),
    )


def evaluate_documentation_check(
    documentation_requirements: Sequence[str],
    *,
    head_commit: str,
    evidence: Sequence[Evidence],
) -> DocumentationEvaluation:
    """Evaluate the documentation check against pre-loaded evidence (never raises).

    Returns a :class:`DocumentationEvaluation`. The rule (D4/D6,
    verification-engine.md):

    - Candidates are every DOCUMENTATION_UPDATE record whose ``commit_sha``
      equals ``head_commit`` exactly and whose tier is ``system``. The only
      producer is the system-tier CI mapper (ATLAS-66); agent-tier doc evidence
      is capped at PENDING and there is no operator-authored doc-evidence path
      in v1, so system-tier-only is the whole candidate set (D4).
    - With NON-EMPTY ``documentation_requirements``: ``PASSED`` iff some
      candidate's covered filenames (read from ``raw_payload["files"]``) include
      a required path, after str/strip normalisation and EXACT equality. The
      latest matching candidate by ``(created_at, id)`` decides; its ``id`` is
      the ``evidence_id``.
    - With EMPTY ``documentation_requirements`` (documentation and spike/research
      types, whose matrix cell is unconditionally required): ``PASSED`` iff ANY
      candidate exists; the latest by ``(created_at, id)`` is the ``evidence_id``
      (findings documented = some doc touched). The matrix made the check
      required, so "no doc touched at all" must not silently pass (D6).
    - Otherwise ``PENDING`` (never ``FAILED``). If a matching system-tier record
      is absent but an agent-tier DOCUMENTATION_UPDATE exists at ``head_commit``,
      the reason states explicitly that agent claims are ignored (the milestone
      crux: an agent cannot manufacture a doc pass).

    Args:
        documentation_requirements: the ticket's required doc paths (the only
            ticket field this check needs; the composition ticket passes
            ``ticket.documentation_requirements``).
        head_commit: the PR head commit the check is pinned to (a parameter —
            resolving it is the CLI's / Phase 8's job, not this evaluator's).
        evidence: pre-loaded Evidence records to evaluate against (loading is
            the CLI's job; this module never touches storage).
    """
    candidates = [
        e
        for e in evidence
        if e.evidence_type == EvidenceType.DOCUMENTATION_UPDATE
        and e.commit_sha == head_commit
        and evidence_tier(e.created_by_type) == "system"
    ]
    authority = documentation_path_authority(candidates)
    required = _normalise_required(documentation_requirements)

    if not required:
        # Empty-requirements existence mode (D6): presence of any system-tier
        # doc record at C is the signal; absence is PENDING, never FAILED.
        if authority.records:
            latest = max(
                (record for record, _paths in authority.records),
                key=lambda e: (e.created_at, e.id),
            )
            return DocumentationEvaluation(
                check_type=VerificationCheckType.DOCUMENTATION,
                status=EvidenceStatus.PASSED,
                evidence_id=latest.id,
                reason=(
                    f"documentation (no required paths — findings mode): "
                    f"system-tier documentation_update evidence {latest.id} "
                    f"pinned to {head_commit} documents a change; PASSED."
                ),
            )
        return DocumentationEvaluation(
            check_type=VerificationCheckType.DOCUMENTATION,
            status=EvidenceStatus.PENDING,
            evidence_id=None,
            reason=_pending_reason_empty(head_commit, evidence),
        )

    matching = [record for record, paths in authority.records if paths & required]
    if matching:
        latest = max(matching, key=lambda e: (e.created_at, e.id))
        return DocumentationEvaluation(
            check_type=VerificationCheckType.DOCUMENTATION,
            status=EvidenceStatus.PASSED,
            evidence_id=latest.id,
            reason=(
                f"documentation: system-tier documentation_update evidence "
                f"{latest.id} pinned to {head_commit} covers a required path; "
                f"PASSED."
            ),
        )

    return DocumentationEvaluation(
        check_type=VerificationCheckType.DOCUMENTATION,
        status=EvidenceStatus.PENDING,
        evidence_id=None,
        reason=_pending_reason_required(head_commit, bool(candidates), evidence),
    )


def _normalise_required(documentation_requirements: Sequence[str]) -> set[str]:
    """The required doc paths as a normalised set (str/strip, blanks dropped).

    Normalisation is the same str/strip applied to covered filenames, so the
    two sides of the equality test are comparable. A requirements list that is
    empty — or holds only blank strings — yields the empty set, routing to the
    existence mode (D6). Never raises: a non-str entry is coerced via ``str``.
    """
    return {s for s in (str(r).strip() for r in documentation_requirements) if s}


def _is_structured(evidence: Evidence) -> bool:
    return evidence.docs_paths is not None or (
        isinstance(evidence.external_run_id, str)
        and evidence.external_run_id.startswith("docs:v2:")
    )


def _project_paths(evidence: Evidence) -> frozenset[str] | None:
    """Project strict structured paths or the supported legacy raw fallback."""

    if _is_structured(evidence):
        canonical = canonical_documentation_paths(evidence.docs_paths)
        expected_identity = (
            None if evidence.commit_sha is None else f"docs:v2:{evidence.commit_sha}"
        )
        if canonical is None or evidence.external_run_id != expected_identity:
            return None
        return frozenset(canonical)

    files = evidence.raw_payload.get("files")
    if not isinstance(files, list) or not files:
        return None
    legacy_paths: list[str] = []
    for entry in files:
        if not isinstance(entry, Mapping):
            return None
        filename = entry.get("filename")
        if not isinstance(filename, str):
            return None
        legacy_paths.append(filename)
    if len(legacy_paths) != len(set(legacy_paths)):
        return None
    canonical = canonical_documentation_paths(tuple(sorted(legacy_paths)))
    return None if canonical is None else frozenset(canonical)


def _has_agent_doc_at_head(head_commit: str, evidence: Sequence[Evidence]) -> bool:
    """Whether an agent-tier DOCUMENTATION_UPDATE exists at ``head_commit``.

    The explicit (d) trigger (P1), mirroring
    ``machine_checks._pending_reason``'s ``agent_at_head`` scan: a real check,
    not an assumption, so the ignored-claim message in the PENDING reason is
    only emitted when an agent actually claimed a doc update at the head commit.
    """
    return any(
        e.evidence_type == EvidenceType.DOCUMENTATION_UPDATE
        and e.commit_sha == head_commit
        and evidence_tier(e.created_by_type) == "agent"
        for e in evidence
    )


def _pending_reason_required(
    head_commit: str, has_system_record: bool, evidence: Sequence[Evidence]
) -> str:
    """Explain a PENDING documentation check with non-empty requirements.

    Distinguishes (b) a system-tier record exists at the head commit but covers
    none of the required paths, from (a) no system-tier record at all — and in
    the (a) case appends the (d) ignored-agent-claim note when an agent-tier
    DOCUMENTATION_UPDATE exists at the head commit (the milestone crux).
    """
    if has_system_record:
        return (
            f"documentation: a system-tier documentation_update record exists at "
            f"{head_commit} but covers none of the required paths; PENDING (the "
            f"required doc was not touched — unproven, not failing)."
        )
    base = (
        f"documentation: no system-tier documentation_update evidence exists at "
        f"{head_commit}; PENDING (a documentation check is unproven, not failing, "
        f"until system-tier evidence lands)."
    )
    if _has_agent_doc_at_head(head_commit, evidence):
        return (
            f"{base} Agent-tier documentation_update claims at {head_commit} are "
            f"ignored — agent evidence cannot satisfy a documentation check "
            f"(ADR-0008)."
        )
    return base


def _pending_reason_empty(head_commit: str, evidence: Sequence[Evidence]) -> str:
    """Explain a PENDING documentation check in empty-requirements mode (D6).

    The matrix made the check required for this ticket type, so the absence of
    any system-tier doc record at the head commit is PENDING, not a pass.
    Appends the (d) ignored-agent-claim note when an agent-tier
    DOCUMENTATION_UPDATE exists at the head commit (the milestone crux).
    """
    base = (
        f"documentation (no required paths — findings mode): no system-tier "
        f"documentation_update evidence exists at {head_commit}; PENDING (the "
        f"required check needs some documented change, unproven not failing)."
    )
    if _has_agent_doc_at_head(head_commit, evidence):
        return (
            f"{base} Agent-tier documentation_update claims at {head_commit} are "
            f"ignored — agent evidence cannot satisfy a documentation check "
            f"(ADR-0008)."
        )
    return base
