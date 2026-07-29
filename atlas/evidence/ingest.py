"""Thin ingest path for normalised CI / review / docs evidence (ATLAS-63/64/65/66).

The I/O half of the mappers: persist the mapped checks/reviews via the
append-only ``EvidenceRepo``. The mapping itself is pure (:mod:`.mapping`);
these functions only walk the inputs and append each one. The check mapper is
total since ATLAS-64 (unrecognised jobs fall back to ``BUILD_RESULT`` with a
warning) and every ``NormalisedReview`` is already commit-pinned (ATLAS-65), so
every input produces a record — nothing is dropped here. The system-tier
pinning guard (ATLAS-61) runs inside ``EvidenceRepo.add`` — this is its first
real producer.

Dedup is enforced at the persistence boundary via the normaliser's
``(external_run_id, payload_hash)`` identity. Re-polling an unchanged source
returns no newly persisted record; a changed payload appends a new one.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from uuid import UUID

from atlas.core.models.evidence import Evidence
from atlas.evidence.mapping import (
    map_check_to_evidence,
    map_docs_to_evidence,
    map_review_to_evidence,
)
from atlas.github import NormalisedCheck, NormalisedDocs, NormalisedReview
from atlas.storage import EvidenceRepo


def _persist_if_new(
    evidence: Evidence, *, repo: EvidenceRepo, require_job_metadata: bool = False
) -> Evidence | None:
    """Append ``evidence`` unless its immutable source payload already exists."""

    assert evidence.external_run_id is not None
    assert evidence.payload_hash is not None
    existing = repo.get_by_dedup_key(
        evidence.external_run_id,
        evidence.payload_hash,
        require_job_metadata=require_job_metadata,
    )
    if existing is not None:
        return None
    return repo.add(evidence)


def ingest_checks(
    checks: Iterable[NormalisedCheck],
    *,
    repo: EvidenceRepo,
    product_id: UUID,
    now: datetime,
) -> list[Evidence]:
    """Map and persist every check, returning the records in input order.

    The mapper is total (ATLAS-64): a check whose job name has no recognised
    prefix falls back to ``BUILD_RESULT`` with a warning rather than being
    dropped, so every check produces a persisted record. ``now`` and
    ``product_id`` are passed straight through to :func:`map_check_to_evidence`.
    """

    persisted: list[Evidence] = []
    for check in checks:
        evidence = map_check_to_evidence(check, product_id=product_id, now=now)
        stored = _persist_if_new(evidence, repo=repo, require_job_metadata=True)
        if stored is not None:
            persisted.append(stored)
    return persisted


def ingest_reviews(
    reviews: Iterable[NormalisedReview],
    *,
    repo: EvidenceRepo,
    product_id: UUID,
    now: datetime,
) -> list[Evidence]:
    """Map and persist every review as ``PR_REVIEW`` evidence (ATLAS-65).

    Mirrors :func:`ingest_checks`: each ``NormalisedReview`` becomes a
    system-tier ``PR_REVIEW`` ``Evidence`` and is appended via the
    ``EvidenceRepo``. Every ``NormalisedReview`` is already commit-pinned (the
    normaliser drops the unpinnable ones), so each one produces a record that
    satisfies the ATLAS-61 guard -- nothing is dropped here. ``now`` and
    ``product_id`` pass straight through to :func:`map_review_to_evidence`.
    """

    persisted: list[Evidence] = []
    for review in reviews:
        evidence = map_review_to_evidence(review, product_id=product_id, now=now)
        stored = _persist_if_new(evidence, repo=repo)
        if stored is not None:
            persisted.append(stored)
    return persisted


def ingest_docs(
    docs: NormalisedDocs | None,
    *,
    repo: EvidenceRepo,
    product_id: UUID,
    now: datetime,
) -> list[Evidence]:
    """Map and persist the one ``DOCUMENTATION_UPDATE`` record, if any (ATLAS-66).

    Unlike :func:`ingest_checks`/:func:`ingest_reviews` (which walk a sequence),
    documentation evidence is one record per PR, so the input is a single
    ``NormalisedDocs | None``: ``normalise_pr_files`` returns ``None`` when the PR
    touches no ``docs/`` path. When ``docs`` is ``None`` this persists nothing and
    returns ``[]`` -- the absence of a ``docs/`` change is the signal, so no
    record is manufactured (evidence-pipeline.md "Documentation evidence"). When
    ``docs`` is present it is mapped to a system-tier ``DOCUMENTATION_UPDATE``
    ``Evidence`` and appended via the ``EvidenceRepo``; the synthesised pin triple
    satisfies the ATLAS-61 guard. The ``list`` return mirrors the other ingest
    paths. ``now`` and ``product_id`` pass straight through to
    :func:`map_docs_to_evidence`.
    """

    if docs is None:
        return []
    evidence = map_docs_to_evidence(docs, product_id=product_id, now=now)
    stored = _persist_if_new(evidence, repo=repo)
    return [] if stored is None else [stored]
