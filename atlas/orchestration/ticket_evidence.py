"""Stored-data assembly for one ticket's evidence projection."""

from __future__ import annotations

from dataclasses import dataclass

from atlas.core.enums import ActorType, EvidenceStatus
from atlas.core.models import Evidence, EvidenceType
from atlas.core.trust import evidence_tier
from atlas.storage import Database, EvidenceRepo, TicketRepo

_SYSTEM_PIN_TRIPLE = ("commit_sha", "external_run_id", "payload_hash")


@dataclass(frozen=True)
class TicketEvidenceRecordState:
    """Stored evidence row fields exposed to the operator API."""

    evidence_type: EvidenceType
    trust_level: ActorType
    status: EvidenceStatus
    has_system_pin_triple: bool


def _has_system_pin_triple(record: Evidence) -> bool:
    return all(getattr(record, field) is not None for field in _SYSTEM_PIN_TRIPLE)


def ticket_evidence(
    db: Database,
    key: str,
) -> tuple[TicketEvidenceRecordState, ...] | None:
    """Compose a key-addressed evidence projection from persisted records only."""
    ticket = TicketRepo(db).get_by_key(key)
    if ticket is None:
        return None

    return tuple(
        TicketEvidenceRecordState(
            evidence_type=record.evidence_type,
            trust_level=ActorType(evidence_tier(record.created_by_type)),
            status=record.status,
            has_system_pin_triple=_has_system_pin_triple(record),
        )
        for record in EvidenceRepo(db).list_for_ticket(ticket.id)
    )
