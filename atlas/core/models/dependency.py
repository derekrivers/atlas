"""TicketDependency model and enum (ATLAS-12), per data-model-and-schemas.md
§3.5."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel

from atlas.core.enums import ActorType


class DependencyType(StrEnum):
    # depends_on is the single stored direction; "blocks" is derived at
    # query time, never stored, to prevent contradictory inverse edges.
    DEPENDS_ON = "depends_on"
    RELATES_TO = "relates_to"
    IMPLEMENTS = "implements"
    SUPERSEDES = "supersedes"


class TicketDependency(BaseModel):
    """Records that one ticket cannot proceed until another ticket, ADR, or
    component exists. ``target_entity_type`` stays a plain string;
    polymorphic target validation is ATLAS-19's concern."""

    id: UUID
    source_ticket_id: UUID
    target_entity_type: str
    target_entity_id: UUID
    dependency_type: DependencyType
    reason: str
    created_by_type: ActorType
    created_by_id: str
    created_at: datetime
