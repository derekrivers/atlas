"""Production cadence adapter for one bounded CI-pending handoff candidate."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from atlas.core.models import CIHandoffDecision, CIHandoffReason, Ticket, TicketStatus
from atlas.evidence.pull import (
    EvidencePullMalformedSourceError,
    drive_evidence_pull,
)
from atlas.github import GitHubAPIError, GitHubClient
from atlas.linear.client import (
    LinearClient,
    LinearGitHubPublication,
    LinearIssue,
)
from atlas.linear.ownership import LinearStatusMap
from atlas.pm.ci_handoff import CIHandoffHooks, CIHandoffResult, reconcile_ci_handoff
from atlas.storage import Database, EvidenceRepo, TicketRepo


class CIHandoffAdapterReason(StrEnum):
    """Closed production-adapter outcomes before/around domain reconciliation."""

    NO_CANDIDATE = "no_ci_pending_candidate"
    PUBLICATION_UNAVAILABLE = "trusted_publication_unavailable"
    PUBLICATION_AMBIGUOUS = "trusted_publication_ambiguous"
    EVIDENCE_INGESTION_FAILED = "system_evidence_ingestion_failed"
    IDENTITY_UNAVAILABLE = "trusted_identity_unavailable"
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
            CIHandoffAdapterReason.PUBLICATION_UNAVAILABLE,
            CIHandoffAdapterReason.PUBLICATION_AMBIGUOUS,
            CIHandoffAdapterReason.EVIDENCE_INGESTION_FAILED,
            CIHandoffAdapterReason.IDENTITY_UNAVAILABLE,
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


def _full_sha(value: str | None) -> str | None:
    if (
        not isinstance(value, str)
        or len(value) != 40
        or any(character not in "0123456789abcdefABCDEF" for character in value)
    ):
        return None
    return value.lower()


def resolve_issue_bound_publication(
    candidate: Ticket, initial_issues: list[LinearIssue]
) -> tuple[LinearGitHubPublication | None, CIHandoffAdapterReason | None]:
    """Resolve one exact issue-bound GitHub publication from the board pull."""

    if candidate.external_linear_id is None:
        return None, CIHandoffAdapterReason.PUBLICATION_UNAVAILABLE
    issues = [
        issue for issue in initial_issues if issue.id == candidate.external_linear_id
    ]
    if len(issues) != 1:
        return None, CIHandoffAdapterReason.PUBLICATION_UNAVAILABLE
    issue = issues[0]
    if not issue.github_publications_complete:
        return None, CIHandoffAdapterReason.PUBLICATION_AMBIGUOUS
    by_identity: dict[tuple[str, str, int], LinearGitHubPublication] = {}
    for publication in issue.github_publications:
        key = (
            publication.repository_owner.casefold(),
            publication.repository_name.casefold(),
            publication.pr_number,
        )
        by_identity[key] = publication
    if not by_identity:
        return None, CIHandoffAdapterReason.PUBLICATION_UNAVAILABLE
    if len(by_identity) != 1:
        return None, CIHandoffAdapterReason.PUBLICATION_AMBIGUOUS
    return next(iter(by_identity.values())), None


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
    publication, publication_reason = resolve_issue_bound_publication(
        candidate, initial_issues
    )
    if publication is None:
        assert publication_reason is not None
        return CIHandoffAdapterResult(
            reason=publication_reason,
            candidate_count=len(candidates),
            ticket_key=candidate.key,
        )
    try:
        pulled = drive_evidence_pull(
            github,
            publication.repository_owner,
            publication.repository_name,
            publication.pr_number,
            evidence_repo=EvidenceRepo(db),
            product_id=candidate.product_id,
            now=now,
        )
    except (GitHubAPIError, EvidencePullMalformedSourceError):
        return CIHandoffAdapterResult(
            reason=CIHandoffAdapterReason.EVIDENCE_INGESTION_FAILED,
            candidate_count=len(candidates),
            ticket_key=candidate.key,
        )
    head_commit = _full_sha(pulled.head_sha)
    if head_commit is None:
        return CIHandoffAdapterResult(
            reason=CIHandoffAdapterReason.IDENTITY_UNAVAILABLE,
            candidate_count=len(candidates),
            ticket_key=candidate.key,
        )
    identity = CIHandoffIdentity(
        repository_owner=publication.repository_owner,
        repository_name=publication.repository_name,
        pr_number=publication.pr_number,
        head_commit=head_commit,
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
        evidence_ids=tuple(record.id for record in pulled.observed),
        hooks=hooks,
    )
    return CIHandoffAdapterResult(
        reason=CIHandoffAdapterReason.RECONCILED,
        candidate_count=len(candidates),
        ticket_key=candidate.key,
        identity=identity,
        reconciliation=reconciliation,
    )
