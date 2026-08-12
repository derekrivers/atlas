"""Exact-head evidence action for one durable acceptance session."""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from atlas.core.enums import ActorType, EvidenceStatus
from atlas.core.models import (
    AcceptanceSession,
    AcceptanceSessionBlockingReason,
    AcceptanceSessionLifecycle,
    AcceptanceSessionStep,
    AcceptanceSessionStepState,
    Evidence,
    EvidenceType,
    OperatorActionOutcome,
    OperatorActionReceipt,
    OperatorActionResultCode,
    Ticket,
)
from atlas.core.models.acceptance_session import (
    AcceptanceEvidenceSummary,
    AcceptanceStepSummary,
)
from atlas.evidence import (
    EvidencePullMalformedSourceError,
    PullResult,
    drive_evidence_pull,
)
from atlas.github import (
    GitHubAPIError,
    GitHubAuthenticationError,
    GitHubClient,
    GitHubMalformedResponseError,
    GitHubRateLimitError,
    GitHubTimeoutError,
    GitHubTransportError,
    MissingGitHubTokenError,
)
from atlas.orchestration.acceptance_sessions import (
    AssessmentService,
    compare_acceptance_session_freshness,
)
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
from atlas.orchestration.pr_integration import assess_pr_integration
from atlas.storage import AcceptanceSessionRepo, EvidenceRepo
from atlas.storage.tables import AcceptanceSessionRow

ACTION_NAME = "acceptance_session.pull_evidence"
TARGET_TYPE = "acceptance_session"

_CHECK_TYPES = frozenset(
    {
        EvidenceType.TEST_RESULT,
        EvidenceType.BUILD_RESULT,
        EvidenceType.LINT_RESULT,
        EvidenceType.COVERAGE_REPORT,
    }
)
_PULL_TYPES = _CHECK_TYPES | {
    EvidenceType.PR_REVIEW,
    EvidenceType.DOCUMENTATION_UPDATE,
}
_SESSION_SUMMARY_FIELDS = (
    "lifecycle",
    "step_summaries",
    "blocking_reasons",
    "stored_merge_ready",
    "historical_readiness_reasons",
    "updated_at",
    "staled_at",
)


@dataclass(frozen=True)
class AcceptanceEvidencePullContext:
    """Authenticated, server-resolved inputs accepted by the action."""

    idempotency_key: str
    created_by_type: ActorType
    created_by_id: str


@dataclass(frozen=True)
class AcceptanceEvidencePullResult:
    """Typed action outcome with the latest stored session projection."""

    status: OperatorActionGatewayStatus
    session: AcceptanceSession | None = None
    receipt: OperatorActionReceipt | None = None
    conflict: OperatorActionConflict | None = None
    failure: OperatorActionFailure | None = None
    reasons: tuple[AcceptanceSessionBlockingReason, ...] = ()


class TicketLookup(Protocol):
    def get_by_key(self, key: str) -> Ticket | None:
        """Return the current canonical ticket definition."""


class EvidencePullService(Protocol):
    def __call__(
        self,
        client: GitHubClient,
        owner: str,
        repo: str,
        pr_number: int,
        *,
        evidence_repo: EvidenceRepo,
        product_id: UUID,
        now: datetime,
    ) -> PullResult:
        """Pull and persist canonical evidence through the shared service."""


Clock = Callable[[], datetime]


@dataclass
class _SessionGuard:
    lock: threading.Lock
    users: int = 0


_SESSION_GUARDS_LOCK = threading.Lock()
_SESSION_GUARDS: dict[UUID, _SessionGuard] = {}


@contextmanager
def _one_action_in_flight(session_id: UUID) -> Any:
    """Serialise synchronous actions for the supported single-process server."""

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


