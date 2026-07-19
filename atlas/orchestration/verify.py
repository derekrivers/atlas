"""Cross-layer orchestration for evaluating and recording a PR verification."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import NamedTuple
from uuid import UUID, uuid4

from atlas.core.models.ticket import Ticket
from atlas.evidence import build_merge_evidence
from atlas.orchestration.pr_context import PRContext
from atlas.storage import (
    Database,
    EvidenceRepo,
    TicketRepo,
    VerificationCheckRepo,
)
from atlas.verification import PRVerification, evaluate_pr, verification_checks_for


class VerifyResult(NamedTuple):
    """The evaluated PR and lookup data needed by the presentation surface."""

    verification: PRVerification
    key_by_id: dict[UUID, str]
    unknown_keys: list[str]


def run_verify(
    context: PRContext,
    close_set: tuple[str, ...],
    db: Database,
) -> VerifyResult:
    """Load, evaluate, and append the verification and merge records for a PR."""
    ticket_repo = TicketRepo(db)
    tickets: list[Ticket] = []
    key_by_id: dict[UUID, str] = {}
    unknown_keys: list[str] = []
    for key in close_set:
        ticket = ticket_repo.get_by_key(key)
        if ticket is None:
            unknown_keys.append(key)
            continue
        tickets.append(ticket)
        key_by_id[ticket.id] = ticket.key

    evidence = EvidenceRepo(db).list()
    verification = evaluate_pr(
        tickets,
        pr_files=context.pr_files,
        head_commit=context.head_commit,
        evidence=evidence,
    )

    # OP-B: persist one append-only VerificationCheck per check. One clock and
    # the uuid4 factory are injected so the pure mapping is deterministic.
    rows = verification_checks_for(verification, now=datetime.now(UTC), new_id=uuid4)
    check_repo = VerificationCheckRepo(db)
    for row in rows:
        check_repo.add(row)

    # ATLAS-134: record the merge as system-tier, commit-pinned evidence per
    # close-set ticket when the PR is merged. The Done gate (pm.complete_verified)
    # reads this; verify only OBSERVES the operator's out-of-band merge. Append-only
    # and idempotent: a re-run on a still-merged PR appends an identical-commit
    # record (harmless). The builder returns None for an unmerged PR -> no record.
    evidence_repo = EvidenceRepo(db)
    for ticket in tickets:
        merge_record = build_merge_evidence(
            context.pull_request,
            head_commit=context.head_commit,
            ticket_id=ticket.id,
            product_id=ticket.product_id,
            evidence_id=uuid4(),
            now=datetime.now(UTC),
        )
        if merge_record is not None:
            evidence_repo.add(merge_record)

    return VerifyResult(verification, key_by_id, unknown_keys)
