"""Exact-head acceptance verification and historical merge-readiness action."""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID, uuid4

from atlas.core.enums import ActorType, EvidenceStatus
from atlas.core.models import (
    AcceptanceSession,
    AcceptanceSessionBlockingReason,
    AcceptanceSessionLifecycle,
    AcceptanceSessionStep,
    AcceptanceSessionStepState,
    OperatorActionOutcome,
    OperatorActionReceipt,
    OperatorActionResultCode,
    Ticket,
)
from atlas.core.models.acceptance_session import (
    AcceptanceReadinessAssessment,
    AcceptanceStepSummary,
    AcceptanceVerificationSummary,
)
from atlas.github import (
    GitHubAPIError,
    GitHubClient,
    GitHubMalformedResponseError,
)
from atlas.orchestration.acceptance_sessions import (
    AssessmentService,
    TicketLookup,
    compare_acceptance_session_freshness,
)
from atlas.orchestration.operator_actions import (
    OperatorActionCommandContext,
    OperatorActionCommandResult,
    OperatorActionConflict,
    OperatorActionEntityLoad,
    OperatorActionEnvelope,
    OperatorActionFailure,
    OperatorActionFailureCode,
    OperatorActionGateway,
    OperatorActionGatewayStatus,
    OperatorActionMutation,
    canonical_request_fingerprint,
)
from atlas.orchestration.pr_context import PRContext, resolve_pr_context
from atlas.orchestration.pr_integration import (
    PRIntegrationAssessment,
    assess_pr_integration,
)
from atlas.orchestration.verify import run_verify
from atlas.storage import AcceptanceSessionRepo, Database
from atlas.storage.tables import AcceptanceSessionRow
from atlas.verification import PRVerification, parse_close_set

ACTION_NAME = "acceptance_session.verify"
TARGET_TYPE = "acceptance_session"

_SESSION_SUMMARY_FIELDS = (
    "lifecycle",
    "step_summaries",
    "blocking_reasons",
    "stored_merge_ready",
    "historical_readiness_reasons",
    "updated_at",
    "staled_at",
)


class AcceptanceVerificationStatus(StrEnum):
    """Typed action outcome independent of a future HTTP mapping."""

    MERGE_READY = "merge_ready"
    REPLAYED = "replayed"
    REFUSED = "refused"
    STALE = "stale"
    CONFLICT = "conflict"
    IN_PROGRESS = "in_progress"
    FAILED = "failed"


@dataclass(frozen=True)
class AcceptanceVerificationContext:
    """Authenticated command context; repository and head are session-owned."""

    idempotency_key: str
    created_by_type: ActorType
    created_by_id: str


@dataclass(frozen=True)
class AcceptanceVerificationResult:
    """Bounded verification outcome and its latest stored history."""

    status: AcceptanceVerificationStatus
    merge_ready: bool = False
    session: AcceptanceSession | None = None
    receipt: OperatorActionReceipt | None = None
    reasons: tuple[AcceptanceSessionBlockingReason, ...] = ()
    conflict: OperatorActionConflict | None = None
    failure: OperatorActionFailure | None = None


class VerificationService(Protocol):
    """Canonical in-process verification seam."""

    def __call__(
        self,
        context: PRContext,
        close_set: tuple[str, ...],
        db: Database,
    ) -> PRVerification:
        """Evaluate and record one PR verdict."""


PRContextService = Callable[[str, int, GitHubClient], PRContext]
Clock = Callable[[], datetime]
IdFactory = Callable[[], UUID]


@dataclass
class _SessionGuard:
    lock: threading.Lock
    users: int = 0


_SESSION_GUARDS_LOCK = threading.Lock()
_SESSION_GUARDS: dict[UUID, _SessionGuard] = {}


@contextmanager
def _one_verification_in_flight(session_id: UUID) -> Any:
    """Serialise verifier work for the supported single-process server."""

    with _SESSION_GUARDS_LOCK:
        guard = _SESSION_GUARDS.setdefault(session_id, _SessionGuard(threading.Lock()))
        guard.users += 1
    guard.lock.acquire()
    try:
        yield
    finally:
        guard.lock.release()
        with _SESSION_GUARDS_LOCK:
            guard.users -= 1
            if guard.users == 0:
                _SESSION_GUARDS.pop(session_id, None)


