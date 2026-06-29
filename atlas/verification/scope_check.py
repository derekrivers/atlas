"""The scope evaluator (ATLAS-73), per docs/atlas/verification-engine.md
"Evaluation semantics" (the scope bullet) and "Principle".

:func:`evaluate_scope` answers one question for a ticket's required ``scope``
check at PR head commit ``C``: is every changed file in the PR within the
ticket's declared scope, OR does an out-of-scope file carry an operator
decision (waive or fail) pinned to ``C``? It is the fifth and last per-check
evaluator, a sibling of :func:`atlas.verification.evaluate_acceptance_criteria`
(acceptance_check.py), :func:`atlas.verification.evaluate_documentation_check`
(documentation_check.py), and :func:`atlas.verification.evaluate_machine_check`
(machine_checks.py). Like acceptance it is HUMAN-tier and MULTI-evidence: an
agent's or a machine's claim about scope can NEVER decide a file (D8; ADR-0008).

In-scope derivation (D3 — OP-2: there is NO ``allowed_paths`` field in v1). The
in-scope path set is the union of every ``relevant_docs`` entry (``.strip()``,
blanks dropped) and the PATH PART of ``source_anchor`` —
``source_anchor.split("#", 1)[0].strip()``, the documented ``path#slug``
invariant. The local split is deliberate: the index-coupled anchor resolver
(``AnchorIndex.resolve``) needs the document corpus and would break this
module's purity, so a path is not resolved here, only split. An empty anchor
path (an ``""`` ``source_anchor``) is dropped by the same blanks rule, so it can
never match a PR entry. Matching is EXACT normalised path equality in v1;
directory/prefix/glob matching is deferred (a known v1 limitation).

Known v1 property: with ``allowed_paths`` deferred, most code files in a normal
PR fall OUTSIDE the declared doc paths and are surfaced for waive — v1 scope is
a forced lightweight operator review, not an automatic verdict, exactly as
verification-engine.md says.

The decision convention (D5 — inherits ATLAS-72's shape; a literal path, not a
hash). A scope decision is a human-tier :attr:`EvidenceType.MANUAL_APPROVAL`
record that:

- is pinned to ``C`` (``commit_sha == head_commit``),
- is scoped to the ticket (``ticket_id == ticket_id``),
- is human-tier (``evidence_tier(created_by_type) == "human"``, D8), and
- carries ``raw_payload[SCOPE_DECISION_PATH_KEY]`` (= ``"scope_decision_path"``)
  whose value is the exact normalised file path it decides.

Its :attr:`Evidence.status` carries the operator's choice: ``PASSED`` == waive
(this out-of-scope file is acceptable), ``FAILED`` == fail (this file should not
be here — a scope dispute). A literal path is the discriminator because a path
is already an identifier and operator transparency matters; this mirrors
ATLAS-72's per-decision shape, differing only in literal-path-vs-hash and in
status-carrying-the-choice. The path is read defensively (a missing key, a
non-str value, or the ATLAS-69 retention-cap marker payload → not a decision);
the evaluator NEVER raises.

PURE and layer-faithful (D9): this module imports ``atlas.core`` only — models,
enums, and ``trust.evidence_tier`` (the ONLY place tier logic lives; not
reimplemented here). It takes pre-loaded evidence as a parameter; it does NOT
load from storage, NOT capture or write operator decisions (that is the verify
CLI's job, ATLAS-80 / OP-3), NOT import the anchor INDEX, NOT resolve the PR
file list or the head commit (the CLI's / Phase 8's job), NOT persist
VerificationCheck rows, and NOT compose a verdict (ATLAS-76/77).
``atlas.verification`` stays below ``atlas.pm`` in the import-linter spine.

Following the sibling evaluators, invalidity and absence are DATA, not
exceptions: the evaluator NEVER raises, for any input — empty ``pr_files``,
empty evidence, an ``""`` ``source_anchor``, a record with ``commit_sha=None``,
or a malformed/again-capped ``raw_payload``. A record that cannot prove a
decision simply does not.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from atlas.core.enums import EvidenceStatus
from atlas.core.models import Evidence, EvidenceType, VerificationCheckType
from atlas.core.trust import evidence_tier

# The raw_payload key that carries the exact normalised file path a scope
# decision decides (D5). Defined once here, the home of the convention, so
# ATLAS-80 (writes) references one name. The sibling discriminator for
# acceptance is a hash (acceptance_check.ACCEPTANCE_CRITERION_HASH_KEY); scope
# uses a literal path because a path is already an identifier and operator
# transparency matters.
SCOPE_DECISION_PATH_KEY = "scope_decision_path"

# Tier names are compared inline as string literals against
# ``evidence_tier(...)`` — the storage/repositories.py and sibling-evaluator
# idiom — so this module defines no tier-named binding of its own (tier logic
# lives only in atlas.core.trust, ADR-0008 / knowledge-core.md). "human" alone
# decides an out-of-scope file; a non-human MANUAL_APPROVAL carrying a matching
# path does NOT decide it (agent-tier is capped PENDING regardless, D8).


def out_of_scope_paths(
    pr_files: Sequence[str],
    *,
    relevant_docs: Sequence[str],
    source_anchor: str,
) -> list[str]:
    """The distinct, normalised, sorted PR paths NOT in the declared scope (D1).

    The single source of truth for the out-of-scope derivation:
    :func:`evaluate_scope` calls this, and the OP-3 operator-confirmation capture
    (``atlas.verification.capture``) calls the SAME function, so the evaluator and
    the capture can never drift on what "out of scope" means. The in-scope set is
    ``_in_scope_paths(relevant_docs, source_anchor)`` (every ``relevant_docs`` entry
    plus the ``source_anchor`` PATH part, each ``.strip()``ed with blanks dropped);
    ``pr_files`` are normalised the same way (``.strip()``, blanks dropped) and
    reduced to DISTINCT paths; the result is every distinct normalised PR path not
    in the in-scope set, sorted (C1: one path, one contribution). Pure and never
    raises — empty inputs yield ``[]``.
    """
    in_scope = _in_scope_paths(relevant_docs, source_anchor)
    files = {f.strip() for f in pr_files if f.strip()}
    return sorted(f for f in files if f not in in_scope)


@dataclass(frozen=True)
class ScopeEvaluation:
    """The result of :func:`evaluate_scope` (D4).

    Frozen: an evaluation is an immutable record. ``check_type`` is always
    :attr:`VerificationCheckType.SCOPE` — this evaluator decides a single fixed
    check, so the type is a constant, not an argument. ``status`` is:

    - ``PASSED`` when every changed file is in the declared scope, or every
      out-of-scope file has a human-tier waive pinned to the head commit;
    - ``FAILED`` when any out-of-scope file's latest decision is a fail — a
      scope dispute, which the PM Engine routes to ``needs_human_decision``
      (verification-engine.md "Verdict and completion");
    - ``PENDING`` while one or more out-of-scope files have NO decision (an
      undecided file is unresolved, NOT rejected — absence is never FAILED).

    ``evidence_ids`` is PLURAL — scope is a MULTI-decision check (one decision
    per out-of-scope file), like acceptance. On a waive-PASSED it names every
    waive; on FAILED it names the failing decision(s); on PENDING it names the
    PARTIAL waive set decided so far (useful to the operator). It is
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


