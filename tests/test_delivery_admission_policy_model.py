"""ATLAS-246 delivery admission policy validation and mode invariants."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from atlas.core.enums import RiskLevel
from atlas.core.models import (
    DeliveryAdmissionMode,
    DeliveryAdmissionPolicyRevision,
)
from atlas.core.models.delivery_admission_policy import (
    ComponentLaneLimit,
    DeliveryAdmissionPolicySpec,
    RiskLaneLimit,
)

NOW = datetime(2026, 8, 2, 14, tzinfo=UTC)


def policy_spec(**overrides: Any) -> DeliveryAdmissionPolicySpec:
    values: dict[str, Any] = {
        "mode": "running",
        "approved_symphony_ceiling": 3,
        "working_budget": 3,
        "review_budget": 2,
        "changes_requested_reserve": 1,
        "risk_lane_limits": [{"risk_level": "critical", "limit": 1}],
        "component_lane_limits": [{"component": "atlas.pm", "limit": 2}],
    }
    return DeliveryAdmissionPolicySpec(**(values | overrides))


def policy_revision(**overrides: Any) -> DeliveryAdmissionPolicyRevision:
    values = policy_spec().model_dump() | {
        "id": uuid4(),
        "product_id": uuid4(),
        "revision": 1,
        "created_by_type": "human",
        "created_by_id": "operator",
        "created_at": NOW,
    }
    return DeliveryAdmissionPolicyRevision(**(values | overrides))


def test_ac1_revision_stores_complete_product_capacity_policy() -> None:
    revision = policy_revision()

    assert revision.mode is DeliveryAdmissionMode.RUNNING
    assert revision.approved_symphony_ceiling == 3
    assert revision.working_budget == 3
    assert revision.review_budget == 2
    assert revision.changes_requested_reserve == 1
    assert revision.risk_lane_limits == (
        RiskLaneLimit(risk_level=RiskLevel.CRITICAL, limit=1),
    )
    assert revision.component_lane_limits == (
        ComponentLaneLimit(component="atlas.pm", limit=2),
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"approved_symphony_ceiling": 0},
        {"approved_symphony_ceiling": 11},
        {"approved_symphony_ceiling": True},
        {"working_budget": 0},
        {"approved_symphony_ceiling": 3, "working_budget": 4},
        {"review_budget": 0},
        {"review_budget": 11},
        {"changes_requested_reserve": -1},
        {"working_budget": 2, "changes_requested_reserve": 3},
    ],
    ids=[
        "zero-ceiling",
        "ceiling-above-ten",
        "boolean-ceiling",
        "zero-working",
        "working-above-ceiling",
        "zero-review",
        "review-above-ten",
        "negative-reserve",
        "reserve-above-working",
    ],
)
def test_ac5_rejects_budgets_outside_approved_bounds(
    overrides: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        policy_spec(**overrides)


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "risk_lane_limits": [
                {"risk_level": "high", "limit": 1},
                {"risk_level": "high", "limit": 2},
            ]
        },
        {
            "component_lane_limits": [
                {"component": "Atlas.PM", "limit": 1},
                {"component": "  atlas.pm  ", "limit": 2},
            ]
        },
        {"risk_lane_limits": [{"risk_level": "high", "limit": 4}]},
        {"component_lane_limits": [{"component": "atlas.pm", "limit": 4}]},
        {"component_lane_limits": [{"component": "\n", "limit": 1}]},
    ],
    ids=[
        "duplicate-risk",
        "ambiguous-component",
        "risk-limit-above-working",
        "component-limit-above-working",
        "empty-component",
    ],
)
def test_ac5_rejects_duplicate_ambiguous_or_unbounded_lanes(
    overrides: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        policy_spec(**overrides)


def test_component_selectors_are_canonical_and_policy_is_frozen() -> None:
    policy = policy_spec(
        component_lane_limits=[{"component": "  Atlas.PM  ", "limit": 2}]
    )

    assert policy.component_lane_limits[0].component == "atlas.pm"
    with pytest.raises(ValidationError):
        policy.working_budget = 2


@pytest.mark.parametrize(
    ("mode", "expected"),
    [("running", True), ("paused", False), ("draining", False)],
)
def test_ac6_mode_is_a_fail_closed_new_admission_gate(
    mode: str, expected: bool
) -> None:
    assert policy_spec(mode=mode).permits_new_admission is expected
