"""Bounded, read-only current merge-readiness evaluation for one session."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from atlas.core.enums import EvidenceStatus
from atlas.core.models import (
    AcceptanceSession,
    AcceptanceSessionBlockingReason,
    AcceptanceSessionLifecycle,
    AcceptanceSessionStep,
    Ticket,
)
from atlas.github import (
    GitHubAPIError,
    GitHubClient,
    GitHubMalformedResponseError,
    GitHubTimeoutError,
)
from atlas.orchestration.acceptance_sessions import (
    AssessmentService,
    TicketLookup,
    compare_acceptance_session_freshness,
)
from atlas.orchestration.pr_integration import (
    PRIntegrationAssessment,
    assess_pr_integration,
)
from atlas.storage import AcceptanceSessionRepo


@dataclass(frozen=True)
class LiveAcceptanceReadinessResult:
    """Current advisory result; stored true is never sufficient by itself."""

    merge_ready: bool
    session: AcceptanceSession | None = None
    reasons: tuple[AcceptanceSessionBlockingReason, ...] = ()


class AcceptanceSessionLiveReadinessService:
    """Combine stored history with one fresh assessment and criteria read."""

    def __init__(
        self,
        *,
        github_client: GitHubClient,
        ticket_lookup: TicketLookup,
        session_repository: AcceptanceSessionRepo,
        assessment_service: AssessmentService | None = None,
    ) -> None:
        self._github_client = github_client
        self._ticket_lookup = ticket_lookup
        self._repository = session_repository
        self._assessment_service = assessment_service or assess_pr_integration

    def evaluate(self, session_id: UUID) -> LiveAcceptanceReadinessResult:
        """Re-evaluate current authority without any store or external mutation."""

        try:
            session = self._repository.get(session_id)
        except Exception:
            return LiveAcceptanceReadinessResult(
                merge_ready=False,
                reasons=(AcceptanceSessionBlockingReason.STORED_HISTORY_INVALID,),
            )
        if session is None:
            return LiveAcceptanceReadinessResult(
                merge_ready=False,
                reasons=(AcceptanceSessionBlockingReason.SESSION_UNKNOWN,),
            )

        reasons = list(_stored_history_reasons(session))
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
            reasons.extend(_external_failure_reasons(error))

        tickets: list[Ticket] = []
        ticket_reads_failed = False
        try:
            for key in session.close_set:
                ticket = self._ticket_lookup.get_by_key(key)
                if ticket is not None:
                    tickets.append(ticket)
        except Exception as error:
            ticket_reads_failed = True
            reasons.extend(_external_failure_reasons(error))

        reasons.extend(
            compare_acceptance_session_freshness(
                session,
                assessment,
                None if ticket_reads_failed else tickets,
            )
        )
        unique_reasons = tuple(dict.fromkeys(reasons))
        return LiveAcceptanceReadinessResult(
            merge_ready=not unique_reasons,
            session=session,
            reasons=unique_reasons,
        )


def _stored_history_reasons(
    session: AcceptanceSession,
) -> tuple[AcceptanceSessionBlockingReason, ...]:
    reasons: list[AcceptanceSessionBlockingReason] = []
    if not session.stored_merge_ready:
        reasons.extend(session.historical_readiness_reasons)
        if not session.historical_readiness_reasons:
            reasons.append(AcceptanceSessionBlockingReason.VERIFICATION_NOT_PASSED)
    if session.lifecycle is not AcceptanceSessionLifecycle.MERGE_READY:
        if session.lifecycle is AcceptanceSessionLifecycle.STALE:
            reasons.extend(
                (
                    AcceptanceSessionBlockingReason.SESSION_STALE,
                    *session.blocking_reasons,
                )
            )
        reasons.append(AcceptanceSessionBlockingReason.SESSION_NOT_VERIFIABLE)

    verification_step = session.step_summaries[AcceptanceSessionStep.VERIFICATION]
    readiness_step = session.step_summaries[AcceptanceSessionStep.READINESS]
    verification = verification_step.verification
    readiness = readiness_step.readiness
    if verification is None or readiness is None:
        reasons.append(AcceptanceSessionBlockingReason.STORED_HISTORY_INVALID)
        return tuple(dict.fromkeys(reasons))
    if verification.status is not EvidenceStatus.PASSED:
        reasons.append(AcceptanceSessionBlockingReason.VERIFICATION_NOT_PASSED)
    if verification.head_commit != session.head_sha:
        reasons.append(AcceptanceSessionBlockingReason.VERIFIED_HEAD_MISMATCH)
    if verification.ticket_count != len(session.close_set):
        reasons.append(AcceptanceSessionBlockingReason.VERIFICATION_CLOSE_SET_MISMATCH)
    if verification.blocking_check_count != 0:
        reasons.append(AcceptanceSessionBlockingReason.VERIFICATION_NOT_PASSED)
    if verification.verdict_id != readiness.verdict_id:
        reasons.append(AcceptanceSessionBlockingReason.STORED_HISTORY_INVALID)

    expected_identity = (
        session.repository_owner,
        session.repository_name,
        session.pr_number,
        session.head_ref,
        session.head_sha,
        session.head_repository,
        session.base_ref,
        session.base_sha,
        session.base_repository,
        session.criteria_fingerprint,
    )
    stored_identity = (
        readiness.repository_owner,
        readiness.repository_name,
        readiness.pr_number,
        readiness.head_ref,
        readiness.head_sha,
        readiness.head_repository,
        readiness.base_ref,
        readiness.base_sha,
        readiness.base_repository,
        readiness.criteria_fingerprint,
    )
    if stored_identity != expected_identity:
        reasons.append(AcceptanceSessionBlockingReason.STORED_HISTORY_INVALID)
    if (
        not verification_step.receipt_ids
        or not readiness_step.receipt_ids
        or not set(verification_step.receipt_ids).intersection(
            readiness_step.receipt_ids
        )
    ):
        reasons.append(AcceptanceSessionBlockingReason.STORED_HISTORY_INVALID)
    return tuple(dict.fromkeys(reasons))


def _external_failure_reasons(
    error: Exception,
) -> tuple[AcceptanceSessionBlockingReason, ...]:
    if isinstance(error, (GitHubTimeoutError, TimeoutError)):
        specific = AcceptanceSessionBlockingReason.EXTERNAL_READ_TIMEOUT
    elif isinstance(error, (GitHubMalformedResponseError, TypeError, ValueError)):
        specific = AcceptanceSessionBlockingReason.EXTERNAL_RESPONSE_MALFORMED
    elif isinstance(error, (GitHubAPIError, OSError)):
        specific = AcceptanceSessionBlockingReason.EXTERNAL_READ_FAILED
    else:
        specific = AcceptanceSessionBlockingReason.EXTERNAL_READ_FAILED
    return (
        specific,
        AcceptanceSessionBlockingReason.EXTERNAL_STATE_INDETERMINATE,
    )
