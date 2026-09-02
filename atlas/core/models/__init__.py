"""Canonical Pydantic models (Phase 1 Knowledge Core).

ATLAS-12 lands Product, ArchitectureDecisionRecord, Epic, Ticket, and
TicketDependency; ATLAS-13 adds Lesson; ATLAS-14 adds Evidence; ATLAS-15
adds PlanRun, ContextPack, and AgentRun, completing the Phase 1 model
set.
"""

from atlas.core.models.acceptance_session import (
    AcceptanceSession,
    AcceptanceSessionBlockingReason,
    AcceptanceSessionLifecycle,
    AcceptanceSessionStep,
    AcceptanceSessionStepState,
)
from atlas.core.models.admission_run import AdmissionRun
from atlas.core.models.adr import ADRStatus, ArchitectureDecisionRecord
from atlas.core.models.agent_run import AgentProvider, AgentRun, AgentRunStatus
from atlas.core.models.atlas_280_bootstrap_recovery import (
    Atlas280BootstrapRecoveryReceipt,
)
from atlas.core.models.ci_handoff_reconciliation import (
    CIHandoffClassification,
    CIHandoffDecision,
    CIHandoffReason,
    CIHandoffReconciliation,
)
from atlas.core.models.context_pack import ContextPack
from atlas.core.models.debt_item import AnomalyType, DebtItem
from atlas.core.models.delivery_admission_policy import (
    DeliveryAdmissionMode,
    DeliveryAdmissionPolicyRevision,
)
from atlas.core.models.dependency import DependencyType, TicketDependency
from atlas.core.models.epic import Epic, EpicStatus
from atlas.core.models.evidence import Evidence, EvidenceType
from atlas.core.models.lesson import Lesson, LessonCategory
from atlas.core.models.operator_action_receipt import (
    OperatorActionOutcome,
    OperatorActionReceipt,
    OperatorActionResultCode,
)
from atlas.core.models.plan_run import PlanRun, PlanRunStatus
from atlas.core.models.planned_ci_pending_recovery import (
    PlannedCIPendingRecovery,
)
from atlas.core.models.planner_call_telemetry import (
    MeasurementAvailability,
    PlannerDigestAlgorithm,
    PlannerLogicalCall,
    PlannerPhysicalTransportAttempt,
    PlannerProcessingDisposition,
    PlannerRetryCategory,
    PlannerTransportDisposition,
    PlanningExecution,
    PlanningExecutionFailureStage,
    PlanningExecutionOutcome,
    PlanningExecutionOutcomeStatus,
    ProviderEvidenceAvailability,
)
from atlas.core.models.pm_recovery import (
    DurablePmBlocker,
    PmBlockerAuthorityKind,
    PmBlockerCode,
    PmBlockerIdentity,
    PmBlockerKind,
    PmBlockerObservationIntent,
    PmBlockerSupersessionKind,
    PmRecoveryEpisode,
    PmRecoveryEpisodeClosureKind,
    PmRecoveryEpisodeIdentity,
    PmStarvedCandidate,
    PmStarvedCandidateRef,
)
from atlas.core.models.pm_sync_receipt import (
    SUCCESSFUL_PM_SYNC_RESULTS,
    PmSyncReceipt,
    PmSyncReceiptResult,
)
from atlas.core.models.product import Product
from atlas.core.models.retrospective_completion import (
    RetrospectiveCompletionDecision,
    RetrospectiveCompletionReason,
    RetrospectiveCompletionReconciliation,
)
from atlas.core.models.tick_failure import TickFailure
from atlas.core.models.ticket import (
    RetrospectiveTransitionOwner,
    Ticket,
    TicketStatus,
    TicketTransitionOwner,
    TicketType,
    ci_pending_transition_owner,
    retrospective_completion_transition_owner,
)
from atlas.core.models.ticket_status_transition import TicketStatusTransition
from atlas.core.models.verification_check import (
    VerificationCheck,
    VerificationCheckType,
)

__all__ = [
    "SUCCESSFUL_PM_SYNC_RESULTS",
    "ADRStatus",
    "AcceptanceSession",
    "AcceptanceSessionBlockingReason",
    "AcceptanceSessionLifecycle",
    "AcceptanceSessionStep",
    "AcceptanceSessionStepState",
    "AdmissionRun",
    "AgentProvider",
    "AgentRun",
    "AgentRunStatus",
    "AnomalyType",
    "ArchitectureDecisionRecord",
    "Atlas280BootstrapRecoveryReceipt",
    "CIHandoffClassification",
    "CIHandoffDecision",
    "CIHandoffReason",
    "CIHandoffReconciliation",
    "ContextPack",
    "DebtItem",
    "DeliveryAdmissionMode",
    "DeliveryAdmissionPolicyRevision",
    "DependencyType",
    "DurablePmBlocker",
    "Epic",
    "EpicStatus",
    "Evidence",
    "EvidenceType",
    "Lesson",
    "LessonCategory",
    "MeasurementAvailability",
    "OperatorActionOutcome",
    "OperatorActionReceipt",
    "OperatorActionResultCode",
    "PlanRun",
    "PlanRunStatus",
    "PlannedCIPendingRecovery",
    "PlannerDigestAlgorithm",
    "PlannerLogicalCall",
    "PlannerPhysicalTransportAttempt",
    "PlannerProcessingDisposition",
    "PlannerRetryCategory",
    "PlannerTransportDisposition",
    "PlanningExecution",
    "PlanningExecutionFailureStage",
    "PlanningExecutionOutcome",
    "PlanningExecutionOutcomeStatus",
    "PmBlockerAuthorityKind",
    "PmBlockerCode",
    "PmBlockerIdentity",
    "PmBlockerKind",
    "PmBlockerObservationIntent",
    "PmBlockerSupersessionKind",
    "PmRecoveryEpisode",
    "PmRecoveryEpisodeClosureKind",
    "PmRecoveryEpisodeIdentity",
    "PmStarvedCandidate",
    "PmStarvedCandidateRef",
    "PmSyncReceipt",
    "PmSyncReceiptResult",
    "Product",
    "ProviderEvidenceAvailability",
    "RetrospectiveCompletionDecision",
    "RetrospectiveCompletionReason",
    "RetrospectiveCompletionReconciliation",
    "RetrospectiveTransitionOwner",
    "TickFailure",
    "Ticket",
    "TicketDependency",
    "TicketStatus",
    "TicketStatusTransition",
    "TicketTransitionOwner",
    "TicketType",
    "VerificationCheck",
    "VerificationCheckType",
    "ci_pending_transition_owner",
    "retrospective_completion_transition_owner",
]