def _canonical_verification(
    context: PRContext,
    close_set: tuple[str, ...],
    db: Database,
) -> PRVerification:
    """Invoke the same in-process engine and persistence path as the CLI."""

    return run_verify(context, close_set, db).verification


class AcceptanceSessionVerificationService:
    """Run canonical verification and atomically store readiness plus receipt."""

    def __init__(
        self,
        *,
        db: Database,
        github_client: GitHubClient,
        ticket_lookup: TicketLookup,
        gateway: OperatorActionGateway,
        clock: Clock,
        assessment_service: AssessmentService | None = None,
        pr_context_service: PRContextService = resolve_pr_context,
        verification_service: VerificationService | None = None,
        verdict_id_factory: IdFactory = uuid4,
    ) -> None:
        self._db = db
        self._repository = AcceptanceSessionRepo(db)
        self._github_client = github_client
        self._ticket_lookup = ticket_lookup
        self._gateway = gateway
        self._clock = clock
        self._assessment_service = assessment_service or assess_pr_integration
        self._pr_context_service = pr_context_service
        self._verification_service = verification_service or _canonical_verification
        self._verdict_id_factory = verdict_id_factory

    def execute(
        self,
        session_id: UUID,
        context: AcceptanceVerificationContext,
    ) -> AcceptanceVerificationResult:
        """Verify only a fresh, evidence-ready and confirmation-ready session."""

        if (
            context.created_by_type is not ActorType.HUMAN
            or context.created_by_id != "operator"
        ):
            raise ValueError("acceptance verification actor must be human/operator")

        target_id = str(session_id)
        envelope = OperatorActionEnvelope(
            action=ACTION_NAME,
            target_type=TARGET_TYPE,
            target_id=target_id,
            created_by_type=context.created_by_type,
            created_by_id=context.created_by_id,
            idempotency_key=context.idempotency_key,
            request_fingerprint=canonical_request_fingerprint(
                action=ACTION_NAME,
                target_type=TARGET_TYPE,
                target_id=target_id,
                payload={},
            ),
        )
        with _one_verification_in_flight(session_id):
            gateway_result = self._gateway.execute_bounded_external(
                envelope,
                self._command,
                loads=(
                    OperatorActionEntityLoad(
                        "acceptance_session", AcceptanceSessionRow, session_id
                    ),
                ),
            )

        stored = self._repository.get(session_id)
        reasons = _result_reasons(stored, gateway_result.failure)
        status = _action_status(gateway_result.status, gateway_result.receipt, stored)
        merge_ready = (
            status
            in {
                AcceptanceVerificationStatus.MERGE_READY,
                AcceptanceVerificationStatus.REPLAYED,
            }
            and stored is not None
            and stored.lifecycle is AcceptanceSessionLifecycle.MERGE_READY
            and stored.stored_merge_ready
            and not reasons
        )
        return AcceptanceVerificationResult(
            status=status,
            merge_ready=merge_ready,
            session=stored,
            receipt=gateway_result.receipt,
            reasons=reasons,
            conflict=gateway_result.conflict,
            failure=gateway_result.failure,
        )

    def _command(
        self, context: OperatorActionCommandContext
    ) -> OperatorActionCommandResult:
        row = context.entity("acceptance_session", AcceptanceSessionRow)
        if row is None:
            return _refused()
        session = AcceptanceSession.model_validate(row, from_attributes=True)

        prerequisite_reasons = _prerequisite_reasons(session)
        if session.lifecycle.is_terminal:
            return _refused(
                conflict=session.lifecycle
                in {
                    AcceptanceSessionLifecycle.BLOCKED,
                    AcceptanceSessionLifecycle.FAILED,
                }
            )
        if session.lifecycle in {
            AcceptanceSessionLifecycle.VERIFICATION_PASSED,
            AcceptanceSessionLifecycle.MERGE_READY,
        }:
            return _refused(conflict=True)

        freshness_reasons, live_tickets, _ = self._freshness(session)
        all_prerequisites = _dedupe((*prerequisite_reasons, *freshness_reasons))
        if all_prerequisites:
            return self._blocked_before_verification(
                row,
                session,
                all_prerequisites,
                receipt_id=context.receipt_id,
                stale=bool(freshness_reasons),
            )

        try:
            pr_context = self._pr_context_service(
                f"{session.repository_owner}/{session.repository_name}",
                session.pr_number,
                self._github_client,
            )
        except Exception as error:
            reasons = _external_failure_reasons(error)
            return self._blocked_before_verification(
                row,
                session,
                reasons,
                receipt_id=context.receipt_id,
                stale=True,
            )

        context_reasons = _pr_context_reasons(session, pr_context)
        just_before_reasons, live_tickets, _ = self._freshness(session)
        before_verifier_reasons = _dedupe((*context_reasons, *just_before_reasons))
        if before_verifier_reasons:
            return self._blocked_before_verification(
                row,
                session,
                before_verifier_reasons,
                receipt_id=context.receipt_id,
                stale=True,
            )

        verdict_id = self._verdict_id_factory()
        try:
            verification = self._verification_service(
                pr_context,
                session.close_set,
                self._db,
            )
        except Exception:
            return self._verification_refusal(
                row,
                session,
                verdict_id=verdict_id,
                verification=None,
                reasons=(AcceptanceSessionBlockingReason.VERIFICATION_MALFORMED,),
                receipt_id=context.receipt_id,
                failed=True,
            )

        verification_reasons = _verification_reasons(
            session,
            verification,
            live_tickets,
        )
        if verification_reasons:
            failed = any(
                reason
                in {
                    AcceptanceSessionBlockingReason.VERIFICATION_FAILED,
                    AcceptanceSessionBlockingReason.VERIFICATION_MALFORMED,
                    AcceptanceSessionBlockingReason.VERIFICATION_CLOSE_SET_MISMATCH,
                    AcceptanceSessionBlockingReason.VERIFIED_HEAD_INVALID,
                    AcceptanceSessionBlockingReason.VERIFIED_HEAD_MISMATCH,
                }
                for reason in verification_reasons
            )
            return self._verification_refusal(
                row,
                session,
                verdict_id=verdict_id,
                verification=verification,
                reasons=verification_reasons,
                receipt_id=context.receipt_id,
                failed=failed,
            )

        final_reasons, _, final_assessment = self._freshness(session)
        verification_summary = _verification_summary(verdict_id, verification)
        assert verification_summary is not None
        if final_reasons or final_assessment is None:
            reasons = final_reasons or (
                AcceptanceSessionBlockingReason.EXTERNAL_STATE_INDETERMINATE,
            )
            return self._post_verification_stale(
                row,
                session,
                verification_summary=verification_summary,
                reasons=reasons,
                receipt_id=context.receipt_id,
            )

        return self._merge_ready(
            row,
            session,
            verification_summary=verification_summary,
            final_assessment=final_assessment,
            receipt_id=context.receipt_id,
        )

    def _freshness(
        self,
        session: AcceptanceSession,
    ) -> tuple[
        tuple[AcceptanceSessionBlockingReason, ...],
        tuple[Ticket, ...],
        PRIntegrationAssessment | None,
    ]:
        extra_reasons: list[AcceptanceSessionBlockingReason] = []
        assessment: PRIntegrationAssessment | None = None
        try:
            candidate = self._assessment_service(
                self._github_client,
                session.repository_owner,
                session.repository_name,
                session.pr_number,
            )
            if not isinstance(candidate, PRIntegrationAssessment):
                raise TypeError("assessment service returned a malformed result")
            assessment = candidate
        except Exception as error:
            extra_reasons.extend(_external_failure_reasons(error))

        tickets, ticket_error = _read_live_tickets(
            self._ticket_lookup,
            session.close_set,
        )
        if ticket_error is not None:
            extra_reasons.extend(_external_failure_reasons(ticket_error))
            live_tickets: Sequence[Ticket] | None = None
        else:
            live_tickets = tickets
        reasons = compare_acceptance_session_freshness(
            session,
            assessment,
            live_tickets,
        )
        return _dedupe((*extra_reasons, *reasons)), tickets, assessment

    def _blocked_before_verification(
        self,
        row: AcceptanceSessionRow,
        session: AcceptanceSession,
        reasons: tuple[AcceptanceSessionBlockingReason, ...],
        *,
        receipt_id: UUID,
        stale: bool,
    ) -> OperatorActionCommandResult:
        occurred_at = self._now_not_before(session.updated_at)
        summaries = dict(session.step_summaries)
        summaries[AcceptanceSessionStep.VERIFICATION] = AcceptanceStepSummary(
            state=AcceptanceSessionStepState.BLOCKED,
            reasons=reasons,
            receipt_ids=(receipt_id,),
            occurred_at=occurred_at,
        )
        update: dict[str, object] = {
            "step_summaries": summaries,
            "historical_readiness_reasons": reasons,
            "updated_at": occurred_at,
        }
        result_code = OperatorActionResultCode.ACTION_REFUSED
        if stale:
            update |= {
                "lifecycle": AcceptanceSessionLifecycle.STALE,
                "blocking_reasons": _dedupe((*session.blocking_reasons, *reasons)),
                "historical_readiness_reasons": _dedupe(
                    (
                        AcceptanceSessionBlockingReason.SESSION_STALE,
                        *reasons,
                    )
                ),
                "staled_at": occurred_at,
            }
            result_code = OperatorActionResultCode.STALE_STATE
        updated = AcceptanceSession.model_validate(
            session.model_copy(update=update).model_dump()
        )
        _apply_session_model(row, updated)
        return OperatorActionCommandResult(
            outcome=OperatorActionOutcome.REFUSED,
            result_code=result_code,
            result_metadata={"changed": True, "affected_count": 1},
            mutations=(_session_mutation(row, session.lifecycle),),
        )

    def _verification_refusal(
        self,
        row: AcceptanceSessionRow,
        session: AcceptanceSession,
        *,
        verdict_id: UUID,
        verification: PRVerification | None,
        reasons: tuple[AcceptanceSessionBlockingReason, ...],
        receipt_id: UUID,
        failed: bool,
    ) -> OperatorActionCommandResult:
        occurred_at = self._now_not_before(session.updated_at)
        summaries = dict(session.step_summaries)
        summaries[AcceptanceSessionStep.VERIFICATION] = AcceptanceStepSummary(
            state=(
                AcceptanceSessionStepState.FAILED
                if failed
                else AcceptanceSessionStepState.BLOCKED
            ),
            reasons=reasons,
            receipt_ids=(receipt_id,),
            occurred_at=occurred_at,
            verification=_verification_summary(verdict_id, verification),
        )
        lifecycle = (
            AcceptanceSessionLifecycle.FAILED
            if failed
            else AcceptanceSessionLifecycle.BLOCKED
        )
        updated = AcceptanceSession.model_validate(
            session.model_copy(
                update={
                    "lifecycle": lifecycle,
                    "step_summaries": summaries,
                    "blocking_reasons": _dedupe((*session.blocking_reasons, *reasons)),
                    "historical_readiness_reasons": reasons,
                    "updated_at": occurred_at,
                }
            ).model_dump()
        )
        _apply_session_model(row, updated)
        return OperatorActionCommandResult(
            outcome=(
                OperatorActionOutcome.FAILED
                if failed
                else OperatorActionOutcome.REFUSED
            ),
            result_code=(
                OperatorActionResultCode.ACTION_FAILED
                if failed
                else OperatorActionResultCode.ACTION_REFUSED
            ),
            result_metadata={"changed": True, "affected_count": 1},
            mutations=(_session_mutation(row, session.lifecycle),),
        )

    def _post_verification_stale(
        self,
        row: AcceptanceSessionRow,
        session: AcceptanceSession,
        *,
        verification_summary: AcceptanceVerificationSummary,
        reasons: tuple[AcceptanceSessionBlockingReason, ...],
        receipt_id: UUID,
    ) -> OperatorActionCommandResult:
        occurred_at = self._now_not_before(session.updated_at)
        summaries = dict(session.step_summaries)
        summaries[AcceptanceSessionStep.VERIFICATION] = AcceptanceStepSummary(
            state=AcceptanceSessionStepState.COMPLETE,
            receipt_ids=(receipt_id,),
            occurred_at=occurred_at,
            verification=verification_summary,
        )
        summaries[AcceptanceSessionStep.READINESS] = AcceptanceStepSummary(
            state=AcceptanceSessionStepState.BLOCKED,
            reasons=reasons,
            receipt_ids=(receipt_id,),
            occurred_at=occurred_at,
        )
        updated = AcceptanceSession.model_validate(
            session.model_copy(
                update={
                    "lifecycle": AcceptanceSessionLifecycle.STALE,
                    "step_summaries": summaries,
                    "blocking_reasons": _dedupe((*session.blocking_reasons, *reasons)),
                    "stored_merge_ready": False,
                    "historical_readiness_reasons": _dedupe(
                        (AcceptanceSessionBlockingReason.SESSION_STALE, *reasons)
                    ),
                    "updated_at": occurred_at,
                    "staled_at": occurred_at,
                }
            ).model_dump()
        )
        _apply_session_model(row, updated)
        return OperatorActionCommandResult(
            outcome=OperatorActionOutcome.REFUSED,
            result_code=OperatorActionResultCode.STALE_STATE,
            result_metadata={"changed": True, "affected_count": 1},
            mutations=(_session_mutation(row, session.lifecycle),),
        )

    def _merge_ready(
        self,
        row: AcceptanceSessionRow,
        session: AcceptanceSession,
        *,
        verification_summary: AcceptanceVerificationSummary,
        final_assessment: PRIntegrationAssessment,
        receipt_id: UUID,
    ) -> OperatorActionCommandResult:
        occurred_at = self._now_not_before(session.updated_at)
        summaries = dict(session.step_summaries)
        summaries[AcceptanceSessionStep.VERIFICATION] = AcceptanceStepSummary(
            state=AcceptanceSessionStepState.COMPLETE,
            receipt_ids=(receipt_id,),
            occurred_at=occurred_at,
            verification=verification_summary,
        )
        summaries[AcceptanceSessionStep.READINESS] = AcceptanceStepSummary(
            state=AcceptanceSessionStepState.COMPLETE,
            receipt_ids=(receipt_id,),
            occurred_at=occurred_at,
            readiness=_readiness_assessment(
                session,
                final_assessment,
                verdict_id=verification_summary.verdict_id,
            ),
        )
        updated = AcceptanceSession.model_validate(
            session.model_copy(
                update={
                    "lifecycle": AcceptanceSessionLifecycle.MERGE_READY,
                    "step_summaries": summaries,
                    "stored_merge_ready": True,
                    "historical_readiness_reasons": (),
                    "updated_at": occurred_at,
                }
            ).model_dump()
        )
        _apply_session_model(row, updated)
        return OperatorActionCommandResult(
            outcome=OperatorActionOutcome.SUCCEEDED,
            result_code=OperatorActionResultCode.ACTION_SUCCEEDED,
            result_metadata={"changed": True, "affected_count": 1},
            mutations=(_session_mutation(row, session.lifecycle),),
        )

    def _now_not_before(self, prior: datetime) -> datetime:
        now = self._clock()
        if now.utcoffset() is None:
            raise ValueError("acceptance verification clock must be timezone-aware")
        return max(now, prior)


