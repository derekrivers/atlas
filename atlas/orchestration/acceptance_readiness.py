"""Bounded, read-only current merge-readiness evaluation for one session."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from atlas.core.acceptance_history import stored_acceptance_history_reasons
from atlas.core.models import (
    AcceptanceSession,
    AcceptanceSessionBlockingReason,
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

        reasons = list(stored_acceptance_history_reasons(session))
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
