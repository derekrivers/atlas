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

from atlas.pm.admission import (
    AdmissionInputMismatchCode,
    AdmissionInputMismatchError,
    evaluate_admission,
)
from atlas.pm.admission_sync import (
    ADMISSION_LEASE_TTL,
    AdmissionSyncHooks,
    AdmissionSyncOutcome,
    AdmissionSyncReason,
    AdmissionSyncResult,
    admit_one_ready,
)
from atlas.pm.agent_runs import (
    AgentRunReconstructionResult,
    agent_run_observation,
    reconstruct_agent_runs,
)
from atlas.pm.ci_handoff import (
    CIHandoffHooks,
    CIHandoffResult,
    reconcile_ci_handoff,
)
from atlas.pm.ci_handoff_adapter import (
    CIHandoffAdapterReason,
    CIHandoffAdapterResult,
    CIHandoffIdentity,
    reconcile_one_ci_handoff,
)
from atlas.pm.completion import complete_verified
from atlas.pm.delivery_snapshot import (
    INTEGRATION_STATUSES,
    ComponentLaneOccupancy,
    DeliverySnapshot,
    LinearBoardPull,
    OccupancyBreach,
    OccupancyDimension,
    ProtectedLaneOccupancy,
    RiskLaneOccupancy,
    SnapshotIncompletenessCode,
    SnapshotIncompletenessReason,
    StatusOccupancy,
    StoredDeliveryOccupancy,
    build_delivery_snapshot,
    build_stored_delivery_occupancy,
    delivery_graph_revision,
    delivery_policy_fingerprint,
    delivery_store_revision,
    linear_board_fingerprint,
)
from atlas.pm.planned_ci_pending_recovery import (
    PlannedCIPendingRecoveryEvaluation,
    PlannedCIPendingRecoveryReason,
    PlannedCIPendingRecoveryResult,
    evaluate_planned_ci_pending_recovery,
    recover_planned_ci_pending,
)
from atlas.pm.promotion import promote_ready
from atlas.pm.protected_lanes import (
    DEFAULT_PROTECTED_LANE_REGISTRY,
    ProtectedLane,
    ProtectedLaneClassification,
    ProtectedLaneClassificationCode,
    ProtectedLaneClassificationIssue,
    ProtectedLaneMatch,
    ProtectedLaneRegistry,
    ProtectedLaneRegistryLoadResult,
    ProtectedLaneRule,
    classify_ticket_protected_lanes,
    load_packaged_protected_lane_registry,
    load_protected_lane_registry_bytes,
    parse_protected_lane_registry,
)
from atlas.pm.protected_lanes import (
    REGISTRY_VERSION as PROTECTED_LANE_REGISTRY_VERSION,
)
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
    "ADMISSION_LEASE_TTL",
    "CRASH_DEDUP_WINDOW",
    "DEFAULT_INTERVAL_SECONDS",
    "DEFAULT_PROTECTED_LANE_REGISTRY",
    "INTEGRATION_STATUSES",
    "PROTECTED_LANE_REGISTRY_VERSION",
    "PUSHABLE_STATUSES",
    "AdmissionInputMismatchCode",
    "AdmissionInputMismatchError",
    "AdmissionSyncHooks",
    "AdmissionSyncOutcome",
    "AdmissionSyncReason",
    "AdmissionSyncResult",
    "AgentRunMetric",
    "AgentRunReconstructionResult",
    "AnomalyCount",
    "CIHandoffAdapterReason",
    "CIHandoffAdapterResult",
    "CIHandoffHooks",
    "CIHandoffIdentity",
    "CIHandoffResult",
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
    "PlannedCIPendingRecoveryEvaluation",
    "PlannedCIPendingRecoveryReason",
    "PlannedCIPendingRecoveryResult",
    "ProtectedLane",
    "ProtectedLaneClassification",
    "ProtectedLaneClassificationCode",
    "ProtectedLaneClassificationIssue",
    "ProtectedLaneMatch",
    "ProtectedLaneOccupancy",
    "ProtectedLaneRegistry",
    "ProtectedLaneRegistryLoadResult",
    "ProtectedLaneRule",
    "RiskLaneOccupancy",
    "SnapshotIncompletenessCode",
    "SnapshotIncompletenessReason",
    "StatusOccupancy",
    "StoredDeliveryOccupancy",
    "SyncDecisionClassification",
    "SyncReceiptPersistenceError",
    "SyncResult",
    "ThroughputBucket",
    "TickConfig",
    "admit_one_ready",
    "agent_run_observation",
    "build_delivery_report",
    "build_delivery_snapshot",
    "build_stored_delivery_occupancy",
    "classify_ticket_protected_lanes",
    "complete_verified",
    "delivery_graph_revision",
    "delivery_policy_fingerprint",
    "delivery_store_revision",
    "evaluate_admission",
    "evaluate_planned_ci_pending_recovery",
    "linear_board_fingerprint",
    "load_packaged_protected_lane_registry",
    "load_protected_lane_registry_bytes",
    "parse_protected_lane_registry",
    "promote_ready",
    "reconcile_ci_handoff",
    "reconcile_one_ci_handoff",
    "reconstruct_agent_runs",
    "recover_planned_ci_pending",
    "render_markdown",
    "report_json",
    "run_scheduler",
    "run_tick",
    "sync_result_is_empty",
    "sync_tick",
]
