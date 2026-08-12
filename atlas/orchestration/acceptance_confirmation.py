"""Governed confirmation of one exact-head acceptance session."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from atlas.core.enums import ActorType
from atlas.core.models import (
    AcceptanceSession,
    AcceptanceSessionBlockingReason,
    AcceptanceSessionLifecycle,
    AcceptanceSessionStep,
    AcceptanceSessionStepState,
    Evidence,
    OperatorActionOutcome,
    OperatorActionReceipt,
    OperatorActionResultCode,
    Ticket,
)
from atlas.core.models.acceptance_session import AcceptanceStepSummary
from atlas.github import GitHubAPIError, GitHubClient, GitHubTimeoutError
from atlas.orchestration.acceptance_sessions import (
    TicketLookup,
    compare_acceptance_session_freshness,
)
from atlas.orchestration.confirm import build_confirmation_records
from atlas.orchestration.operator_actions import (
    OperatorActionCommandContext,
    OperatorActionCommandResult,
    OperatorActionConflict,
    OperatorActionEntityLoad,
    OperatorActionEnvelope,
    OperatorActionFailure,
    OperatorActionGateway,
    OperatorActionGatewayStatus,
    OperatorActionMutation,
    canonical_request_fingerprint,
)
from atlas.orchestration.pr_integration import (
    PRIntegrationAssessment,
    assess_pr_integration,
)
from atlas.storage import AcceptanceSessionRepo, Database
from atlas.storage.tables import AcceptanceSessionRow

ACTION = "acceptance_session.confirm"
TARGET_TYPE = "acceptance_session"
OPERATOR_ID = "operator"

AssessmentService = Callable[[GitHubClient, str, str, int], PRIntegrationAssessment]
Clock = Callable[[], datetime]
IdFactory = Callable[[], UUID]


class AcceptanceConfirmationRequest(BaseModel):
    """Operator intent only; all record identity is resolved server-side."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    session_id: UUID
    criteria_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    criterion_indexes: tuple[StrictInt, ...]
    manual_approval: StrictBool


class AcceptanceConfirmationValidationCode(StrEnum):
    """Closed validation vocabulary; validation performs no write."""

    SESSION_UNKNOWN = "session_unknown"
    CRITERIA_FINGERPRINT_MISMATCH = "criteria_fingerprint_mismatch"
    MANUAL_APPROVAL_REQUIRED = "manual_approval_required"
    MISSING_CRITERION_INDEX = "missing_criterion_index"
    DUPLICATE_CRITERION_INDEX = "duplicate_criterion_index"
    UNKNOWN_CRITERION_INDEX = "unknown_criterion_index"
    EXTRA_CRITERION_INDEX = "extra_criterion_index"


class AcceptanceConfirmationStatus(StrEnum):
    """Typed action result independent of any future HTTP mapping."""

    CONFIRMED = "confirmed"
    REPLAYED = "replayed"
    VALIDATION_FAILED = "validation_failed"
    STALE = "stale"
    REFUSED = "refused"
    CONFLICT = "conflict"
    IN_PROGRESS = "in_progress"
    FAILED = "failed"


@dataclass(frozen=True)
class AcceptanceConfirmationResult:
    """Bounded result carrying the durable receipt when one was committed."""

    status: AcceptanceConfirmationStatus
    session: AcceptanceSession | None = None
    receipt: OperatorActionReceipt | None = None
    reasons: tuple[AcceptanceSessionBlockingReason, ...] = ()
    validation_errors: tuple[AcceptanceConfirmationValidationCode, ...] = ()
    conflict: OperatorActionConflict | None = None
    failure: OperatorActionFailure | None = None


