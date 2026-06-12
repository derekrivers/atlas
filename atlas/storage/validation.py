"""Dangling polymorphic target detection (ATLAS-19).

knowledge-core "Testing strategy": a dependency whose target id
resolves to nothing is detected here; Phase 3 graph validation consumes
this helper. Detection only — no traversal, no graph semantics.

Fail closed: targets resolve against every table-backed entity type;
`component` is documented (data-model §3.5/§4.1) but storeless and is
reported unresolvable; unknown target types are detections, never
skips.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from atlas.core.models import TicketDependency
from atlas.storage.db import Database
from atlas.storage.repositories import TicketDependencyRepo
from atlas.storage.tables import (
    AgentRunRow,
    ArchitectureDecisionRecordRow,
    Base,
    ContextPackRow,
    EpicRow,
    EvidenceRow,
    LessonRow,
    PlanRunRow,
    ProductRow,
    TicketDependencyRow,
    TicketRow,
)

# Every table-backed entity type resolves by primary-key lookup.
_RESOLVABLE: dict[str, type[Base]] = {
    "product": ProductRow,
    "adr": ArchitectureDecisionRecordRow,
    "epic": EpicRow,
    "ticket": TicketRow,
    "ticket_dependency": TicketDependencyRow,
    "lesson": LessonRow,
    "evidence": EvidenceRow,
    "agent_run": AgentRunRow,
    "context_pack": ContextPackRow,
    "plan_run": PlanRunRow,
}

# Documented in §3.5 ("ticket, ADR, or component") but has no backing
# table today; unresolvable, therefore detected.
_STORELESS = frozenset({"component"})


@dataclass(frozen=True)
class DanglingTarget:
    """One unresolvable dependency target, attributably."""

    dependency_id: UUID
    target_entity_type: str
    target_entity_id: UUID
    reason: str


def find_dangling_targets(
    db: Database, dependencies: Sequence[TicketDependency] | None = None
) -> list[DanglingTarget]:
    """Detect dependencies whose targets resolve to nothing.

    An empty result is a clean pass. Storeless and unknown target types
    fail closed: they are reported, never skipped.
    """
    if dependencies is None:
        dependencies = TicketDependencyRepo(db).list()
    findings = []
    with db.session() as session:
        for dependency in dependencies:
            target_type = dependency.target_entity_type
            row_cls = _RESOLVABLE.get(target_type)
            if row_cls is not None:
                if session.get(row_cls, dependency.target_entity_id) is None:
                    reason = f"missing {target_type}"
                else:
                    continue
            elif target_type in _STORELESS:
                reason = f"storeless target type {target_type!r} (unresolvable)"
            else:
                reason = f"unknown target type {target_type!r}"
            findings.append(
                DanglingTarget(
                    dependency_id=dependency.id,
                    target_entity_type=target_type,
                    target_entity_id=dependency.target_entity_id,
                    reason=reason,
                )
            )
    return findings