def _prerequisite_reasons(
    session: AcceptanceSession,
) -> tuple[AcceptanceSessionBlockingReason, ...]:
    reasons: list[AcceptanceSessionBlockingReason] = []
    if (
        session.step_summaries[AcceptanceSessionStep.EVIDENCE].state
        is not AcceptanceSessionStepState.COMPLETE
    ):
        reasons.append(AcceptanceSessionBlockingReason.EVIDENCE_NOT_READY)
    if (
        session.step_summaries[AcceptanceSessionStep.CONFIRMATIONS].state
        is not AcceptanceSessionStepState.COMPLETE
    ):
        reasons.append(AcceptanceSessionBlockingReason.CONFIRMATIONS_NOT_READY)
    if session.lifecycle is AcceptanceSessionLifecycle.STALE:
        reasons.extend(
            (
                AcceptanceSessionBlockingReason.SESSION_STALE,
                *session.blocking_reasons,
            )
        )
    elif session.lifecycle in {
        AcceptanceSessionLifecycle.BLOCKED,
        AcceptanceSessionLifecycle.FAILED,
    }:
        reasons.extend(session.historical_readiness_reasons)
        reasons.append(AcceptanceSessionBlockingReason.SESSION_NOT_VERIFIABLE)
    elif session.lifecycle in {
        AcceptanceSessionLifecycle.VERIFICATION_PASSED,
        AcceptanceSessionLifecycle.MERGE_READY,
    }:
        reasons.append(AcceptanceSessionBlockingReason.VERIFICATION_ALREADY_COMPLETED)
    elif session.lifecycle is not AcceptanceSessionLifecycle.CONFIRMATIONS_READY:
        reasons.append(AcceptanceSessionBlockingReason.SESSION_NOT_VERIFIABLE)
    return _dedupe(reasons)


