"""Stored-data assembly for the operator ticket board."""

from __future__ import annotations

from dataclasses import dataclass

from atlas.core.enums import RiskLevel
from atlas.core.models import TicketStatus, TicketType
from atlas.storage import Database, EpicRepo, TicketRepo


@dataclass(frozen=True)
class TicketBoardItemState:
    """Stored ticket board fields plus the owning epic key."""

    key: str
    title: str
    status: TicketStatus
    ticket_type: TicketType
    priority: int
    risk_level: RiskLevel
    epic_key: str | None


def ticket_board(
    db: Database,
    status: TicketStatus | None = None,
) -> tuple[TicketBoardItemState, ...]:
    """Compose the key-ordered board from persisted tickets and epics."""
    ticket_repo = TicketRepo(db)
    tickets = (
        ticket_repo.list_by_status(status) if status is not None else ticket_repo.list()
    )
    epic_key_by_id = {epic.id: epic.key for epic in EpicRepo(db).list()}
    return tuple(
        TicketBoardItemState(
            key=ticket.key,
            title=ticket.title,
            status=ticket.status,
            ticket_type=ticket.ticket_type,
            priority=ticket.priority,
            risk_level=ticket.risk_level,
            epic_key=epic_key_by_id.get(ticket.epic_id) if ticket.epic_id else None,
        )
        for ticket in sorted(tickets, key=lambda record: record.key)
    )
