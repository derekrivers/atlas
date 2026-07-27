"""Pure schema presenters for HTTP responses."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypedDict, cast

from atlas.api.schemas import (
    CriticalPathStepSchema,
    DependencyBlockerSchema,
    DependencyCriticalPathResponse,
    DependencyGraphEdgeSchema,
    DependencyGraphNodeSchema,
    DependencyGraphResponse,
    EpicItemSchema,
    EpicsResponse,
    LessonItemSchema,
    LessonsResponse,
    NotReadyReasonSchema,
    ReviewCheckSchema,
    ReviewQueueItemSchema,
    ReviewQueueResponse,
    SystemStatusResponse,
    TicketBoardItemSchema,
    TicketBoardResponse,
    TicketDependenciesResponse,
    TicketDetailResponse,
    TicketEvidenceItemSchema,
    TicketEvidenceResponse,
    TicketReadinessSchema,
)
from atlas.core.keys import natural_key
from atlas.core.models import Epic, Lesson, Ticket
from atlas.dependencies import CriticalPath, NotReadyCode
from atlas.dependencies.views import (
    blocked_payload,
    critical_path_payload,
    unlocks_payload,
)
from atlas.orchestration import (
    DependencyGraphState,
    SystemStatus,
    TicketBoardItemState,
    TicketDependencyState,
    TicketEvidenceRecordState,
    TicketReviewState,
)


class _BlockerPayloadTarget(TypedDict):
    key: str
    code: str


class _CriticalPathPayloadStep(TypedDict):
    key: str
    effort: int
    cumulative_effort: int


def present_ticket_board(states: Sequence[TicketBoardItemState]) -> TicketBoardResponse:
    """Present board state as lean ticket cards."""
    return TicketBoardResponse(
        tickets=[
            TicketBoardItemSchema(
                key=state.key,
                title=state.title,
                status=state.status,
                ticket_type=state.ticket_type,
                priority=state.priority,
                risk_level=state.risk_level,
                epic_key=state.epic_key,
            )
            for state in states
        ]
    )


def present_epics(epics: Sequence[Epic]) -> EpicsResponse:
    """Present stored epics in natural key order."""
    return EpicsResponse(
        epics=[
            EpicItemSchema(
                id=epic.id,
                product_id=epic.product_id,
                key=epic.key,
                title=epic.title,
                description=epic.description,
                objective=epic.objective,
                status=epic.status,
                priority=epic.priority,
                risk_level=epic.risk_level,
                source_anchor=epic.source_anchor,
                created_by_type=epic.created_by_type,
                created_by_id=epic.created_by_id,
                created_at=epic.created_at,
                updated_at=epic.updated_at,
                completed_at=epic.completed_at,
            )
            for epic in sorted(epics, key=lambda record: natural_key(record.key))
        ]
    )


def present_ticket_detail(ticket: Ticket) -> TicketDetailResponse:
    """Present one stored ticket without deriving cross-resource state."""
    return TicketDetailResponse(
        key=ticket.key,
        title=ticket.title,
        objective=ticket.objective,
        context=ticket.context,
        status=ticket.status,
        ticket_type=ticket.ticket_type,
        risk_level=ticket.risk_level,
        priority=ticket.priority,
        estimated_effort=ticket.estimated_effort,
        relevant_docs=ticket.relevant_docs,
        acceptance_criteria=ticket.acceptance_criteria,
        non_goals=ticket.non_goals,
        implementation_notes=ticket.implementation_notes,
        test_requirements=ticket.test_requirements,
        documentation_requirements=ticket.documentation_requirements,
        definition_of_done=ticket.definition_of_done,
        tags=ticket.tags,
        component=ticket.component,
        external_linear_id=ticket.external_linear_id,
        external_github_issue_id=ticket.external_github_issue_id,
        source_anchor=ticket.source_anchor,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
        completed_at=ticket.completed_at,
    )


def present_ticket_evidence(
    records: tuple[TicketEvidenceRecordState, ...],
) -> TicketEvidenceResponse:
    """Present one ticket's stored evidence without exposing raw payloads."""
    return TicketEvidenceResponse(
        evidence=[
            TicketEvidenceItemSchema(
                type=record.evidence_type,
                tier=record.trust_level,
                status=record.status,
                has_system_pin_triple=record.has_system_pin_triple,
            )
            for record in records
        ]
    )


