"""Pydantic response schemas exposed by the HTTP adapter."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from atlas.core.enums import ActorType, EntityStatus, EvidenceStatus, RiskLevel
from atlas.core.models import (
    AcceptanceSessionBlockingReason,
    AcceptanceSessionLifecycle,
    AcceptanceSessionStep,
    DependencyType,
    EpicStatus,
    EvidenceType,
    LessonCategory,
    OperatorActionOutcome,
    OperatorActionResultCode,
    PmSyncReceiptResult,
    TicketStatus,
    TicketType,
    VerificationCheckType,
)
from atlas.core.models.acceptance_session import (
    AcceptanceAssessmentSnapshot,
    AcceptanceCriterionSnapshot,
    AcceptanceStepSummary,
)
from atlas.core.models.admission_run import AdmissionDecisionType, AdmissionHoldCode
from atlas.core.models.ci_handoff_reconciliation import (
    CIHandoffClassification,
    CIHandoffDecision,
    CIHandoffReason,
)
from atlas.core.models.delivery_admission_policy import (
    MAX_COMPONENT_LANES,
    ComponentLaneLimit,
    DeliveryAdmissionMode,
    DeliveryAdmissionPolicySpec,
    RiskLaneLimit,
)
from atlas.core.models.operator_action_receipt import (
    OperatorActionMetadataKey,
    OperatorActionMetadataValue,
)
from atlas.dependencies import NotReadyCode
from atlas.orchestration import (
    AcceptanceConfirmationValidationCode,
    AcceptanceSessionCreationStatus,
    OperatorActionConflictCode,
    OperatorActionFailureCode,
)
from atlas.orchestration.delivery_admission_policy import (
    DeliveryAdmissionPolicyConflictCode,
)
from atlas.orchestration.delivery_control import (
    DeliveryControlExactBaseStatus,
    DeliveryControlProjectionReason,
    DeliveryControlSnapshotStatus,
    DeliveryControlValidationPlanStatus,
)
from atlas.pm.admission_sync import AdmissionSyncReason
from atlas.pm.delivery_snapshot import (
    OccupancyDimension,
)


class TicketCountResponse(BaseModel):
    """Number of tickets in the Atlas store."""

    count: int


class SessionLoginRequest(BaseModel):
    """Bootstrap-token login request for the single local operator."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"token": "<ATLAS_OPERATOR_TOKEN>"}]},
    )

    token: str = Field(min_length=1)


class SessionLoginResponse(BaseModel):
    """Successful operator login state plus one in-memory CSRF value."""

    authenticated: Literal[True]
    expires_at: datetime
    csrf_token: str


class SessionStateResponse(BaseModel):
    """Current browser session state without credential material."""

    authenticated: bool
    expires_at: datetime | None


class TicketBoardItemSchema(BaseModel):
    """One lean ticket card on the operator board."""

    key: str
    title: str
    status: TicketStatus
    ticket_type: TicketType
    priority: int
    risk_level: RiskLevel
    epic_key: str | None


class TicketBoardResponse(BaseModel):
    """Lean tickets displayed on the operator board."""

    tickets: list[TicketBoardItemSchema]


class EpicItemSchema(BaseModel):
    """One stored epic exposed to the operator."""

    id: UUID
    product_id: UUID
    key: str
    title: str
    description: str
    objective: str
    status: EpicStatus
    priority: int
    risk_level: RiskLevel
    source_anchor: str
    created_by_type: ActorType
    created_by_id: str
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class EpicsResponse(BaseModel):
    """Stored epics for the operator."""

    epics: list[EpicItemSchema]


class TicketDetailResponse(BaseModel):
    """Operator-facing definition and execution state for one ticket."""

    key: str
    title: str
    objective: str
    context: str
    status: TicketStatus
    ticket_type: TicketType
    risk_level: RiskLevel
    priority: int
    estimated_effort: int | None
    relevant_docs: list[str]
    acceptance_criteria: list[str]
    non_goals: list[str]
    implementation_notes: list[str]
    test_requirements: list[str]
    documentation_requirements: list[str]
    definition_of_done: list[str]
    tags: list[str]
    component: str | None
    external_linear_id: str | None
    external_github_issue_id: str | None
    source_anchor: str
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class TicketEvidenceItemSchema(BaseModel):
    """One stored evidence row exposed without raw payload material."""

    model_config = ConfigDict(populate_by_name=True)

    type: EvidenceType
    trust_level: ActorType = Field(alias="tier")
    status: EvidenceStatus
    has_system_pin_triple: bool


