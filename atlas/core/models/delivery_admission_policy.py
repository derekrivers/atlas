"""Immutable, operator-owned delivery admission policy revisions."""

from __future__ import annotations

import unicodedata
from datetime import datetime
from enum import StrEnum
from typing import Any, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from atlas.core.enums import ActorType, RiskLevel

MAX_APPROVED_SYMPHONY_CEILING = 10
MAX_COMPONENT_LANES = 64
MAX_COMPONENT_SELECTOR_LENGTH = 128


class DeliveryAdmissionMode(StrEnum):
    """Operator-selected admission posture."""

    RUNNING = "running"
    PAUSED = "paused"
    DRAINING = "draining"


class RiskLaneLimit(BaseModel):
    """Maximum working occupancy for one exact risk level."""

    model_config = ConfigDict(frozen=True)

    risk_level: RiskLevel
    limit: int = Field(ge=0, le=MAX_APPROVED_SYMPHONY_CEILING)

    @field_validator("limit", mode="before")
    @classmethod
    def _limit_is_a_strict_integer(cls, value: Any) -> Any:
        if type(value) is not int:
            raise ValueError("risk lane limit must be an integer")
        return value


class ComponentLaneLimit(BaseModel):
    """Maximum working occupancy for one canonical component selector."""

    model_config = ConfigDict(frozen=True)

    component: str = Field(min_length=1, max_length=MAX_COMPONENT_SELECTOR_LENGTH)
    limit: int = Field(ge=0, le=MAX_APPROVED_SYMPHONY_CEILING)

    @field_validator("component", mode="before")
    @classmethod
    def _canonical_component_selector(cls, value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("component lane selector must be a string")
        canonical = unicodedata.normalize("NFKC", value).strip().casefold()
        if not canonical:
            raise ValueError("component lane selector must be non-empty")
        if len(canonical) > MAX_COMPONENT_SELECTOR_LENGTH:
            raise ValueError("component lane selector is too long")
        if any(ord(character) < 32 or ord(character) == 127 for character in canonical):
            raise ValueError("component lane selector contains a control character")
        return canonical

    @field_validator("limit", mode="before")
    @classmethod
    def _limit_is_a_strict_integer(cls, value: Any) -> Any:
        if type(value) is not int:
            raise ValueError("component lane limit must be an integer")
        return value


class DeliveryAdmissionPolicySpec(BaseModel):
    """Complete validated values used to create one policy revision."""

    model_config = ConfigDict(frozen=True)

    mode: DeliveryAdmissionMode
    approved_symphony_ceiling: int = Field(ge=1, le=MAX_APPROVED_SYMPHONY_CEILING)
    working_budget: int = Field(ge=1, le=MAX_APPROVED_SYMPHONY_CEILING)
    review_budget: int = Field(ge=1, le=MAX_APPROVED_SYMPHONY_CEILING)
    changes_requested_reserve: int = Field(ge=0, le=MAX_APPROVED_SYMPHONY_CEILING)
    risk_lane_limits: tuple[RiskLaneLimit, ...] = Field(
        default_factory=tuple,
        max_length=len(RiskLevel),
    )
    component_lane_limits: tuple[ComponentLaneLimit, ...] = Field(
        default_factory=tuple,
        max_length=MAX_COMPONENT_LANES,
    )

    @field_validator(
        "approved_symphony_ceiling",
        "working_budget",
        "review_budget",
        "changes_requested_reserve",
        mode="before",
    )
    @classmethod
    def _budgets_are_strict_integers(cls, value: Any) -> Any:
        if type(value) is not int:
            raise ValueError("delivery policy budgets must be integers")
        return value

    @model_validator(mode="after")
    def _capacity_is_coherent(self) -> Self:
        if self.working_budget > self.approved_symphony_ceiling:
            raise ValueError("working_budget exceeds approved_symphony_ceiling")
        if self.changes_requested_reserve > self.working_budget:
            raise ValueError("changes_requested_reserve exceeds working_budget")

        risk_levels = [lane.risk_level for lane in self.risk_lane_limits]
        if len(risk_levels) != len(set(risk_levels)):
            raise ValueError("risk lane selectors must be unique")
        components = [lane.component for lane in self.component_lane_limits]
        if len(components) != len(set(components)):
            raise ValueError("component lane selectors are duplicate or ambiguous")

        for risk_lane in self.risk_lane_limits:
            if risk_lane.limit > self.working_budget:
                raise ValueError("lane limit exceeds working_budget")
        for component_lane in self.component_lane_limits:
            if component_lane.limit > self.working_budget:
                raise ValueError("lane limit exceeds working_budget")
        return self

    @property
    def permits_new_admission(self) -> bool:
        """Whether admission may proceed before occupancy checks are applied."""

        return self.mode is DeliveryAdmissionMode.RUNNING


class DeliveryAdmissionPolicyRevision(DeliveryAdmissionPolicySpec):
    """One immutable policy revision for one product."""

    id: UUID
    product_id: UUID
    revision: int = Field(ge=1)
    created_by_type: ActorType
    created_by_id: str = Field(min_length=1, max_length=128)
    created_at: datetime

    @field_validator("revision", mode="before")
    @classmethod
    def _revision_is_a_strict_integer(cls, value: Any) -> Any:
        if type(value) is not int:
            raise ValueError("delivery policy revision must be an integer")
        return value