def evaluate_scope(
    pr_files: Sequence[str],
    *,
    relevant_docs: Sequence[str],
    source_anchor: str,
    ticket_id: UUID,
    head_commit: str,
    evidence: Sequence[Evidence],
) -> ScopeEvaluation:
    """Evaluate the scope check against pre-loaded evidence (never raises).

    Returns a :class:`ScopeEvaluation`. The rule (D3/D5/D6,
    verification-engine.md):

    - The in-scope set is every ``relevant_docs`` entry plus the path part of
      ``source_anchor`` (``.split("#", 1)[0]``), each ``.strip()``ed with blanks
      dropped (D3). ``pr_files`` are normalised the same way (``.strip()``,
      blanks dropped) and reduced to DISTINCT paths. ``out_of_scope`` is the set
      of distinct normalised PR paths NOT in the in-scope set (C1: one path, one
      latest-decision, one contribution to the verdict — a duplicated PR entry
      cannot count a path as both waived and undecided).
    - If ``out_of_scope`` is empty → ``PASSED`` with empty ``evidence_ids``.
    - Otherwise each distinct out-of-scope path is resolved ONCE to its latest
      (by ``(created_at, id)``) human-tier MANUAL_APPROVAL decision at the head
      commit for this ticket (D7: a file may carry both a waive and a fail if the
      operator changed their mind; the latest decides). Aggregate by precedence:

      * any path's latest decision is ``FAILED`` → ``FAILED`` (the operator
        rejected the scope; the reason names the failed path(s); ``evidence_ids``
        names the failing decision(s));
      * else every out-of-scope path has a ``PASSED`` (waive) decision →
        ``PASSED`` (``evidence_ids`` names the waives);
      * else (≥1 out-of-scope path has no decision) → ``PENDING``, NEVER FAILED
        for the absence (``evidence_ids`` names the PARTIAL waive set).

    Args:
        pr_files: the PR's changed-file paths (resolving them is the CLI's /
            Phase 8's job — this evaluator does not).
        relevant_docs: the ticket's ``relevant_docs`` (in-scope doc paths).
        source_anchor: the ticket's ``source_anchor`` (``path#slug``); only its
            path part contributes to the in-scope set (a local split, never the
            index-coupled resolver — purity).
        ticket_id: the ticket the decisions must be scoped to (a parameter —
            this evaluator does not resolve it).
        head_commit: the PR head commit the decisions must be pinned to (a
            parameter — resolving it is the CLI's / Phase 8's job).
        evidence: pre-loaded Evidence records to evaluate against (loading is
            the CLI's job; this module never touches storage).
    """
    files = {f.strip() for f in pr_files if f.strip()}
    out_of_scope = out_of_scope_paths(
        pr_files, relevant_docs=relevant_docs, source_anchor=source_anchor
    )

    if not out_of_scope:
        return ScopeEvaluation(
            check_type=VerificationCheckType.SCOPE,
            status=EvidenceStatus.PASSED,
            evidence_ids=(),
            reason=(
                f"scope: all {len(files)} PR file(s) are within the declared "
                f"scope (relevant_docs + the source_anchor path); PASSED."
            ),
        )

    failed_paths: list[str] = []
    failed_decisions: list[Evidence] = []
    waived_decisions: list[Evidence] = []
    undecided_paths: list[str] = []
    for path in out_of_scope:
        decision = _latest_decision(path, ticket_id, head_commit, evidence)
        if decision is None:
            undecided_paths.append(path)
        elif decision.status == EvidenceStatus.FAILED:
            failed_paths.append(path)
            failed_decisions.append(decision)
        elif decision.status == EvidenceStatus.PASSED:
            waived_decisions.append(decision)
        else:
            # A matching decision whose status is neither waive nor fail does
            # not resolve the file (defensive — ATLAS-80 writes only
            # PASSED/FAILED); treat it as undecided, never a silent pass.
            undecided_paths.append(path)

    if failed_paths:
        return ScopeEvaluation(
            check_type=VerificationCheckType.SCOPE,
            status=EvidenceStatus.FAILED,
            evidence_ids=_sorted_ids(failed_decisions),
            reason=(
                f"scope: {len(failed_paths)} out-of-scope file(s) rejected by an "
                f"operator at {head_commit} (a scope dispute): "
                f"{', '.join(failed_paths)}; FAILED."
            ),
        )

    if not undecided_paths:
        return ScopeEvaluation(
            check_type=VerificationCheckType.SCOPE,
            status=EvidenceStatus.PASSED,
            evidence_ids=_sorted_ids(waived_decisions),
            reason=(
                f"scope: all {len(out_of_scope)} out-of-scope file(s) waived by an "
                f"operator at {head_commit}; PASSED."
            ),
        )

    return ScopeEvaluation(
        check_type=VerificationCheckType.SCOPE,
        status=EvidenceStatus.PENDING,
        evidence_ids=_sorted_ids(waived_decisions),
        reason=(
            f"scope: {len(waived_decisions)} of {len(out_of_scope)} out-of-scope "
            f"file(s) waived at {head_commit}; {len(undecided_paths)} awaiting an "
            f"operator decision; PENDING (an undecided out-of-scope file is "
            f"unresolved, not rejected)."
        ),
    )


