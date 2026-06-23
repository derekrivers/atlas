"""ATLAS-125: TickFailure model matches data-model §6.3.

Expected fields are transcribed from the document, not derived from the
model, so a divergence fails. The exact field set is the falsifiable proof
of the append-only field shape (no created_at-vs-updated_at pair, no status)
and of the tick-level shape (no ticket_id, no product_id — the very reason
it is a separate model from DebtItem and not a DebtItem itself). The
structural tests pin that TickFailure is an operational record, NOT
evidence: it carries no EvidenceStatus and no trust field, so it is not
subject to the agent PENDING cap. Append-only and dedup enforcement belong
to the repository layer, not the model.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

import atlas.core.enums
import atlas.core.models
from atlas.core.enums import ActorType
from atlas.core.models import TickFailure

REQUIRED = object()  # sentinel: field has no default

# data-model §6.3, in documented order: field -> (annotation, default).
DOCUMENTED_FIELDS: dict[str, tuple[Any, Any]] = {
    "id": (UUID, REQUIRED),
    "occurred_at": (datetime, REQUIRED),
    "failure_signature": (str, REQUIRED),
    "detail": (str, REQUIRED),
    "created_by_type": (ActorType, REQUIRED),
    "created_by_id": (str, REQUIRED),
}


def tick_failure_kwargs() -> dict[str, Any]:
    return {
        "id": uuid4(),
        "occurred_at": datetime(2026, 6, 23, tzinfo=UTC),
        "failure_signature": "LinearTimeoutError@sync_tick.pull",
        "detail": "httpx.ReadTimeout: timed out fetching Linear states",
        "created_by_type": "system",
        "created_by_id": "pm-scheduler",
    }


def test_field_set_matches_documented() -> None:
    # Exact set and order. Also the append-only field-shape proof and the
    # tick-level proof: no ticket_id/product_id can exist without failing here.
    assert list(TickFailure.model_fields) == list(DOCUMENTED_FIELDS)


def test_no_ticket_or_product_scope() -> None:
    # §6.3 intent, stated directly: a tick crash is observed at the tick
    # level, not against any one ticket — so no ticket_id and no product_id
    # (the reason it is a separate model from DebtItem, not a DebtItem).
    assert "ticket_id" not in TickFailure.model_fields
    assert "product_id" not in TickFailure.model_fields


def test_no_mutation_or_status_fields() -> None:
    # One append-only row per crash never updates, completes, or carries a
    # lifecycle status.
    assert "updated_at" not in TickFailure.model_fields
    assert "completed_at" not in TickFailure.model_fields
    assert "status" not in TickFailure.model_fields


def test_annotations_requiredness() -> None:
    for name, (annotation, default) in DOCUMENTED_FIELDS.items():
        field = TickFailure.model_fields[name]
        assert field.annotation == annotation, name
        assert default is REQUIRED  # §6.3 has no defaulted fields
        assert field.is_required(), name


def test_is_not_evidence_carries_no_status_or_trust_field() -> None:
    # Structural: a TickFailure is an operational record, not evidence. It is
    # not the Evidence type, and no field is typed EvidenceStatus — so there
    # is no status for the agent-tier PENDING cap to constrain.
    from atlas.core.models import Evidence

    assert not issubclass(TickFailure, Evidence)
    annotations = {field.annotation for field in TickFailure.model_fields.values()}
    assert atlas.core.enums.EvidenceStatus not in annotations


def test_model_is_not_frozen() -> None:
    # Append-only enforcement lives in the repository layer (TickFailureRepo),
    # not in a frozen model config — mirroring DebtItem and Evidence.
    assert TickFailure.model_config.get("frozen") is not True


def test_missing_required_field_rejected() -> None:
    incomplete = tick_failure_kwargs()
    del incomplete["failure_signature"]
    with pytest.raises(ValidationError, match="failure_signature"):
        TickFailure(**incomplete)


def test_wrong_type_rejected() -> None:
    with pytest.raises(ValidationError):
        TickFailure(**tick_failure_kwargs() | {"created_by_type": "not-an-actor"})


def test_created_by_type_is_shared_actor_type() -> None:
    # Identity, not equality: created_by_type is the §2 shared ActorType.
    assert TickFailure.model_fields["created_by_type"].annotation is (
        atlas.core.enums.ActorType
    )
