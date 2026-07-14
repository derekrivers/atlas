"""Lesson model and category enum (ATLAS-13), per data-model-and-schemas.md
§3.6.

Contract only: promotion enforcement is the operator gate (ATLAS-97) and
ACTIVE-only retrieval is ATLAS-53 — the model does not police status.
"""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field

from atlas.core.enums import ActorType, EntityStatus


class LessonCategory(StrEnum):
    """What kind of organisational learning a lesson records (data-model
    §3.6)."""

    SUCCESS_PATTERN = "success_pattern"
    FAILURE_PATTERN = "failure_pattern"
    ARCHITECTURE = "architecture"
    TESTING = "testing"
    DELIVERY = "delivery"
    PRODUCT = "product"
    RESEARCH = "research"
    TECH_DEBT = "tech_debt"


class Lesson(BaseModel):
    """Organisational learning from successful work, failed work, incidents,
    recurring blockers, or human decisions."""

    id: UUID
    product_id: UUID
    # Agent-authored lessons default to DRAFT; only ACTIVE lessons are
    # retrievable into context packs (ADR-0009).
    status: EntityStatus = EntityStatus.DRAFT
    category: LessonCategory
    title: str
    problem: str
    solution: str
    outcome: str
    confidence: float | None = Field(default=None, ge=0, le=1)
    related_ticket_ids: list[UUID] = Field(default_factory=list)
    related_adr_ids: list[UUID] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    created_by_type: ActorType
    created_by_id: str
    created_at: datetime
    updated_at: datetime
