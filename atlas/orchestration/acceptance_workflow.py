"""Composition root for the authenticated acceptance-session resource."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from atlas.github import GitHubClient
from atlas.orchestration.acceptance_confirmation import (
    AcceptanceSessionConfirmationService,
)
from atlas.orchestration.acceptance_evidence import (
    AcceptanceSessionEvidencePullService,
)
from atlas.orchestration.acceptance_readiness import (
    AcceptanceSessionLiveReadinessService,
)
from atlas.orchestration.acceptance_sessions import (
    AcceptanceSessionCreationService,
    TicketLookup,
)
from atlas.orchestration.acceptance_verification import (
    AcceptanceSessionVerificationService,
    VerificationService,
)
from atlas.orchestration.operator_actions import OperatorActionGateway
from atlas.storage import AcceptanceSessionRepo, Database, EvidenceRepo, TicketRepo

Clock = Callable[[], datetime]


@dataclass(frozen=True)
class AcceptanceSessionWorkflowServices:
    """Request-independent application operations behind the five HTTP routes."""

    creation: AcceptanceSessionCreationService
    live_readiness: AcceptanceSessionLiveReadinessService
    evidence: AcceptanceSessionEvidencePullService
    confirmation: AcceptanceSessionConfirmationService
    verification: AcceptanceSessionVerificationService


def build_acceptance_session_workflow(
    database: Database,
    github_client: GitHubClient,
    clock: Clock,
    *,
    ticket_lookup: TicketLookup | None = None,
    verification_service: VerificationService | None = None,
) -> AcceptanceSessionWorkflowServices:
    """Wire Phase 14 services once around explicit external/test boundaries."""

    tickets = ticket_lookup or TicketRepo(database)
    sessions = AcceptanceSessionRepo(database)
    gateway = OperatorActionGateway(database, clock=clock)
    return AcceptanceSessionWorkflowServices(
        creation=AcceptanceSessionCreationService(
            github_client=github_client,
            ticket_lookup=tickets,
            repository=sessions,
            clock=clock,
        ),
        live_readiness=AcceptanceSessionLiveReadinessService(
            github_client=github_client,
            ticket_lookup=tickets,
            session_repository=sessions,
        ),
        evidence=AcceptanceSessionEvidencePullService(
            github_client=github_client,
            ticket_lookup=tickets,
            session_repository=sessions,
            evidence_repository=EvidenceRepo(database),
            gateway=gateway,
            clock=clock,
        ),
        confirmation=AcceptanceSessionConfirmationService(
            db=database,
            github_client=github_client,
            ticket_lookup=tickets,
            clock=clock,
            gateway=gateway,
        ),
        verification=AcceptanceSessionVerificationService(
            db=database,
            github_client=github_client,
            ticket_lookup=tickets,
            gateway=gateway,
            clock=clock,
            verification_service=verification_service,
        ),
    )
