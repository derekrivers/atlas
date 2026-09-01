"""Shared product-lease guard for ordinary PM workflow provider calls."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TypeVar
from uuid import UUID, uuid4

from atlas.storage import (
    AdmissionCoordinationRepo,
    AdmissionLeaseLostError,
    CIHandoffFencePresentError,
    Database,
)

_WORKFLOW_LEASE_TTL = timedelta(minutes=5)
_T = TypeVar("_T")


class WorkflowWriteWindowClosed(RuntimeError):
    """The product cannot safely start another workflow provider call."""


class PMWorkflowWriteGuard:
    """Serialize an ordinary workflow call against CI-handoff ambiguity."""

    def __init__(self, *, db: Database, observed_at: datetime) -> None:
        self._coordination = AdmissionCoordinationRepo(db)
        self._observed_at = observed_at
        self._consumed = False

    @property
    def consumed(self) -> bool:
        """Whether this tick already completed one workflow-effect call."""

        return self._consumed

    def execute(self, *, product_id: UUID, call: Callable[[], _T]) -> _T:
        """Execute once while the shared lease is live and no CI fence exists."""

        if self._consumed:
            raise WorkflowWriteWindowClosed(
                "the tick workflow-write window was already consumed"
            )
        owner_id = uuid4()
        if not self._coordination.try_acquire(
            product_id=product_id,
            owner_id=owner_id,
            acquired_at=self._observed_at,
            ttl=_WORKFLOW_LEASE_TTL,
        ):
            raise WorkflowWriteWindowClosed(
                "the product workflow lease is already owned"
            )
        try:
            try:
                result = self._coordination.execute_owned_call_if_no_ci_fence(
                    product_id=product_id,
                    owner_id=owner_id,
                    observed_at=self._observed_at,
                    call=call,
                )
                self._consumed = True
                return result
            except (AdmissionLeaseLostError, CIHandoffFencePresentError) as exc:
                raise WorkflowWriteWindowClosed(str(exc)) from exc
        finally:
            self._coordination.release(product_id=product_id, owner_id=owner_id)