class AcceptanceSessionConfirmationService:
    """Validate intent, re-read live state and atomically confirm one session."""

    def __init__(
        self,
        *,
        db: Database,
        github_client: GitHubClient,
        ticket_lookup: TicketLookup,
        clock: Clock,
        evidence_id_factory: IdFactory = uuid4,
        assessment_service: AssessmentService | None = None,
        gateway: OperatorActionGateway | None = None,
    ) -> None:
        self._repository = AcceptanceSessionRepo(db)
        self._github_client = github_client
        self._ticket_lookup = ticket_lookup
        self._evidence_id_factory = evidence_id_factory
        self._assessment_service = assessment_service or assess_pr_integration
        self._gateway = gateway or OperatorActionGateway(db, clock=clock)

    def confirm(
        self,
        request: AcceptanceConfirmationRequest,
        *,
        idempotency_key: str,
    ) -> AcceptanceConfirmationResult:
        """Confirm the complete pinned set without accepting caller definitions."""

        session = self._repository.get(request.session_id)
        if session is None:
            return AcceptanceConfirmationResult(
                status=AcceptanceConfirmationStatus.VALIDATION_FAILED,
                validation_errors=(
                    AcceptanceConfirmationValidationCode.SESSION_UNKNOWN,
                ),
            )

        validation_errors = _validate_request(request, session)
        if validation_errors:
            return AcceptanceConfirmationResult(
                status=AcceptanceConfirmationStatus.VALIDATION_FAILED,
                session=session,
                validation_errors=validation_errors,
            )

        payload = {
            "criteria_fingerprint": request.criteria_fingerprint,
            "criterion_indexes": sorted(request.criterion_indexes),
            "manual_approval": request.manual_approval,
        }
        envelope = OperatorActionEnvelope(
            action=ACTION,
            target_type=TARGET_TYPE,
            target_id=str(request.session_id),
            created_by_type=ActorType.HUMAN,
            created_by_id=OPERATOR_ID,
            idempotency_key=idempotency_key,
            request_fingerprint=canonical_request_fingerprint(
                action=ACTION,
                target_type=TARGET_TYPE,
                target_id=str(request.session_id),
                payload=payload,
            ),
        )
        gateway_result = self._gateway.execute(
            envelope,
            self._command,
            loads=(
                OperatorActionEntityLoad(
                    "session",
                    AcceptanceSessionRow,
                    request.session_id,
                    for_update=True,
                ),
            ),
        )
        stored = self._repository.get(request.session_id)
        if gateway_result.status is OperatorActionGatewayStatus.REPLAYED:
            return AcceptanceConfirmationResult(
                status=AcceptanceConfirmationStatus.REPLAYED,
                session=stored,
                receipt=gateway_result.receipt,
                reasons=_receipt_reasons(stored, gateway_result.receipt),
            )
        if gateway_result.status is OperatorActionGatewayStatus.CONFLICT:
            return AcceptanceConfirmationResult(
                status=AcceptanceConfirmationStatus.CONFLICT,
                session=stored,
                conflict=gateway_result.conflict,
            )
        if gateway_result.status is OperatorActionGatewayStatus.IN_PROGRESS:
            return AcceptanceConfirmationResult(
                status=AcceptanceConfirmationStatus.IN_PROGRESS,
                session=stored,
                conflict=gateway_result.conflict,
            )
        if gateway_result.status is OperatorActionGatewayStatus.FAILED:
            return AcceptanceConfirmationResult(
                status=AcceptanceConfirmationStatus.FAILED,
                session=stored,
                failure=gateway_result.failure,
            )

        receipt = gateway_result.receipt
        assert receipt is not None
        status = AcceptanceConfirmationStatus.REFUSED
        if receipt.outcome is OperatorActionOutcome.SUCCEEDED:
            status = AcceptanceConfirmationStatus.CONFIRMED
        elif receipt.result_code is OperatorActionResultCode.STALE_STATE:
            status = AcceptanceConfirmationStatus.STALE
        elif receipt.outcome is OperatorActionOutcome.CONFLICT:
            status = AcceptanceConfirmationStatus.CONFLICT
        return AcceptanceConfirmationResult(
            status=status,
            session=stored,
            receipt=receipt,
            reasons=_receipt_reasons(stored, receipt),
        )

    def _command(
        self, context: OperatorActionCommandContext
    ) -> OperatorActionCommandResult:
        row = context.entity("session", AcceptanceSessionRow)
        if row is None:
            return _refused_command(OperatorActionResultCode.ACTION_REFUSED)

        current = AcceptanceSession.model_validate(row, from_attributes=True)
        if current.lifecycle is AcceptanceSessionLifecycle.STALE:
            return _refused_command(OperatorActionResultCode.STALE_STATE)
        if current.lifecycle is not AcceptanceSessionLifecycle.EVIDENCE_READY:
            if current.lifecycle in {
                AcceptanceSessionLifecycle.CONFIRMATIONS_READY,
                AcceptanceSessionLifecycle.VERIFICATION_PASSED,
                AcceptanceSessionLifecycle.MERGE_READY,
            }:
                return _refused_command(OperatorActionResultCode.ACTION_CONFLICT)
            return _refused_command(OperatorActionResultCode.ACTION_REFUSED)

        try:
            live_assessment: PRIntegrationAssessment | None = self._assessment_service(
                self._github_client,
                current.repository_owner,
                current.repository_name,
                current.pr_number,
            )
        except (GitHubTimeoutError, TimeoutError):
            return _failed_command(OperatorActionResultCode.EXTERNAL_TIMEOUT)
        except GitHubAPIError:
            live_assessment = None

        live_tickets = tuple(
            ticket
            for key in current.close_set
            if (ticket := self._ticket_lookup.get_by_key(key)) is not None
        )
        reasons = compare_acceptance_session_freshness(
            current,
            live_assessment,
            live_tickets,
        )
        occurred_at = max(context.created_at, current.updated_at)
        if reasons:
            stale = _stale_session(current, reasons, occurred_at=occurred_at)
            _apply_mutable_session_fields(row, stale)
            return OperatorActionCommandResult(
                outcome=OperatorActionOutcome.REFUSED,
                result_code=OperatorActionResultCode.STALE_STATE,
                result_metadata={"affected_count": 0, "changed": True},
                mutations=(OperatorActionMutation(row),),
            )

        records = _confirmation_records(
            current,
            live_tickets,
            now=occurred_at,
            new_id=self._evidence_id_factory,
        )
        advanced = _advance_confirmations(
            current,
            receipt_id=context.receipt_id,
            occurred_at=occurred_at,
        )
        _apply_mutable_session_fields(row, advanced)
        return OperatorActionCommandResult(
            outcome=OperatorActionOutcome.SUCCEEDED,
            result_code=OperatorActionResultCode.ACTION_SUCCEEDED,
            result_metadata={"affected_count": len(records), "changed": True},
            mutations=(OperatorActionMutation(row),),
            evidence_appends=records,
        )


