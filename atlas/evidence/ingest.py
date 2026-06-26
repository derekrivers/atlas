"""Thin ingest path for normalised CI evidence (ATLAS-63).

The I/O half of the mapper: persist the recognised, mapped checks via the
append-only ``EvidenceRepo``. The mapping itself is pure (:mod:`.mapping`); this
function only walks the checks, drops the unrecognised ones (mapper -> ``None``),
and appends the rest. The system-tier pinning guard (ATLAS-61) runs inside
``EvidenceRepo.add`` — this is its first real producer.

Dedup (skip a re-polled run already stored, via the normaliser's
``(external_run_id, payload_hash)`` key) belongs to the poller/tick loop
(Phase 8), not here; ATLAS-63 ingests every recognised check it is handed.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from uuid import UUID

from atlas.core.models.evidence import Evidence
from atlas.evidence.mapping import map_check_to_evidence
from atlas.github import NormalisedCheck
from atlas.storage import EvidenceRepo


def ingest_checks(
    checks: Iterable[NormalisedCheck],
    *,
    repo: EvidenceRepo,
    product_id: UUID,
    now: datetime,
) -> list[Evidence]:
    """Map and persist each recognised check; skip the unrecognised ones.

    Returns the persisted records in input order. A check whose job name is
    unrecognised maps to ``None`` and is skipped — nothing is written for it
    (the ATLAS-64 ``BUILD_RESULT`` fallback is deliberately not here). ``now``
    and ``product_id`` are passed straight through to :func:`map_check_to_evidence`.
    """

    persisted: list[Evidence] = []
    for check in checks:
        evidence = map_check_to_evidence(check, product_id=product_id, now=now)
        if evidence is None:
            continue
        persisted.append(repo.add(evidence))
    return persisted
