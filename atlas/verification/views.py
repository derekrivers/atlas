"""Serialisers for verification presentation surfaces."""

from __future__ import annotations

from atlas.verification.pr_completion import PRVerification


def pr_verification_json(pr: PRVerification) -> dict[str, object]:
    """The serialised PRVerification for `--json` (D4): head_commit, status, and
    tickets[] with each ticket_id, status, and checks[] {check_type, required,
    status, evidence_ids, reason}. Deterministic — tickets are already key-ordered
    and checks in resolver order."""
    return {
        "head_commit": pr.head_commit,
        "status": pr.status.value,
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
