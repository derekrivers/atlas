"""Production cadence adapter for one bounded CI-pending handoff candidate."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

from atlas.core.enums import ActorType
from atlas.core.models import (
    CIHandoffDecision,
    CIHandoffReason,
    Evidence,
    Ticket,
    TicketStatus,
    TicketStatusTransition,
)
from atlas.github import GitHubClient
from atlas.linear.client import LinearClient, LinearIssue
from atlas.linear.ownership import LinearStatusMap
from atlas.pm.ci_handoff import CIHandoffHooks, CIHandoffResult, reconcile_ci_handoff
from atlas.storage import (
    Database,
    EvidenceRepo,
    TicketRepo,
    TicketStatusTransitionRepo,
    VerificationCheckRepo,
)

_GITHUB_ACTOR = "github-actions"
_REPOSITORY_PART_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_PR_NUMBER_KEYS = frozenset(
    {"number", "pr_number", "pull_number", "pull_request_number"}
)


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


def _repository_from_uri(value: str | None) -> tuple[str, str] | None:
    if not value:
        return None
    parsed = urlsplit(value)
    if parsed.scheme != "https" or parsed.netloc.casefold() != "github.com":
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return None
    owner, name = parts[:2]
    if name.endswith(".git"):
        name = name[:-4]
    if not _REPOSITORY_PART_RE.fullmatch(owner) or not _REPOSITORY_PART_RE.fullmatch(
        name
    ):
        return None
    return owner, name


def _pr_numbers_from_payload(value: Any, *, depth: int = 0) -> set[int]:
    """Collect explicit positive PR numbers from one bounded provider payload."""

    if depth > 6:
        return set()
    found: set[int] = set()
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if (
                str(key).lower() in _PR_NUMBER_KEYS
                and isinstance(nested, int)
                and not isinstance(nested, bool)
                and nested > 0
            ):
                found.add(nested)
            found.update(_pr_numbers_from_payload(nested, depth=depth + 1))
    elif isinstance(value, Sequence) and not isinstance(value, str | bytes):
        for nested in value:
            found.update(_pr_numbers_from_payload(nested, depth=depth + 1))
    return found


def _pr_number_from_uri(value: str | None) -> int | None:
    if not value:
        return None
    parsed = urlsplit(value)
    if parsed.scheme != "https" or parsed.netloc.casefold() != "github.com":
        return None
    parts = [part for part in parsed.path.split("/") if part]
    for index, part in enumerate(parts[:-1]):
        if part == "pull" and parts[index + 1].isdigit():
            number = int(parts[index + 1])
            return number if number > 0 else None
    return None


def _full_sha(value: str | None) -> str | None:
    if (
        not isinstance(value, str)
        or len(value) != 40
        or any(character not in "0123456789abcdefABCDEF" for character in value)
    ):
        return None
    return value.lower()


def _ticket_evidence(db: Database, ticket: Ticket) -> list[Evidence]:
    """Load only evidence explicitly bounded to this ticket.

    Direct ``ticket_id`` linkage and immutable VerificationCheck linkage are
    the two existing trusted attribution seams.  Product-wide evidence, ticket
    titles, branch names, provider rollups and operator input are never identity
    sources.
    """

    evidence_repo = EvidenceRepo(db)
    bounded = {record.id: record for record in evidence_repo.list_for_ticket(ticket.id)}
    for check in VerificationCheckRepo(db).list_for_ticket(ticket.id):
        for evidence_id in check.evidence_ids:
            record = evidence_repo.get(evidence_id)
            if record is not None and record.product_id == ticket.product_id:
                bounded[record.id] = record
    return sorted(bounded.values(), key=lambda record: (record.created_at, record.id))


def _episode_started_at(
    ticket: Ticket,
    *,
    transitions: list[TicketStatusTransition],
    latest_transition: TicketStatusTransition,
) -> datetime:
    """Bound identity evidence to the locally known source-status episode."""

    source_entries = [
        transition.occurred_at
        for transition in transitions
        if transition.occurred_at <= latest_transition.occurred_at
        and transition.to_status == latest_transition.from_status
    ]
    if source_entries:
        return max(source_entries)
    predecessors = [
        transition.occurred_at
        for transition in transitions
        if transition.id != latest_transition.id
        and transition.occurred_at <= latest_transition.occurred_at
    ]
    return max(predecessors, default=ticket.created_at)


def _identity_from_record(
    record: Evidence,
) -> tuple[CIHandoffIdentity | None, bool]:
    """Resolve one complete exact identity, flagging contradictions explicitly."""

    repository = _repository_from_uri(record.source_uri)
    head_commit = _full_sha(record.commit_sha)
    pr_numbers = _pr_numbers_from_payload(record.raw_payload)
    uri_number = _pr_number_from_uri(record.source_uri)
    if uri_number is not None:
        pr_numbers.add(uri_number)
    if len(pr_numbers) > 1:
        return None, True
    if repository is None or head_commit is None or len(pr_numbers) != 1:
        return None, False
    owner, name = repository
    return (
        CIHandoffIdentity(
            repository_owner=owner,
            repository_name=name,
            pr_number=next(iter(pr_numbers)),
            head_commit=head_commit,
        ),
        False,
    )


def _trusted_identity(
    db: Database, ticket: Ticket
) -> tuple[CIHandoffIdentity | None, bool]:
    """Resolve the latest CI-pending episode from bounded system-tier evidence.

    This path deliberately does not require a reconstructed ``AgentRun``. A PM
    poll may miss both ``in_progress`` and ``pr_open`` before observing
    ``ci_pending``; requiring either transient transition would make the
    supported 60-second production cadence unable to recover. The durable
    transition into ``ci_pending`` still bounds the episode, while the latest
    trusted GitHub evidence batch supplies only exact repository/PR/head facts.
    """

    transitions = [
        transition
        for transition in TicketStatusTransitionRepo(db).list_for_ticket(ticket.id)
        if transition.to_status == TicketStatus.CI_PENDING.value
    ]
    if not transitions:
        return None, False
    latest_transition = transitions[-1]
    episode_start = _episode_started_at(
        ticket,
        transitions=TicketStatusTransitionRepo(db).list_for_ticket(ticket.id),
        latest_transition=latest_transition,
    )
    trusted = [
        record
        for record in _ticket_evidence(db, ticket)
        if record.created_at >= episode_start
        and record.created_by_type is ActorType.SYSTEM
        and record.created_by_id == _GITHUB_ACTOR
    ]
    if not trusted:
        return None, False
    latest_batch_at = max(record.created_at for record in trusted)
    identities: set[CIHandoffIdentity] = set()
    ambiguous = False
    for record in trusted:
        if record.created_at != latest_batch_at:
            continue
        identity, record_ambiguous = _identity_from_record(record)
        ambiguous = ambiguous or record_ambiguous
        if identity is not None:
            identities.add(identity)
    if ambiguous or len(identities) > 1:
        return None, True
    if not identities:
        return None, False
    return next(iter(identities)), False


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
