"""Dependency HTTP adapter."""

from typing import Any

from fastapi import APIRouter

from atlas.api.dependencies import (
    DependencyCriticalPathDependency,
    DependencyGraphDependency,
)
from atlas.api.schemas import (
    DependencyCriticalPathResponse,
    DependencyGraphResponse,
    GraphValidationErrorResponse,
)

router = APIRouter(prefix="/dependencies", tags=["dependencies"])
GRAPH_VALIDATION_RESPONSE: dict[int | str, dict[str, Any]] = {
    409: {
        "model": GraphValidationErrorResponse,
        "description": "Stored dependency graph failed integrity validation",
    }
}


@router.get(
    "/critical-path",
    response_model=DependencyCriticalPathResponse,
    responses=GRAPH_VALIDATION_RESPONSE,
)
def dependency_critical_path_route(
    critical_path: DependencyCriticalPathDependency,
) -> DependencyCriticalPathResponse:
    return critical_path


@router.get(
    "/graph",
    response_model=DependencyGraphResponse,
    responses=GRAPH_VALIDATION_RESPONSE,
)
def dependency_graph_route(
    graph: DependencyGraphDependency,
) -> DependencyGraphResponse:
    return graph
