"""Stored-data assembly for the operator system status snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from atlas import __version__
from atlas.storage import Database, EvidenceRepo, TicketRepo
from atlas.storage.preconditions import database_schema_revision


@dataclass(frozen=True)
class SystemStatus:
    """Operator-relevant status fields available without background jobs."""

    package_version: str
    schema_revision: str | None
    ticket_count: int
    evidence_count: int
    last_linear_sync_at: datetime | None
    last_evidence_pull_at: datetime | None


def system_status(db: Database) -> SystemStatus:
    """Compose the singleton system snapshot from persisted state only."""
    tickets = TicketRepo(db)
    evidence = EvidenceRepo(db)
    return SystemStatus(
        package_version=__version__,
        schema_revision=database_schema_revision(db),
        ticket_count=tickets.count(),
        evidence_count=evidence.count(),
        last_linear_sync_at=tickets.latest_linear_synced_at(),
        last_evidence_pull_at=evidence.latest_created_at(),
    )