def _read_live_tickets(
    ticket_lookup: TicketLookup,
    close_set: tuple[str, ...],
) -> tuple[tuple[Ticket, ...], Exception | None]:
    tickets: list[Ticket] = []
    try:
        for key in close_set:
            ticket = ticket_lookup.get_by_key(key)
            if ticket is not None:
                tickets.append(ticket)
    except Exception as error:
        return tuple(tickets), error
    return tuple(tickets), None


def _external_failure_reasons(
    error: Exception,
) -> tuple[AcceptanceSessionBlockingReason, ...]:
    if isinstance(error, TimeoutError):
        specific = AcceptanceSessionBlockingReason.EXTERNAL_READ_TIMEOUT
    elif isinstance(
        error,
        (GitHubMalformedResponseError, KeyError, TypeError, ValueError),
    ):
        specific = AcceptanceSessionBlockingReason.EXTERNAL_RESPONSE_MALFORMED
    elif isinstance(error, (GitHubAPIError, OSError)):
        specific = AcceptanceSessionBlockingReason.EXTERNAL_READ_FAILED
    else:
        specific = AcceptanceSessionBlockingReason.EXTERNAL_READ_FAILED
    return (
        specific,
        AcceptanceSessionBlockingReason.EXTERNAL_STATE_INDETERMINATE,
    )


