"""PM Engine (Phase 4).

The reconciliation loop above the ATLAS-41 Linear boundary: it mirrors state
between Atlas and Linear under strict field ownership. ATLAS-42 delivers steps
1+2 of the ``pm-engine-and-linear-sync.md`` "Sync loop" (:func:`sync_tick`);
readiness promotion, anomaly writes, follow-up ingestion, and the scheduler
are later tickets. A layer above ``atlas.storage``/``atlas.linear``/
``atlas.core`` in the import spine.
"""

from atlas.pm.sync import PUSHABLE_STATUSES, SyncResult, sync_tick

__all__ = [
    "PUSHABLE_STATUSES",
    "SyncResult",
    "sync_tick",
]
