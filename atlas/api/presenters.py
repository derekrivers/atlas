"""Schema presenters and typed command-to-HTTP outcome mapping."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypedDict, cast

from fastapi import status
from fastapi.responses import JSONResponse

from atlas.api.schemas import (
    AcceptanceActionReceiptSchema,
    AcceptanceCreationReceiptSchema,
    AcceptanceSessionActionResponse,
    AcceptanceSessionCreationResponse,
    AcceptanceSessionErrorResponse,
    AcceptanceSessionReadResponse,
    AcceptanceSessionSchema,
    CriticalPathStepSchema,
    DeliveryAdmissionPolicyConflictResponse,
    DeliveryAdmissionPolicyResponse,
    DeliveryAdmissionPolicySchema,
    DeliveryControlAdmissionSchema,
    DeliveryControlComponentLaneOccupancySchema,
    DeliveryControlDecisionSchema,
    DeliveryControlErrorResponse,
    DeliveryControlHoldReasonSchema,
    DeliveryControlIndeterminateReasonSchema,
    DeliveryControlOccupancySchema,
    DeliveryControlOverCapacityReasonSchema,
    DeliveryControlResponse,
    DeliveryControlRiskLaneOccupancySchema,
    DeliveryControlStatusOccupancySchema,
    DeliveryPolicyActionReceiptSchema,
    DependencyBlockerSchema,
    DependencyCriticalPathResponse,
    DependencyGraphEdgeSchema,
    DependencyGraphNodeSchema,
    DependencyGraphResponse,
    EpicItemSchema,
    EpicsResponse,
    LessonDispositionConflictResponse,
    LessonDispositionErrorResponse,
    LessonDispositionResponse,
    LessonItemSchema,
    LessonsResponse,
    NotReadyReasonSchema,
    OperatorActionReceiptSchema,
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
from atlas.core.models import (
    AcceptanceSession,
    AcceptanceSessionBlockingReason,
    DeliveryAdmissionPolicyRevision,
    Epic,
    Lesson,
    OperatorActionOutcome,
    OperatorActionReceipt,
    OperatorActionResultCode,
    Ticket,
)
from atlas.dependencies import CriticalPath, NotReadyCode
from atlas.dependencies.views import (
    blocked_payload,
    critical_path_payload,
    unlocks_payload,
)
from atlas.orchestration import (
    AcceptanceConfirmationResult,
    AcceptanceConfirmationStatus,
    AcceptanceConfirmationValidationCode,
    AcceptanceEvidencePullResult,
    AcceptanceSessionCreationResult,
    AcceptanceSessionCreationStatus,
    AcceptanceVerificationResult,
    AcceptanceVerificationStatus,
    DeliveryAdmissionPolicyChangeResult,
    DeliveryAdmissionPolicyChangeStatus,
    DeliveryAdmissionPolicyConflictCode,
    DeliveryControlReadStatus,
    DeliveryControlState,
    DependencyGraphState,
    LessonDispositionResult,
    LessonDispositionStatus,
    LiveAcceptanceReadinessResult,
    OperatorActionConflictCode,
    OperatorActionFailureCode,
    OperatorActionGatewayStatus,
    SystemStatus,
    TicketBoardItemState,
    TicketDependencyState,
    TicketEvidenceRecordState,
    TicketReviewState,
    present_operator_action_receipt,
    stored_acceptance_session_status,
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


def _present_lesson(lesson: Lesson) -> LessonItemSchema:
    return LessonItemSchema(
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


def present_lessons(lessons: Sequence[Lesson]) -> LessonsResponse:
    """Present stored lessons without cross-resource assembly."""
    return LessonsResponse(lessons=[_present_lesson(lesson) for lesson in lessons])


def _present_acceptance_session(session: AcceptanceSession) -> AcceptanceSessionSchema:
    return AcceptanceSessionSchema.model_validate(
        stored_acceptance_session_status(session)
    )


def _present_acceptance_receipt(
    receipt: OperatorActionReceipt,
) -> AcceptanceActionReceiptSchema:
    return AcceptanceActionReceiptSchema.model_validate(
        present_operator_action_receipt(receipt)
    )


def _acceptance_error(
    status_code: int,
    detail: str,
    *,
    reasons: Sequence[AcceptanceSessionBlockingReason] = (),
    validation_errors: Sequence[AcceptanceConfirmationValidationCode] = (),
    result_code: OperatorActionResultCode | None = None,
    conflict_code: OperatorActionConflictCode | None = None,
    failure_code: OperatorActionFailureCode | None = None,
    recovery_command: str | None = None,
    ticket_keys: Sequence[str] = (),
) -> JSONResponse:
    response = AcceptanceSessionErrorResponse(
        detail=detail,
        reasons=list(reasons),
        validation_errors=list(validation_errors),
        result_code=result_code,
        conflict_code=conflict_code,
        failure_code=failure_code,
        recovery_command=recovery_command,
        ticket_keys=list(ticket_keys),
    )
    return JSONResponse(
        status_code=status_code,
        content=response.model_dump(mode="json"),
    )


def _acceptance_reason_status(
    reasons: Sequence[AcceptanceSessionBlockingReason],
    *,
    default: int,
) -> int:
    if AcceptanceSessionBlockingReason.SESSION_UNKNOWN in reasons:
        return status.HTTP_404_NOT_FOUND
    if AcceptanceSessionBlockingReason.PR_UNKNOWN in reasons:
        return status.HTTP_404_NOT_FOUND
    if AcceptanceSessionBlockingReason.EXTERNAL_READ_TIMEOUT in reasons:
        return status.HTTP_504_GATEWAY_TIMEOUT
    if any(
        reason
        in {
            AcceptanceSessionBlockingReason.EXTERNAL_READ_FAILED,
            AcceptanceSessionBlockingReason.EXTERNAL_RESPONSE_MALFORMED,
            AcceptanceSessionBlockingReason.EXTERNAL_STATE_INDETERMINATE,
        }
        for reason in reasons
    ):
        return status.HTTP_502_BAD_GATEWAY
    return default


def present_acceptance_session_creation(
    result: AcceptanceSessionCreationResult,
) -> AcceptanceSessionCreationResponse | JSONResponse:
    """Map one typed exact-head preflight/create result to the HTTP contract."""

    if result.status in {
        AcceptanceSessionCreationStatus.CREATED,
        AcceptanceSessionCreationStatus.REPLAYED,
    }:
        if result.session is None:
            return _acceptance_error(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "acceptance session creation failed",
            )
        session = result.session
        return AcceptanceSessionCreationResponse(
            session=_present_acceptance_session(session),
            receipt=AcceptanceCreationReceiptSchema.model_validate(
                {
                    "action": "acceptance_session.create",
                    "target": {"type": "acceptance_session", "id": session.id},
                    "actor": {
                        "type": session.created_by_type,
                        "id": session.created_by_id,
                    },
                    "idempotency_key_identity": (
                        session.creation_idempotency_key_identity
                    ),
                    "outcome": result.status,
                    "completed_at": session.created_at,
                }
            ),
        )
    return _acceptance_error(
        _acceptance_reason_status(result.reasons, default=status.HTTP_409_CONFLICT),
        (
            "acceptance session preflight was refused"
            if result.status is AcceptanceSessionCreationStatus.REFUSED
            else "acceptance session creation conflicted"
        ),
        reasons=result.reasons,
        recovery_command=result.recovery_command,
        ticket_keys=result.ticket_keys,
    )


def present_live_acceptance_readiness(
    result: LiveAcceptanceReadinessResult,
) -> AcceptanceSessionReadResponse | JSONResponse:
    """Present current read authority without turning external failure into true."""

    if result.session is None:
        return _acceptance_error(
            _acceptance_reason_status(
                result.reasons,
                default=status.HTTP_500_INTERNAL_SERVER_ERROR,
            ),
            "acceptance session was not found",
            reasons=result.reasons,
        )
    return AcceptanceSessionReadResponse(
        session=_present_acceptance_session(result.session),
        merge_ready=result.merge_ready,
        reasons=list(result.reasons),
    )


def _acceptance_receipt_error(
    session: AcceptanceSession | None,
    receipt: OperatorActionReceipt,
    *,
    reasons: Sequence[AcceptanceSessionBlockingReason] = (),
) -> JSONResponse:
    del session
    status_code = _acceptance_reason_status(
        reasons,
        default=status.HTTP_409_CONFLICT,
    )
    if receipt.result_code is OperatorActionResultCode.EVIDENCE_RATE_LIMIT_FAILED:
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    elif receipt.result_code is OperatorActionResultCode.EXTERNAL_TIMEOUT:
        status_code = status.HTTP_504_GATEWAY_TIMEOUT
    elif receipt.result_code in {
        OperatorActionResultCode.EVIDENCE_TRANSPORT_FAILED,
        OperatorActionResultCode.EVIDENCE_AUTHENTICATION_FAILED,
        OperatorActionResultCode.EVIDENCE_MALFORMED_SOURCE,
    }:
        status_code = status.HTTP_502_BAD_GATEWAY
    elif receipt.outcome is OperatorActionOutcome.FAILED:
        status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    return _acceptance_error(
        status_code,
        "acceptance session action did not advance",
        reasons=reasons,
        result_code=receipt.result_code,
    )


def present_acceptance_evidence(
    result: AcceptanceEvidencePullResult,
) -> AcceptanceSessionActionResponse | JSONResponse:
    """Map the bounded evidence action and its Phase 13 gateway outcome."""

    if result.session is None:
        return _acceptance_error(
            status.HTTP_404_NOT_FOUND,
            "acceptance session was not found",
            reasons=result.reasons,
        )
    if result.status in {
        OperatorActionGatewayStatus.CONFLICT,
        OperatorActionGatewayStatus.IN_PROGRESS,
    }:
        return _acceptance_error(
            status.HTTP_409_CONFLICT,
            "acceptance session action conflicted",
            reasons=result.reasons,
            conflict_code=(
                result.conflict.code if result.conflict is not None else None
            ),
        )
    if result.status is OperatorActionGatewayStatus.FAILED:
        return _acceptance_error(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "acceptance session action failed",
            reasons=result.reasons,
            failure_code=(result.failure.code if result.failure is not None else None),
        )
    if result.receipt is None:
        return _acceptance_error(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "acceptance session action failed",
        )
    if result.receipt.outcome is not OperatorActionOutcome.SUCCEEDED:
        return _acceptance_receipt_error(
            result.session,
            result.receipt,
            reasons=result.reasons,
        )
    return AcceptanceSessionActionResponse(
        session=_present_acceptance_session(result.session),
        receipt=_present_acceptance_receipt(result.receipt),
        merge_ready=False,
    )


def present_acceptance_confirmation(
    result: AcceptanceConfirmationResult,
) -> AcceptanceSessionActionResponse | JSONResponse:
    """Map strict validation separately from authenticated command outcomes."""

    if result.status is AcceptanceConfirmationStatus.VALIDATION_FAILED:
        unknown = (
            AcceptanceConfirmationValidationCode.SESSION_UNKNOWN
            in result.validation_errors
        )
        return _acceptance_error(
            (
                status.HTTP_404_NOT_FOUND
                if unknown
                else status.HTTP_422_UNPROCESSABLE_CONTENT
            ),
            (
                "acceptance session was not found"
                if unknown
                else "acceptance confirmation request was invalid"
            ),
            validation_errors=result.validation_errors,
        )
    if result.session is None:
        return _acceptance_error(
            status.HTTP_404_NOT_FOUND,
            "acceptance session was not found",
            reasons=result.reasons,
        )
    if (
        result.receipt is not None
        and result.receipt.outcome is not OperatorActionOutcome.SUCCEEDED
    ):
        return _acceptance_receipt_error(
            result.session,
            result.receipt,
            reasons=result.reasons,
        )
    if result.status in {
        AcceptanceConfirmationStatus.CONFLICT,
        AcceptanceConfirmationStatus.IN_PROGRESS,
    }:
        return _acceptance_error(
            status.HTTP_409_CONFLICT,
            "acceptance confirmation conflicted",
            reasons=result.reasons,
            conflict_code=(
                result.conflict.code if result.conflict is not None else None
            ),
        )
    if result.status is AcceptanceConfirmationStatus.FAILED:
        return _acceptance_error(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "acceptance confirmation failed",
            reasons=result.reasons,
            failure_code=(result.failure.code if result.failure is not None else None),
        )
    if result.receipt is None:
        return _acceptance_error(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "acceptance confirmation failed",
            reasons=result.reasons,
        )
    if result.receipt.outcome is not OperatorActionOutcome.SUCCEEDED:
        return _acceptance_receipt_error(
            result.session,
            result.receipt,
            reasons=result.reasons,
        )
    return AcceptanceSessionActionResponse(
        session=_present_acceptance_session(result.session),
        receipt=_present_acceptance_receipt(result.receipt),
        merge_ready=False,
    )


def present_acceptance_verification(
    result: AcceptanceVerificationResult,
) -> AcceptanceSessionActionResponse | JSONResponse:
    """Present exact-head verification without deriving readiness in the API."""

    if result.session is None:
        return _acceptance_error(
            _acceptance_reason_status(
                result.reasons,
                default=status.HTTP_404_NOT_FOUND,
            ),
            "acceptance session was not found",
            reasons=result.reasons,
        )
    if (
        result.receipt is not None
        and result.receipt.outcome is not OperatorActionOutcome.SUCCEEDED
    ):
        return _acceptance_receipt_error(
            result.session,
            result.receipt,
            reasons=result.reasons,
        )
    if result.status in {
        AcceptanceVerificationStatus.CONFLICT,
        AcceptanceVerificationStatus.IN_PROGRESS,
    }:
        return _acceptance_error(
            status.HTTP_409_CONFLICT,
            "acceptance verification conflicted",
            reasons=result.reasons,
            conflict_code=(
                result.conflict.code if result.conflict is not None else None
            ),
        )
    if result.status is AcceptanceVerificationStatus.FAILED and result.receipt is None:
        return _acceptance_error(
            _acceptance_reason_status(
                result.reasons,
                default=status.HTTP_500_INTERNAL_SERVER_ERROR,
            ),
            "acceptance verification failed",
            reasons=result.reasons,
            failure_code=(result.failure.code if result.failure is not None else None),
        )
    if result.receipt is None:
        return _acceptance_error(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "acceptance verification failed",
            reasons=result.reasons,
        )
    if (
        result.receipt.outcome is not OperatorActionOutcome.SUCCEEDED
        or not result.merge_ready
    ):
        return _acceptance_receipt_error(
            result.session,
            result.receipt,
            reasons=result.reasons,
        )
    return AcceptanceSessionActionResponse(
        session=_present_acceptance_session(result.session),
        receipt=_present_acceptance_receipt(result.receipt),
        merge_ready=result.merge_ready,
    )


def _lesson_disposition_error(
    status_code: int,
    detail: str,
) -> JSONResponse:
    response = LessonDispositionErrorResponse(detail=detail)
    return JSONResponse(
        status_code=status_code,
        content=response.model_dump(mode="json"),
    )


def _lesson_disposition_conflict(
    detail: str,
    lesson: Lesson | None,
) -> JSONResponse:
    response = LessonDispositionConflictResponse(
        detail=detail,
        lesson=_present_lesson(lesson) if lesson is not None else None,
    )
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content=response.model_dump(mode="json"),
    )


def present_lesson_disposition(
    result: LessonDispositionResult,
) -> LessonDispositionResponse | JSONResponse:
    """Map one typed disposition outcome without inspecting lesson state."""
    if result.status in {
        LessonDispositionStatus.SUCCEEDED,
        LessonDispositionStatus.REPLAYED,
    }:
        if result.lesson is None or result.receipt is None:
            return _lesson_disposition_error(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "lesson disposition failed",
            )
        return LessonDispositionResponse(
            lesson=_present_lesson(result.lesson),
            receipt=OperatorActionReceiptSchema.model_validate(
                present_operator_action_receipt(result.receipt)
            ),
        )
    if result.status is LessonDispositionStatus.NOT_FOUND:
        return _lesson_disposition_error(
            status.HTTP_404_NOT_FOUND,
            "lesson was not found",
        )
    if result.status is LessonDispositionStatus.INVALID:
        return _lesson_disposition_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "lesson disposition request was invalid",
        )
    if result.status is LessonDispositionStatus.NOT_DRAFT:
        return _lesson_disposition_conflict(
            "lesson is not DRAFT",
            result.lesson,
        )
    if result.status is LessonDispositionStatus.STALE_STATE:
        return _lesson_disposition_conflict(
            "lesson state changed before disposition committed",
            result.lesson,
        )
    if result.status is LessonDispositionStatus.IDEMPOTENCY_CONFLICT:
        return _lesson_disposition_conflict(
            "idempotency key conflicts with an existing command",
            None,
        )
    if result.status is LessonDispositionStatus.IN_PROGRESS:
        return _lesson_disposition_conflict(
            "idempotent command is still in progress",
            None,
        )
    return _lesson_disposition_error(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "lesson disposition failed",
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


def _present_delivery_policy(
    policy: DeliveryAdmissionPolicyRevision,
) -> DeliveryAdmissionPolicySchema:
    return DeliveryAdmissionPolicySchema(
        id=policy.id,
        revision=policy.revision,
        mode=policy.mode,
        approved_symphony_ceiling=policy.approved_symphony_ceiling,
        working_budget=policy.working_budget,
        review_budget=policy.review_budget,
        changes_requested_reserve=policy.changes_requested_reserve,
        risk_lane_limits=list(policy.risk_lane_limits),
        component_lane_limits=list(policy.component_lane_limits),
        created_at=policy.created_at,
    )


def _delivery_control_error(status_code: int, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=DeliveryControlErrorResponse(detail=detail).model_dump(mode="json"),
    )


def present_delivery_control(
    state: DeliveryControlState,
) -> DeliveryControlResponse | JSONResponse:
    """Present one bounded read projection without consulting external state."""

    if (
        state.status is not DeliveryControlReadStatus.AVAILABLE
        or state.policy is None
        or state.occupancy is None
    ):
        return _delivery_control_error(
            status.HTTP_409_CONFLICT,
            "delivery control is unavailable",
        )

    occupancy = state.occupancy
    latest = state.latest_admission
    return DeliveryControlResponse(
        policy=_present_delivery_policy(state.policy),
        last_linear_sync_at=state.last_linear_sync_at,
        occupancy=DeliveryControlOccupancySchema(
            source="materialized_atlas_statuses",
            status_occupancy=[
                DeliveryControlStatusOccupancySchema(
                    status=item.status,
                    count=item.count,
                )
                for item in occupancy.status_occupancy
            ],
            working_occupancy=occupancy.working_occupancy,
            review_occupancy=occupancy.review_occupancy,
            changes_requested_occupancy=occupancy.changes_requested_occupancy,
            changes_requested_reserve_remaining=(
                occupancy.changes_requested_reserve_remaining
            ),
            new_admission_working_capacity=(occupancy.new_admission_working_capacity),
            risk_lane_occupancy=[
                DeliveryControlRiskLaneOccupancySchema(
                    risk_level=item.risk_level,
                    count=item.count,
                    limit=item.limit,
                )
                for item in occupancy.risk_lane_occupancy
            ],
            component_lane_occupancy=[
                DeliveryControlComponentLaneOccupancySchema(
                    component=item.component,
                    count=item.count,
                    limit=item.limit,
                )
                for item in occupancy.component_lane_occupancy
            ],
            over_capacity_reasons=[
                DeliveryControlOverCapacityReasonSchema(
                    dimension=item.dimension,
                    selector=item.selector,
                    count=item.count,
                    limit=item.limit,
                )
                for item in occupancy.over_capacity
            ],
        ),
        latest_admission=(
            None
            if latest is None
            else DeliveryControlAdmissionSchema(
                run_id=latest.run_id,
                policy_revision=latest.policy_revision,
                policy_fingerprint=latest.policy_fingerprint,
                snapshot_fingerprint=latest.snapshot_fingerprint,
                snapshot_observed_at=latest.snapshot_observed_at,
                evaluated_at=latest.evaluated_at,
                selected_ticket_key=latest.selected_ticket_key,
                decision_count=latest.decision_count,
                decisions_truncated=latest.decisions_truncated,
                decisions=[
                    DeliveryControlDecisionSchema(
                        ticket_key=decision.ticket_key,
                        rank=decision.rank,
                        decision=decision.decision,
                        reasons=[
                            DeliveryControlHoldReasonSchema(
                                code=reason.code,
                                source_code=reason.source_code,
                                selector=reason.selector,
                                observed=reason.observed,
                                limit=reason.limit,
                                reserved_capacity=reason.reserved_capacity,
                            )
                            for reason in decision.reasons
                        ],
                    )
                    for decision in latest.decisions
                ],
            )
        ),
        indeterminate_reasons=[
            DeliveryControlIndeterminateReasonSchema(
                reason=item.reason,
                state=item.state,
                admission_run_id=item.admission_run_id,
                ticket_key=item.ticket_key,
                policy_revision=item.policy_revision,
                observed_at=item.observed_at,
            )
            for item in state.indeterminate_reasons
        ],
    )


def _present_policy_receipt(
    receipt: OperatorActionReceipt,
) -> DeliveryPolicyActionReceiptSchema:
    return DeliveryPolicyActionReceiptSchema.model_validate(
        present_operator_action_receipt(receipt)
    )


def _policy_conflict_detail(
    code: DeliveryAdmissionPolicyConflictCode | None,
) -> str:
    if code is DeliveryAdmissionPolicyConflictCode.STALE_REVISION:
        return "expected policy revision is stale"
    if code is DeliveryAdmissionPolicyConflictCode.IDEMPOTENCY_KEY_REUSED:
        return "idempotency key conflicts with an existing command"
    if code is DeliveryAdmissionPolicyConflictCode.IN_PROGRESS:
        return "idempotent command is still in progress"
    return "policy replacement was refused"


def present_delivery_admission_policy_change(
    result: DeliveryAdmissionPolicyChangeResult,
) -> DeliveryAdmissionPolicyResponse | JSONResponse:
    """Map one governed policy result without recomputing policy state."""

    if result.status in {
        DeliveryAdmissionPolicyChangeStatus.APPLIED,
        DeliveryAdmissionPolicyChangeStatus.REPLAYED,
    }:
        if result.policy is None or result.receipt is None:
            return _delivery_control_error(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "policy replacement failed",
            )
        return DeliveryAdmissionPolicyResponse(
            policy=_present_delivery_policy(result.policy),
            receipt=_present_policy_receipt(result.receipt),
        )

    if result.status in {
        DeliveryAdmissionPolicyChangeStatus.CONFLICT,
        DeliveryAdmissionPolicyChangeStatus.REFUSED,
    }:
        response = DeliveryAdmissionPolicyConflictResponse(
            detail=_policy_conflict_detail(result.conflict_code),
            conflict_code=result.conflict_code,
            current_policy=(
                None
                if result.current_policy is None
                else _present_delivery_policy(result.current_policy)
            ),
            receipt=(
                None
                if result.receipt is None
                else _present_policy_receipt(result.receipt)
            ),
        )
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=response.model_dump(mode="json"),
        )

    return _delivery_control_error(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "policy replacement failed",
    )