def _pr_context_reasons(
    session: AcceptanceSession,
    context: PRContext,
) -> tuple[AcceptanceSessionBlockingReason, ...]:
    reasons: list[AcceptanceSessionBlockingReason] = []
    if (
        context.owner != session.repository_owner
        or context.repo != session.repository_name
    ):
        reasons.append(AcceptanceSessionBlockingReason.REPOSITORY_MISMATCH)
    live_close_set = tuple(
        sorted(
            set(
                parse_close_set(
                    context.pull_request.get("title"),
                    context.pull_request.get("body"),
                )
            )
        )
    )
    if live_close_set != session.close_set:
        reasons.append(AcceptanceSessionBlockingReason.CLOSE_SET_MISMATCH)
    if not _is_sha(context.head_commit):
        reasons.append(AcceptanceSessionBlockingReason.EXTERNAL_RESPONSE_MALFORMED)
    elif context.head_commit.lower() != session.head_sha:
        reasons.append(AcceptanceSessionBlockingReason.HEAD_SHA_MISMATCH)
    return _dedupe(reasons)


def _verification_reasons(
    session: AcceptanceSession,
    verification: object,
    live_tickets: Sequence[Ticket],
) -> tuple[AcceptanceSessionBlockingReason, ...]:
    if not isinstance(verification, PRVerification):
        return (AcceptanceSessionBlockingReason.VERIFICATION_MALFORMED,)

    reasons: list[AcceptanceSessionBlockingReason] = []
    status_reasons = {
        EvidenceStatus.PENDING: AcceptanceSessionBlockingReason.VERIFICATION_PENDING,
        EvidenceStatus.FAILED: AcceptanceSessionBlockingReason.VERIFICATION_FAILED,
        EvidenceStatus.WARNING: AcceptanceSessionBlockingReason.VERIFICATION_WARNING,
        EvidenceStatus.NOT_APPLICABLE: (
            AcceptanceSessionBlockingReason.VERIFICATION_NOT_APPLICABLE
        ),
    }
    if verification.status is not EvidenceStatus.PASSED:
        reason = status_reasons.get(verification.status)
        reasons.append(reason or AcceptanceSessionBlockingReason.VERIFICATION_MALFORMED)
    elif any(
        ticket.status is not EvidenceStatus.PASSED
        or any(
            outcome.required and outcome.status is not EvidenceStatus.PASSED
            for outcome in ticket.checks
        )
        for ticket in verification.tickets
    ):
        reasons.append(AcceptanceSessionBlockingReason.VERIFICATION_MALFORMED)

    if not _is_sha(verification.head_commit):
        reasons.append(AcceptanceSessionBlockingReason.VERIFIED_HEAD_INVALID)
    elif verification.head_commit.lower() != session.head_sha:
        reasons.append(AcceptanceSessionBlockingReason.VERIFIED_HEAD_MISMATCH)

    expected_ids = {ticket.id for ticket in live_tickets}
    actual_ids = {ticket.ticket_id for ticket in verification.tickets}
    if (
        len(live_tickets) != len(session.close_set)
        or len(verification.tickets) != len(session.close_set)
        or actual_ids != expected_ids
    ):
        reasons.append(AcceptanceSessionBlockingReason.VERIFICATION_CLOSE_SET_MISMATCH)
    return _dedupe(reasons)


