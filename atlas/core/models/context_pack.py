"""ContextPack model (ATLAS-15), per data-model-and-schemas.md §3.9.

The reference lists (relevant_adrs, related_tickets, historical_lessons)
are list[UUID] by explicit operator decision: UUIDs for DB traceability;
rendered_markdown carries the human-readable form.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class ContextPack(BaseModel):
    """The compact execution brief given to an agent."""

    id: UUID
    product_id: UUID
    ticket_id: UUID | None = None
    title: str
    objective: str
    constraints: list[str] = Field(default_factory=list)
    relevant_docs: list[str] = Field(default_factory=list)
    relevant_adrs: list[UUID] = Field(default_factory=list)
    related_tickets: list[UUID] = Field(default_factory=list)
    historical_lessons: list[UUID] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    test_commands: list[str] = Field(default_factory=list)
    definition_of_done: list[str] = Field(default_factory=list)
    rendered_markdown: str
    # The ordered compression-ladder rungs that fired while rendering (ATLAS-55,
    # context-renderer.md "Token budget and compression ladder"); empty when the
    # pack was under budget unchanged. Values are the four stable rung ids only.
    compression_applied: list[str] = Field(default_factory=list)
    input_doc_shas: dict[str, str] = Field(default_factory=dict)  # staleness detection
    # SQL INTEGER range.
    token_estimate: int | None = Field(default=None, ge=-2147483648, le=2147483647)
    created_at: datetime