class TicketEvidenceResponse(BaseModel):
    """Stored evidence for one ticket."""

    evidence: list[TicketEvidenceItemSchema]


class LessonItemSchema(BaseModel):
    """One stored lesson exposed as a read-only projection."""

    id: UUID
    product_id: UUID
    status: EntityStatus
    category: LessonCategory
    title: str
    problem: str
    solution: str
    outcome: str
    confidence: float | None
    source_ticket_id: UUID
    related_ticket_ids: list[UUID]
    related_adr_ids: list[UUID]
    tags: list[str]
    created_by_type: ActorType
    created_by_id: str
    created_at: datetime
    updated_at: datetime


class LessonsResponse(BaseModel):
    """Stored lessons for the operator."""

    lessons: list[LessonItemSchema]


class PromoteLessonRequest(BaseModel):
    """Exact command payload for promoting one DRAFT lesson."""

    model_config = ConfigDict(extra="forbid")

    confidence: float = Field(
        strict=True,
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )


class RejectLessonRequest(BaseModel):
    """Exact empty command payload for rejecting one DRAFT lesson."""

    model_config = ConfigDict(extra="forbid")


class CreateAcceptanceSessionRequest(BaseModel):
    """Repository policy selector; PR identity remains path/server owned."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    repository: str = Field(min_length=3, max_length=257)


class AcceptanceEvidenceRequest(BaseModel):
    """Strict empty intent for an exact-session evidence pull."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class AcceptanceConfirmationRequestSchema(BaseModel):
    """Minimal confirmation intent; criterion definitions stay server owned."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    criteria_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    criterion_indexes: tuple[StrictInt, ...] = Field(max_length=1_000_000)
    manual_approval: bool = Field(strict=True)


class AcceptanceVerificationRequest(BaseModel):
    """Strict empty intent for exact-head verification."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class AcceptanceRepositoryIdentitySchema(BaseModel):
    """Configured repository owner and name, never a caller-controlled URL."""

    owner: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=128)


class AcceptanceGitIdentitySchema(BaseModel):
    """One exact ref/SHA/repository identity pinned by the application service."""

    ref: str = Field(min_length=1, max_length=256)
    sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    repository: str = Field(min_length=3, max_length=257)


class AcceptancePinnedIdentitySchema(BaseModel):
    """Immutable repository, PR, head and base accepted at preflight."""

    repository: AcceptanceRepositoryIdentitySchema
    pr_number: int = Field(gt=0)
    head: AcceptanceGitIdentitySchema
    base: AcceptanceGitIdentitySchema


class AcceptanceSessionActorSchema(BaseModel):
    """Server-owned single-operator identity stored with a session."""

    type: Literal[ActorType.HUMAN]
    id: Literal["operator"]


class AcceptanceHistoricalReadinessSchema(BaseModel):
    """Stored verification-time history, explicitly not current authority."""

    stored_merge_ready: bool
    reasons: list[AcceptanceSessionBlockingReason] = Field(
        max_length=len(AcceptanceSessionBlockingReason)
    )
    authority: Literal["historical_only"]
    is_current_merge_authority: Literal[False]


class AcceptanceSessionTimestampsSchema(BaseModel):
    """Bounded session lifecycle timestamps."""

    created_at: datetime
    updated_at: datetime
    staled_at: datetime | None


class AcceptanceSessionSchema(BaseModel):
    """Safe complete acceptance-session history plus immutable pinned identity."""

    session_id: UUID
    pinned_identity: AcceptancePinnedIdentitySchema
    close_set: list[str]
    criteria_snapshot: list[AcceptanceCriterionSnapshot]
    criteria_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    initial_assessment: AcceptanceAssessmentSnapshot
    actor: AcceptanceSessionActorSchema
    lifecycle: AcceptanceSessionLifecycle
    steps: dict[AcceptanceSessionStep, AcceptanceStepSummary]
    receipts: list[UUID]
    blocking_reasons: list[AcceptanceSessionBlockingReason] = Field(
        max_length=len(AcceptanceSessionBlockingReason)
    )
    historical_readiness: AcceptanceHistoricalReadinessSchema
    timestamps: AcceptanceSessionTimestampsSchema