def _verification_summary(
    verdict_id: UUID,
    verification: object | None,
) -> AcceptanceVerificationSummary | None:
    if not isinstance(verification, PRVerification) or not isinstance(
        verification.status, EvidenceStatus
    ):
        return None
    head_commit = (
        verification.head_commit.lower() if _is_sha(verification.head_commit) else None
    )
    blocking = sum(
        outcome.required and outcome.status is not EvidenceStatus.PASSED
        for ticket in verification.tickets
        for outcome in ticket.checks
    )
    return AcceptanceVerificationSummary(
        verdict_id=verdict_id,
        status=verification.status,
        head_commit=head_commit,
        ticket_count=len(verification.tickets),
        blocking_check_count=blocking,
    )


def _readiness_assessment(
    session: AcceptanceSession,
    assessment: PRIntegrationAssessment,
    *,
    verdict_id: UUID,
) -> AcceptanceReadinessAssessment:
    return AcceptanceReadinessAssessment(
        verdict_id=verdict_id,
        repository_owner=assessment.owner,
        repository_name=assessment.repo,
        pr_number=assessment.pr_number,
        head_ref=assessment.head_ref,
        head_sha=assessment.head_sha,
        head_repository=assessment.head_repository,
        base_ref=assessment.base_ref,
        base_sha=assessment.base_sha,
        base_repository=assessment.base_repository,
        eligibility="eligible",
        integration_status="current",
        criteria_fingerprint=session.criteria_fingerprint,
    )


