"""Product model (ATLAS-12), contract per data-model-and-schemas.md §3.1."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from atlas.core.enums import ActorType, EntityStatus


class Product(BaseModel):
    """A software product, platform, or internal system managed by Atlas."""

    id: UUID
    key: str
    name: str
    description: str
    vision: str
    status: EntityStatus
    goals: list[str] = Field(default_factory=list)
    non_goals: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    created_by_type: ActorType
    created_by_id: str
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None