class AcceptanceActionTargetSchema(BaseModel):
    """Server-owned acceptance-session target in a durable command receipt."""

    type: Literal["acceptance_session"]
    id: UUID


class AcceptanceActionReceiptSchema(BaseModel):
    """Safe Phase 13 receipt for evidence, confirmation or verification."""

    receipt_id: UUID
    correlation_id: UUID
    action: Literal[
        "acceptance_session.pull_evidence",
        "acceptance_session.confirm",
        "acceptance_session.verify",
    ]
    target: AcceptanceActionTargetSchema
    actor: AcceptanceSessionActorSchema
    idempotency_key_identity: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    request_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    outcome: OperatorActionOutcome
    result_code: OperatorActionResultCode
    result_metadata: dict[
        OperatorActionMetadataKey,
        OperatorActionMetadataValue,
    ]
    before_status: None
    after_status: None
    created_at: datetime
    completed_at: datetime


class AcceptanceCreationReceiptSchema(BaseModel):
    """Durable creation-command identity stored on the new immutable session."""

    action: Literal["acceptance_session.create"]
    target: AcceptanceActionTargetSchema
    actor: AcceptanceSessionActorSchema
    idempotency_key_identity: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    outcome: Literal[
        AcceptanceSessionCreationStatus.CREATED,
        AcceptanceSessionCreationStatus.REPLAYED,
    ]
    completed_at: datetime


class AcceptanceSessionCreationResponse(BaseModel):
    """Created or replayed session and its durable creation receipt."""

    session: AcceptanceSessionSchema
    receipt: AcceptanceCreationReceiptSchema


class AcceptanceSessionActionResponse(BaseModel):
    """Updated session, durable receipt and current write-time readiness flag."""

    session: AcceptanceSessionSchema
    receipt: AcceptanceActionReceiptSchema
    merge_ready: bool


class AcceptanceSessionReadResponse(BaseModel):
    """One fresh read-only readiness assessment with every blocking reason."""

    session: AcceptanceSessionSchema
    merge_ready: bool
    reasons: list[AcceptanceSessionBlockingReason] = Field(
        max_length=len(AcceptanceSessionBlockingReason)
    )


class AcceptanceSessionErrorResponse(BaseModel):
    """Bounded typed error without tokens, raw payloads or foreign messages."""

    detail: str = Field(max_length=256)
    reasons: list[AcceptanceSessionBlockingReason] = Field(
        default_factory=list,
        max_length=len(AcceptanceSessionBlockingReason),
    )
    validation_errors: list[AcceptanceConfirmationValidationCode] = Field(
        default_factory=list,
        max_length=len(AcceptanceConfirmationValidationCode),
    )
    result_code: OperatorActionResultCode | None = None
    conflict_code: OperatorActionConflictCode | None = None
    failure_code: OperatorActionFailureCode | None = None
    recovery_command: str | None = Field(default=None, max_length=512)
    ticket_keys: list[str] = Field(default_factory=list, max_length=32)


class OperatorActionTargetSchema(BaseModel):
    """Bounded lesson target recorded by an operator-action receipt."""

    type: Literal["lesson"]
    id: UUID


class OperatorActionActorSchema(BaseModel):
    """Server-owned actor recorded by an operator-action receipt."""

    type: Literal[ActorType.HUMAN]
    id: Literal["operator"]


class OperatorActionReceiptSchema(BaseModel):
    """Safe, bounded operator-action receipt returned by a command."""

    receipt_id: UUID
    correlation_id: UUID
    action: Literal["lesson.promote", "lesson.reject"]
    target: OperatorActionTargetSchema
    actor: OperatorActionActorSchema
    idempotency_key_identity: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    request_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    outcome: OperatorActionOutcome
    result_code: OperatorActionResultCode
    result_metadata: dict[
        OperatorActionMetadataKey,
        OperatorActionMetadataValue,
    ]
    before_status: EntityStatus | None
    after_status: EntityStatus | None
    created_at: datetime
    completed_at: datetime


class LessonDispositionResponse(BaseModel):
    """Updated safe lesson projection and its durable action receipt."""

    lesson: LessonItemSchema
    receipt: OperatorActionReceiptSchema


class LessonDispositionErrorResponse(BaseModel):
    """Non-secret command error without internal failure material."""

    detail: str


class LessonDispositionConflictResponse(LessonDispositionErrorResponse):
    """Typed command conflict with the safe current lesson when available."""

    lesson: LessonItemSchema | None


