"""Pydantic response schemas exposed by the HTTP adapter."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from atlas.core.enums import ActorType, EntityStatus, EvidenceStatus, RiskLevel
from atlas.core.models import (
    DependencyType,
    EpicStatus,
    EvidenceType,
    LessonCategory,
    TicketStatus,
    TicketType,
    VerificationCheckType,
)
from atlas.dependencies import NotReadyCode


class TicketCountResponse(BaseModel):
    """Number of tickets in the Atlas store."""

    count: int


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
