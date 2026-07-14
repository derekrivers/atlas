"""Phase 5 context renderer (docs/atlas/context-renderer.md).

Assembles the minimum high-value context for one ticket. The retrievers are
pure functions over already-projected state — ADR retrieval (ATLAS-51) is
the first; documentation (ATLAS-52), lessons (ATLAS-53), dependencies
(ATLAS-54), compression (ATLAS-55), pack generation (ATLAS-56) and the CLI
(ATLAS-58) layer on top.
"""

from atlas.context.adr_retrieval import (
    DEFAULT_ADR_CAP,
    ADRMatch,
    ADRMatchSource,
    select_adrs,
)
from atlas.context.doc_retrieval import (
    DEFAULT_DOC_CAP,
    DocContext,
    DocReference,
    DocSection,
    select_doc_sections,
)
from atlas.context.lesson_retrieval import (
    DEFAULT_LESSON_CAP,
    LessonMatch,
    retrieve_lessons,
    select_lessons,
)
from atlas.context.pack import (
    DEFAULT_TOKEN_BUDGET,
    ContextBudgetExceededError,
    build_context_pack,
)
from atlas.context.related_tickets import (
    DEFAULT_RELATED_CAP,
    RelatedTicket,
    RelatedTicketSource,
    select_related_tickets,
)
from atlas.context.validation import (
    ContextPackValidation,
    validate_context_pack,
)

__all__ = [
    "DEFAULT_ADR_CAP",
    "DEFAULT_DOC_CAP",
    "DEFAULT_LESSON_CAP",
    "DEFAULT_RELATED_CAP",
    "DEFAULT_TOKEN_BUDGET",
    "ADRMatch",
    "ADRMatchSource",
    "ContextBudgetExceededError",
    "ContextPackValidation",
    "DocContext",
    "DocReference",
    "DocSection",
    "LessonMatch",
    "RelatedTicket",
    "RelatedTicketSource",
    "build_context_pack",
    "retrieve_lessons",
    "select_adrs",
    "select_doc_sections",
    "select_lessons",
    "select_related_tickets",
    "validate_context_pack",
]
