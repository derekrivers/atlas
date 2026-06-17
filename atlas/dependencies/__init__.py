"""Phase 3 dependency engine (ATLAS-31): the on-demand graph projection.

Later Phase 3 tickets add readiness (ATLAS-34), critical path (ATLAS-35),
blockers (ATLAS-36), graph validation (ATLAS-40), Mermaid viz (ATLAS-37),
and the ``atlas deps`` CLI (ATLAS-39) on top of this projection.
"""

from atlas.dependencies.graph import (
    adr_key,
    build_dependency_graph,
    project_graph,
)

__all__ = [
    "adr_key",
    "build_dependency_graph",
    "project_graph",
]
