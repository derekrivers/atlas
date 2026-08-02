"""PM sync receipt model (ATLAS-245).

An append-only operational record of one PM sync tick's local completion
boundary. It records fingerprints and counters only: no Linear payload bodies,
credentials, tokens, or unbounded error detail.
"""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field

from atlas.core.enums import ActorType


class PmSyncReceiptResult(StrEnum):
    """Bounded classification for one sync tick receipt."""

    SUCCESS_DEFINITION_CHANGED = "success_definition_changed"
    SUCCESS_STATUS_ONLY = "success_status_only"
    SUCCESS_ZERO_ACTION = "success_zero_action"
    PARTIAL = "partial"
    MALFORMED_PULL = "malformed_pull"
    CANCELLED = "cancelled"
    FAILED = "failed"


SUCCESSFUL_PM_SYNC_RESULTS: frozenset[PmSyncReceiptResult] = frozenset(
    {
        PmSyncReceiptResult.SUCCESS_DEFINITION_CHANGED,
        PmSyncReceiptResult.SUCCESS_STATUS_ONLY,
        PmSyncReceiptResult.SUCCESS_ZERO_ACTION,
    }
)


class PmSyncReceipt(BaseModel):
    """One durable PM sync tick receipt.

    ``finished_at`` is the timestamp used by status projections for the latest
    genuinely successful Linear observation. Failed, cancelled, partial and
    malformed-pull receipts remain visible but are excluded by the repository
    predicate that computes the latest successful instant.
    """

    id: UUID
    product_id: UUID | None = None
    product_key: str | None = None
    linear_project_id: str
    started_at: datetime
    finished_at: datetime
    status_map_fingerprint: str
    fetched_board_fingerprint: str
    fetched_board_issue_count: int = Field(ge=0, le=2147483647)
    result: PmSyncReceiptResult
    counters: dict[str, int]
    error_summary: str | None = None
    created_by_type: ActorType
    created_by_id: str
