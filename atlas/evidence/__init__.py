"""Phase 6 evidence mappers (ATLAS-63): normalised CI -> Evidence.

A layer that imports ``atlas.github``, ``atlas.storage``, and ``atlas.core``
ONLY (the import-linter spine forbids reaching up into ``atlas.planning`` etc.,
so PRODUCT_KEY never leaks in — the caller resolves the product and passes the
id). The pure job-name -> ``EvidenceType`` contract and the pure check/review ->
``Evidence`` mappers live in :mod:`.mapping`; the thin persistence paths live in
:mod:`.ingest`. ATLAS-64 adds the lint/build/coverage rows and the unrecognised
-> BUILD_RESULT fallback; ATLAS-65 adds the PR-review mapper + ingest (always
``PR_REVIEW``, no job-name lookup); ATLAS-66 adds the documentation mapper +
ingest (always ``DOCUMENTATION_UPDATE``, one record per PR, ``None`` when no
``docs/`` path changed).
"""

from atlas.evidence.ingest import ingest_checks, ingest_docs, ingest_reviews
from atlas.evidence.mapping import (
    GITHUB_ACTIONS_ACTOR_ID,
    build_merge_evidence,
    evidence_type_for_job,
    map_check_to_evidence,
    map_docs_to_evidence,
    map_review_to_evidence,
)
from atlas.evidence.pull import PullResult, drive_evidence_pull
from atlas.evidence.views import evidence_summary

__all__ = [
    "GITHUB_ACTIONS_ACTOR_ID",
    "PullResult",
    "build_merge_evidence",
    "drive_evidence_pull",
    "evidence_summary",
    "evidence_type_for_job",
    "ingest_checks",
    "ingest_docs",
    "ingest_reviews",
    "map_check_to_evidence",
    "map_docs_to_evidence",
    "map_review_to_evidence",
]
