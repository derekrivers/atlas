"""One-time ATLAS-280/ATLAS-281 bootstrap mirror-recovery receipt.

This model is deliberately incident-specific.  It is not a reusable ticket
transition primitive and its fixed identities prevent callers from retargeting
the storage seam to arbitrary backlog work.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from atlas.core.enums import ActorType

ATLAS_280_TICKET_ID = UUID("2bc3ba0a-c48d-4be9-8818-37270fd79f49")
ATLAS_281_TICKET_ID = UUID("fc932a13-b0c3-41f5-801d-98b62597aeb9")
ATLAS_280_LINEAR_ID = "f573f3e5-78f8-4d55-a609-036647718c11"
ATLAS_281_LINEAR_ID = "4b24c888-7e10-4b75-8f97-b60cdc0893ea"
ATLAS_280_ADMISSION_RUN_ID = UUID("07c7d02a-8eb6-5a66-870b-b8cb9c3a6cef")
ATLAS_280_PM_RECEIPT_ID = UUID("7eb68c0e-7b49-42b1-b146-eef974df3cd3")
ATLAS_280_DEBT_ITEM_ID = UUID("26bfc848-f7a9-4287-be73-c7c2de12ed44")
ATLAS_280_PUBLICATION_HEAD = "f2e1ab3b8e72f11350f1da7315ad4952ef074a1b"
ATLAS_280_ADMISSION_POLICY_REVISION = 16
ATLAS_280_POLICY_REVISION = 17
ATLAS_280_POLICY_FINGERPRINT = (
    "5bb3af49adb7dc87ecb602256a197c44626f18256cbbc59e64aa82cd99578655"
)


class Atlas280BootstrapRecoveryReceipt(BaseModel):
    """Bounded append-only proof of the one authorized local mirror repair."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["atlas-280-bootstrap-mirror-recovery-v1"] = (
        "atlas-280-bootstrap-mirror-recovery-v1"
    )
    id: UUID
    product_id: UUID
    blocker_ticket_id: UUID
    blocker_ticket_key: Literal["ATLAS-280"] = "ATLAS-280"
    blocker_linear_issue_id: str = Field(min_length=36, max_length=36)
    blocker_linear_identifier: Literal["ATL-456"] = "ATL-456"
    blocker_linear_state_id: str = Field(min_length=1, max_length=128)
    repair_ticket_id: UUID
    repair_ticket_key: Literal["ATLAS-281"] = "ATLAS-281"
    repair_linear_issue_id: str = Field(min_length=36, max_length=36)
    repair_linear_identifier: Literal["ATL-457"] = "ATL-457"
    repair_linear_state_id: str = Field(min_length=1, max_length=128)
    source_local_status: Literal["planned"] = "planned"
    recovered_local_status: Literal["ci_pending"] = "ci_pending"
    admission_run_id: UUID
    pm_sync_receipt_id: UUID
    publication_repository_owner: Literal["derekrivers"] = "derekrivers"
    publication_repository_name: Literal["atlas"] = "atlas"
    publication_pr_number: Literal[350] = 350
    publication_head: str = Field(pattern=r"^[0-9a-f]{40}$")
    historical_debt_item_id: UUID
    board_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy_id: UUID
    policy_revision: Literal[17] = 17
    policy_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    accepted_main_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    created_at: datetime
    created_by_type: Literal[ActorType.HUMAN] = ActorType.HUMAN
    created_by_id: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def _fixed_incident_identities(self) -> Self:
        expected = {
            "blocker_ticket_id": ATLAS_280_TICKET_ID,
            "blocker_linear_issue_id": ATLAS_280_LINEAR_ID,
            "repair_ticket_id": ATLAS_281_TICKET_ID,
            "repair_linear_issue_id": ATLAS_281_LINEAR_ID,
            "admission_run_id": ATLAS_280_ADMISSION_RUN_ID,
            "pm_sync_receipt_id": ATLAS_280_PM_RECEIPT_ID,
            "publication_head": ATLAS_280_PUBLICATION_HEAD,
            "historical_debt_item_id": ATLAS_280_DEBT_ITEM_ID,
            "policy_revision": ATLAS_280_POLICY_REVISION,
            "policy_fingerprint": ATLAS_280_POLICY_FINGERPRINT,
        }
        for field, value in expected.items():
            if getattr(self, field) != value:
                raise ValueError(f"{field} is not the ruled ATLAS-280 identity")
        return self
