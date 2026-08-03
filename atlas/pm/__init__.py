"""PM Engine (Phase 4).

The reconciliation loop above the ATLAS-41 Linear boundary: it mirrors state
between Atlas and Linear under strict field ownership. ATLAS-42 delivers steps
1+2 of the ``pm-engine-and-linear-sync.md`` "Sync loop" (:func:`sync_tick`);
ATLAS-43 adds step 3, readiness promotion to ``Ready for Agent``
(:func:`promote_ready`, the sole writer of that transition). ATLAS-131 adds step
3b, verified completion: :func:`complete_verified` moves every ``review_required``
ticket whose persisted Verification Engine verdict is PASSED to ``Done``, through
the same sanctioned Linear-only ``set_state`` path. ATLAS-47 adds the
read side: :func:`build_delivery_report` and its renderers, a pure reader of
stored tickets, ``DebtItem``s, transition rows, and DRAFT ``Lesson`` rows for the
``atlas pm report`` command. The follow-up ingestion is a later ticket. ATLAS-50
adds the recurring scheduler:
:func:`run_scheduler` calls :func:`sync_tick` on a cadence and records one
``TickFailure`` on a crashing tick (create-on-crash), the sole writer of that
record. A layer above ``atlas.storage``/``atlas.linear``/``atlas.core`` in the
import spine.
"""

from atlas.pm.agent_runs import (
    AgentRunReconstructionResult,
    agent_run_observation,
    reconstruct_agent_runs,
)
from atlas.pm.completion import complete_verified
from atlas.pm.delivery_snapshot import (
    ComponentLaneOccupancy,
    DeliverySnapshot,
    LinearBoardPull,
    OccupancyBreach,
    OccupancyDimension,
    RiskLaneOccupancy,
    SnapshotIncompletenessCode,
    SnapshotIncompletenessReason,
    StatusOccupancy,
    build_delivery_snapshot,
)
from atlas.pm.promotion import promote_ready
from atlas.pm.report import (
    AgentRunMetric,
    AnomalyCount,
    CycleTimeStat,
    DeliveryReport,
    DraftLesson,
    DwellBreach,
    ThroughputBucket,
    build_delivery_report,
    render_markdown,
    report_json,
)
from atlas.pm.scheduler import (
    CRASH_DEDUP_WINDOW,
    DEFAULT_INTERVAL_SECONDS,
    TickConfig,
    run_scheduler,
    run_tick,
)
from atlas.pm.sync import (
    PUSHABLE_STATUSES,
    MalformedLinearPullError,
    SyncDecisionClassification,
    SyncReceiptPersistenceError,
    SyncResult,
    sync_result_is_empty,
    sync_tick,
)

__all__ = [
    "CRASH_DEDUP_WINDOW",
    "DEFAULT_INTERVAL_SECONDS",
    "PUSHABLE_STATUSES",
    "AgentRunMetric",
    "AgentRunReconstructionResult",
    "AnomalyCount",
    "ComponentLaneOccupancy",
    "CycleTimeStat",
    "DeliveryReport",
    "DeliverySnapshot",
    "DraftLesson",
    "DwellBreach",
    "LinearBoardPull",
    "MalformedLinearPullError",
    "OccupancyBreach",
    "OccupancyDimension",
    "RiskLaneOccupancy",
    "SnapshotIncompletenessCode",
    "SnapshotIncompletenessReason",
    "StatusOccupancy",
    "SyncDecisionClassification",
    "SyncReceiptPersistenceError",
    "SyncResult",
    "ThroughputBucket",
    "TickConfig",
    "agent_run_observation",
    "build_delivery_report",
    "build_delivery_snapshot",
    "complete_verified",
    "promote_ready",
    "reconstruct_agent_runs",
    "render_markdown",
    "report_json",
    "run_scheduler",
    "run_tick",
    "sync_result_is_empty",
    "sync_tick",
]
