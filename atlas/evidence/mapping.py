"""Pure normalised-CI/review -> Evidence mapping (ATLAS-63/64/65), per
evidence-pipeline.md "Job-name convention"/"Status normalisation" and ADR-0008.

Pure functions, no I/O:

* :func:`evidence_type_for_job` is the job-name -> ``EvidenceType`` contract —
  a repo-owned mapping (evidence-pipeline.md) rather than a payload heuristic.
  ATLAS-63 seeded it with ONLY the ``test`` prefix; ATLAS-64 ADDs the
  lint/build/coverage rows. It stays honest about unrecognised names: a name
  with no recognised prefix returns ``None`` (the BUILD_RESULT fallback is the
  caller's concern, not the lookup's).
* :func:`map_check_to_evidence` builds a system-tier ``Evidence`` from a frozen
  ``NormalisedCheck`` (ATLAS-62). It is total: an unrecognised job logs a
  warning and falls back to ``BUILD_RESULT`` so nothing is silently dropped
  (evidence-pipeline.md "Job-name convention"). It is the FIRST real producer
  for ATLAS-61's system-tier pinning guard: the commit-pin triple comes straight
  from the check, so ``EvidenceRepo.add`` accepts it. Persistence is the
  separate thin ingest path (:mod:`.ingest`), mirroring the codebase's
  pure-logic / I/O separation.
* :func:`map_review_to_evidence` builds a system-tier ``PR_REVIEW`` ``Evidence``
  from a frozen ``NormalisedReview`` (ATLAS-65). A review is always ``PR_REVIEW``
  -- no job-name lookup -- and the reviewer lives in ``raw_payload``/``summary``,
  never in ``created_by_id`` (which stays the SYSTEM-tier ``github-actions``).
* :func:`map_docs_to_evidence` builds a system-tier ``DOCUMENTATION_UPDATE``
  ``Evidence`` from a frozen ``NormalisedDocs`` (ATLAS-66). Like the review
  mapper it hard-codes the type (no job-name lookup) and takes the status
  (always ``PASSED``) verbatim; the no-docs-change case never reaches here (it
  is ``None`` upstream), so this mapper never produces a FAILED record.
"""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID, uuid4

from atlas.core.enums import ActorType
from atlas.core.models.evidence import Evidence, EvidenceType
from atlas.github import NormalisedCheck, NormalisedDocs, NormalisedReview

# Module logger for the unrecognised-job fallback warning (ATLAS-64), mirroring
# atlas/github/client.py's logger + logger.warning pattern.
logger = logging.getLogger("atlas.evidence.mapping")

# Job-name prefix -> EvidenceType (evidence-pipeline.md "Job-name convention").
# A name whose lowercased job/check name STARTS WITH the prefix takes the type.
# ATLAS-63 seeded ONLY the ``test`` row; ATLAS-64 adds ``lint``/``build``/
# ``coverage``. Ordered tuple, not a dict, so the prefix-match order is explicit
# and a future longer-prefix row can sit ahead of a shorter one if it ever needs
# to. An unrecognised name is the mapper's BUILD_RESULT fallback, not a row here.
_JOB_PREFIX_TYPES: tuple[tuple[str, EvidenceType], ...] = (
    ("test", EvidenceType.TEST_RESULT),
    ("lint", EvidenceType.LINT_RESULT),
    ("build", EvidenceType.BUILD_RESULT),
    ("coverage", EvidenceType.COVERAGE_REPORT),
)

# Attribution for GitHub-CI-ingested evidence (ADR-0008 system tier). Each
# system component owns its actor id (the PM engine's is "pm-engine", planning
# has its own); CI's is "github-actions" — the actor that actually produced the
# evidence, matching tests/test_evidence_model.py::evidence_kwargs. Defined here,
# never imported from another component.
GITHUB_ACTIONS_ACTOR_ID = "github-actions"


def evidence_type_for_job(name: str) -> EvidenceType | None:
    """Map a CI job/check ``name`` to its ``EvidenceType`` via the prefix table.

    The match is case-insensitive on the leading token: a name whose stripped,
    lowercased form starts with a recognised prefix takes that type, so
    ``"test"``, ``"test (3.12)"``, and ``"Test Suite"`` map to ``TEST_RESULT``,
    ``"lint (mypy)"`` to ``LINT_RESULT``, ``"build / wheel"`` to
    ``BUILD_RESULT``, and ``"coverage"`` to ``COVERAGE_REPORT``. Any other name
    returns ``None`` — the lookup stays honest about unrecognised prefixes; the
    ``BUILD_RESULT`` fallback for those lives in :func:`map_check_to_evidence`,
    not here.
    """

    lowered = name.strip().lower()
    for prefix, evidence_type in _JOB_PREFIX_TYPES:
        if lowered.startswith(prefix):
            return evidence_type
    return None


