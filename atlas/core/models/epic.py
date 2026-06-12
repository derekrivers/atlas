"""Epic model and status enum (ATLAS-12), per data-model-and-schemas.md §3.3."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field

from atlas.core.enums import ActorType, RiskLevel


class EpicStatus(StrEnum):
    """Lifecycle of an epic (data-model §3.3)."""

    BACKLOG = "backlog"
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    ARCHIVED = "archived"


class Epic(BaseModel):
    """An epic groups related work."""

    id: UUID
    product_id: UUID
    key: str
    title: str
    description: str
    objective: str
    status: EpicStatus
    priority: int = Field(ge=-2147483648, le=2147483647)  # SQL INTEGER range
    risk_level: RiskLevel
    # Reconciler anchor-match pass; AT-1 traceability — every item
    # traceable to a document anchor.
    source_anchor: str
    created_by_type: ActorType
    created_by_id: str
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