def _in_scope_paths(relevant_docs: Sequence[str], source_anchor: str) -> set[str]:
    """The declared in-scope path set (D3): relevant_docs + the anchor path.

    Every ``relevant_docs`` entry plus ``source_anchor.split("#", 1)[0]``, each
    ``.strip()``ed with blanks dropped. The anchor split is local and never the
    index-coupled resolver (purity); the documented ``path#slug`` invariant
    means the part before the first ``#`` is the path. An empty anchor path (an
    ``""`` ``source_anchor``) is dropped by the same blanks rule, so it cannot
    match a PR entry. Matching is exact normalised path equality in v1;
    glob/prefix matching is deferred.
    """
    paths = {d.strip() for d in relevant_docs if d.strip()}
    anchor_path = source_anchor.split("#", 1)[0].strip()
    if anchor_path:
        paths.add(anchor_path)
    return paths


def _latest_decision(
    path: str,
    ticket_id: UUID,
    head_commit: str,
    evidence: Sequence[Evidence],
) -> Evidence | None:
    """The latest human-tier scope decision for ``path`` at the head commit (D7).

    A decision is a human-tier MANUAL_APPROVAL pinned to ``head_commit`` and
    scoped to ``ticket_id`` whose ``scope_decision_path`` equals ``path``. The
    LATEST by ``(created_at, id)`` decides the file, so a waive-then-fail
    resolves as a fail and a fail-then-waive as a waive (the operator changed
    their mind). Returns ``None`` when no record decides this path. A non-human
    record carrying a matching path is NOT a decision (D8) — agent-tier is capped
    PENDING regardless and a system claim cannot decide scope.
    """
    matches = [
        e
        for e in evidence
        if e.evidence_type == EvidenceType.MANUAL_APPROVAL
        and e.ticket_id == ticket_id
        and e.commit_sha == head_commit
        and evidence_tier(e.created_by_type) == "human"
        and _decision_path(e) == path
    ]
    if not matches:
        return None
    return max(matches, key=lambda e: (e.created_at, e.id))


def _decision_path(evidence: Evidence) -> str | None:
    """The file path a record decides, read from ``raw_payload`` (D5).

    Reads ``raw_payload[SCOPE_DECISION_PATH_KEY]`` defensively and never raises:
    a missing key (a blanket human_approval, or the ATLAS-69 retention-cap marker
    payload, both of which lack it), a non-str value, or a blank string yields
    ``None`` — that record decides nothing. The value is ``.strip()``ed so it is
    comparable to the normalised out-of-scope paths (exact equality in v1).
    """
    value = evidence.raw_payload.get(SCOPE_DECISION_PATH_KEY)
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _sorted_ids(records: Sequence[Evidence]) -> tuple[UUID, ...]:
    """Record ids ordered by ``(created_at, id)`` — the deterministic order the
    composition ticket (ATLAS-76/77) folds into ``VerificationCheck.evidence_ids``
    without re-sorting (matches the acceptance evaluator)."""
    return tuple(e.id for e in sorted(records, key=lambda e: (e.created_at, e.id)))
