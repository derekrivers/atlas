"""Pure normalised-CI -> Evidence mapping (ATLAS-63), per
evidence-pipeline.md "Job-name convention" and ADR-0008.

Two pure functions, no I/O:

* :func:`evidence_type_for_job` is the job-name -> ``EvidenceType`` contract —
  a repo-owned mapping (evidence-pipeline.md) rather than a payload heuristic.
  ATLAS-63 seeds it with ONLY the ``test`` prefix; ATLAS-64 ADDs the
  lint/build/coverage rows and the unrecognised -> ``BUILD_RESULT`` + warning
  fallback. Today an unrecognised job returns ``None`` (the caller persists
  nothing), NOT ``BUILD_RESULT``.
* :func:`map_check_to_evidence` builds a system-tier ``Evidence`` from a frozen
  ``NormalisedCheck`` (ATLAS-62), or ``None`` when the job name is unrecognised.
  It is the FIRST real producer for ATLAS-61's system-tier pinning guard: the
  commit-pin triple comes straight from the check, so ``EvidenceRepo.add``
  accepts it. Persistence is the separate thin ingest path (:mod:`.ingest`),
  mirroring the codebase's pure-logic / I/O separation.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from atlas.core.enums import ActorType
from atlas.core.models.evidence import Evidence, EvidenceType
from atlas.github import NormalisedCheck

# Job-name prefix -> EvidenceType (evidence-pipeline.md "Job-name convention").
# A name whose lowercased job/check name STARTS WITH the prefix takes the type.
# ATLAS-63 seeds ONLY the ``test`` row it owns; ATLAS-64 adds ``lint``/``build``/
# ``coverage`` and the unrecognised -> BUILD_RESULT fallback. Ordered tuple, not
# a dict, so the prefix-match order is explicit and a future longer-prefix row
# can sit ahead of a shorter one if it ever needs to.
_JOB_PREFIX_TYPES: tuple[tuple[str, EvidenceType], ...] = (
    ("test", EvidenceType.TEST_RESULT),
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
    lowercased form starts with a seeded prefix takes that type, so ``"test"``,
    ``"test (3.12)"``, and ``"Test Suite"`` all map to ``TEST_RESULT``. Any other
    name returns ``None`` today — the caller persists nothing. ATLAS-64 replaces
    that ``None`` for unrecognised jobs with a ``BUILD_RESULT`` + warning
    fallback; do not anticipate it here.
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
) -> Evidence | None:
    """Build a system-tier ``Evidence`` from ``check``, or ``None`` when its job
    name is unrecognised. PURE: it touches no ``Database`` and persists nothing
    (that is :func:`atlas.evidence.ingest.ingest_checks`).

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
        return None  # unrecognised job: ATLAS-64 will map this to BUILD_RESULT
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
