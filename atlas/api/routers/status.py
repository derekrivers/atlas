"""Operator system status route."""

from fastapi import APIRouter

from atlas.api.dependencies import SystemStatusDependency
from atlas.api.schemas import SystemStatusResponse

router = APIRouter(tags=["status"])


@router.get("/status", response_model=SystemStatusResponse)
def read_system_status(snapshot: SystemStatusDependency) -> SystemStatusResponse:
    return snapshot
