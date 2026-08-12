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
    TicketStatus,
    TicketType,
    VerificationCheckType,
)
from atlas.core.models.acceptance_session import (
    AcceptanceAssessmentSnapshot,
    AcceptanceCriterionSnapshot,
    AcceptanceStepSummary,
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
