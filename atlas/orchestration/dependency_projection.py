"""Stored-data assembly for dependency read projections."""

from __future__ import annotations

from dataclasses import dataclass

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


@dataclass(frozen=True)
class TicketDependencyState:
    """Dependency state for one ticket over the projected graph."""

    key: str
    blockers: BlockedResult
    blocked_by: UnlocksResult
    readiness: ReadinessResult


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
