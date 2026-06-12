"""PlanRun model and status enum (ATLAS-15), per data-model-and-schemas.md
§3.10 (authoritative; planning-engine-specification.md §6 mirrors it).

Contract only: rows are inserted at proposed and finalised exactly once
to applied, rejected, or failed, setting only approved_by, applied_at,
and failure_reason — that single-transition rule is enforced by the
repository layer's finalize method (ATLAS-18), not here. The model is a
stateless snapshot and does not police status transitions.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PlanRunStatus(StrEnum):
    """Lifecycle of a planning run (data-model §3.10)."""

    PROPOSED = "proposed"
    APPLIED = "applied"
    REJECTED = "rejected"
    FAILED = "failed"


class PlanRun(BaseModel):
    """One execution of the Planning Engine (ADR-0007)."""

    # The documented field names model_provider and model_name collide
    # with Pydantic's default protected namespace "model_"; cleared so
    # the contract names stay verbatim.
    model_config = ConfigDict(protected_namespaces=())

    id: UUID
    product_id: UUID
    status: PlanRunStatus
    input_doc_shas: dict[str, str]  # path -> git blob SHA
    model_provider: str
    model_name: str
    prompt_version: str
    # Provenance chain: input_doc_shas (what was read) -> prompt_hash
    # (what was asked) -> raw_output_hash (what came back).
    prompt_hash: str  # SHA-256 of the rendered prompt text
    similarity_threshold: float
    raw_output_hash: str  # SHA-256 of raw model output
    diff_summary: dict[str, Any] = Field(default_factory=dict)  # counts + entries
    failure_reason: str | None = None
    approved_by: str | None = None
    created_at: datetime
    applied_at: datetime | None = None
