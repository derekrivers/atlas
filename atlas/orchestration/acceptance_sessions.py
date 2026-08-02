"""Durable exact-head acceptance-session creation and pure projections."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4

from atlas.core.enums import ActorType
from atlas.core.models import (
    AcceptanceSession,
    AcceptanceSessionBlockingReason,
    AcceptanceSessionLifecycle,
    AcceptanceSessionStep,
    AcceptanceSessionStepState,
    Ticket,
    TicketStatus,
)
from atlas.core.models.acceptance_session import (
    AcceptanceAssessmentSnapshot,
    AcceptanceCriterionSnapshot,
    AcceptanceStepSummary,
)
from atlas.github import GitHubAPIError, GitHubClient
from atlas.orchestration.operator_actions import idempotency_key_identity
from atlas.orchestration.pr_integration import (
    PRAncestryStatus,
    PRIntegrationAssessment,
    PRIntegrationEligibility,
    PRIntegrationStatus,
    PRMergeabilityStatus,
    assess_pr_integration,
)
from atlas.storage import (
    AcceptanceSessionRepo,
    AcceptanceSessionStateError,
)
from atlas.verification import parse_close_set

MAX_DIAGNOSTIC_TICKET_KEYS = 32


class AcceptanceSessionCreationStatus(StrEnum):
    """Typed service outcome before any future HTTP presentation."""

    CREATED = "created"
    REPLAYED = "replayed"
    REFUSED = "refused"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class AcceptanceSessionCreationResult:
    """Bounded, secret-free creation result."""

    status: AcceptanceSessionCreationStatus
    session: AcceptanceSession | None = None
    reasons: tuple[AcceptanceSessionBlockingReason, ...] = ()
    recovery_command: str | None = None
    ticket_keys: tuple[str, ...] = ()


class TicketLookup(Protocol):
    def get_by_key(self, key: str) -> Ticket | None:
        """Return the current canonical ticket definition."""


AssessmentService = Callable[[GitHubClient, str, str, int], PRIntegrationAssessment]
Clock = Callable[[], datetime]
IdFactory = Callable[[], UUID]


def acceptance_criteria_snapshot(
    close_set: Sequence[str], tickets: Iterable[Ticket]
) -> tuple[AcceptanceCriterionSnapshot, ...]:
    """Build the canonical key/index/text snapshot from live ticket models."""

    canonical_keys = tuple(sorted(set(close_set)))
    tickets_by_key: dict[str, Ticket] = {}
    for ticket in tickets:
        if ticket.key in tickets_by_key:
            raise ValueError("criteria snapshot received a duplicate ticket key")
        tickets_by_key[ticket.key] = ticket
    if set(tickets_by_key) != set(canonical_keys):
        raise ValueError("criteria snapshot tickets must equal the close-set")
    return tuple(
        AcceptanceCriterionSnapshot(
            ticket_key=key,
            criterion_index=index,
            text=criterion,
        )
        for key in canonical_keys
        for index, criterion in enumerate(tickets_by_key[key].acceptance_criteria)
    )


def acceptance_criteria_fingerprint(
    snapshot: Sequence[AcceptanceCriterionSnapshot],
) -> str:
    """Hash one ordered criteria snapshot as canonical UTF-8 JSON."""

    payload = [criterion.model_dump(mode="json") for criterion in snapshot]
    rendered = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return "sha256:" + hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def compare_acceptance_session_freshness(
    session: AcceptanceSession,
    live_assessment: PRIntegrationAssessment | None,
    live_criteria: Sequence[AcceptanceCriterionSnapshot] | None,
) -> tuple[AcceptanceSessionBlockingReason, ...]:
    """Return every live identity/eligibility/criteria mismatch, without I/O."""

    reasons: list[AcceptanceSessionBlockingReason] = []
    if live_assessment is None:
        reasons.append(AcceptanceSessionBlockingReason.EXTERNAL_STATE_INDETERMINATE)
    else:
        if (
            live_assessment.owner != session.repository_owner
            or live_assessment.repo != session.repository_name
        ):
            reasons.append(AcceptanceSessionBlockingReason.REPOSITORY_MISMATCH)
        if live_assessment.pr_number != session.pr_number:
            reasons.append(AcceptanceSessionBlockingReason.PR_NUMBER_MISMATCH)
        if live_assessment.head_ref != session.head_ref:
            reasons.append(AcceptanceSessionBlockingReason.HEAD_REF_MISMATCH)
        if live_assessment.head_sha != session.head_sha:
            reasons.append(AcceptanceSessionBlockingReason.HEAD_SHA_MISMATCH)
        if live_assessment.head_repository != session.head_repository:
            reasons.append(AcceptanceSessionBlockingReason.HEAD_REPOSITORY_MISMATCH)
        if live_assessment.base_ref != session.base_ref:
            reasons.append(AcceptanceSessionBlockingReason.BASE_REF_MISMATCH)
        if live_assessment.base_sha != session.base_sha:
            reasons.append(AcceptanceSessionBlockingReason.BASE_SHA_MISMATCH)
        if live_assessment.base_repository != session.base_repository:
            reasons.append(AcceptanceSessionBlockingReason.BASE_REPOSITORY_MISMATCH)
        if live_assessment.eligibility is not PRIntegrationEligibility.ELIGIBLE:
            reasons.append(AcceptanceSessionBlockingReason.ELIGIBILITY_MISMATCH)
        if live_assessment.integration_status is not PRIntegrationStatus.CURRENT:
            reasons.append(AcceptanceSessionBlockingReason.INTEGRATION_STATUS_MISMATCH)
        if (
            live_assessment.integration_status is PRIntegrationStatus.INDETERMINATE
            or live_assessment.ancestry is PRAncestryStatus.INDETERMINATE
            or live_assessment.mergeability is PRMergeabilityStatus.INDETERMINATE
        ):
            reasons.append(AcceptanceSessionBlockingReason.EXTERNAL_STATE_INDETERMINATE)

    if live_criteria is None:
        reasons.append(AcceptanceSessionBlockingReason.EXTERNAL_STATE_INDETERMINATE)
    elif acceptance_criteria_fingerprint(live_criteria) != session.criteria_fingerprint:
        reasons.append(AcceptanceSessionBlockingReason.CRITERIA_MISMATCH)
    return tuple(dict.fromkeys(reasons))


def mark_acceptance_session_stale_for_mutation(
    repository: AcceptanceSessionRepo,
    session: AcceptanceSession,
    live_assessment: PRIntegrationAssessment | None,
    live_criteria: Sequence[AcceptanceCriterionSnapshot] | None,
    *,
    observed_at: datetime,
) -> AcceptanceSession:
    """Atomically terminalise a mutation target when the pure check moved."""

    reasons = compare_acceptance_session_freshness(
        session, live_assessment, live_criteria
    )
    if not reasons:
        return session
    return repository.mark_stale(session.id, reasons, staled_at=observed_at)


def stored_acceptance_session_status(
    session: AcceptanceSession,
) -> dict[str, object]:
    """Project stored history only; no service, repository or external reads."""

    receipts = list(
        dict.fromkeys(
            receipt_id
            for step in AcceptanceSessionStep
            for receipt_id in session.step_summaries[step].receipt_ids
        )
    )
    return {
        "session_id": str(session.id),
        "pinned_identity": {
            "repository": {
                "owner": session.repository_owner,
                "name": session.repository_name,
            },
            "pr_number": session.pr_number,
            "head": {
                "ref": session.head_ref,
                "sha": session.head_sha,
                "repository": session.head_repository,
            },
            "base": {
                "ref": session.base_ref,
                "sha": session.base_sha,
                "repository": session.base_repository,
            },
        },
        "close_set": list(session.close_set),
        "criteria_snapshot": [
            criterion.model_dump(mode="json") for criterion in session.criteria_snapshot
        ],
        "criteria_fingerprint": session.criteria_fingerprint,
        "initial_assessment": session.initial_assessment.model_dump(mode="json"),
        "actor": {
            "type": session.created_by_type.value,
            "id": session.created_by_id,
        },
        "lifecycle": session.lifecycle.value,
        "steps": {
            step.value: session.step_summaries[step].model_dump(mode="json")
            for step in AcceptanceSessionStep
        },
        "receipts": [str(receipt_id) for receipt_id in receipts],
        "blocking_reasons": [reason.value for reason in session.blocking_reasons],
        "historical_readiness": {
            "stored_merge_ready": session.stored_merge_ready,
            "reasons": [
                reason.value for reason in session.historical_readiness_reasons
            ],
            "authority": "historical_only",
            "is_current_merge_authority": False,
        },
        "timestamps": {
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
            "staled_at": (
                session.staled_at.isoformat() if session.staled_at is not None else None
            ),
        },
    }


class AcceptanceSessionCreationService:
    """Read-only exact-head preflight followed by one durable session insert."""

    def __init__(
        self,
        *,
        github_client: GitHubClient,
        ticket_lookup: TicketLookup,
        repository: AcceptanceSessionRepo,
        clock: Clock,
        id_factory: IdFactory = uuid4,
        assessment_service: AssessmentService | None = None,
    ) -> None:
        self._github_client = github_client
        self._ticket_lookup = ticket_lookup
        self._repository = repository
        self._clock = clock
        self._id_factory = id_factory
        self._assessment_service = assessment_service or assess_pr_integration

    def create(
        self,
        *,
        repository_owner: str,
        repository_name: str,
        pr_number: int,
        idempotency_key: str,
        created_by_type: ActorType,
        created_by_id: str,
    ) -> AcceptanceSessionCreationResult:
        """Create one eligible session; caller criterion text is not accepted."""

        owner = _repository_part(repository_owner, "repository_owner")
        name = _repository_part(repository_name, "repository_name")
        if pr_number <= 0:
            raise ValueError("pr_number must be positive")
        if created_by_type is not ActorType.HUMAN or created_by_id != "operator":
            raise ValueError("acceptance session actor must be human/operator")
        command_identity = idempotency_key_identity(idempotency_key)

        replay = self._repository.get_by_creation_idempotency_key_identity(
            command_identity
        )
        if replay is not None:
            if (
                replay.repository_owner == owner
                and replay.repository_name == name
                and replay.pr_number == pr_number
                and replay.created_by_type is created_by_type
                and replay.created_by_id == created_by_id
            ):
                return AcceptanceSessionCreationResult(
                    status=AcceptanceSessionCreationStatus.REPLAYED,
                    session=replay,
                )
            return AcceptanceSessionCreationResult(
                status=AcceptanceSessionCreationStatus.CONFLICT,
                reasons=(AcceptanceSessionBlockingReason.IDEMPOTENCY_KEY_REUSED,),
            )

        try:
            assessment = self._assessment_service(
                self._github_client, owner, name, pr_number
            )
        except GitHubAPIError as error:
            reason = (
                AcceptanceSessionBlockingReason.PR_UNKNOWN
                if _is_unknown_pr_error(error)
                else AcceptanceSessionBlockingReason.EXTERNAL_STATE_INDETERMINATE
            )
            return AcceptanceSessionCreationResult(
                status=AcceptanceSessionCreationStatus.REFUSED,
                reasons=(reason,),
            )

        preflight_reasons = _assessment_preflight_reasons(assessment, owner, name)
        if preflight_reasons:
            return AcceptanceSessionCreationResult(
                status=AcceptanceSessionCreationStatus.REFUSED,
                reasons=preflight_reasons,
                recovery_command=(
                    _rebase_recovery_command(assessment)
                    if _is_rebase_eligible(assessment)
                    else None
                ),
            )

        close_set = tuple(
            sorted(set(parse_close_set(assessment.pr_title, assessment.pr_body)))
        )
        if not close_set:
            return AcceptanceSessionCreationResult(
                status=AcceptanceSessionCreationStatus.REFUSED,
                reasons=(AcceptanceSessionBlockingReason.CLOSE_SET_EMPTY,),
            )

        tickets: list[Ticket] = []
        unknown_keys: list[str] = []
        for key in close_set:
            ticket = self._ticket_lookup.get_by_key(key)
            if ticket is None:
                unknown_keys.append(key)
            else:
                tickets.append(ticket)
        if unknown_keys:
            return AcceptanceSessionCreationResult(
                status=AcceptanceSessionCreationStatus.REFUSED,
                reasons=(AcceptanceSessionBlockingReason.UNKNOWN_TICKET,),
                ticket_keys=tuple(unknown_keys[:MAX_DIAGNOSTIC_TICKET_KEYS]),
            )

        wrong_state = [
            ticket.key
            for ticket in tickets
            if ticket.status is not TicketStatus.REVIEW_REQUIRED
        ]
        if wrong_state:
            return AcceptanceSessionCreationResult(
                status=AcceptanceSessionCreationStatus.REFUSED,
                reasons=(AcceptanceSessionBlockingReason.TICKET_NOT_REVIEW_REQUIRED,),
                ticket_keys=tuple(wrong_state[:MAX_DIAGNOSTIC_TICKET_KEYS]),
            )

        criteria = acceptance_criteria_snapshot(close_set, tickets)
        now = self._clock()
        if now.utcoffset() is None:
            raise ValueError("acceptance-session clock must be timezone-aware")
        session = AcceptanceSession(
            id=self._id_factory(),
            repository_owner=owner,
            repository_name=name,
            pr_number=pr_number,
            close_set=close_set,
            head_ref=assessment.head_ref,
            head_sha=assessment.head_sha,
            head_repository=assessment.head_repository,
            base_ref=assessment.base_ref,
            base_sha=assessment.base_sha,
            base_repository=assessment.base_repository,
            initial_assessment=_assessment_snapshot(assessment),
            criteria_snapshot=criteria,
            criteria_fingerprint=acceptance_criteria_fingerprint(criteria),
            creation_idempotency_key_identity=command_identity,
            created_by_type=created_by_type,
            created_by_id=created_by_id,
            lifecycle=AcceptanceSessionLifecycle.PREFLIGHT_PASSED,
            step_summaries=_initial_step_summaries(now),
            blocking_reasons=(),
            stored_merge_ready=False,
            historical_readiness_reasons=(
                AcceptanceSessionBlockingReason.EVIDENCE_NOT_READY,
                AcceptanceSessionBlockingReason.CONFIRMATIONS_NOT_READY,
                AcceptanceSessionBlockingReason.VERIFICATION_NOT_PASSED,
            ),
            created_at=now,
            updated_at=now,
            staled_at=None,
        )
        try:
            stored = self._repository.create(session)
        except AcceptanceSessionStateError as error:
            return AcceptanceSessionCreationResult(
                status=AcceptanceSessionCreationStatus.CONFLICT,
                reasons=(error.reason,),
            )
        return AcceptanceSessionCreationResult(
            status=(
                AcceptanceSessionCreationStatus.CREATED
                if stored.created
                else AcceptanceSessionCreationStatus.REPLAYED
            ),
            session=stored.session,
        )


def _initial_step_summaries(
    now: datetime,
) -> dict[AcceptanceSessionStep, AcceptanceStepSummary]:
    return {
        step: AcceptanceStepSummary(
            state=(
                AcceptanceSessionStepState.COMPLETE
                if step is AcceptanceSessionStep.PREFLIGHT
                else AcceptanceSessionStepState.PENDING
            ),
            occurred_at=now if step is AcceptanceSessionStep.PREFLIGHT else None,
        )
        for step in AcceptanceSessionStep
    }


def _assessment_snapshot(
    assessment: PRIntegrationAssessment,
) -> AcceptanceAssessmentSnapshot:
    return AcceptanceAssessmentSnapshot(
        pr_state=assessment.pr_state,
        pr_draft=assessment.pr_draft,
        pr_merged=assessment.pr_merged,
        merge_base_sha=assessment.merge_base_sha,
        ahead_by=assessment.ahead_by,
        behind_by=assessment.behind_by,
        compare_status=(
            assessment.compare_status.value
            if assessment.compare_status is not None
            else None
        ),
        mergeability=assessment.mergeability.value,
        ancestry=assessment.ancestry.value,
        eligibility=assessment.eligibility.value,
        integration_status=assessment.integration_status.value,
    )


def _assessment_preflight_reasons(
    assessment: PRIntegrationAssessment, owner: str, name: str
) -> tuple[AcceptanceSessionBlockingReason, ...]:
    eligibility_reasons = {
        PRIntegrationEligibility.MERGED: AcceptanceSessionBlockingReason.PR_MERGED,
        PRIntegrationEligibility.CLOSED: AcceptanceSessionBlockingReason.PR_CLOSED,
        PRIntegrationEligibility.DRAFT: AcceptanceSessionBlockingReason.PR_DRAFT,
        PRIntegrationEligibility.FORK: AcceptanceSessionBlockingReason.PR_FORK_HEAD,
        PRIntegrationEligibility.NON_MAIN: AcceptanceSessionBlockingReason.PR_NON_MAIN,
    }
    if assessment.eligibility is not PRIntegrationEligibility.ELIGIBLE:
        return (eligibility_reasons[assessment.eligibility],)

    reasons: list[AcceptanceSessionBlockingReason] = []
    expected_repository = f"{owner}/{name}"
    if (
        assessment.owner != owner
        or assessment.repo != name
        or assessment.head_repository != expected_repository
        or assessment.base_repository != expected_repository
    ):
        reasons.append(AcceptanceSessionBlockingReason.REPOSITORY_MISMATCH)
    integration_reasons = {
        PRIntegrationStatus.BEHIND: AcceptanceSessionBlockingReason.INTEGRATION_BEHIND,
        PRIntegrationStatus.DIVERGED: (
            AcceptanceSessionBlockingReason.INTEGRATION_DIVERGED
        ),
        PRIntegrationStatus.CONFLICTED: (
            AcceptanceSessionBlockingReason.INTEGRATION_CONFLICTED
        ),
        PRIntegrationStatus.INDETERMINATE: (
            AcceptanceSessionBlockingReason.INTEGRATION_INDETERMINATE
        ),
        PRIntegrationStatus.INELIGIBLE: (
            AcceptanceSessionBlockingReason.ELIGIBILITY_MISMATCH
        ),
    }
    if assessment.integration_status is not PRIntegrationStatus.CURRENT:
        reasons.append(integration_reasons[assessment.integration_status])
    return tuple(dict.fromkeys(reasons))


def _is_rebase_eligible(assessment: PRIntegrationAssessment) -> bool:
    return assessment.eligibility is PRIntegrationEligibility.ELIGIBLE and (
        assessment.integration_status
        in {
            PRIntegrationStatus.BEHIND,
            PRIntegrationStatus.DIVERGED,
            PRIntegrationStatus.CONFLICTED,
        }
    )


def _rebase_recovery_command(assessment: PRIntegrationAssessment) -> str:
    return (
        f"atlas pr rebase prepare --pr {assessment.pr_number} "
        f"--repo {assessment.owner}/{assessment.repo}"
    )


def _repository_part(value: str, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 128
        or value.strip() != value
        or "/" in value
        or ":" in value
    ):
        raise ValueError(f"{field_name} must be one repository-name component")
    return value


def _is_unknown_pr_error(error: GitHubAPIError) -> bool:
    rendered = str(error).lower()
    return "http 404" in rendered or "not found" in rendered
