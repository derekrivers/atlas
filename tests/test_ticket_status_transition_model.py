"""ATLAS-121: TicketStatusTransition model matches data-model §6.5.

Expected fields are transcribed from the document, not derived from the model,
so a divergence fails. The exact field set is the falsifiable proof of the
append-only field shape (no created_at-vs-updated_at pair, no status) and of the
ticket-scoped shape (ticket_id present and required, but no product_id — unlike
DebtItem and unlike the tick-level TickFailure). The structural tests pin that a
TicketStatusTransition is an operational record, NOT evidence: it carries no
EvidenceStatus and no trust field, so it is not subject to the agent PENDING cap.
Append-only enforcement belongs to the repository layer, not the model.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

import atlas.core.enums
import atlas.core.models
from atlas.core.enums import ActorType
from atlas.core.models import TicketStatusTransition

REQUIRED = object()  # sentinel: field has no default

# data-model §6.5, in documented order: field -> (annotation, default).
DOCUMENTED_FIELDS: dict[str, tuple[Any, Any]] = {
    "id": (UUID, REQUIRED),
    "ticket_id": (UUID, REQUIRED),
    "from_status": (str, REQUIRED),
    "to_status": (str, REQUIRED),
    "occurred_at": (datetime, REQUIRED),
    "created_by_type": (ActorType, REQUIRED),
    "created_by_id": (str, REQUIRED),
}


def transition_kwargs() -> dict[str, Any]:
    return {
        "id": uuid4(),
        "ticket_id": uuid4(),
        "from_status": "in_progress",
        "to_status": "pr_open",
        "occurred_at": datetime(2026, 6, 23, tzinfo=UTC),
        "created_by_type": "system",
        "created_by_id": "pm-engine",
    }


def test_field_set_matches_documented() -> None:
    # Exact set and order. Also the append-only field-shape proof and the
    # ticket-scoped proof: a product_id or a status column cannot exist here.
    assert list(TicketStatusTransition.model_fields) == list(DOCUMENTED_FIELDS)


def test_ticket_scoped_but_not_product_scoped() -> None:
    # §6.5 intent, stated directly: a transition belongs to a ticket (ticket_id
    # required and FK-backed at the table layer, like DebtItem) but is not
    # product-scoped — no product_id, unlike DebtItem.
    assert "ticket_id" in TicketStatusTransition.model_fields
    assert "product_id" not in TicketStatusTransition.model_fields


def test_no_mutation_or_status_fields() -> None:
    # One append-only row per real transition never updates, completes, or
    # carries a lifecycle status; occurred_at is the only instant (no created_at).
    assert "updated_at" not in TicketStatusTransition.model_fields
    assert "created_at" not in TicketStatusTransition.model_fields
    assert "completed_at" not in TicketStatusTransition.model_fields
    assert "status" not in TicketStatusTransition.model_fields


def test_annotations_requiredness() -> None:
    for name, (annotation, default) in DOCUMENTED_FIELDS.items():
        field = TicketStatusTransition.model_fields[name]
        assert field.annotation == annotation, name
        assert default is REQUIRED  # §6.5 has no defaulted fields
        assert field.is_required(), name


def test_is_not_evidence_carries_no_status_or_trust_field() -> None:
    # Structural: a TicketStatusTransition is an operational record, not
    # evidence. It is not the Evidence type, and no field is typed
    # EvidenceStatus — so there is no status for the agent-tier PENDING cap.
    from atlas.core.models import Evidence

    assert not issubclass(TicketStatusTransition, Evidence)
    annotations = {
        field.annotation for field in TicketStatusTransition.model_fields.values()
    }
    assert atlas.core.enums.EvidenceStatus not in annotations


def test_model_is_not_frozen() -> None:
    # Append-only enforcement lives in the repository layer
    # (TicketStatusTransitionRepo), not a frozen model — mirroring DebtItem,
    # TickFailure, and Evidence.
    assert TicketStatusTransition.model_config.get("frozen") is not True


def test_missing_required_field_rejected() -> None:
    incomplete = transition_kwargs()
    del incomplete["from_status"]
    with pytest.raises(ValidationError, match="from_status"):
        TicketStatusTransition(**incomplete)


def test_wrong_type_rejected() -> None:
    with pytest.raises(ValidationError):
        TicketStatusTransition(
            **transition_kwargs() | {"created_by_type": "not-an-actor"}
        )


def test_created_by_type_is_shared_actor_type() -> None:
    # Identity, not equality: created_by_type is the §2 shared ActorType.
    assert TicketStatusTransition.model_fields["created_by_type"].annotation is (
        atlas.core.enums.ActorType
    )
