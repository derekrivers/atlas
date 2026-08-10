"""Authenticated delivery-control status and complete policy replacement."""

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from atlas.api.dependencies import (
    DeliveryControlDependency,
    ReplacedDeliveryAdmissionPolicyDependency,
)
from atlas.api.schemas import (
    DeliveryAdmissionPolicyConflictResponse,
    DeliveryAdmissionPolicyResponse,
    DeliveryControlErrorResponse,
    DeliveryControlResponse,
)

router = APIRouter(prefix="/delivery-control", tags=["delivery-control"])

_DELIVERY_CONTROL_READ_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": DeliveryControlErrorResponse},
    409: {"model": DeliveryControlErrorResponse},
}
_POLICY_REPLACEMENT_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": DeliveryControlErrorResponse},
    403: {"model": DeliveryControlErrorResponse},
    409: {"model": DeliveryAdmissionPolicyConflictResponse},
    415: {"model": DeliveryControlErrorResponse},
    500: {"model": DeliveryControlErrorResponse},
}


@router.get(
    "",
    response_model=DeliveryControlResponse,
    responses=_DELIVERY_CONTROL_READ_RESPONSES,
)
def read_delivery_control(
    delivery_control: DeliveryControlDependency,
) -> DeliveryControlResponse | JSONResponse:
    return delivery_control


@router.post(
    "/policy",
    response_model=DeliveryAdmissionPolicyResponse,
    responses=_POLICY_REPLACEMENT_RESPONSES,
)
def replace_delivery_admission_policy(
    replacement: ReplacedDeliveryAdmissionPolicyDependency,
) -> DeliveryAdmissionPolicyResponse | JSONResponse:
    return replacement
