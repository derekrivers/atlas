"""Pure schema presenters for HTTP responses."""

from __future__ import annotations

from collections.abc import Sequence

from atlas.api.schemas import (
    ReviewCheckSchema,
    ReviewQueueItemSchema,
    ReviewQueueResponse,
    TicketBoardItemSchema,
    TicketBoardResponse,
    TicketDetailResponse,
    TicketEvidenceItemSchema,
    TicketEvidenceResponse,
)
from atlas.core.models import Ticket
from atlas.orchestration import TicketEvidenceRecordState, TicketReviewState


def present_ticket_board(tickets: Sequence[Ticket]) -> TicketBoardResponse:
    """Present tickets as a key-ordered lean board."""
    ordered = sorted(tickets, key=lambda ticket: ticket.key)
    return TicketBoardResponse(
        tickets=[
            TicketBoardItemSchema(
                key=ticket.key,
                title=ticket.title,
                status=ticket.status,
                ticket_type=ticket.ticket_type,
                priority=ticket.priority,
                risk_level=ticket.risk_level,
            )
            for ticket in ordered
        ]
    )


def present_ticket_detail(ticket: Ticket) -> TicketDetailResponse:
    """Present one stored ticket without deriving cross-resource state."""
    return TicketDetailResponse(
        key=ticket.key,
        title=ticket.title,
        objective=ticket.objective,
        context=ticket.context,
        status=ticket.status,
        ticket_type=ticket.ticket_type,
        risk_level=ticket.risk_level,
        priority=ticket.priority,
        estimated_effort=ticket.estimated_effort,
        relevant_docs=ticket.relevant_docs,
        acceptance_criteria=ticket.acceptance_criteria,
        non_goals=ticket.non_goals,
        implementation_notes=ticket.implementation_notes,
        test_requirements=ticket.test_requirements,
        documentation_requirements=ticket.documentation_requirements,
        definition_of_done=ticket.definition_of_done,
        tags=ticket.tags,
        component=ticket.component,
        external_linear_id=ticket.external_linear_id,
        external_github_issue_id=ticket.external_github_issue_id,
        source_anchor=ticket.source_anchor,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
        completed_at=ticket.completed_at,
    )


def present_ticket_evidence(
    records: tuple[TicketEvidenceRecordState, ...],
) -> TicketEvidenceResponse:
    """Present one ticket's stored evidence without exposing raw payloads."""
    return TicketEvidenceResponse(
        evidence=[
            TicketEvidenceItemSchema(
                type=record.evidence_type,
                tier=record.trust_level,
                status=record.status,
                has_system_pin_triple=record.has_system_pin_triple,
            )
            for record in records
        ]
    )


def present_review_queue(
    states: tuple[TicketReviewState, ...],
) -> ReviewQueueResponse:
    """Present stored review states as an HTTP response."""
    return ReviewQueueResponse(
        reviews=[
            ReviewQueueItemSchema(
                key=state.key,
                title=state.title,
                status=state.status,
                ticket_type=state.ticket_type,
                verdict=state.verdict,
                checks=[
                    ReviewCheckSchema(
                        check_type=check.check_type,
                        status=check.status,
                    )
                    for check in state.checks
                ],
                has_system_evidence=state.has_system_evidence,
                has_pr_merged_evidence=state.has_pr_merged_evidence,
            )
            for state in states
        ]
    )
