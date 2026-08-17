"""Production cadence adapter for one bounded CI-pending handoff candidate."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from atlas.core.models import CIHandoffDecision, CIHandoffReason, Ticket, TicketStatus
from atlas.github import GitHubClient
from atlas.linear.client import LinearClient, LinearIssue
from atlas.linear.ownership import LinearStatusMap
from atlas.pm.agent_runs import agent_run_observation
from atlas.pm.ci_handoff import CIHandoffHooks, CIHandoffResult, reconcile_ci_handoff
from atlas.storage import AgentRunRepo, Database, TicketRepo, TicketStatusTransitionRepo


class CIHandoffAdapterReason(StrEnum):
    """Closed production-adapter outcomes before/around domain reconciliation."""

    NO_CANDIDATE = "no_ci_pending_candidate"
    IDENTITY_UNAVAILABLE = "trusted_identity_unavailable"
    IDENTITY_AMBIGUOUS = "trusted_identity_ambiguous"
    RECONCILED = "reconciled"


@dataclass(frozen=True)
class CIHandoffIdentity:
    """Exact same-repository contributor-head identity supplied to the service."""

    repository_owner: str
    repository_name: str
    pr_number: int
    head_commit: str


@dataclass(frozen=True)
class CIHandoffAdapterResult:
    """Bounded, secret-free result exposed by a production PM tick."""

    reason: CIHandoffAdapterReason
    candidate_count: int
    ticket_key: str | None = None
    identity: CIHandoffIdentity | None = None
    reconciliation: CIHandoffResult | None = None

    @property
    def routine(self) -> bool:
        return self.reason is CIHandoffAdapterReason.NO_CANDIDATE

    @property
    def held(self) -> bool:
        if self.reason in {
            CIHandoffAdapterReason.IDENTITY_UNAVAILABLE,
            CIHandoffAdapterReason.IDENTITY_AMBIGUOUS,
        }:
            return True
        return bool(
            self.reconciliation
            and self.reconciliation.decision is CIHandoffDecision.HOLD
        )

    @property
    def linear_mutations(self) -> int:
        return (
            0 if self.reconciliation is None else self.reconciliation.linear_mutations
        )

    @property
    def ends_workflow_write_window(self) -> bool:
        """Whether later workflow writers must not run during this tick."""

        if self.reconciliation is None:
            return False
        return bool(
            self.reconciliation.linear_mutations
            or self.reconciliation.reason is CIHandoffReason.FENCE_RECONCILED_TARGET
        )

    @property
    def safe_summary(self) -> str:
        prefix = (
            "CI handoff adapter: "
            f"reason={self.reason.value} candidates={self.candidate_count}"
        )
        if self.ticket_key is not None:
            prefix = f"{prefix} ticket={self.ticket_key}"
        if self.identity is not None:
            prefix = (
                f"{prefix} repository={self.identity.repository_owner}/"
                f"{self.identity.repository_name} pr={self.identity.pr_number} "
                f"head={self.identity.head_commit}"
            )
        if self.reconciliation is not None:
            prefix = f"{prefix}; {self.reconciliation.safe_summary}"
        return prefix


def _candidate_key(ticket: Ticket) -> tuple[int, str]:
    suffix = ticket.key.removeprefix("ATLAS-")
    return (int(suffix) if suffix.isdigit() else 2**31 - 1, ticket.key)


def _trusted_identity(
    db: Database, ticket: Ticket
) -> tuple[CIHandoffIdentity | None, bool]:
    """Resolve the latest CI-pending episode's reconstructed trusted identity.

    Returns ``(identity, ambiguous)``.  The handoff transition id is the episode
    boundary: an earlier run/head can never be carried into a redispatch.
    """

    transitions = [
        transition
        for transition in TicketStatusTransitionRepo(db).list_for_ticket(ticket.id)
        if transition.to_status == TicketStatus.CI_PENDING.value
    ]
    if not transitions:
        return None, False
    latest_transition = transitions[-1]
    matching_runs = []
    for run in AgentRunRepo(db).list_for_ticket(ticket.id):
        observation = agent_run_observation(run)
        if observation.get("handoff_transition_id") == str(latest_transition.id):
            matching_runs.append(observation)
    if len(matching_runs) != 1:
        return None, len(matching_runs) > 1
    observation = matching_runs[0]
    repository_status = observation.get("repository_identity_status")
    if repository_status == "ambiguous":
        return None, True
    owner = observation.get("repository_owner")
    name = observation.get("repository_name")
    pr_number = observation.get("pr_number")
    head_commit = observation.get("head_commit")
    if (
        repository_status != "resolved"
        or not isinstance(owner, str)
        or not owner
        or not isinstance(name, str)
        or not name
        or isinstance(pr_number, bool)
        or not isinstance(pr_number, int)
        or pr_number <= 0
        or not isinstance(head_commit, str)
        or len(head_commit) != 40
        or any(character not in "0123456789abcdefABCDEF" for character in head_commit)
    ):
        return None, False
    return (
        CIHandoffIdentity(
            repository_owner=owner,
            repository_name=name,
            pr_number=pr_number,
            head_commit=head_commit.lower(),
        ),
        False,
    )


def reconcile_one_ci_handoff(
    *,
    db: Database,
    tickets: TicketRepo,
    github: GitHubClient,
    linear: LinearClient,
    status_map: LinearStatusMap,
    project_id: str,
    initial_issues: list[LinearIssue],
    now: datetime,
    hooks: CIHandoffHooks | None = None,
) -> CIHandoffAdapterResult:
    """Discover deterministically and evaluate at most one CI-pending ticket."""

    candidates = sorted(
        (
            ticket
            for ticket in tickets.list()
            if ticket.status is TicketStatus.CI_PENDING
        ),
        key=_candidate_key,
    )
    if not candidates:
        return CIHandoffAdapterResult(
            reason=CIHandoffAdapterReason.NO_CANDIDATE,
            candidate_count=0,
        )
    candidate = candidates[0]
    identity, ambiguous = _trusted_identity(db, candidate)
    if identity is None:
        return CIHandoffAdapterResult(
            reason=(
                CIHandoffAdapterReason.IDENTITY_AMBIGUOUS
                if ambiguous
                else CIHandoffAdapterReason.IDENTITY_UNAVAILABLE
            ),
            candidate_count=len(candidates),
            ticket_key=candidate.key,
        )
    reconciliation = reconcile_ci_handoff(
        db=db,
        tickets=tickets,
        github=github,
        linear=linear,
        status_map=status_map,
        project_id=project_id,
        initial_issues=initial_issues,
        ticket_key=candidate.key,
        repository_owner=identity.repository_owner,
        repository_name=identity.repository_name,
        pr_number=identity.pr_number,
        expected_head=identity.head_commit,
        now=now,
        hooks=hooks,
    )
    return CIHandoffAdapterResult(
        reason=CIHandoffAdapterReason.RECONCILED,
        candidate_count=len(candidates),
        ticket_key=candidate.key,
        identity=identity,
        reconciliation=reconciliation,
    )
