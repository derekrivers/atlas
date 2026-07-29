"""Evidence model and type enum (ATLAS-14), per data-model-and-schemas.md
§3.7.

Contract only: append-only enforcement and the agent-tier PENDING cap
live in the repository layer (ATLAS-18, consuming evidence_tier per
knowledge-core.md) — the model does not police mutation or status.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from atlas.core.enums import ActorType, EvidenceStatus


class EvidenceType(StrEnum):
    """What an evidence record attests (data-model §3.7)."""

    TEST_RESULT = "test_result"
    BUILD_RESULT = "build_result"
    LINT_RESULT = "lint_result"
    COVERAGE_REPORT = "coverage_report"
    SCREENSHOT = "screenshot"
    PR_REVIEW = "pr_review"
    DEPLOYMENT_RESULT = "deployment_result"
    DOCUMENTATION_UPDATE = "documentation_update"
    MANUAL_APPROVAL = "manual_approval"
    PR_MERGED = "pr_merged"


class Evidence(BaseModel):
    """Proof of completion, failure, warning, or validation.

    Append-only. Trust tiers per ADR-0008: created_by_type system|human
    may carry any status; agent-created evidence is capped at PENDING
    until corroborated by a system-tier record or human approval.
    """

    id: UUID
    product_id: UUID
    ticket_id: UUID | None = None
    # Deliberately FK-less: Phase 8 reconstructs agent runs from
    # observation, so evidence may precede its run row.
    agent_run_id: UUID | None = None
    evidence_type: EvidenceType
    status: EvidenceStatus
    summary: str
    commit_sha: str | None = None  # required for system-tier CI evidence
    external_run_id: str | None = None  # CI workflow / check run ID
    job_name: str | None = None  # CI job/check identity for per-job resolution
    source_event_at: datetime | None = None  # lifecycle time supplied by GitHub
    payload_hash: str | None = None  # SHA-256 of raw payload at ingestion
    source_uri: str | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    created_by_type: ActorType
    created_by_id: str
    created_at: datetime
