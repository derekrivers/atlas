"""Stored-data assembly for dependency read projections."""

from __future__ import annotations

from dataclasses import dataclass

from atlas.core.keys import natural_key
from atlas.core.models import DependencyType
from atlas.dependencies import (
    BlockedResult,
    CriticalPath,
    ReadinessResult,
    UnlocksResult,
    blocked,
    build_dependency_graph,
    critical_path,
    is_ready,
    unlocks,
    validate_graph,
)
from atlas.storage import Database

_DEPENDS_ON = DependencyType.DEPENDS_ON.value


@dataclass(frozen=True)
class TicketDependencyState:
    """Dependency state for one ticket over the projected graph."""

    key: str
    blockers: BlockedResult
    blocked_by: UnlocksResult
    readiness: ReadinessResult


@dataclass(frozen=True)
class DependencyGraphNodeState:
    """One node in the projected dependency graph."""

    key: str
    status: str
    node_type: str


@dataclass(frozen=True)
class DependencyGraphEdgeState:
    """One depends_on edge in the projected dependency graph."""

    source: str
    target: str
    dependency_type: DependencyType


@dataclass(frozen=True)
class DependencyGraphState:
    """The whole projected dependency graph in deterministic response order."""

    nodes: tuple[DependencyGraphNodeState, ...]
    edges: tuple[DependencyGraphEdgeState, ...]


def ticket_dependencies(
    db: Database,
    key: str,
) -> TicketDependencyState | None:
    """Compose one ticket's dependency projection from the stored graph."""
    graph = build_dependency_graph(db)
    try:
        blockers = blocked(graph, key)
        blocked_by = unlocks(graph, key)
        readiness = is_ready(graph, key)
    except ValueError:
        return None

    return TicketDependencyState(
        key=key,
        blockers=blockers,
        blocked_by=blocked_by,
        readiness=readiness,
    )


def dependency_critical_path(db: Database) -> CriticalPath:
    """Return the validated graph-wide critical path."""
    graph = build_dependency_graph(db)
    validate_graph(graph)
    return critical_path(graph)


def dependency_graph(db: Database) -> DependencyGraphState:
    """Return the validated projected dependency graph in response order."""
    graph = build_dependency_graph(db)
    validate_graph(graph)

    nodes = [
        DependencyGraphNodeState(
            key=str(data["key"]),
            status=str(data["status"]),
            node_type=str(data["node_type"]),
        )
        for _node, data in graph.nodes(data=True)
    ]
    nodes.sort(key=lambda node: natural_key(node.key))

    edges = [
        DependencyGraphEdgeState(
            source=source,
            target=target,
            dependency_type=DependencyType(dep_type),
        )
        for source, target, dep_type in graph.edges(data="dependency_type")
        if dep_type == _DEPENDS_ON
    ]
    edges.sort(
        key=lambda edge: (
            natural_key(edge.source),
            natural_key(edge.target),
            edge.dependency_type.value,
        )
    )

    return DependencyGraphState(nodes=tuple(nodes), edges=tuple(edges))
