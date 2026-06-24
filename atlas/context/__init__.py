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
from atlas.context.related_tickets import (
    DEFAULT_RELATED_CAP,
    RelatedTicket,
    RelatedTicketSource,
    select_related_tickets,
)

__all__ = [
    "DEFAULT_ADR_CAP",
    "DEFAULT_DOC_CAP",
    "DEFAULT_RELATED_CAP",
    "ADRMatch",
    "ADRMatchSource",
    "DocContext",
    "DocReference",
    "DocSection",
    "RelatedTicket",
    "RelatedTicketSource",
    "select_adrs",
    "select_doc_sections",
    "select_related_tickets",
]