def _apply_session_model(
    row: AcceptanceSessionRow,
    session: AcceptanceSession,
) -> None:
    payload = session.model_dump(mode="json")
    row.lifecycle = session.lifecycle.value
    row.step_summaries = payload["step_summaries"]
    row.blocking_reasons = payload["blocking_reasons"]
    row.stored_merge_ready = session.stored_merge_ready
    row.historical_readiness_reasons = payload["historical_readiness_reasons"]
    row.updated_at = session.updated_at
    row.staled_at = session.staled_at


def _session_mutation(
    row: AcceptanceSessionRow,
    expected_lifecycle: AcceptanceSessionLifecycle,
) -> OperatorActionMutation:
    return OperatorActionMutation(
        row,
        expected_values={"lifecycle": expected_lifecycle.value},
        updated_fields=_SESSION_SUMMARY_FIELDS,
    )


def _result_reasons(
    session: AcceptanceSession | None,
    failure: OperatorActionFailure | None,
) -> tuple[AcceptanceSessionBlockingReason, ...]:
    reasons: list[AcceptanceSessionBlockingReason] = []
    if failure is not None:
        if failure.code in {
            OperatorActionFailureCode.RECEIPT_COMMIT_FAILED,
            OperatorActionFailureCode.STORAGE_FAILED,
        }:
            reasons.append(AcceptanceSessionBlockingReason.READINESS_PERSISTENCE_FAILED)
        else:
            reasons.append(AcceptanceSessionBlockingReason.VERIFICATION_MALFORMED)
    if session is None:
        reasons.append(AcceptanceSessionBlockingReason.SESSION_UNKNOWN)
        return _dedupe(reasons)

    if (
        session.step_summaries[AcceptanceSessionStep.EVIDENCE].state
        is not AcceptanceSessionStepState.COMPLETE
    ):
        reasons.append(AcceptanceSessionBlockingReason.EVIDENCE_NOT_READY)
    if (
        session.step_summaries[AcceptanceSessionStep.CONFIRMATIONS].state
        is not AcceptanceSessionStepState.COMPLETE
    ):
        reasons.append(AcceptanceSessionBlockingReason.CONFIRMATIONS_NOT_READY)
    verification = session.step_summaries[AcceptanceSessionStep.VERIFICATION]
    readiness = session.step_summaries[AcceptanceSessionStep.READINESS]
    reasons.extend(verification.reasons)
    reasons.extend(readiness.reasons)
    if session.lifecycle is AcceptanceSessionLifecycle.STALE:
        reasons.extend(
            (
                AcceptanceSessionBlockingReason.SESSION_STALE,
                *session.blocking_reasons,
            )
        )
    elif (
        session.lifecycle
        in {
            AcceptanceSessionLifecycle.BLOCKED,
            AcceptanceSessionLifecycle.FAILED,
        }
        or not session.stored_merge_ready
    ):
        reasons.extend(session.historical_readiness_reasons)
    return _dedupe(reasons)


