"""Pure validation of persisted acceptance-session history."""

from __future__ import annotations

from atlas.core.enums import EvidenceStatus
from atlas.core.models.acceptance_session import (
    AcceptanceSession,
    AcceptanceSessionBlockingReason,
    AcceptanceSessionLifecycle,
    AcceptanceSessionStep,
)


def stored_acceptance_history_reasons(
    session: AcceptanceSession,
) -> tuple[AcceptanceSessionBlockingReason, ...]:
    """Return canonical defects in one persisted acceptance history.

    Both live acceptance readiness and retrospective completion consume this
    pure contract. A session rejected by the ordinary authority cannot become
    stronger merely because its PR later merged.
    """

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
