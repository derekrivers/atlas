"""Evidence model and type enum (ATLAS-14), per data-model-and-schemas.md
§3.7.

Contract only: append-only enforcement and the agent-tier PENDING cap
live in the repository layer (ATLAS-18, consuming evidence_tier per
knowledge-core.md) — the model does not police mutation or status.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, Any, Final
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints, model_validator

from atlas.core.enums import ActorType, EvidenceStatus

MAX_DOCUMENTATION_PATHS: Final = 256
MAX_DOCUMENTATION_PATH_LENGTH: Final = 240
DocumentationPath = Annotated[
    str,
    StringConstraints(min_length=1, max_length=MAX_DOCUMENTATION_PATH_LENGTH),
]


def canonical_documentation_paths(value: object) -> tuple[str, ...] | None:
    """Return one exact bounded docs-path set, or ``None`` when malformed.

    Structured documentation evidence is deliberately stricter than arbitrary
    Git paths: paths must use the repository's canonical POSIX-relative form,
    stay under ``docs/``, and already be a sorted unique tuple.  Callers never
    truncate, reorder, de-duplicate, or otherwise guess at coverage.
    """

    if not isinstance(value, (list, tuple)):
        return None
    paths = tuple(value)
    if not 1 <= len(paths) <= MAX_DOCUMENTATION_PATHS:
        return None
    if any(not _is_canonical_documentation_path(path) for path in paths):
        return None
    if paths != tuple(sorted(set(paths))):
        return None
    return paths


def _is_canonical_documentation_path(value: object) -> bool:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= MAX_DOCUMENTATION_PATH_LENGTH
        or not value.startswith("docs/")
        or value.endswith("/")
        or "\\" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        return False
    path = PurePosixPath(value)
    return (
        not path.is_absolute()
        and path.as_posix() == value
        and all(part not in {"", ".", ".."} for part in path.parts)
    )


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
    docs_paths: tuple[DocumentationPath, ...] | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_DOCUMENTATION_PATHS,
    )
    created_by_type: ActorType
    created_by_id: str
    created_at: datetime

    @model_validator(mode="after")
    def validate_documentation_projection(self) -> Evidence:
        """Keep the nullable legacy shape separate from strict v2 evidence."""

        versioned_identity = isinstance(
            self.external_run_id, str
        ) and self.external_run_id.startswith("docs:v2:")
        if self.docs_paths is None:
            if versioned_identity:
                raise ValueError("docs:v2 evidence requires docs_paths")
            return self
        if self.evidence_type is not EvidenceType.DOCUMENTATION_UPDATE:
            raise ValueError("docs_paths is only valid for documentation_update")
        if canonical_documentation_paths(self.docs_paths) is None:
            raise ValueError(
                "docs_paths must be a bounded sorted unique set of canonical "
                "repository-relative docs/ paths"
            )
        expected_identity = (
            None if self.commit_sha is None else f"docs:v2:{self.commit_sha}"
        )
        if self.external_run_id != expected_identity:
            raise ValueError(
                "structured documentation evidence requires docs:v2:<commit_sha>"
            )
        return self