def _validate_request(
    request: AcceptanceConfirmationRequest,
    session: AcceptanceSession,
) -> tuple[AcceptanceConfirmationValidationCode, ...]:
    errors: list[AcceptanceConfirmationValidationCode] = []
    if request.criteria_fingerprint != session.criteria_fingerprint:
        errors.append(
            AcceptanceConfirmationValidationCode.CRITERIA_FINGERPRINT_MISMATCH
        )
    if request.manual_approval is not True:
        errors.append(AcceptanceConfirmationValidationCode.MANUAL_APPROVAL_REQUIRED)

    expected = set(range(len(session.criteria_snapshot)))
    submitted = tuple(request.criterion_indexes)
    counts = Counter(submitted)
    submitted_set = set(submitted)
    if expected - submitted_set:
        errors.append(AcceptanceConfirmationValidationCode.MISSING_CRITERION_INDEX)
    if any(count > 1 for count in counts.values()):
        errors.append(AcceptanceConfirmationValidationCode.DUPLICATE_CRITERION_INDEX)
    if submitted_set - expected:
        errors.append(AcceptanceConfirmationValidationCode.UNKNOWN_CRITERION_INDEX)
    if len(submitted) > len(expected):
        errors.append(AcceptanceConfirmationValidationCode.EXTRA_CRITERION_INDEX)
    return tuple(errors)


def _receipt_reasons(
    session: AcceptanceSession | None,
    receipt: OperatorActionReceipt | None,
) -> tuple[AcceptanceSessionBlockingReason, ...]:
    if (
        session is None
        or receipt is None
        or receipt.result_code is not OperatorActionResultCode.STALE_STATE
    ):
        return ()
    return session.blocking_reasons


