"""JSON-shaped view records over dependency-domain objects, shared by any front-end."""

from __future__ import annotations

from atlas.dependencies import (
    BlockedResult,
    CriticalPath,
    GraphValidationError,
    HighRiskBlocker,
    UnlocksResult,
)


def violation_json(violation: GraphValidationError) -> dict[str, str]:
    """A stable JSON form of one typed validation violation: its class name
    and its message (the message names the offending nodes — a cycle's full
    path, a dangling target's sources)."""
    return {"type": type(violation).__name__, "message": str(violation)}


def blocked_payload(result: BlockedResult) -> dict[str, object]:
    return {
        "key": result.key,
        "is_blocked": result.is_blocked,
        "targets": [
            {"key": target.key, "code": target.code.value} for target in result.targets
        ],
    }


def critical_path_payload(path: CriticalPath) -> dict[str, object]:
    return {
        "keys": list(path.keys),
        "steps": [
            {
                "key": step.key,
                "effort": step.effort,
                "cumulative_effort": step.cumulative_effort,
            }
            for step in path.steps
        ],
        "total_effort": path.total_effort,
    }


def unlocks_payload(result: UnlocksResult) -> dict[str, object]:
    return {
        "key": result.key,
        "dependents": list(result.dependents),
        "count": result.count,
    }


def high_risk_blockers_payload(
    report: tuple[HighRiskBlocker, ...],
) -> list[dict[str, object]]:
    return [
        {
            "target": blocker.target,
            "risk_level": blocker.risk_level,
            "blocks": list(blocker.blocks),
            "blocked_count": blocker.blocked_count,
        }
        for blocker in report
    ]