def _action_status(
    gateway_status: OperatorActionGatewayStatus,
    receipt: OperatorActionReceipt | None,
    session: AcceptanceSession | None,
) -> AcceptanceVerificationStatus:
    if gateway_status is OperatorActionGatewayStatus.CONFLICT:
        return AcceptanceVerificationStatus.CONFLICT
    if receipt is not None and (
        receipt.outcome is OperatorActionOutcome.CONFLICT
        or receipt.result_code is OperatorActionResultCode.ACTION_CONFLICT
    ):
        return AcceptanceVerificationStatus.CONFLICT
    if gateway_status is OperatorActionGatewayStatus.REPLAYED:
        return AcceptanceVerificationStatus.REPLAYED
    if gateway_status is OperatorActionGatewayStatus.IN_PROGRESS:
        return AcceptanceVerificationStatus.IN_PROGRESS
    if gateway_status is OperatorActionGatewayStatus.FAILED:
        return AcceptanceVerificationStatus.FAILED
    if (
        receipt is not None
        and receipt.outcome is OperatorActionOutcome.SUCCEEDED
        and session is not None
        and session.lifecycle is AcceptanceSessionLifecycle.MERGE_READY
        and session.stored_merge_ready
    ):
        return AcceptanceVerificationStatus.MERGE_READY
    if (
        receipt is not None
        and receipt.result_code is OperatorActionResultCode.STALE_STATE
    ):
        return AcceptanceVerificationStatus.STALE
    if receipt is not None and receipt.outcome is OperatorActionOutcome.FAILED:
        return AcceptanceVerificationStatus.FAILED
    return AcceptanceVerificationStatus.REFUSED


def _refused(*, conflict: bool = False) -> OperatorActionCommandResult:
    return OperatorActionCommandResult(
        outcome=(
            OperatorActionOutcome.CONFLICT
            if conflict
            else OperatorActionOutcome.REFUSED
        ),
        result_code=(
            OperatorActionResultCode.ACTION_CONFLICT
            if conflict
            else OperatorActionResultCode.ACTION_REFUSED
        ),
        result_metadata={"changed": False, "affected_count": 0},
    )


def _dedupe(
    reasons: Sequence[AcceptanceSessionBlockingReason],
) -> tuple[AcceptanceSessionBlockingReason, ...]:
    return tuple(dict.fromkeys(reasons))


def _is_sha(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 40
        and all(character in "0123456789abcdefABCDEF" for character in value)
    )
