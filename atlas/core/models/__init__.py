"""Canonical Pydantic models (Phase 1 Knowledge Core).

ATLAS-12 lands Product, ArchitectureDecisionRecord, Epic, Ticket, and
TicketDependency; ATLAS-13 adds Lesson; later tickets add the remaining
siblings (Evidence, PlanRun, ContextPack, AgentRun) as modules in this
package.
"""

from atlas.core.models.adr import ADRStatus, ArchitectureDecisionRecord
from atlas.core.models.dependency import DependencyType, TicketDependency
from atlas.core.models.epic import Epic, EpicStatus
from atlas.core.models.lesson import Lesson, LessonCategory
from atlas.core.models.product import Product
from atlas.core.models.ticket import Ticket, TicketStatus, TicketType

__all__ = [
    "ADRStatus",
    "ArchitectureDecisionRecord",
    "DependencyType",
    "Epic",
    "EpicStatus",
    "Lesson",
    "LessonCategory",
    "Product",
    "Ticket",
    "TicketDependency",
    "TicketStatus",
    "TicketType",
]