class DependencyBlockerSchema(BaseModel):
    """One dependency target currently blocking a ticket."""

    key: str
    code: NotReadyCode


class NotReadyReasonSchema(BaseModel):
    """One readiness condition that is not satisfied."""

    code: NotReadyCode
    message: str
    target: str | None
    status: str | None


class TicketReadinessSchema(BaseModel):
    """Readiness verdict for one ticket."""

    ready: bool
    reasons: list[NotReadyReasonSchema]


class TicketDependenciesResponse(BaseModel):
    """Dependency readiness and reverse dependency state for one ticket."""

    key: str
    blockers: list[DependencyBlockerSchema]
    blocked_by: list[str]
    readiness: TicketReadinessSchema


class CriticalPathStepSchema(BaseModel):
    """One ticket on the graph-wide critical path."""

    key: str
    effort: int
    cumulative_effort: int


class DependencyCriticalPathResponse(BaseModel):
    """Graph-wide critical path in execution order."""

    keys: list[str]
    steps: list[CriticalPathStepSchema]
    total_effort: int


class DependencyGraphNodeSchema(BaseModel):
    """One node in the projected dependency graph."""

    key: str
    status: str
    node_type: str


class DependencyGraphEdgeSchema(BaseModel):
    """One depends_on edge in the projected dependency graph."""

    source: str
    target: str
    dependency_type: DependencyType


class DependencyGraphResponse(BaseModel):
    """Whole projected dependency graph in deterministic order."""

    nodes: list[DependencyGraphNodeSchema]
    edges: list[DependencyGraphEdgeSchema]


class GraphValidationViolationSchema(BaseModel):
    """One typed dependency-graph integrity violation."""

    code: str
    message: str


class GraphValidationErrorResponse(BaseModel):
    """A dependency projection refused because stored graph data is invalid."""

    detail: str
    violations: list[GraphValidationViolationSchema]


class ReviewCheckSchema(BaseModel):
    """One persisted verification outcome in a review item."""

    check_type: VerificationCheckType
    status: EvidenceStatus


class ReviewQueueItemSchema(BaseModel):
    """One ticket awaiting operator review."""

    key: str
    title: str
    status: TicketStatus
    ticket_type: TicketType
    verdict: EvidenceStatus
    checks: list[ReviewCheckSchema]
    has_system_evidence: bool
    has_pr_merged_evidence: bool


class ReviewQueueResponse(BaseModel):
    """Operator review queue."""

    reviews: list[ReviewQueueItemSchema]


class SystemStatusResponse(BaseModel):
    """Singleton operator-facing Atlas instance status."""

    package_version: str
    schema_revision: str | None
    ticket_count: int
    evidence_count: int
    last_linear_sync_at: datetime | None
    last_evidence_pull_at: datetime | None