def _confirmation_records(
    session: AcceptanceSession,
    live_tickets: Sequence[Ticket],
    *,
    now: datetime,
    new_id: IdFactory,
) -> tuple[Evidence, ...]:
    tickets_by_key = {ticket.key: ticket for ticket in live_tickets}
    records: list[Evidence] = []
    for key in session.close_set:
        ticket = tickets_by_key[key]
        criteria = tuple(
            criterion.text
            for criterion in session.criteria_snapshot
            if criterion.ticket_key == key
        )
        records.extend(
            build_confirmation_records(
                ticket,
                confirmed_criteria=criteria,
                manual_approval=True,
                head_commit=session.head_sha,
                product_id=ticket.product_id,
                operator_id=OPERATOR_ID,
                now=now,
                new_id=new_id,
            )
        )
    return tuple(records)


def _advance_confirmations(
    session: AcceptanceSession,
    *,
    receipt_id: UUID,
    occurred_at: datetime,
) -> AcceptanceSession:
    summaries = dict(session.step_summaries)
    existing = summaries[AcceptanceSessionStep.CONFIRMATIONS]
    summaries[AcceptanceSessionStep.CONFIRMATIONS] = AcceptanceStepSummary(
        state=AcceptanceSessionStepState.COMPLETE,
        receipt_ids=tuple(dict.fromkeys((*existing.receipt_ids, receipt_id))),
        occurred_at=occurred_at,
    )
    historical = tuple(
        reason
        for reason in session.historical_readiness_reasons
        if reason is not AcceptanceSessionBlockingReason.CONFIRMATIONS_NOT_READY
    )
    return AcceptanceSession.model_validate(
        session.model_dump()
        | {
            "lifecycle": AcceptanceSessionLifecycle.CONFIRMATIONS_READY,
            "step_summaries": summaries,
            "historical_readiness_reasons": historical,
            "updated_at": occurred_at,
        }
    )


def _stale_session(
    session: AcceptanceSession,
    reasons: tuple[AcceptanceSessionBlockingReason, ...],
    *,
    occurred_at: datetime,
) -> AcceptanceSession:
    blocking = tuple(dict.fromkeys((*session.blocking_reasons, *reasons)))
    historical = tuple(
        dict.fromkeys(
            (
                *session.historical_readiness_reasons,
                AcceptanceSessionBlockingReason.SESSION_STALE,
                *reasons,
            )
        )
    )
    return AcceptanceSession.model_validate(
        session.model_dump()
        | {
            "lifecycle": AcceptanceSessionLifecycle.STALE,
            "blocking_reasons": blocking,
            "historical_readiness_reasons": historical,
            "updated_at": occurred_at,
            "staled_at": occurred_at,
        }
    )


def _apply_mutable_session_fields(
    row: AcceptanceSessionRow,
    session: AcceptanceSession,
) -> None:
    json_payload = session.model_dump(mode="json")
    row.lifecycle = session.lifecycle.value
    row.step_summaries = json_payload["step_summaries"]
    row.blocking_reasons = json_payload["blocking_reasons"]
    row.stored_merge_ready = session.stored_merge_ready
    row.historical_readiness_reasons = json_payload["historical_readiness_reasons"]
    row.updated_at = session.updated_at
    row.staled_at = session.staled_at


def _refused_command(
    result_code: OperatorActionResultCode,
) -> OperatorActionCommandResult:
    outcome = (
        OperatorActionOutcome.CONFLICT
        if result_code is OperatorActionResultCode.ACTION_CONFLICT
        else OperatorActionOutcome.REFUSED
    )
    return OperatorActionCommandResult(outcome=outcome, result_code=result_code)


def _failed_command(
    result_code: OperatorActionResultCode,
) -> OperatorActionCommandResult:
    return OperatorActionCommandResult(
        outcome=OperatorActionOutcome.FAILED,
        result_code=result_code,
        result_metadata={"affected_count": 0, "changed": False},
    )
