"""Record-capture orchestration for operator confirmations."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Literal, Protocol, runtime_checkable
from uuid import UUID

from atlas.core.models import Evidence, Ticket
from atlas.storage import EvidenceRepo
from atlas.verification import (
    build_acceptance_confirmation,
    build_blanket_approval,
    build_scope_decision,
    pending_capture,
)


@runtime_checkable
class ConfirmPrompts(Protocol):
    """The interactive seam `atlas confirm` walks the operator through (D-3).

    Three methods, one per pending decision kind, each returning the operator's
    ruling as DATA the command routes to an OP-3.1 builder — never the record
    shape itself. Tests inject a scripted fake (no TTY, deterministic); production
    uses :func:`_make_confirm_prompts`, the stdin default. The seam is the ONLY
    place a human is consulted, so a no-prompt / no-TTY run can refuse cleanly
    rather than auto-confirm (D-4)."""

    def acceptance(self, criterion: str) -> bool:
        """Confirm this acceptance criterion at ``C`` (``True``) or skip it."""
        ...

    def scope(self, path: str) -> Literal["waive", "fail", "skip"]:
        """Rule on this out-of-scope file: waive it, fail it, or skip it."""
        ...

    def approval(self) -> Literal["approve", "reject", "skip"]:
        """Rule on the blanket PR approval: approve, reject, or skip it."""
        ...


def capture_ticket(
    ticket: Ticket,
    *,
    prompts: ConfirmPrompts,
    head_commit: str,
    pr_files: list[str],
    evidence: list[Evidence],
    product_id: UUID,
    operator_id: str,
    evidence_repo: EvidenceRepo,
    now: datetime,
    new_id: Callable[[], UUID],
) -> int:
    """Prompt the operator for one ticket's pending items and persist the rulings.

    Calls OP-3.1's :func:`pending_capture` (the inverse view of the three human
    evaluators at ``C``) and, for each returned item, routes the operator's answer
    to the matching builder (D-2): a confirmed criterion →
    :func:`build_acceptance_confirmation`; a waived/failed scope file →
    :func:`build_scope_decision`; an approved/rejected blanket →
    :func:`build_blanket_approval`. A skip persists nothing. Returns the number of
    records written so the command can summarise the session."""
    pending = pending_capture(
        ticket, head_commit=head_commit, pr_files=pr_files, evidence=evidence
    )
    recorded = 0

    for prompt in pending.unconfirmed_criteria:
        if prompts.acceptance(prompt.criterion):
            evidence_repo.add(
                build_acceptance_confirmation(
                    prompt.criterion,
                    ticket_id=ticket.id,
                    head_commit=head_commit,
                    product_id=product_id,
                    operator_id=operator_id,
                    evidence_id=new_id(),
                    now=now,
                )
            )
            recorded += 1

    for path in pending.undecided_scope_files:
        scope_decision = prompts.scope(path)
        if scope_decision in ("waive", "fail"):
            evidence_repo.add(
                build_scope_decision(
                    path,
                    waive=scope_decision == "waive",
                    ticket_id=ticket.id,
                    head_commit=head_commit,
                    product_id=product_id,
                    operator_id=operator_id,
                    evidence_id=new_id(),
                    now=now,
                )
            )
            recorded += 1

    if pending.human_approval_required_and_missing:
        approval_decision = prompts.approval()
        if approval_decision in ("approve", "reject"):
            evidence_repo.add(
                build_blanket_approval(
                    approved=approval_decision == "approve",
                    ticket_id=ticket.id,
                    head_commit=head_commit,
                    product_id=product_id,
                    operator_id=operator_id,
                    evidence_id=new_id(),
                    now=now,
                )
            )
            recorded += 1

    return recorded