class DeliveryAdmissionRiskLaneLimitRequest(RiskLaneLimit):
    """Strict risk-lane entry accepted only inside a complete policy request."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class DeliveryAdmissionComponentLaneLimitRequest(ComponentLaneLimit):
    """Strict component-lane entry accepted only inside a complete policy request."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class DeliveryAdmissionPolicyRequest(DeliveryAdmissionPolicySpec):
    """Complete strict policy replacement plus compare-and-set revision."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    expected_revision: int = Field(strict=True, ge=0)
    approved_symphony_ceiling: int = Field(
        strict=True,
        ge=1,
        le=10,
        description=(
            "Active Atlas delivery-policy value; not an independently observed "
            "Symphony or WORKFLOW.md configuration value."
        ),
    )
    risk_lane_limits: tuple[DeliveryAdmissionRiskLaneLimitRequest, ...] = Field(
        max_length=len(RiskLevel)
    )
    component_lane_limits: tuple[DeliveryAdmissionComponentLaneLimitRequest, ...] = (
        Field(max_length=MAX_COMPONENT_LANES)
    )

    def policy_spec(self) -> DeliveryAdmissionPolicySpec:
        """Return only canonical policy fields; command identity stays server-owned."""

        return DeliveryAdmissionPolicySpec(
            mode=self.mode,
            approved_symphony_ceiling=self.approved_symphony_ceiling,
            working_budget=self.working_budget,
            integration_budget=self.integration_budget,
            review_budget=self.review_budget,
            changes_requested_reserve=self.changes_requested_reserve,
            risk_lane_limits=self.risk_lane_limits,
            component_lane_limits=self.component_lane_limits,
        )


class DeliveryAdmissionPolicySchema(BaseModel):
    """Safe active policy revision exposed to the local operator."""

    id: UUID
    revision: int = Field(ge=1)
    mode: DeliveryAdmissionMode
    approved_symphony_ceiling: int = Field(
        ge=1,
        le=10,
        description=(
            "Active Atlas delivery-policy value; not an independently observed "
            "Symphony or WORKFLOW.md configuration value."
        ),
    )
    working_budget: int = Field(ge=1, le=10)
    integration_budget: int = Field(ge=1, le=10)
    review_budget: int = Field(ge=1, le=10)
    changes_requested_reserve: int = Field(ge=0, le=10)
    risk_lane_limits: list[RiskLaneLimit]
    component_lane_limits: list[ComponentLaneLimit]
    created_at: datetime


class DeliveryControlStatusOccupancySchema(BaseModel):
    """Current materialised ticket count for one canonical status."""

    status: TicketStatus
    count: int = Field(ge=0)


class DeliveryControlRiskLaneOccupancySchema(BaseModel):
    """Current stored occupancy for one configured risk lane."""

    risk_level: RiskLevel
    count: int = Field(ge=0)
    limit: int = Field(ge=0, le=10)


class DeliveryControlComponentLaneOccupancySchema(BaseModel):
    """Current stored occupancy for one configured component lane."""

    component: str = Field(min_length=1, max_length=128)
    count: int = Field(ge=0)
    limit: int = Field(ge=0, le=10)


class DeliveryControlProtectedLaneOccupancySchema(BaseModel):
    """Current owners and immutable capacity for one protected lane."""

    lane: str = Field(min_length=1, max_length=128)
    count: int = Field(ge=0)
    limit: int = Field(ge=1, le=10)
    ticket_keys: list[str] = Field(max_length=100)
    operator_declared: bool


class DeliveryControlOverCapacityReasonSchema(BaseModel):
    """One currently breached capacity dimension."""

    dimension: OccupancyDimension
    selector: str | None = Field(default=None, max_length=128)
    count: int = Field(ge=0)
    limit: int = Field(ge=0, le=10)


class DeliveryControlOccupancySchema(BaseModel):
    """Current persisted working, integration and review pressure."""

    source: Literal["materialized_atlas_statuses"]
    status_occupancy: list[DeliveryControlStatusOccupancySchema]
    working_occupancy: int = Field(ge=0)
    integration_occupancy: int = Field(ge=0)
    integration_ticket_keys: list[str] = Field(max_length=100)
    integration_ticket_keys_truncated: bool
    new_admission_integration_capacity: int = Field(ge=0, le=10)
    review_occupancy: int = Field(ge=0)
    changes_requested_occupancy: int = Field(ge=0)
    changes_requested_reserve_remaining: int = Field(ge=0, le=10)
    new_admission_working_capacity: int = Field(ge=0, le=10)
    risk_lane_occupancy: list[DeliveryControlRiskLaneOccupancySchema]
    component_lane_occupancy: list[DeliveryControlComponentLaneOccupancySchema]
    protected_lane_registry_version: str = Field(min_length=1, max_length=128)
    protected_lane_registry_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    protected_lane_state_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    protected_lane_occupancy: list[DeliveryControlProtectedLaneOccupancySchema] = Field(
        max_length=32
    )
    over_capacity_reasons: list[DeliveryControlOverCapacityReasonSchema]


class DeliveryControlBoardIdentitySchema(BaseModel):
    """Pinned last-good board plus the newest refresh attempt."""

    status: DeliveryControlSnapshotStatus
    reasons: list[DeliveryControlProjectionReason] = Field(max_length=8)
    receipt_id: UUID | None
    status_map_fingerprint: str | None = Field(default=None, max_length=128)
    fetched_board_fingerprint: str | None = Field(default=None, max_length=128)
    fetched_board_issue_count: int | None = Field(default=None, ge=0)
    observed_at: datetime | None
    latest_attempt_receipt_id: UUID | None
    latest_attempt_result: PmSyncReceiptResult | None
    latest_attempt_finished_at: datetime | None
    materialized_ticket_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class DeliveryControlEvidenceIdentitySchema(BaseModel):
    """Exact selected evidence set without provider payloads."""

    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_count: int = Field(ge=0)
    evidence_ids: list[UUID] = Field(max_length=100)
    evidence_ids_truncated: bool


class DeliveryControlIntegrationIdentitySchema(BaseModel):
    """Exact integration, registry and assessment identity set."""

    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    reconciliation_count: int = Field(ge=0)
    reconciliation_ids: list[UUID] = Field(max_length=100)
    reconciliation_ids_truncated: bool
    acceptance_session_count: int = Field(ge=0)
    acceptance_session_ids: list[UUID] = Field(max_length=100)
    acceptance_session_ids_truncated: bool
    protected_lane_registry_version: str = Field(min_length=1, max_length=128)
    protected_lane_registry_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    protected_lane_state_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    validation_registry_version: str = Field(min_length=1, max_length=128)
    validation_registry_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class DeliveryControlSnapshotSchema(BaseModel):
    """One coherent policy, board, evidence and integration snapshot."""

    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: DeliveryControlSnapshotStatus
    reasons: list[DeliveryControlProjectionReason] = Field(max_length=32)
    policy_id: UUID
    policy_revision: int = Field(ge=1)
    policy_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    board: DeliveryControlBoardIdentitySchema
    evidence: DeliveryControlEvidenceIdentitySchema
    integration: DeliveryControlIntegrationIdentitySchema


class DeliveryControlCICheckSchema(BaseModel):
    """One persisted typed check result without raw CI material."""

    check_type: VerificationCheckType
    status: EvidenceStatus
    classification: CIHandoffClassification
    evidence_count: int = Field(ge=0)
    evidence_ids: list[UUID] = Field(max_length=32)
    evidence_ids_truncated: bool


class DeliveryControlCIOutcomeSchema(BaseModel):
    """Latest canonical outcome for one CI-pending ticket."""

    reconciliation_id: UUID | None
    classification: CIHandoffClassification
    decision: CIHandoffDecision
    reason: CIHandoffReason | None
    observed_at: datetime | None
    check_results: list[DeliveryControlCICheckSchema] = Field(max_length=16)
    projection_reasons: list[DeliveryControlProjectionReason] = Field(max_length=8)


class DeliveryControlValidationPlanIdentitySchema(BaseModel):
    """Exact plan provenance or a typed fail-closed absence."""

    status: DeliveryControlValidationPlanStatus
    registry_version: str = Field(min_length=1, max_length=128)
    registry_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    plan_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    base_sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{40,64}$")
    head_sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{40,64}$")
    profiles: list[str] = Field(max_length=32)
    reasons: list[DeliveryControlProjectionReason] = Field(max_length=8)


class DeliveryControlExactBaseAssessmentSchema(BaseModel):
    """Stored exact-base status; never a live merge or rebase action."""

    status: DeliveryControlExactBaseStatus
    assessment_id: UUID | None
    head_sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    base_sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    observed_at: datetime | None
    reasons: list[DeliveryControlProjectionReason] = Field(max_length=8)


class DeliveryControlCIPendingTicketSchema(BaseModel):
    """One bounded CI-pending candidate with exact source provenance."""

    ticket_key: str = Field(min_length=1, max_length=128)
    repository_owner: str | None = Field(default=None, max_length=128)
    repository_name: str | None = Field(default=None, max_length=128)
    pr_number: int | None = Field(default=None, gt=0)
    head_sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    outcome: DeliveryControlCIOutcomeSchema
    validation_plan: DeliveryControlValidationPlanIdentitySchema
    exact_base: DeliveryControlExactBaseAssessmentSchema


class DeliveryControlProtectedLaneHoldSchema(BaseModel):
    """One bounded persisted protected-lane admission hold."""

    ticket_key: str = Field(min_length=1, max_length=128)
    lane: str = Field(min_length=1, max_length=128)
    observed: int | None = Field(default=None, ge=0, le=1_000_000)
    limit: int | None = Field(default=None, ge=0, le=1_000_000)
    owner_ticket_keys: list[str] = Field(max_length=100)


class DeliveryControlHoldReasonSchema(BaseModel):
    """One distinct typed hold reason without raw external identities."""

    code: AdmissionHoldCode
    source_code: str | None = Field(default=None, max_length=128)
    selector: str | None = Field(default=None, max_length=128)
    observed: int | None = Field(default=None, ge=0, le=1_000_000)
    limit: int | None = Field(default=None, ge=0, le=1_000_000)
    reserved_capacity: int | None = Field(default=None, ge=0, le=1_000_000)
    owner_ticket_keys: list[str] = Field(default_factory=list, max_length=100)


class DeliveryControlRankInputsSchema(BaseModel):
    """Fixed secret-free inputs that produced one deterministic candidate rank."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    unlock_count: int = Field(ge=0)
    critical_path_member: bool
    critical_path_position: int | None = Field(ge=0)
    priority: int = Field(ge=-2147483648, le=2147483647)
    risk_level: RiskLevel
    risk_severity: int = Field(ge=0, le=3)
    continuously_eligible_since: datetime
    continuously_eligible_age_microseconds: int = Field(ge=0)


