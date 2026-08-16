"""Governed command service for delivery admission policy revisions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from atlas.core.enums import ActorType
from atlas.core.models import (
    DeliveryAdmissionPolicyRevision,
    OperatorActionOutcome,
    OperatorActionReceipt,
    OperatorActionResultCode,
)
from atlas.core.models.delivery_admission_policy import DeliveryAdmissionPolicySpec
from atlas.orchestration.operator_actions import (
    OperatorActionCommandContext,
    OperatorActionCommandResult,
    OperatorActionEntityLoad,
    OperatorActionEnvelope,
    OperatorActionFailureCode,
    OperatorActionGateway,
    OperatorActionGatewayResult,
    OperatorActionGatewayStatus,
    OperatorActionMutation,
    canonical_request_fingerprint,
)
from atlas.pm.protected_lanes import (
    DEFAULT_PROTECTED_LANE_REGISTRY,
    ProtectedLaneRegistry,
)
from atlas.storage import Database, DeliveryAdmissionPolicyRepo, ProductRepo
from atlas.storage.tables import (
    DeliveryAdmissionPolicyActiveRow,
    DeliveryAdmissionPolicyRevisionRow,
    ProductRow,
)

POLICY_ACTION = "delivery_admission_policy.revise"
POLICY_TARGET_TYPE = "product"
SERVER_OPERATOR_ID = "operator"


class DeliveryAdmissionPolicyChangeStatus(StrEnum):
    """Stable application-service result for a policy command."""

    APPLIED = "applied"
    REPLAYED = "replayed"
    CONFLICT = "conflict"
    REFUSED = "refused"
    FAILED = "failed"


class DeliveryAdmissionPolicyConflictCode(StrEnum):
    """Conflict causes that never widen policy authority."""

    STALE_REVISION = "stale_revision"
    IDEMPOTENCY_KEY_REUSED = "idempotency_key_reused"
    IN_PROGRESS = "in_progress"


@dataclass(frozen=True)
class DeliveryAdmissionPolicyChangeResult:
    """One governed policy-command result and safe store projection."""

    status: DeliveryAdmissionPolicyChangeStatus
    policy: DeliveryAdmissionPolicyRevision | None = None
    current_policy: DeliveryAdmissionPolicyRevision | None = None
    receipt: OperatorActionReceipt | None = None
    conflict_code: DeliveryAdmissionPolicyConflictCode | None = None
    failure_code: OperatorActionFailureCode | None = None


class DeliveryAdmissionPolicyService:
    """Create immutable revisions through the Phase 13 command boundary.

    Actor attribution is deliberately not a method parameter. This service is
    the authenticated server-side operator boundary and always supplies the
    single-operator identity itself.
    """

    def __init__(
        self,
        db: Database,
        *,
        clock: Callable[[], datetime],
        policy_id_factory: Callable[[], UUID] = uuid4,
        receipt_id_factory: Callable[[], UUID] = uuid4,
        correlation_id_factory: Callable[[], UUID] = uuid4,
        protected_lane_registry: ProtectedLaneRegistry = (
            DEFAULT_PROTECTED_LANE_REGISTRY
        ),
    ) -> None:
        self._repo = DeliveryAdmissionPolicyRepo(db)
        self._products = ProductRepo(db)
        self._gateway = OperatorActionGateway(
            db,
            clock=clock,
            receipt_id_factory=receipt_id_factory,
            correlation_id_factory=correlation_id_factory,
        )
        self._policy_id_factory = policy_id_factory
        self._protected_lane_registry = protected_lane_registry

    def revise_current(
        self,
        *,
        expected_revision: int,
        idempotency_key: str,
        policy: DeliveryAdmissionPolicySpec,
    ) -> DeliveryAdmissionPolicyChangeResult:
        """Revise the single local product without accepting client identity."""

        products = self._products.list()
        if len(products) != 1:
            return DeliveryAdmissionPolicyChangeResult(
                status=DeliveryAdmissionPolicyChangeStatus.REFUSED
            )
        return self.revise(
            product_id=products[0].id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            policy=policy,
        )

    def revise(
        self,
        *,
        product_id: UUID,
        expected_revision: int,
        idempotency_key: str,
        policy: DeliveryAdmissionPolicySpec,
    ) -> DeliveryAdmissionPolicyChangeResult:
        """Compare-and-set one complete policy and append its action receipt."""

        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("expected_revision must be a non-negative integer")
        if not isinstance(policy, DeliveryAdmissionPolicySpec):
            raise TypeError("policy must be a validated DeliveryAdmissionPolicySpec")

        payload = (
            {"expected_revision": expected_revision}
            | policy.model_dump(mode="json")
            | {
                "protected_lane_registry": {
                    "version": self._protected_lane_registry.version,
                    "fingerprint": self._protected_lane_registry.fingerprint,
                }
            }
        )
        envelope = OperatorActionEnvelope(
            action=POLICY_ACTION,
            target_type=POLICY_TARGET_TYPE,
            target_id=str(product_id),
            created_by_type=ActorType.HUMAN,
            created_by_id=SERVER_OPERATOR_ID,
            idempotency_key=idempotency_key,
            request_fingerprint=canonical_request_fingerprint(
                action=POLICY_ACTION,
                target_type=POLICY_TARGET_TYPE,
                target_id=str(product_id),
                payload=payload,
            ),
        )

        result = self._gateway.execute(
            envelope,
            self._revision_command(
                product_id=product_id,
                expected_revision=expected_revision,
                policy=policy,
            ),
            loads=(
                OperatorActionEntityLoad(
                    "product", ProductRow, product_id, for_update=True
                ),
                OperatorActionEntityLoad(
                    "active_policy",
                    DeliveryAdmissionPolicyActiveRow,
                    product_id,
                    for_update=True,
                ),
            ),
        )
        return self._present_result(
            product_id=product_id,
            expected_revision=expected_revision,
            gateway_result=result,
        )

    def _revision_command(
        self,
        *,
        product_id: UUID,
        expected_revision: int,
        policy: DeliveryAdmissionPolicySpec,
    ) -> Callable[[OperatorActionCommandContext], OperatorActionCommandResult]:
        def command(
            context: OperatorActionCommandContext,
        ) -> OperatorActionCommandResult:
            product = context.entity("product", ProductRow)
            if product is None:
                return OperatorActionCommandResult(
                    outcome=OperatorActionOutcome.REFUSED,
                    result_code=OperatorActionResultCode.ACTION_REFUSED,
                    result_metadata={"changed": False, "affected_count": 0},
                )

            active = context.entity("active_policy", DeliveryAdmissionPolicyActiveRow)
            current_revision = 0 if active is None else active.revision
            if current_revision != expected_revision:
                return OperatorActionCommandResult(
                    outcome=OperatorActionOutcome.CONFLICT,
                    result_code=OperatorActionResultCode.ACTION_CONFLICT,
                    result_metadata={"changed": False, "affected_count": 0},
                )

            next_revision = current_revision + 1
            revision = DeliveryAdmissionPolicyRevision(
                **policy.model_dump(),
                id=self._policy_id_factory(),
                product_id=product_id,
                revision=next_revision,
                created_by_type=ActorType.HUMAN,
                created_by_id=SERVER_OPERATOR_ID,
                created_at=context.created_at,
            )
            revision_values = revision.model_dump()
            revision_values["mode"] = revision.mode.value
            revision_values["created_by_type"] = revision.created_by_type.value
            revision_values["risk_lane_limits"] = [
                lane.model_dump(mode="json") for lane in revision.risk_lane_limits
            ]
            revision_values["component_lane_limits"] = [
                lane.model_dump(mode="json") for lane in revision.component_lane_limits
            ]
            revision_row = DeliveryAdmissionPolicyRevisionRow(**revision_values)

            if active is None:
                active = DeliveryAdmissionPolicyActiveRow(
                    product_id=product_id,
                    revision=next_revision,
                )
            else:
                active.revision = next_revision

            return OperatorActionCommandResult(
                outcome=OperatorActionOutcome.SUCCEEDED,
                result_code=OperatorActionResultCode.ACTION_SUCCEEDED,
                result_metadata={"changed": True, "affected_count": 1},
                mutations=(
                    OperatorActionMutation(revision_row),
                    OperatorActionMutation(active),
                ),
            )

        return command

    def _present_result(
        self,
        *,
        product_id: UUID,
        expected_revision: int,
        gateway_result: OperatorActionGatewayResult,
    ) -> DeliveryAdmissionPolicyChangeResult:
        current = self._repo.get_active(product_id)
        receipt = gateway_result.receipt
        if receipt is not None and receipt.outcome is OperatorActionOutcome.SUCCEEDED:
            policy = self._repo.get_revision(product_id, expected_revision + 1)
            if policy is None:
                return DeliveryAdmissionPolicyChangeResult(
                    status=DeliveryAdmissionPolicyChangeStatus.FAILED,
                    current_policy=current,
                    receipt=receipt,
                    failure_code=OperatorActionFailureCode.STORAGE_FAILED,
                )
            status = (
                DeliveryAdmissionPolicyChangeStatus.REPLAYED
                if gateway_result.status is OperatorActionGatewayStatus.REPLAYED
                else DeliveryAdmissionPolicyChangeStatus.APPLIED
            )
            return DeliveryAdmissionPolicyChangeResult(
                status=status,
                policy=policy,
                current_policy=current,
                receipt=receipt,
            )

        if receipt is not None and receipt.outcome is OperatorActionOutcome.CONFLICT:
            return DeliveryAdmissionPolicyChangeResult(
                status=DeliveryAdmissionPolicyChangeStatus.CONFLICT,
                current_policy=current,
                receipt=receipt,
                conflict_code=DeliveryAdmissionPolicyConflictCode.STALE_REVISION,
            )

        if receipt is not None and receipt.outcome is OperatorActionOutcome.REFUSED:
            return DeliveryAdmissionPolicyChangeResult(
                status=DeliveryAdmissionPolicyChangeStatus.REFUSED,
                current_policy=current,
                receipt=receipt,
            )

        if gateway_result.status is OperatorActionGatewayStatus.CONFLICT:
            return DeliveryAdmissionPolicyChangeResult(
                status=DeliveryAdmissionPolicyChangeStatus.CONFLICT,
                current_policy=current,
                conflict_code=(
                    DeliveryAdmissionPolicyConflictCode.IDEMPOTENCY_KEY_REUSED
                ),
            )
        if gateway_result.status is OperatorActionGatewayStatus.IN_PROGRESS:
            return DeliveryAdmissionPolicyChangeResult(
                status=DeliveryAdmissionPolicyChangeStatus.CONFLICT,
                current_policy=current,
                conflict_code=DeliveryAdmissionPolicyConflictCode.IN_PROGRESS,
            )
        return DeliveryAdmissionPolicyChangeResult(
            status=DeliveryAdmissionPolicyChangeStatus.FAILED,
            current_policy=current,
            failure_code=(
                None if gateway_result.failure is None else gateway_result.failure.code
            ),
        )
