"""ADR model and status enum (ATLAS-12), per data-model-and-schemas.md §3.2."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field

from atlas.core.enums import ActorType


class ADRStatus(StrEnum):
    """Lifecycle of an architecture decision record (data-model §3.2)."""

    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


class ArchitectureDecisionRecord(BaseModel):
    """An architectural decision; prevents agents from rediscovering or
    contradicting previous choices."""

    id: UUID
    product_id: UUID
    number: int
    title: str
    status: ADRStatus
    context: str
    decision: str
    rationale: str
    consequences: list[str] = Field(default_factory=list)
    alternatives_considered: list[str] = Field(default_factory=list)
    supersedes_adr_id: UUID | None = None
    created_by_type: ActorType
    created_by_id: str
    created_at: datetime
    updated_at: datetime
