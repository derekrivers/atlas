"""Pydantic response schemas exposed by the HTTP adapter."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from atlas.core.enums import ActorType, EntityStatus, EvidenceStatus, RiskLevel
from atlas.core.models import (
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
from atlas.core.models.operator_action_receipt import (
    OperatorActionMetadataKey,
    OperatorActionMetadataValue,
)
from atlas.dependencies import NotReadyCode


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
