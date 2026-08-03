"""Transactional lesson disposition service shared by CLI and HTTP adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from atlas.core.enums import ActorType, EntityStatus
from atlas.core.models import (
    Lesson,
    OperatorActionOutcome,
    OperatorActionReceipt,
    OperatorActionResultCode,
)
from atlas.learning.disposition import (
    LessonDispositionCommand,
    LessonDispositionDecision,
    LessonDispositionDecisionStatus,
    PromoteLesson,
    decide_lesson_disposition,
    validate_lesson_disposition_command,
)
from atlas.orchestration.operator_actions import (
    Clock,
    OperatorActionCommandContext,
    OperatorActionCommandResult,
    OperatorActionConflictCode,
    OperatorActionEntityLoad,
    OperatorActionEnvelope,
    OperatorActionFailureCode,
    OperatorActionGateway,
    OperatorActionGatewayResult,
    OperatorActionGatewayStatus,
    OperatorActionMutation,
    canonical_request_fingerprint,
)
from atlas.storage import Database
from atlas.storage.tables import LessonRow

_LESSON_LOAD = "lesson"
_OPERATOR_ACTOR_TYPE = ActorType.HUMAN
_OPERATOR_ACTOR_ID = "operator"


@dataclass(frozen=True, slots=True)
class LessonDispositionCommandContext:
    """Trusted command metadata resolved by a presentation adapter."""

    created_by_type: ActorType
    created_by_id: str
    idempotency_key: str

    @classmethod
    def operator(cls, idempotency_key: str) -> LessonDispositionCommandContext:
        """Build the ADR-0009 single-operator context for a local adapter."""

        return cls(_OPERATOR_ACTOR_TYPE, _OPERATOR_ACTOR_ID, idempotency_key)


class LessonDispositionStatus(StrEnum):
    """Typed service outcomes for transport-independent presentation."""

    SUCCEEDED = "succeeded"
    REPLAYED = "replayed"
    INVALID = "invalid"
    NOT_FOUND = "not_found"
    NOT_DRAFT = "not_draft"
    STALE_STATE = "stale_state"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    IN_PROGRESS = "in_progress"
    COMMAND_FAILED = "command_failed"
    RECEIPT_PERSISTENCE_FAILED = "receipt_persistence_failed"
    STORAGE_FAILED = "storage_failed"


@dataclass(frozen=True)
class LessonDispositionResult:
    """One safe command outcome consumable by CLI and future HTTP presenters."""

    status: LessonDispositionStatus
    lesson: Lesson | None = None
    receipt: OperatorActionReceipt | None = None
    message: str | None = None


def _utc_now() -> datetime:
    return datetime.now(UTC)


class LessonDispositionService:
    """Rule on a DRAFT lesson once through the operator-action gateway."""

    def __init__(
        self,
        db: Database,
        *,
        clock: Clock = _utc_now,
        gateway: OperatorActionGateway | None = None,
    ) -> None:
        self._gateway = gateway or OperatorActionGateway(db, clock=clock)

    def execute(
        self,
        command: LessonDispositionCommand,
        context: LessonDispositionCommandContext,
    ) -> LessonDispositionResult:
        """Validate, load once in the unit of work, CAS, and record the receipt."""

        validation_error = validate_lesson_disposition_command(command)
        if validation_error is not None:
            return LessonDispositionResult(
                status=LessonDispositionStatus.INVALID,
                message=validation_error,
            )
        context_error = _validate_command_context(context)
        if context_error is not None:
            return LessonDispositionResult(
                status=LessonDispositionStatus.INVALID,
                message=context_error,
            )

        action = (
            "lesson.promote" if isinstance(command, PromoteLesson) else "lesson.reject"
        )
        payload = (
            {"confidence": float(command.confidence)}
            if isinstance(command, PromoteLesson)
            else {}
        )
        envelope = OperatorActionEnvelope(
            action=action,
            target_type="lesson",
            target_id=str(command.lesson_id),
            created_by_type=context.created_by_type,
            created_by_id=context.created_by_id,
            idempotency_key=context.idempotency_key,
            request_fingerprint=canonical_request_fingerprint(
                action=action,
                target_type="lesson",
                target_id=str(command.lesson_id),
                payload=payload,
            ),
        )
        decision: LessonDispositionDecision | None = None

        def run_domain_command(
            gateway_context: OperatorActionCommandContext,
        ) -> OperatorActionCommandResult:
            nonlocal decision
            row = gateway_context.entity(_LESSON_LOAD, LessonRow)
            lesson = (
                Lesson.model_validate(row, from_attributes=True)
                if row is not None
                else None
            )
            decision = decide_lesson_disposition(
                command,
                lesson,
                now=gateway_context.created_at,
                actor_type=gateway_context.created_by_type,
                actor_id=gateway_context.created_by_id,
            )
            return _command_result(decision, row)

        gateway_result = self._gateway.execute(
            envelope,
            run_domain_command,
            loads=(
                OperatorActionEntityLoad(
                    name=_LESSON_LOAD,
                    entity_type=LessonRow,
                    entity_id=command.lesson_id,
                ),
            ),
        )
        return _present_gateway_result(gateway_result, decision)


def _validate_command_context(
    context: LessonDispositionCommandContext,
) -> str | None:
    if (
        context.created_by_type is not _OPERATOR_ACTOR_TYPE
        or context.created_by_id != _OPERATOR_ACTOR_ID
    ):
        return "lesson disposition requires the ADR-0009 human/operator actor"
    if (
        not isinstance(context.idempotency_key, str)
        or not context.idempotency_key.strip()
    ):
        return "lesson disposition idempotency key must be non-empty"
    return None


def _command_result(
    decision: LessonDispositionDecision,
    row: LessonRow | None,
) -> OperatorActionCommandResult:
    if decision.status is LessonDispositionDecisionStatus.NOT_FOUND:
        return OperatorActionCommandResult(
            outcome=OperatorActionOutcome.REFUSED,
            result_code=OperatorActionResultCode.ACTION_REFUSED,
            result_metadata={"changed": False},
        )
    if decision.status is LessonDispositionDecisionStatus.NOT_DRAFT:
        assert decision.current_lesson is not None
        return OperatorActionCommandResult(
            outcome=OperatorActionOutcome.REFUSED,
            result_code=OperatorActionResultCode.ACTION_REFUSED,
            result_metadata={"changed": False},
            before_status=decision.current_lesson.status,
            after_status=decision.current_lesson.status,
        )
    if decision.status is not LessonDispositionDecisionStatus.READY or row is None:
        raise ValueError("validated lesson disposition did not produce a write plan")

    assert decision.current_lesson is not None
    assert decision.updated_lesson is not None
    updated = decision.updated_lesson
    row.status = updated.status.value
    row.confidence = updated.confidence
    row.updated_at = updated.updated_at
    metadata: dict[str, object] = {"changed": True}
    if isinstance(updated.confidence, float) and updated.status is EntityStatus.ACTIVE:
        metadata["confidence"] = updated.confidence
    return OperatorActionCommandResult(
        outcome=OperatorActionOutcome.SUCCEEDED,
        result_code=OperatorActionResultCode.ACTION_SUCCEEDED,
        result_metadata=metadata,
        before_status=EntityStatus.DRAFT,
        after_status=updated.status,
        mutations=(
            OperatorActionMutation(
                entity=row,
                expected_values={"status": EntityStatus.DRAFT.value},
                updated_fields=("status", "confidence", "updated_at"),
            ),
        ),
    )


def _present_gateway_result(
    gateway_result: OperatorActionGatewayResult,
    decision: LessonDispositionDecision | None,
) -> LessonDispositionResult:
    status = gateway_result.status
    receipt = gateway_result.receipt
    if status is OperatorActionGatewayStatus.EXECUTED:
        assert decision is not None
        if decision.status is LessonDispositionDecisionStatus.READY:
            return LessonDispositionResult(
                status=LessonDispositionStatus.SUCCEEDED,
                lesson=decision.updated_lesson,
                receipt=receipt,
            )
        if decision.status is LessonDispositionDecisionStatus.NOT_FOUND:
            return LessonDispositionResult(
                status=LessonDispositionStatus.NOT_FOUND,
                receipt=receipt,
                message=decision.message,
            )
        return LessonDispositionResult(
            status=LessonDispositionStatus.NOT_DRAFT,
            lesson=decision.current_lesson,
            receipt=receipt,
            message=decision.message,
        )

    if status is OperatorActionGatewayStatus.REPLAYED:
        replayed_row = gateway_result.loaded_entities.get(_LESSON_LOAD)
        replayed_lesson = (
            Lesson.model_validate(replayed_row, from_attributes=True)
            if isinstance(replayed_row, LessonRow)
            else None
        )
        assert receipt is not None
        if receipt.result_code is OperatorActionResultCode.ACTION_SUCCEEDED:
            return LessonDispositionResult(
                status=LessonDispositionStatus.REPLAYED,
                lesson=replayed_lesson,
                receipt=receipt,
            )
        if receipt.result_code is OperatorActionResultCode.STALE_STATE:
            return LessonDispositionResult(
                status=LessonDispositionStatus.STALE_STATE,
                lesson=replayed_lesson,
                receipt=receipt,
                message="lesson state changed before the disposition committed",
            )
        if receipt.before_status is None:
            return LessonDispositionResult(
                status=LessonDispositionStatus.NOT_FOUND,
                receipt=receipt,
                message="lesson was not found",
            )
        return LessonDispositionResult(
            status=LessonDispositionStatus.NOT_DRAFT,
            lesson=replayed_lesson,
            receipt=receipt,
            message="lesson was not DRAFT",
        )

    conflict = gateway_result.conflict
    if status is OperatorActionGatewayStatus.CONFLICT and conflict is not None:
        if conflict.code is OperatorActionConflictCode.STALE_STATE:
            current = (
                Lesson.model_validate(conflict.current_entity, from_attributes=True)
                if isinstance(conflict.current_entity, LessonRow)
                else None
            )
            return LessonDispositionResult(
                status=LessonDispositionStatus.STALE_STATE,
                lesson=current,
                message="lesson state changed before the disposition committed",
            )
        return LessonDispositionResult(
            status=LessonDispositionStatus.IDEMPOTENCY_CONFLICT,
            message="idempotency key was already used for a different command",
        )
    if status is OperatorActionGatewayStatus.IN_PROGRESS:
        return LessonDispositionResult(
            status=LessonDispositionStatus.IN_PROGRESS,
            message="an action with this idempotency key is still in progress",
        )

    failure = gateway_result.failure
    if failure is not None:
        if failure.code is OperatorActionFailureCode.RECEIPT_COMMIT_FAILED:
            return LessonDispositionResult(
                status=LessonDispositionStatus.RECEIPT_PERSISTENCE_FAILED,
                lesson=decision.current_lesson if decision is not None else None,
                message="operator action receipt could not be persisted",
            )
        if failure.code is OperatorActionFailureCode.STORAGE_FAILED:
            return LessonDispositionResult(
                status=LessonDispositionStatus.STORAGE_FAILED,
                message="operator action storage failed",
            )
    return LessonDispositionResult(
        status=LessonDispositionStatus.COMMAND_FAILED,
        message="lesson disposition command failed",
    )
