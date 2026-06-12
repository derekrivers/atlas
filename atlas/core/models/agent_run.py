"""AgentRun model and its enums (ATLAS-15), per data-model-and-schemas.md
§3.8.

The documented contract has no created_by_* attribution fields, and
input_context_pack_id is FK-less: Phase 8 reconstructs agent runs from
observation (PR ingestion, ATLAS-84), so referential enforcement is
deliberately absent at this layer.
"""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field


class AgentProvider(StrEnum):
    """Which execution provider ran the work (data-model §3.8)."""

    OPENAI = "openai"
    SYMPHONY = "symphony"
    CODEX = "codex"
    CLAUDE = "claude"
    LOCAL = "local"
    HUMAN = "human"


class AgentRunStatus(StrEnum):
    """Lifecycle of an agent run (data-model §3.8)."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    NEEDS_HUMAN = "needs_human"


class AgentRun(BaseModel):
    """A discrete execution by an AI agent or orchestration system."""

    id: UUID
    product_id: UUID
    ticket_id: UUID | None = None
    provider: AgentProvider
    model: str | None = None
    status: AgentRunStatus
    objective: str
    input_context_pack_id: UUID | None = None
    output_summary: str | None = None
    error_summary: str | None = None
    cost_estimate_usd: float | None = None
    # SQL INTEGER range.
    prompt_tokens: int | None = Field(default=None, ge=-2147483648, le=2147483647)
    completion_tokens: int | None = Field(default=None, ge=-2147483648, le=2147483647)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
