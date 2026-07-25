"""Dependency HTTP adapter."""

from fastapi import APIRouter

from atlas.api.dependencies import DependencyCriticalPathDependency
from atlas.api.schemas import DependencyCriticalPathResponse

router = APIRouter(prefix="/dependencies", tags=["dependencies"])


@router.get("/critical-path", response_model=DependencyCriticalPathResponse)
def dependency_critical_path_route(
    critical_path: DependencyCriticalPathDependency,
) -> DependencyCriticalPathResponse:
    return critical_path
