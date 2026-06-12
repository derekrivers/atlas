"""Shared enums used across all canonical models (ATLAS-11).

Members and string values are specified verbatim in
docs/architecture/data-model-and-schemas.md §2 (Shared Types). The doc
declares them as ``(str, Enum)``; ``StrEnum`` is the py3.11+ equivalent
(repo lint rule UP042) with identical members and values.
Model-specific enums (TicketStatus, EvidenceType, ...) live with their
models, not here.
"""

from enum import StrEnum


class ActorType(StrEnum):
    """Who or what created a record (data-model §2.2, attribution §1.5)."""

    HUMAN = "human"
    AGENT = "agent"
    SYSTEM = "system"


class EntityStatus(StrEnum):
    """Generic lifecycle status for knowledge entities (data-model §2.3)."""

    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"
    DEPRECATED = "deprecated"


class RiskLevel(StrEnum):
    """Risk classification shared by epics and tickets (data-model §2.4)."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class EvidenceStatus(StrEnum):
    """Outcome of an evidence record or verification check (data-model §2.5)."""

    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    WARNING = "warning"
    NOT_APPLICABLE = "not_applicable"
