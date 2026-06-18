"""ATLAS-116: DebtItem model and AnomalyType match data-model §6.1.

Expected fields are transcribed from the document, not derived from the
model, so a divergence fails. The exact field set is the falsifiable
proof of the append-only field shape (created_at only, no updated_at, no
status). The structural tests pin that DebtItem is an operational record,
NOT evidence: it carries no EvidenceStatus and no trust field, so it is
not subject to the agent PENDING cap (decision D2). Append-only and
recurrence enforcement belong to the repository layer (decision D4), not
the model.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

import atlas.core.enums
import atlas.core.models
from atlas.core.enums import ActorType
from atlas.core.models import AnomalyType, DebtItem

REQUIRED = object()  # sentinel: field has no default

# data-model §6.1, in documented order: field -> (annotation, default).
DOCUMENTED_FIELDS: dict[str, tuple[Any, Any]] = {
    "id": (UUID, REQUIRED),
    "product_id": (UUID, REQUIRED),
    "ticket_id": (UUID, REQUIRED),
    "anomaly_type": (AnomalyType, REQUIRED),
    "summary": (str, REQUIRED),
    "observed_at": (datetime, REQUIRED),
    "created_by_type": (ActorType, REQUIRED),
    "created_by_id": (str, REQUIRED),
    "created_at": (datetime, REQUIRED),
}

# data-model §6.1 (three members)
DOCUMENTED_TYPES = {
    "OUT_OF_OWNERSHIP_TRANSITION": "out_of_ownership_transition",
    "REVIEW_CYCLE": "review_cycle",
    "DWELL_BREACH": "dwell_breach",
}


def debt_item_kwargs() -> dict[str, Any]:
    return {
        "id": uuid4(),
        "product_id": uuid4(),
        "ticket_id": uuid4(),
        "anomaly_type": "out_of_ownership_transition",
        "summary": "ATLAS-7 moved Linear In Review -> Done (Atlas owns that edge)",
        "observed_at": datetime(2026, 6, 12, tzinfo=UTC),
        "created_by_type": "system",
        "created_by_id": "pm-engine",
        "created_at": datetime(2026, 6, 12, tzinfo=UTC),
    }


def test_field_set_matches_documented() -> None:
    # Exact set and order. Also the append-only field-shape proof:
    # created_at is present and no updated_at or status can exist without
    # failing here.
    assert list(DebtItem.model_fields) == list(DOCUMENTED_FIELDS)


def test_no_mutation_or_status_fields() -> None:
    # States the §6.1 intent directly: one append-only row per
    # observation never updates, completes, or carries a lifecycle status.
    assert "updated_at" not in DebtItem.model_fields
    assert "completed_at" not in DebtItem.model_fields
    assert "status" not in DebtItem.model_fields


def test_annotations_requiredness() -> None:
    for name, (annotation, default) in DOCUMENTED_FIELDS.items():
        field = DebtItem.model_fields[name]
        assert field.annotation == annotation, name
        assert default is REQUIRED  # §6.1 has no defaulted fields
        assert field.is_required(), name


def test_anomaly_type_members_and_values_match_documented() -> None:
    actual = {member.name: member.value for member in AnomalyType}
    assert actual == DOCUMENTED_TYPES


def test_anomaly_type_is_string_valued() -> None:
    assert issubclass(AnomalyType, str)
    for member in AnomalyType:
        assert member == member.value


def test_is_not_evidence_carries_no_status_or_trust_field() -> None:
    # Decision D2, structural: a DebtItem is an operational record, not
    # evidence. It is not the Evidence type, and no field is typed
    # EvidenceStatus — so there is no status for the agent-tier PENDING
    # cap (which EvidenceRepo enforces on EvidenceStatus) to constrain. An
    # EvidenceStatus field silently appearing here fails this test.
    from atlas.core.models import Evidence

    assert not issubclass(DebtItem, Evidence)
    annotations = {field.annotation for field in DebtItem.model_fields.values()}
    assert atlas.core.enums.EvidenceStatus not in annotations


def test_model_is_not_frozen() -> None:
    # Decision D4: append-only enforcement lives in the repository layer
    # (DebtItemRepo), not in a frozen model config — mirroring Evidence. A
    # mutation guard silently appearing here fails this test.
    assert DebtItem.model_config.get("frozen") is not True


def test_missing_required_field_rejected() -> None:
    incomplete = debt_item_kwargs()
    del incomplete["summary"]
    with pytest.raises(ValidationError, match="summary"):
        DebtItem(**incomplete)


def test_wrong_type_rejected() -> None:
    with pytest.raises(ValidationError):
        DebtItem(**debt_item_kwargs() | {"anomaly_type": "not-an-anomaly"})


def test_enum_identity() -> None:
    # Identity, not equality: the annotations are the canonical classes.
    # anomaly_type is the model-local AnomalyType (data-model §6.1);
    # created_by_type is the §2 shared ActorType.
    assert DebtItem.model_fields["anomaly_type"].annotation is (
        atlas.core.models.AnomalyType
    )
    assert DebtItem.model_fields["created_by_type"].annotation is (
        atlas.core.enums.ActorType
    )
