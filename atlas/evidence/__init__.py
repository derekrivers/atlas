"""Phase 6 evidence mappers (ATLAS-63): normalised CI -> Evidence.

A layer that imports ``atlas.github``, ``atlas.storage``, and ``atlas.core``
ONLY (the import-linter spine forbids reaching up into ``atlas.planning`` etc.,
so PRODUCT_KEY never leaks in — the caller resolves the product and passes the
id). The pure job-name -> ``EvidenceType`` contract and the pure check ->
``Evidence`` mapper live in :mod:`.mapping`; the thin persistence path lives in
:mod:`.ingest`. ATLAS-64 adds the lint/build/coverage rows and the unrecognised
-> BUILD_RESULT fallback; review (ATLAS-65) and docs (ATLAS-66) ingestion are
different sources entirely.
"""

from atlas.evidence.ingest import ingest_checks
from atlas.evidence.mapping import (
    GITHUB_ACTIONS_ACTOR_ID,
    evidence_type_for_job,
    map_check_to_evidence,
)

__all__ = [
    "GITHUB_ACTIONS_ACTOR_ID",
    "evidence_type_for_job",
    "ingest_checks",
    "map_check_to_evidence",
]