class DeliveryControlDecisionSchema(BaseModel):
    """One candidate decision from the latest immutable admission run."""

    ticket_key: str = Field(min_length=1, max_length=128)
    rank: int = Field(ge=1, le=1_000_000)
    rank_inputs: DeliveryControlRankInputsSchema
    decision: AdmissionDecisionType
    reasons: list[DeliveryControlHoldReasonSchema]
    protected_lanes: list[str]
    protected_lane_registry_version: str | None = Field(default=None, max_length=128)
    protected_lane_registry_fingerprint: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )


class DeliveryControlAdmissionSchema(BaseModel):
    """Bounded latest admission-run state."""

    run_id: UUID
    policy_revision: int = Field(ge=1)
    policy_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot_observed_at: datetime
    evaluated_at: datetime
    selected_ticket_key: str | None = Field(default=None, max_length=128)
    decision_count: int = Field(ge=0)
    decisions_truncated: bool
    decisions: list[DeliveryControlDecisionSchema]


class DeliveryControlIndeterminateReasonSchema(BaseModel):
    """One unresolved durable external-write fence."""

    reason: AdmissionSyncReason
    state: Literal["pending", "indeterminate"]
    admission_run_id: UUID
    ticket_key: str = Field(min_length=1, max_length=128)
    policy_revision: int = Field(ge=1)
    observed_at: datetime


