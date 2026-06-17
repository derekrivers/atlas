"""Phase 3 dependency engine (ATLAS-31): the on-demand graph projection,
with graph validation (ATLAS-40) layered over it.

Later Phase 3 tickets add readiness (ATLAS-34), critical path (ATLAS-35),
blockers (ATLAS-36), Mermaid viz (ATLAS-37), and the ``atlas deps`` CLI
(ATLAS-39) on top of this projection.
"""

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
    "CycleError",
    "DanglingTargetError",
    "DuplicateEdgeError",
    "GraphValidationError",
    "GraphValidationFailed",
    "NotReadyCode",
    "NotReadyReason",
    "ReadinessResult",
    "SelfEdgeError",
    "TerminalDependencyError",
    "adr_key",
    "build_dependency_graph",
    "is_ready",
    "project_graph",
    "ready_tickets",
    "validate_graph",
]
