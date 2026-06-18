"""Phase 3 dependency engine (ATLAS-31): the on-demand graph projection,
with graph validation (ATLAS-40) layered over it.

Later Phase 3 tickets add Mermaid viz (ATLAS-37) and the ``atlas deps`` CLI
(ATLAS-39) on top of this projection; readiness (ATLAS-34), critical path
(ATLAS-35), and blockers (ATLAS-36) are layered here already.
"""

from atlas.dependencies.blockers import (
    BlockedResult,
    BlockedTarget,
    HighRiskBlocker,
    UnlocksResult,
    blocked,
    high_risk_blockers,
    unlocks,
)
from atlas.dependencies.critical_path import (
    CriticalPath,
    CriticalPathStep,
    critical_path,
)
from atlas.dependencies.errors import (
    CycleError,
    DanglingTargetError,
    DuplicateEdgeError,
    GraphValidationError,
    GraphValidationFailed,
    SelfEdgeError,
    TerminalDependencyError,
)
from atlas.dependencies.graph import (
    adr_key,
    build_dependency_graph,
    project_graph,
)
from atlas.dependencies.readiness import (
    READY_STATUSES,
    NotReadyCode,
    NotReadyReason,
    ReadinessResult,
    is_ready,
    ready_tickets,
)
from atlas.dependencies.validation import (
    TERMINAL_STATUSES,
    validate_graph,
)

__all__ = [
    "READY_STATUSES",
    "TERMINAL_STATUSES",
    "BlockedResult",
    "BlockedTarget",
    "CriticalPath",
    "CriticalPathStep",
    "CycleError",
    "DanglingTargetError",
    "DuplicateEdgeError",
    "GraphValidationError",
    "GraphValidationFailed",
    "HighRiskBlocker",
    "NotReadyCode",
    "NotReadyReason",
    "ReadinessResult",
    "SelfEdgeError",
    "TerminalDependencyError",
    "UnlocksResult",
    "adr_key",
    "blocked",
    "build_dependency_graph",
    "critical_path",
    "high_risk_blockers",
    "is_ready",
    "project_graph",
    "ready_tickets",
    "unlocks",
    "validate_graph",
]