class DeliveryControlResponse(BaseModel):
    """Authenticated, observational delivery policy and admission status."""

    policy: DeliveryAdmissionPolicySchema
    last_linear_sync_at: datetime | None
    snapshot: DeliveryControlSnapshotSchema
    occupancy: DeliveryControlOccupancySchema
    ci_pending_ticket_count: int = Field(ge=0)
    ci_pending_tickets_truncated: bool
    ci_pending_tickets: list[DeliveryControlCIPendingTicketSchema] = Field(
        max_length=100
    )
    protected_lane_holds: list[DeliveryControlProtectedLaneHoldSchema] = Field(
        max_length=3_200
    )
    latest_admission: DeliveryControlAdmissionSchema | None
    indeterminate_reasons: list[DeliveryControlIndeterminateReasonSchema]


class DeliveryPolicyActionTargetSchema(BaseModel):
    """Server-selected product target for a policy command receipt."""

    type: Literal["product"]
    id: UUID


class DeliveryPolicyActionReceiptSchema(BaseModel):
    """Bounded append-only receipt for one policy replacement."""

    receipt_id: UUID
    correlation_id: UUID
    action: Literal["delivery_admission_policy.revise"]
    target: DeliveryPolicyActionTargetSchema
    actor: OperatorActionActorSchema
    idempotency_key_identity: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    request_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    outcome: OperatorActionOutcome
    result_code: OperatorActionResultCode
    result_metadata: dict[
        OperatorActionMetadataKey,
        OperatorActionMetadataValue,
    ]
    before_status: None
    after_status: None
    created_at: datetime
    completed_at: datetime


class DeliveryAdmissionPolicyResponse(BaseModel):
    """Applied or replayed policy revision and its original receipt."""

    policy: DeliveryAdmissionPolicySchema
    receipt: DeliveryPolicyActionReceiptSchema


class DeliveryControlErrorResponse(BaseModel):
    """Bounded delivery-control error without internal failure material."""

    detail: str


class DeliveryAdmissionPolicyConflictResponse(DeliveryControlErrorResponse):
    """Policy conflict with safe server-owned current state."""

    conflict_code: DeliveryAdmissionPolicyConflictCode | None
    current_policy: DeliveryAdmissionPolicySchema | None
    receipt: DeliveryPolicyActionReceiptSchema | None
