"""Cross-layer orchestration shared by Atlas presentation surfaces."""

from atlas.orchestration.confirm import ConfirmPrompts, capture_ticket
from atlas.orchestration.context_inputs import (
    ContextInputs,
    ContextNotFoundError,
    load_context_inputs,
)
from atlas.orchestration.dependency_projection import (
    DependencyGraphEdgeState,
    DependencyGraphNodeState,
    DependencyGraphState,
    TicketDependencyState,
    dependency_critical_path,
    dependency_graph,
    ticket_dependencies,
)
from atlas.orchestration.pr_context import (
    PRContext,
    resolve_github_client,
    resolve_pr_context,
)
from atlas.orchestration.review_queue import (
    ReviewCheckState,
    TicketReviewState,
    review_queue,
)
from atlas.orchestration.system_status import SystemStatus, system_status
from atlas.orchestration.tick_config import build_tick_config
from atlas.orchestration.ticket_board import TicketBoardItemState, ticket_board
from atlas.orchestration.ticket_evidence import (
    TicketEvidenceRecordState,
    ticket_evidence,
)
from atlas.orchestration.verify import VerifyResult, run_verify

__all__ = [
    "ConfirmPrompts",
    "ContextInputs",
    "ContextNotFoundError",
    "DependencyGraphEdgeState",
    "DependencyGraphNodeState",
    "DependencyGraphState",
    "PRContext",
    "ReviewCheckState",
    "SystemStatus",
    "TicketBoardItemState",
    "TicketDependencyState",
    "TicketEvidenceRecordState",
    "TicketReviewState",
    "VerifyResult",
    "build_tick_config",
    "capture_ticket",
    "dependency_critical_path",
    "dependency_graph",
    "load_context_inputs",
    "resolve_github_client",
    "resolve_pr_context",
    "review_queue",
    "run_verify",
    "system_status",
    "ticket_board",
    "ticket_dependencies",
    "ticket_evidence",
]
