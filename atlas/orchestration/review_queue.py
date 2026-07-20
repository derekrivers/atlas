"""Stored-data assembly for the operator review queue."""

from __future__ import annotations

from dataclasses import dataclass

from atlas.core.enums import EvidenceStatus
from atlas.core.models import (
    EvidenceType,
    TicketStatus,
    TicketType,
    VerificationCheckType,
)
from atlas.core.trust import evidence_tier
from atlas.storage import (
    Database,
    EvidenceRepo,
    TicketRepo,
    VerificationCheckRepo,
)
from atlas.verification import ticket_verdict_from_checks


@dataclass(frozen=True)
class ReviewCheckState:
    """One persisted check outcome in the review breakdown."""

    check_type: VerificationCheckType
    status: EvidenceStatus


@dataclass(frozen=True)
class TicketReviewState:
    """Stored review state for one ticket awaiting operator review."""

    key: str
    title: str
    status: TicketStatus
    ticket_type: TicketType
    verdict: EvidenceStatus
    checks: tuple[ReviewCheckState, ...]
    has_system_evidence: bool
    has_pr_merged_evidence: bool


def review_queue(db: Database) -> tuple[TicketReviewState, ...]:
    """Compose the ticket-centric review queue from persisted records only."""
    tickets = TicketRepo(db).list_by_status(TicketStatus.REVIEW_REQUIRED)
    check_repo = VerificationCheckRepo(db)
    evidence_repo = EvidenceRepo(db)
    states: list[TicketReviewState] = []

    for ticket in tickets:
        checks = check_repo.list_for_ticket(ticket.id)
        evidence = evidence_repo.list_for_ticket(ticket.id)
        states.append(
            TicketReviewState(
                key=ticket.key,
                title=ticket.title,
                status=ticket.status,
                ticket_type=ticket.ticket_type,
                verdict=ticket_verdict_from_checks(ticket, checks),
                checks=tuple(
                    ReviewCheckState(
                        check_type=check.check_type,
                        status=check.status,
                    )
                    for check in checks
                ),
                has_system_evidence=any(
                    evidence_tier(record.created_by_type) == "system"
                    for record in evidence
                ),
                has_pr_merged_evidence=any(
                    record.evidence_type is EvidenceType.PR_MERGED
                    for record in evidence
                ),
            )
        )

    return tuple(sorted(states, key=lambda state: state.key))