def map_check_to_evidence(
    check: NormalisedCheck,
    *,
    product_id: UUID,
    now: datetime,
) -> Evidence:
    """Build a system-tier ``Evidence`` from ``check``. PURE: it touches no
    ``Database`` and persists nothing (that is
    :func:`atlas.evidence.ingest.ingest_checks`).

    Total: when the job name has no recognised prefix
    (:func:`evidence_type_for_job` returns ``None``), it logs a warning naming
    the job and falls back to ``BUILD_RESULT`` so nothing is silently dropped
    (evidence-pipeline.md "Job-name convention").

    The status is taken VERBATIM from the already-normalised ``check.status`` —
    never re-derived from ``raw_payload`` (ATLAS-62 owns the GitHub-conclusion ->
    ``EvidenceStatus`` mapping). The commit-pin triple
    (``commit_sha``/``external_run_id``/``payload_hash``) is copied straight from
    the check, so the result satisfies ATLAS-61's system-tier pinning guard.

    ``product_id`` is an explicit parameter (D4): resolving the product key lives
    in ``atlas.planning``, a higher layer this one must not import, so the caller
    (the ATLAS-67 CLI) resolves the product and passes the id. ``now`` is an
    injected clock (mirroring the system-attributed append-only records in
    ``atlas.pm``), keeping ``created_at`` deterministic under test.
    """

    evidence_type = evidence_type_for_job(check.name)
    if evidence_type is None:
        logger.warning("unrecognised CI job %r -> BUILD_RESULT (ATLAS-64)", check.name)
        evidence_type = EvidenceType.BUILD_RESULT
    return Evidence(
        id=uuid4(),
        product_id=product_id,
        evidence_type=evidence_type,
        status=check.status,  # verbatim from ATLAS-62; never re-derived
        summary=f"{check.name}: {check.status.value}",
        commit_sha=check.commit_sha,
        external_run_id=check.external_run_id,
        payload_hash=check.payload_hash,
        source_uri=check.source_uri,
        raw_payload=check.raw_payload,
        created_by_type=ActorType.SYSTEM,
        created_by_id=GITHUB_ACTIONS_ACTOR_ID,
        created_at=now,
    )


def map_review_to_evidence(
    review: NormalisedReview,
    *,
    product_id: UUID,
    now: datetime,
) -> Evidence:
    """Build a system-tier ``PR_REVIEW`` ``Evidence`` from ``review``. PURE: it
    touches no ``Database`` and persists nothing (that is
    :func:`atlas.evidence.ingest.ingest_reviews`).

    Unlike :func:`map_check_to_evidence`, there is no job-name -> type lookup: a
    review is ALWAYS ``PR_REVIEW`` evidence (evidence-pipeline.md "Status
    normalisation"), so the type is hard-coded. The status is taken VERBATIM
    from the already-normalised ``review.status`` -- never re-derived from
    ``raw_payload`` (ATLAS-65 owns the review-state -> ``EvidenceStatus``
    mapping). The commit-pin triple
    (``commit_sha``/``external_run_id``/``payload_hash``) is copied straight
    from the review (the review is only normalised when commit-pinned), so the
    result satisfies ATLAS-61's system-tier pinning guard.

    Attribution stays SYSTEM/``github-actions`` (D4): the GitHub poller is the
    ingesting actor for all system-tier evidence. The human reviewer is NOT the
    ``created_by_id`` -- that would put a human id on a SYSTEM-tier record; the
    reviewer lives in ``raw_payload`` and the ``summary``. ``product_id`` and
    ``now`` are explicit for the same reasons as :func:`map_check_to_evidence`.
    """

    return Evidence(
        id=uuid4(),
        product_id=product_id,
        evidence_type=EvidenceType.PR_REVIEW,
        status=review.status,  # verbatim from ATLAS-65; never re-derived
        summary=f"review by {review.reviewer}: {review.status.value}",
        commit_sha=review.commit_sha,
        external_run_id=review.external_run_id,
        payload_hash=review.payload_hash,
        source_uri=review.source_uri,
        raw_payload=review.raw_payload,
        created_by_type=ActorType.SYSTEM,
        created_by_id=GITHUB_ACTIONS_ACTOR_ID,
        created_at=now,
    )


def map_docs_to_evidence(
    docs: NormalisedDocs,
    *,
    product_id: UUID,
    now: datetime,
) -> Evidence:
    """Build a system-tier ``DOCUMENTATION_UPDATE`` ``Evidence`` from ``docs``.
    PURE: it touches no ``Database`` and persists nothing (that is
    :func:`atlas.evidence.ingest.ingest_docs`).

    Like :func:`map_review_to_evidence` -- and unlike
    :func:`map_check_to_evidence` -- there is no job-name -> type lookup: a docs
    change is ALWAYS ``DOCUMENTATION_UPDATE`` evidence (evidence-pipeline.md
    "Documentation evidence"), so the type is hard-coded. The status is taken
    VERBATIM from ``docs.status`` -- always ``PASSED`` here, since the *presence*
    of a ``docs/`` change is the signal (ATLAS-66 only normalises a docs change
    when there is one; the no-change case is ``None``, never a FAILED record).
    The commit-pin triple (``commit_sha``/``external_run_id``/``payload_hash``)
    is copied straight from ``docs`` -- the synthesised ``docs:<head_sha>`` id and
    head-SHA pin satisfy ATLAS-61's system-tier guard.

    Attribution stays SYSTEM/``github-actions`` (D4), as for the check and review
    mappers: the GitHub poller is the ingesting actor for all system-tier
    evidence. ``product_id`` and ``now`` are explicit for the same reasons as
    :func:`map_check_to_evidence`.
    """

    return Evidence(
        id=uuid4(),
        product_id=product_id,
        evidence_type=EvidenceType.DOCUMENTATION_UPDATE,
        status=docs.status,  # verbatim (PASSED); the presence is the signal
        summary=f"docs updated: {len(docs.docs_paths)} path(s)",
        commit_sha=docs.commit_sha,
        external_run_id=docs.external_run_id,
        payload_hash=docs.payload_hash,
        source_uri=docs.source_uri,
        raw_payload=docs.raw_payload,
        created_by_type=ActorType.SYSTEM,
        created_by_id=GITHUB_ACTIONS_ACTOR_ID,
        created_at=now,
    )
