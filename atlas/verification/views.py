"""Serialisers for verification presentation surfaces."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from atlas.core.enums import EvidenceStatus
from atlas.verification.pr_completion import PRVerification


def blocking_verification_checks(
    pr: PRVerification,
    *,
    key_by_id: Mapping[UUID, str] | None = None,
) -> list[dict[str, object]]:
    """Ordered required checks that block a PR verdict from passing."""
    return [
        {
            "ticket_id": str(tv.ticket_id),
            "ticket_key": None if key_by_id is None else key_by_id.get(tv.ticket_id),
            "head_commit": pr.head_commit,
            "check_type": outcome.check_type.value,
            "required": outcome.required,
            "status": outcome.status.value,
            "evidence_ids": [str(eid) for eid in outcome.evidence_ids],
            "reason": outcome.reason,
        }
        for tv in pr.tickets
        for outcome in tv.checks
        if outcome.required and outcome.status != EvidenceStatus.PASSED
    ]


def pr_verification_json(
    pr: PRVerification,
    *,
    key_by_id: Mapping[UUID, str] | None = None,
) -> dict[str, object]:
    """The serialised PRVerification for `--json` (D4): head_commit, status, and
    tickets[] with each ticket_id, status, and checks[] {check_type, required,
    status, evidence_ids, reason}. Deterministic — tickets are already key-ordered
    and checks in resolver order."""
    return {
        "head_commit": pr.head_commit,
        "status": pr.status.value,
        "blocking_checks": blocking_verification_checks(pr, key_by_id=key_by_id),
        "tickets": [
            {
                "ticket_id": str(tv.ticket_id),
                "status": tv.status.value,
                "checks": [
                    {
                        "check_type": outcome.check_type.value,
                        "required": outcome.required,
                        "status": outcome.status.value,
                        "evidence_ids": [str(eid) for eid in outcome.evidence_ids],
                        "reason": outcome.reason,
                    }
                    for outcome in tv.checks
                ],
            }
            for tv in pr.tickets
        ],
    }