def present_lessons(lessons: Sequence[Lesson]) -> LessonsResponse:
    """Present stored lessons without cross-resource assembly."""
    return LessonsResponse(
        lessons=[
            LessonItemSchema(
                id=lesson.id,
                product_id=lesson.product_id,
                status=lesson.status,
                category=lesson.category,
                title=lesson.title,
                problem=lesson.problem,
                solution=lesson.solution,
                outcome=lesson.outcome,
                confidence=lesson.confidence,
                source_ticket_id=lesson.source_ticket_id,
                related_ticket_ids=lesson.related_ticket_ids,
                related_adr_ids=lesson.related_adr_ids,
                tags=lesson.tags,
                created_by_type=lesson.created_by_type,
                created_by_id=lesson.created_by_id,
                created_at=lesson.created_at,
                updated_at=lesson.updated_at,
            )
            for lesson in lessons
        ]
    )


def present_ticket_dependencies(
    state: TicketDependencyState,
) -> TicketDependenciesResponse:
    """Present one ticket's dependency state."""
    blocker_payload = blocked_payload(state.blockers)
    blocked_by_payload = unlocks_payload(state.blocked_by)
    blocker_targets = cast(
        list[_BlockerPayloadTarget],
        blocker_payload["targets"],
    )
    blocked_by = cast(list[str], blocked_by_payload["dependents"])
    return TicketDependenciesResponse(
        key=state.key,
        blockers=[
            DependencyBlockerSchema(
                key=target["key"],
                code=NotReadyCode(target["code"]),
            )
            for target in blocker_targets
        ],
        blocked_by=blocked_by,
        readiness=TicketReadinessSchema(
            ready=state.readiness.ready,
            reasons=[
                NotReadyReasonSchema(
                    code=reason.code,
                    message=reason.message,
                    target=reason.target,
                    status=reason.status,
                )
                for reason in state.readiness.reasons
            ],
        ),
    )


def present_dependency_critical_path(
    path: CriticalPath,
) -> DependencyCriticalPathResponse:
    """Present the graph-wide dependency critical path."""
    payload = critical_path_payload(path)
    keys = cast(list[str], payload["keys"])
    steps = cast(list[_CriticalPathPayloadStep], payload["steps"])
    total_effort = cast(int, payload["total_effort"])
    return DependencyCriticalPathResponse(
        keys=keys,
        steps=[
            CriticalPathStepSchema(
                key=step["key"],
                effort=step["effort"],
                cumulative_effort=step["cumulative_effort"],
            )
            for step in steps
        ],
        total_effort=total_effort,
    )


def present_dependency_graph(
    graph: DependencyGraphState,
) -> DependencyGraphResponse:
    """Present the whole projected dependency graph."""
    return DependencyGraphResponse(
        nodes=[
            DependencyGraphNodeSchema(
                key=node.key,
                status=node.status,
                node_type=node.node_type,
            )
            for node in graph.nodes
        ],
        edges=[
            DependencyGraphEdgeSchema(
                source=edge.source,
                target=edge.target,
                dependency_type=edge.dependency_type,
            )
            for edge in graph.edges
        ],
    )


def present_review_queue(
    states: tuple[TicketReviewState, ...],
) -> ReviewQueueResponse:
    """Present stored review states as an HTTP response."""
    return ReviewQueueResponse(
        reviews=[
            ReviewQueueItemSchema(
                key=state.key,
                title=state.title,
                status=state.status,
                ticket_type=state.ticket_type,
                verdict=state.verdict,
                checks=[
                    ReviewCheckSchema(
                        check_type=check.check_type,
                        status=check.status,
                    )
                    for check in state.checks
                ],
                has_system_evidence=state.has_system_evidence,
                has_pr_merged_evidence=state.has_pr_merged_evidence,
            )
            for state in states
        ]
    )


def present_system_status(state: SystemStatus) -> SystemStatusResponse:
    """Present the singleton operator system snapshot."""
    return SystemStatusResponse(
        package_version=state.package_version,
        schema_revision=state.schema_revision,
        ticket_count=state.ticket_count,
        evidence_count=state.evidence_count,
        last_linear_sync_at=state.last_linear_sync_at,
        last_evidence_pull_at=state.last_evidence_pull_at,
    )