class AcceptanceSessionEvidencePullService:
    """Coordinate freshness, canonical evidence pull and one session advance."""

    def __init__(
        self,
        *,
        github_client: GitHubClient,
        ticket_lookup: TicketLookup,
        session_repository: AcceptanceSessionRepo,
        evidence_repository: EvidenceRepo,
        gateway: OperatorActionGateway,
        clock: Clock,
        assessment_service: AssessmentService | None = None,
        evidence_pull_service: EvidencePullService = drive_evidence_pull,
    ) -> None:
        self._github_client = github_client
        self._ticket_lookup = ticket_lookup
        self._session_repository = session_repository
        self._evidence_repository = evidence_repository
        self._gateway = gateway
        self._clock = clock
        self._assessment_service = assessment_service or assess_pr_integration
        self._evidence_pull_service = evidence_pull_service

    def execute(
        self,
        session_id: UUID,
        context: AcceptanceEvidencePullContext,
    ) -> AcceptanceEvidencePullResult:
        """Pull evidence using only session-owned PR identity and auth context."""

        if (
            context.created_by_type is not ActorType.HUMAN
            or context.created_by_id != "operator"
        ):
            raise ValueError("acceptance evidence actor must be human/operator")
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

        with _one_action_in_flight(session_id):
            gateway_result = self._gateway.execute_bounded_external(
                envelope,
                self._command,
                loads=(
                    OperatorActionEntityLoad(
                        "acceptance_session", AcceptanceSessionRow, session_id
                    ),
                ),
            )

        stored = self._session_repository.get(session_id)
        reasons = (
            stored.blocking_reasons
            if stored is not None
            and stored.lifecycle is AcceptanceSessionLifecycle.STALE
            else _external_failure_reasons(gateway_result.receipt)
        )
        return AcceptanceEvidencePullResult(
            status=gateway_result.status,
            session=stored,
            receipt=gateway_result.receipt,
            conflict=gateway_result.conflict,
            failure=gateway_result.failure,
            reasons=reasons,
        )

    def _command(
        self, context: OperatorActionCommandContext
    ) -> OperatorActionCommandResult:
        row = context.entity("acceptance_session", AcceptanceSessionRow)
        if row is None:
            return _refused()
        session = AcceptanceSession.model_validate(row, from_attributes=True)
        if session.lifecycle is AcceptanceSessionLifecycle.STALE:
            return OperatorActionCommandResult(
                outcome=OperatorActionOutcome.REFUSED,
                result_code=OperatorActionResultCode.STALE_STATE,
            )
        if session.lifecycle is not AcceptanceSessionLifecycle.PREFLIGHT_PASSED:
            return _refused()

        before_reasons, tickets, before_timed_out = self._freshness(session)
        if before_timed_out:
            return _failed(OperatorActionResultCode.EXTERNAL_TIMEOUT)
        if before_reasons:
            return self._stale_result(row, session, before_reasons, context.receipt_id)

        product_ids = {ticket.product_id for ticket in tickets}
        if len(product_ids) != 1:
            return _refused()
        product_id = next(iter(product_ids))
        existing_head_record_ids = {
            record.id
            for record in self._evidence_repository.list_for_product_commit(
                product_id, session.head_sha
            )
            if record.evidence_type in _PULL_TYPES
        }
        pull_started_at = self._now_not_before(session.updated_at)
        try:
            pulled = self._evidence_pull_service(
                self._github_client,
                session.repository_owner,
                session.repository_name,
                session.pr_number,
                evidence_repo=self._evidence_repository,
                product_id=product_id,
                now=pull_started_at,
            )
        except (MissingGitHubTokenError, GitHubAuthenticationError):
            return _failed(OperatorActionResultCode.EVIDENCE_AUTHENTICATION_FAILED)
        except GitHubRateLimitError:
            return _failed(OperatorActionResultCode.EVIDENCE_RATE_LIMIT_FAILED)
        except (
            EvidencePullMalformedSourceError,
            GitHubMalformedResponseError,
            ValueError,
        ):
            return _failed(OperatorActionResultCode.EVIDENCE_MALFORMED_SOURCE)
        except (GitHubTimeoutError, TimeoutError):
            return _failed(OperatorActionResultCode.EXTERNAL_TIMEOUT)
        except (GitHubTransportError, GitHubAPIError, OSError):
            return _failed(OperatorActionResultCode.EVIDENCE_TRANSPORT_FAILED)

        after_reasons, _, after_timed_out = self._freshness(session)
        if after_timed_out:
            return _failed(OperatorActionResultCode.EXTERNAL_TIMEOUT)
        if after_reasons:
            return self._stale_result(row, session, after_reasons, context.receipt_id)

        projected = [
            record
            for record in self._evidence_repository.list_for_product_commit(
                product_id, session.head_sha
            )
            if record.evidence_type in _PULL_TYPES
        ]
        try:
            summary = _summarise_evidence(
                session.head_sha,
                pulled,
                projected,
                existing_head_record_ids=existing_head_record_ids,
            )
        except EvidencePullMalformedSourceError:
            return _failed(OperatorActionResultCode.EVIDENCE_MALFORMED_SOURCE)

        now = self._now_not_before(session.updated_at)
        summaries = dict(session.step_summaries)
        summaries[AcceptanceSessionStep.EVIDENCE] = AcceptanceStepSummary(
            state=AcceptanceSessionStepState.COMPLETE,
            receipt_ids=(context.receipt_id,),
            occurred_at=now,
            evidence=summary,
        )
        updated = AcceptanceSession.model_validate(
            session.model_copy(
                update={
                    "lifecycle": AcceptanceSessionLifecycle.EVIDENCE_READY,
                    "step_summaries": summaries,
                    "historical_readiness_reasons": tuple(
                        reason
                        for reason in session.historical_readiness_reasons
                        if reason
                        is not AcceptanceSessionBlockingReason.EVIDENCE_NOT_READY
                    ),
                    "updated_at": now,
                }
            ).model_dump()
        )
        _apply_session_model(row, updated)
        return OperatorActionCommandResult(
            outcome=OperatorActionOutcome.SUCCEEDED,
            result_code=OperatorActionResultCode.ACTION_SUCCEEDED,
            result_metadata={"changed": True, "affected_count": summary.total_count},
            mutations=(_session_mutation(row, session.lifecycle),),
        )

    def _freshness(
        self, session: AcceptanceSession
    ) -> tuple[
        tuple[AcceptanceSessionBlockingReason, ...],
        tuple[Ticket, ...],
        bool,
    ]:
        timed_out = False
        try:
            assessment = self._assessment_service(
                self._github_client,
                session.repository_owner,
                session.repository_name,
                session.pr_number,
            )
        except (GitHubTimeoutError, TimeoutError):
            assessment = None
            timed_out = True
        except GitHubAPIError:
            assessment = None

        tickets: list[Ticket] = []
        ticket_reads_indeterminate = False
        try:
            for key in session.close_set:
                ticket = self._ticket_lookup.get_by_key(key)
                if ticket is not None:
                    tickets.append(ticket)
        except (GitHubTimeoutError, TimeoutError):
            ticket_reads_indeterminate = True
            timed_out = True
        except Exception:
            ticket_reads_indeterminate = True
        live_tickets: Sequence[Ticket] | None = (
            None if ticket_reads_indeterminate else tickets
        )
        return (
            compare_acceptance_session_freshness(
                session,
                assessment,
                live_tickets,
            ),
            tuple(tickets),
            timed_out,
        )

    def _stale_result(
        self,
        row: AcceptanceSessionRow,
        session: AcceptanceSession,
        reasons: tuple[AcceptanceSessionBlockingReason, ...],
        receipt_id: UUID,
    ) -> OperatorActionCommandResult:
        now = self._now_not_before(session.updated_at)
        summaries = dict(session.step_summaries)
        summaries[AcceptanceSessionStep.EVIDENCE] = AcceptanceStepSummary(
            state=AcceptanceSessionStepState.BLOCKED,
            reasons=reasons,
            receipt_ids=(receipt_id,),
            occurred_at=now,
        )
        updated = AcceptanceSession.model_validate(
            session.model_copy(
                update={
                    "lifecycle": AcceptanceSessionLifecycle.STALE,
                    "step_summaries": summaries,
                    "blocking_reasons": tuple(
                        dict.fromkeys([*session.blocking_reasons, *reasons])
                    ),
                    "historical_readiness_reasons": tuple(
                        dict.fromkeys(
                            [
                                *session.historical_readiness_reasons,
                                AcceptanceSessionBlockingReason.SESSION_STALE,
                                *reasons,
                            ]
                        )
                    ),
                    "updated_at": now,
                    "staled_at": now,
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

    def _now_not_before(self, prior: datetime) -> datetime:
        now = self._clock()
        if now.utcoffset() is None:
            raise ValueError("acceptance evidence clock must be timezone-aware")
        return max(now, prior)


def _apply_session_model(row: AcceptanceSessionRow, model: AcceptanceSession) -> None:
    payload = model.model_dump(mode="json")
    row.lifecycle = model.lifecycle.value
    row.step_summaries = payload["step_summaries"]
    row.blocking_reasons = payload["blocking_reasons"]
    row.stored_merge_ready = model.stored_merge_ready
    row.historical_readiness_reasons = payload["historical_readiness_reasons"]
    row.updated_at = model.updated_at
    row.staled_at = model.staled_at


def _session_mutation(
    row: AcceptanceSessionRow,
    expected_lifecycle: AcceptanceSessionLifecycle,
) -> OperatorActionMutation:
    return OperatorActionMutation(
        row,
        expected_values={"lifecycle": expected_lifecycle.value},
        updated_fields=_SESSION_SUMMARY_FIELDS,
    )


def _refused() -> OperatorActionCommandResult:
    return OperatorActionCommandResult(
        outcome=OperatorActionOutcome.REFUSED,
        result_code=OperatorActionResultCode.ACTION_REFUSED,
        result_metadata={"changed": False, "affected_count": 0},
    )


def _external_failure_reasons(
    receipt: OperatorActionReceipt | None,
) -> tuple[AcceptanceSessionBlockingReason, ...]:
    if receipt is None or receipt.outcome is not OperatorActionOutcome.FAILED:
        return ()
    if receipt.result_code is OperatorActionResultCode.EXTERNAL_TIMEOUT:
        specific = AcceptanceSessionBlockingReason.EXTERNAL_READ_TIMEOUT
    elif receipt.result_code is OperatorActionResultCode.EVIDENCE_MALFORMED_SOURCE:
        specific = AcceptanceSessionBlockingReason.EXTERNAL_RESPONSE_MALFORMED
    elif receipt.result_code in {
        OperatorActionResultCode.EVIDENCE_TRANSPORT_FAILED,
        OperatorActionResultCode.EVIDENCE_AUTHENTICATION_FAILED,
        OperatorActionResultCode.EVIDENCE_RATE_LIMIT_FAILED,
    }:
        specific = AcceptanceSessionBlockingReason.EXTERNAL_READ_FAILED
    else:
        return ()
    return (
        specific,
        AcceptanceSessionBlockingReason.EXTERNAL_STATE_INDETERMINATE,
    )


def _failed(result_code: OperatorActionResultCode) -> OperatorActionCommandResult:
    return OperatorActionCommandResult(
        outcome=OperatorActionOutcome.FAILED,
        result_code=result_code,
    )


def _summarise_evidence(
    head_sha: str,
    pulled: PullResult,
    records: Sequence[Evidence],
    *,
    existing_head_record_ids: set[UUID],
) -> AcceptanceEvidenceSummary:
    new_records = [*pulled.checks, *pulled.reviews, *pulled.docs]
    if any(
        record.commit_sha is None
        or record.external_run_id is None
        or record.payload_hash is None
        for record in new_records
    ):
        raise EvidencePullMalformedSourceError(
            "pulled evidence did not carry a complete pin triple"
        )
    if (
        any(record.evidence_type not in _CHECK_TYPES for record in pulled.checks)
        or any(
            record.evidence_type is not EvidenceType.PR_REVIEW
            for record in pulled.reviews
        )
        or any(
            record.evidence_type is not EvidenceType.DOCUMENTATION_UPDATE
            for record in pulled.docs
        )
        or any(record.commit_sha != head_sha for record in pulled.checks)
        or any(record.commit_sha != head_sha for record in pulled.docs)
    ):
        raise EvidencePullMalformedSourceError(
            "pulled evidence was inconsistent with its source or session head"
        )

    new_head_records = [
        record for record in new_records if record.commit_sha == head_sha
    ]
    if any(record not in records for record in new_head_records):
        raise EvidencePullMalformedSourceError(
            "pulled evidence was absent from the canonical exact-head projection"
        )

    source_timestamps = sorted(
        record.source_event_at
        for record in records
        if record.source_event_at is not None
    )
    total = len(records)
    complete_pins = sum(
        record.commit_sha is not None
        and record.external_run_id is not None
        and record.payload_hash is not None
        for record in records
    )
    exact_head_pins = sum(
        record.commit_sha == head_sha
        and record.external_run_id is not None
        and record.payload_hash is not None
        for record in records
    )
    return AcceptanceEvidenceSummary(
        total_count=total,
        new_count=sum(
            record.id not in existing_head_record_ids for record in new_head_records
        ),
        checks_count=sum(record.evidence_type in _CHECK_TYPES for record in records),
        reviews_count=sum(
            record.evidence_type is EvidenceType.PR_REVIEW for record in records
        ),
        docs_count=sum(
            record.evidence_type is EvidenceType.DOCUMENTATION_UPDATE
            for record in records
        ),
        system_count=sum(
            record.created_by_type is ActorType.SYSTEM for record in records
        ),
        human_count=sum(
            record.created_by_type is ActorType.HUMAN for record in records
        ),
        agent_count=sum(
            record.created_by_type is ActorType.AGENT for record in records
        ),
        pending_count=sum(
            record.status is EvidenceStatus.PENDING for record in records
        ),
        passed_count=sum(record.status is EvidenceStatus.PASSED for record in records),
        failed_count=sum(record.status is EvidenceStatus.FAILED for record in records),
        warning_count=sum(
            record.status is EvidenceStatus.WARNING for record in records
        ),
        not_applicable_count=sum(
            record.status is EvidenceStatus.NOT_APPLICABLE for record in records
        ),
        complete_pin_count=complete_pins,
        exact_head_pin_count=exact_head_pins,
        pin_complete=complete_pins == total,
        exact_head_pin_complete=exact_head_pins == total,
        oldest_source_event_at=(source_timestamps[0] if source_timestamps else None),
        latest_source_event_at=(source_timestamps[-1] if source_timestamps else None),
    )
