"""VerificationCheck model and type enum (ATLAS-71), per
data-model-and-schemas.md §5.1.

A VerificationCheck records one Verification Engine evaluation for a
ticket. It is NOT evidence (ADR-0008): it carries an ``EvidenceStatus``
outcome but no trust tier and no commit pin, and it is never the proof
itself — ``evidence_ids`` references the Evidence records that justify the
outcome. Append-only enforcement lives in the repository layer
(VerificationCheckRepo), mirroring Evidence — the model does not police
mutation or status.

Contract only: this ticket lands the model, its storage, the required-check
matrix, and the pure rule-resolver. The per-check evaluators that populate
``status``/``evidence_ids`` and the validators/CLI that consume these rows
are later Phase 7 tickets (verification-engine.md).
"""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field

from atlas.core.enums import EvidenceStatus


class VerificationCheckType(StrEnum):
    """Which Verification Engine check a row records (data-model §5.1).

    A model-specific enum, defined with its model like EvidenceType — not a
    §2 shared type — and used by VerificationCheck, VerificationCheckRepo,
    and the rule-resolver. Members and values are verbatim from §5.1.
    """

    ACCEPTANCE_CRITERIA = "acceptance_criteria"
    TESTS = "tests"
    LINT = "lint"
    DOCUMENTATION = "documentation"
    SCOPE = "scope"
    SECURITY = "security"
    HUMAN_APPROVAL = "human_approval"


class VerificationCheck(BaseModel):
    """One Verification Engine evaluation for a ticket (data-model §5.1).

    NOT evidence: ``status`` is an :class:`EvidenceStatus` outcome, but the
    record carries no trust tier and no commit pin (contrast Evidence). The
    proof lives elsewhere — ``evidence_ids`` references the Evidence records
    that justify ``status``. Append-only: there is no ``updated_at``;
    ``completed_at`` marks when the evaluation reached a terminal outcome
    (append-only enforcement lives in VerificationCheckRepo, not here).
    """

    id: UUID
    ticket_id: UUID
    check_type: VerificationCheckType
    status: EvidenceStatus
    summary: str
    required: bool = True
    evidence_ids: list[UUID] = Field(default_factory=list)
    created_at: datetime
    completed_at: datetime | None = None
