"""Durable fair scheduling for the ordinary PM CI-handoff lane."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from atlas.core.keys import natural_key
from atlas.core.models import CIHandoffReason, Ticket, TicketStatus
from atlas.core.models.pm_recovery import (
    MAX_PM_STARVED_CANDIDATES,
    PmBlockerAuthorityKind,
    PmBlockerCode,
    PmBlockerKind,
    PmBlockerObservationIntent,
    PmBlockerSupersessionKind,
    PmRecoveryEpisode,
    PmRecoveryEpisodeClosureKind,
    PmRecoveryEpisodeIdentity,
    PmStarvedCandidateRef,
)
from atlas.linear.client import LinearIssue
from atlas.pm.ci_handoff_adapter import (
    CIHandoffAdapterReason,
    CIHandoffAdapterResult,
    resolve_issue_bound_publication,
)
from atlas.storage import (
    CIHandoffCoordinationRepo,
    Database,
    PmRecoveryRepo,
    TicketRepo,
    TicketStatusTransitionRepo,
)

CI_HANDOFF_RECOVERY_OPERATION = "ci_handoff"
CI_HANDOFF_RECOVERY_AUTHORITY = "pm:ci-handoff"
CI_HANDOFF_BLOCKER_POLICY_NAMESPACE = "pm-ci-handoff-fairness"
CI_HANDOFF_BLOCKER_POLICY_REVISION = 1
CI_HANDOFF_BLOCKER_POLICY_FINGERPRINT = hashlib.sha256(
    b"pm-ci-handoff-fairness-v1"
).hexdigest()


class CIHandoffFairnessError(RuntimeError):
    """The eligible snapshot cannot be reconciled without inventing identity."""


@dataclass(frozen=True)
class FairCIHandoffSelection:
    """One caller-owned finite snapshot and its least durable cursor."""

    candidates: tuple[Ticket, ...]
    candidate: Ticket | None
    episode: PmRecoveryEpisode | None

    @property
    def candidate_count(self) -> int:
        return len(self.candidates)


def cross_product_fairness_key(episode: PmRecoveryEpisode) -> tuple[datetime, int]:
    """Globally comparable outer rank; local cursors order within a product."""

    if episode.last_evaluated_at is None:
        return (episode.created_at, 0)
    return (episode.last_evaluated_at, 1)


def _canonical_hash(payload: object) -> str:
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode()).hexdigest()


def _lifecycle_entry(db: Database, ticket: Ticket) -> str:
    transitions = TicketStatusTransitionRepo(db).list_for_ticket(ticket.id)
    entries = [
        transition
        for transition in transitions
        if transition.to_status == TicketStatus.CI_PENDING.value
    ]
    if entries:
        return f"transition:{entries[-1].id}"
    # Legacy CI-pending rows may predate append-only transition history. Their
    # durable ticket identity is the one-time bootstrap generation; any later
    # real re-entry has a transition UUID and therefore creates a new episode.
    return f"legacy:{ticket.id}"


def _publication_generation(
    ticket: Ticket, initial_issues: list[LinearIssue]
) -> str | None:
    publication, reason = resolve_issue_bound_publication(ticket, initial_issues)
    if publication is None:
        # Missing/ambiguous observations are blockers, never replacement proof.
        assert reason is not None
        return None
    return _canonical_hash(
        {
            "attachment_id": publication.attachment_id,
            "issue_id": ticket.external_linear_id,
            "repository_name": publication.repository_name.casefold(),
            "repository_owner": publication.repository_owner.casefold(),
            "pr_number": publication.pr_number,
        }
    )


def _identity(
    db: Database,
    ticket: Ticket,
    initial_issues: list[LinearIssue],
) -> tuple[PmRecoveryEpisodeIdentity, str]:
    lifecycle = _lifecycle_entry(db, ticket)
    publication = _publication_generation(ticket, initial_issues)
    authoritative = lifecycle
    if publication is not None:
        authoritative = f"{lifecycle}:publication:{publication}"
    return (
        PmRecoveryEpisodeIdentity(
            product_id=ticket.product_id,
            operation=CI_HANDOFF_RECOVERY_OPERATION,
            authority_id=CI_HANDOFF_RECOVERY_AUTHORITY,
            authoritative_episode_id=authoritative,
            candidate_ticket_id=ticket.id,
            candidate_ticket_key=ticket.key,
        ),
        lifecycle,
    )


def _active_ci_episodes(
    repo: PmRecoveryRepo, product_id: UUID
) -> list[PmRecoveryEpisode]:
    return [
        episode
        for episode in repo.list_active_episodes_ordered(product_id)
        if episode.operation == CI_HANDOFF_RECOVERY_OPERATION
        and episode.authority_id == CI_HANDOFF_RECOVERY_AUTHORITY
    ]


def select_fair_ci_handoff_candidate(
    *,
    db: Database,
    tickets: TicketRepo,
    initial_issues: list[LinearIssue],
    now: datetime,
    excluded_product_ids: frozenset[UUID] = frozenset(),
) -> FairCIHandoffSelection:
    """Reconcile one finite eligible snapshot and select its least cursor."""

    all_tickets = tickets.list()
    candidates = tuple(
        sorted(
            (
                ticket
                for ticket in all_tickets
                if ticket.status is TicketStatus.CI_PENDING
                and ticket.product_id not in excluded_product_ids
            ),
            key=lambda ticket: (natural_key(ticket.key), ticket.key, ticket.id),
        )
    )
    repo = PmRecoveryRepo(db)
    candidates_by_id = {candidate.id: candidate for candidate in candidates}
    # Lifecycle exit is authoritative even when the complete finite snapshot is
    # empty.  Retire every now-ineligible episode before returning no work.
    for product_id in {ticket.product_id for ticket in all_tickets}:
        if product_id in excluded_product_ids:
            continue
        for episode in _active_ci_episodes(repo, product_id):
            if episode.candidate_ticket_id not in candidates_by_id:
                repo.close_episode(
                    episode_id=episode.id,
                    closure_event_id=(
                        "ci-exit:"
                        + _canonical_hash(
                            {
                                "candidate": str(episode.candidate_ticket_id),
                                "episode": str(episode.id),
                            }
                        )
                    ),
                    closure_kind=(
                        PmRecoveryEpisodeClosureKind.AUTHORITATIVE_LIFECYCLE_ENTRY
                    ),
                    closed_at=now,
                )
    if not candidates:
        return FairCIHandoffSelection((), None, None)
    product_ids = {candidate.product_id for candidate in candidates}
    active_by_candidate = {
        episode.candidate_ticket_id: episode
        for product_id in product_ids
        for episode in _active_ci_episodes(repo, product_id)
        if episode.candidate_ticket_id is not None
    }
    resolved: list[tuple[Ticket, PmRecoveryEpisode]] = []
    for candidate in candidates:
        desired, lifecycle = _identity(db, candidate, initial_issues)
        current = active_by_candidate.get(candidate.id)
        if current is None:
            current = repo.establish_episode(desired, created_at=now)
        else:
            current_lifecycle = current.authoritative_episode_id.split(
                ":publication:", 1
            )[0]
            desired_has_publication = (
                ":publication:" in desired.authoritative_episode_id
            )
            lifecycle_changed = current_lifecycle != lifecycle
            publication_changed = (
                desired_has_publication
                and current.authoritative_episode_id != desired.authoritative_episode_id
            )
            if lifecycle_changed or publication_changed:
                closure_kind = (
                    PmRecoveryEpisodeClosureKind.AUTHORITATIVE_LIFECYCLE_ENTRY
                    if lifecycle_changed
                    else PmRecoveryEpisodeClosureKind.PUBLICATION_REPLACEMENT
                )
                event_kind = "ci-entry" if lifecycle_changed else "ci-publication"
                current = repo.replace_episode(
                    expected_episode_id=current.id,
                    replacement=desired,
                    closure_event_id=(
                        f"{event_kind}:"
                        + _canonical_hash(
                            {
                                "old": str(current.id),
                                "new": str(desired.episode_id),
                            }
                        )
                    ),
                    closure_kind=closure_kind,
                    replaced_at=now,
                ).episode
        resolved.append((candidate, current))

    product_representatives = []
    for product_id in sorted(product_ids, key=str):
        product_representatives.append(
            min(
                (item for item in resolved if item[0].product_id == product_id),
                key=lambda item: (
                    item[1].fairness_cursor,
                    natural_key(item[0].key),
                    item[0].key,
                    item[1].id,
                ),
            )
        )
    candidate, episode = min(
        product_representatives,
        key=lambda item: (
            cross_product_fairness_key(item[1]),
            str(item[0].product_id),
        ),
    )
    return FairCIHandoffSelection(candidates, candidate, episode)


def _fence_authority_id(db: Database, candidate: Ticket) -> str:
    fence = CIHandoffCoordinationRepo(db).get_fence(candidate.product_id)
    if fence is None:
        return f"ci-handoff-fence:{candidate.product_id}"
    return f"ci-handoff-fence:{fence.reconciliation_id}"


def _blocker_intent(
    *,
    db: Database,
    candidate: Ticket,
    result: CIHandoffAdapterResult,
    snapshot: tuple[Ticket, ...],
    now: datetime,
) -> PmBlockerObservationIntent:
    code: PmBlockerCode
    kind: PmBlockerKind
    authority_kind = PmBlockerAuthorityKind.OPERATION
    authority_id = CI_HANDOFF_RECOVERY_AUTHORITY

    if result.reason is CIHandoffAdapterReason.PUBLICATION_UNAVAILABLE:
        code = PmBlockerCode.PUBLICATION_NOT_YET_COMPLETE
        kind = PmBlockerKind.ROUTINE_WAIT
    elif result.reason is CIHandoffAdapterReason.PUBLICATION_AMBIGUOUS:
        code = PmBlockerCode.PUBLICATION_AMBIGUOUS
        kind = PmBlockerKind.UNKNOWN
    elif result.reason is CIHandoffAdapterReason.EVIDENCE_INGESTION_FAILED:
        code = PmBlockerCode.PROVIDER_UNAVAILABLE
        kind = PmBlockerKind.RETRYABLE
    elif result.reason is CIHandoffAdapterReason.IDENTITY_UNAVAILABLE:
        code = PmBlockerCode.CI_EVIDENCE_AMBIGUOUS
        kind = PmBlockerKind.UNKNOWN
    elif result.reconciliation is not None:
        reason = result.reconciliation.reason
        if reason in {CIHandoffReason.LEASE_UNAVAILABLE, CIHandoffReason.LEASE_LOST}:
            code = PmBlockerCode.LEASE_UNAVAILABLE
            kind = PmBlockerKind.RETRYABLE
            authority_kind = PmBlockerAuthorityKind.LEASE
            authority_id = f"pm-write-lease:{candidate.product_id}"
        elif reason in {
            CIHandoffReason.CONCURRENT_WRITE_FENCE,
            CIHandoffReason.FENCE_STILL_UNRESOLVED,
            CIHandoffReason.WRITE_INDETERMINATE,
        }:
            code = PmBlockerCode.WRITE_FENCE_UNRESOLVED
            kind = PmBlockerKind.UNRESOLVED_FENCE
            authority_kind = PmBlockerAuthorityKind.FENCE
            authority_id = _fence_authority_id(db, candidate)
        elif reason in {
            CIHandoffReason.REQUIRED_CHECKS_PENDING,
            CIHandoffReason.REQUIRED_CHECKS_MISSING,
            CIHandoffReason.NO_CI_REQUIRED_CHECKS,
        }:
            code = PmBlockerCode.CI_EVIDENCE_NOT_YET_COMPLETE
            kind = PmBlockerKind.ROUTINE_WAIT
        elif reason in {
            CIHandoffReason.MALFORMED_EVIDENCE,
            CIHandoffReason.CONTRADICTORY_EVIDENCE,
            CIHandoffReason.INDETERMINATE_EVIDENCE,
        }:
            code = PmBlockerCode.CI_EVIDENCE_AMBIGUOUS
            kind = (
                PmBlockerKind.RETRYABLE
                if reason is CIHandoffReason.INDETERMINATE_EVIDENCE
                else PmBlockerKind.UNKNOWN
            )
        elif reason in {
            CIHandoffReason.INFRASTRUCTURE_EVIDENCE,
            CIHandoffReason.GITHUB_INFRASTRUCTURE,
            CIHandoffReason.BOARD_REVALIDATION_FAILED,
            CIHandoffReason.POLICY_UNAVAILABLE,
            CIHandoffReason.SNAPSHOT_INCOMPLETE,
        }:
            code = PmBlockerCode.PROVIDER_UNAVAILABLE
            kind = PmBlockerKind.RETRYABLE
        elif reason in {
            CIHandoffReason.STALE_EVIDENCE,
            CIHandoffReason.EVIDENCE_CHANGED,
            CIHandoffReason.TICKET_IDENTITY_MISMATCH,
            CIHandoffReason.LINEAR_ISSUE_MISSING,
            CIHandoffReason.LINEAR_STATE_MISMATCH,
            CIHandoffReason.PR_IDENTITY_MALFORMED,
            CIHandoffReason.PR_HEAD_MOVED,
            CIHandoffReason.BOARD_STATE_MOVED,
            CIHandoffReason.POLICY_CHANGED,
            CIHandoffReason.SNAPSHOT_CHANGED,
            CIHandoffReason.FENCE_RECONCILED_MOVED,
        }:
            code = PmBlockerCode.AUTHORITY_CHANGED
            kind = PmBlockerKind.RETRYABLE
        else:
            raise CIHandoffFairnessError(
                f"unmapped held CI-handoff reason: {reason.value}"
            )
    else:
        raise CIHandoffFairnessError(
            f"unmapped held CI-handoff adapter result: {result.reason.value}"
        )

    starved: tuple[PmStarvedCandidateRef, ...] = ()
    if (
        kind is not PmBlockerKind.UNRESOLVED_FENCE
        and code is not PmBlockerCode.LEASE_UNAVAILABLE
    ):
        same_product = tuple(
            ticket
            for ticket in snapshot
            if ticket.product_id == candidate.product_id and ticket.id != candidate.id
        )
        starved = tuple(
            PmStarvedCandidateRef(ticket_id=ticket.id, ticket_key=ticket.key)
            for ticket in same_product[:MAX_PM_STARVED_CANDIDATES]
        )
        starved_candidates_truncated = len(same_product) > len(starved)
    else:
        starved_candidates_truncated = False
    return PmBlockerObservationIntent(
        code=code,
        kind=kind,
        authority_kind=authority_kind,
        authority_id=authority_id,
        next_safe_retry_at=(
            now + timedelta(minutes=1)
            if kind in {PmBlockerKind.ROUTINE_WAIT, PmBlockerKind.RETRYABLE}
            else None
        ),
        capacity_impact=bool(starved),
        starved_candidates=starved,
        starved_candidates_truncated=starved_candidates_truncated,
        policy_namespace=CI_HANDOFF_BLOCKER_POLICY_NAMESPACE,
        policy_revision=CI_HANDOFF_BLOCKER_POLICY_REVISION,
        policy_fingerprint=CI_HANDOFF_BLOCKER_POLICY_FINGERPRINT,
    )


def _evaluation_id(
    episode: PmRecoveryEpisode,
    result: CIHandoffAdapterResult,
    now: datetime,
) -> str:
    if (
        result.reconciliation is not None
        and result.reconciliation.reconciliation_id is not None
    ):
        return (
            f"ci-reconciliation:{result.reconciliation.reconciliation_id}:"
            f"{episode.fairness_cursor}:{result.reconciliation.reason.value}"
        )
    return "ci-evaluation:" + _canonical_hash(
        {
            "cursor": episode.fairness_cursor,
            "episode": str(episode.id),
            "evaluated_at": now.isoformat(),
            "reason": result.reason.value,
        }
    )


def _supersede_fence_blockers(
    *, db: Database, candidate: Ticket, event_id: str, now: datetime
) -> None:
    repo = PmRecoveryRepo(db)
    for blocker in repo.list_blockers(
        product_id=candidate.product_id,
        active_only=True,
        operation=CI_HANDOFF_RECOVERY_OPERATION,
        candidate_ticket_id=candidate.id,
    ):
        if blocker.authority_kind is PmBlockerAuthorityKind.FENCE:
            repo.supersede_blocker(
                blocker_id=blocker.id,
                superseded_by_event_id=event_id,
                supersession_kind=PmBlockerSupersessionKind.RECOVERY,
                superseded_at=now,
            )


def record_fair_ci_handoff_evaluation(
    *,
    db: Database,
    selection: FairCIHandoffSelection,
    result: CIHandoffAdapterResult,
    now: datetime,
) -> PmRecoveryEpisode:
    """Atomically move the selected episode to the tail and diagnose a hold."""

    candidate = selection.candidate
    episode = selection.episode
    if candidate is None or episode is None:
        raise CIHandoffFairnessError("cannot record an empty fairness selection")
    resolved_fence = bool(
        result.reconciliation
        and result.reconciliation.reason
        in {
            CIHandoffReason.FENCE_RECONCILED_TARGET,
            CIHandoffReason.FENCE_RECONCILED_SOURCE,
            CIHandoffReason.FENCE_RECONCILED_MOVED,
        }
    )
    fence_resolution_without_blocker = bool(
        result.reconciliation
        and result.reconciliation.reason
        in {
            CIHandoffReason.FENCE_RECONCILED_TARGET,
            CIHandoffReason.FENCE_RECONCILED_SOURCE,
        }
    )
    authoritative_progress = bool(
        result.reconciliation
        and result.reconciliation.reason is CIHandoffReason.TICKET_NOT_CI_PENDING
    )
    blocker = None
    if (
        result.held
        and not fence_resolution_without_blocker
        and not authoritative_progress
    ):
        blocker = _blocker_intent(
            db=db,
            candidate=candidate,
            result=result,
            snapshot=selection.candidates,
            now=now,
        )
    recorded = (
        PmRecoveryRepo(db)
        .record_evaluation(
            episode_id=episode.id,
            expected_cursor_sequence=episode.fairness_cursor,
            evaluation_id=_evaluation_id(episode, result, now),
            evaluated_at=now,
            blocker=blocker,
            relieve_starvation_for_candidate=True,
            supersede_prior_blockers_for_episode=True,
        )
        .episode
    )

    if resolved_fence:
        assert result.reconciliation is not None
        reconciliation_id = result.reconciliation.reconciliation_id
        assert reconciliation_id is not None
        _supersede_fence_blockers(
            db=db,
            candidate=candidate,
            event_id=f"fence-reconciled:{reconciliation_id}",
            now=now,
        )

    current = TicketRepo(db).get_by_key(candidate.key)
    fence_remains = (
        CIHandoffCoordinationRepo(db).get_fence(candidate.product_id) is not None
    )
    if (
        current is not None
        and current.status is not TicketStatus.CI_PENDING
        and not fence_remains
    ):
        PmRecoveryRepo(db).close_episode(
            episode_id=recorded.id,
            closure_event_id=(
                "ci-progress:"
                + _canonical_hash(
                    {
                        "episode": str(recorded.id),
                        "status": current.status.value,
                    }
                )
            ),
            closure_kind=PmRecoveryEpisodeClosureKind.RECOVERY_COMPLETED,
            closed_at=now,
        )
    return recorded


def active_episode_for_ticket(db: Database, ticket: Ticket) -> PmRecoveryEpisode | None:
    """Return the one active ordinary CI episode for an exact ticket."""

    matches = [
        episode
        for episode in _active_ci_episodes(PmRecoveryRepo(db), ticket.product_id)
        if episode.candidate_ticket_id == ticket.id
    ]
    if len(matches) > 1:
        raise CIHandoffFairnessError("multiple active CI episodes for one ticket")
    return matches[0] if matches else None


def ensure_ci_handoff_episode(
    *,
    db: Database,
    ticket: Ticket,
    initial_issues: list[LinearIssue],
    now: datetime,
) -> PmRecoveryEpisode:
    """Return or establish the exact fence ticket's durable CI episode.

    This is a compatibility bridge for a fence left by a process/version that
    died before ordinary fair selection established its episode.  An existing
    active episode is never publication-replaced while its older write fence is
    still the authority being recovered.
    """

    active = active_episode_for_ticket(db, ticket)
    if active is not None:
        return active
    identity, _lifecycle = _identity(db, ticket, initial_issues)
    return PmRecoveryRepo(db).establish_episode(identity, created_at=now)
