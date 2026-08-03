"""Record-capture orchestration for operator confirmations."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Literal, NamedTuple, Protocol, runtime_checkable
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


class ConfirmCaptureResult(NamedTuple):
    """Structured outcome for one ticket's confirmation capture."""

    passed_or_approved: int
    failed_or_rejected: int
    skipped: int

    @property
    def recorded(self) -> int:
        """Persisted decisions, whether positive or negative."""
        return self.passed_or_approved + self.failed_or_rejected

    @property
    def pending_actions(self) -> int:
        """Actions presented during this capture, including skipped actions."""
        return self.recorded + self.skipped


def build_confirmation_records(
    ticket: Ticket,
    *,
    confirmed_criteria: Sequence[str],
    manual_approval: bool | None,
    head_commit: str,
    product_id: UUID,
    operator_id: str,
    now: datetime,
    new_id: Callable[[], UUID],
) -> tuple[Evidence, ...]:
    """Build the canonical evaluator-compatible confirmation record set.

    Both the interactive CLI and governed acceptance-session action delegate to
    this service. ``None`` means the CLI did not rule on blanket approval;
    otherwise the boolean is recorded explicitly. Persistence remains the
    caller's transaction boundary.
    """

    records = [
        build_acceptance_confirmation(
            criterion,
            ticket_id=ticket.id,
            head_commit=head_commit,
            product_id=product_id,
            operator_id=operator_id,
            evidence_id=new_id(),
            now=now,
        )
        for criterion in confirmed_criteria
    ]
    if manual_approval is not None:
        records.append(
            build_blanket_approval(
                approved=manual_approval,
                ticket_id=ticket.id,
                head_commit=head_commit,
                product_id=product_id,
                operator_id=operator_id,
                evidence_id=new_id(),
                now=now,
            )
        )
    return tuple(records)


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
    """Prompt the operator and return only the number of persisted rulings."""
    return capture_ticket_result(
        ticket,
        prompts=prompts,
        head_commit=head_commit,
        pr_files=pr_files,
        evidence=evidence,
        product_id=product_id,
        operator_id=operator_id,
        evidence_repo=evidence_repo,
        now=now,
        new_id=new_id,
    ).recorded


def capture_ticket_result(
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
) -> ConfirmCaptureResult:
    """Prompt the operator for one ticket's pending items and persist the rulings.

    Calls OP-3.1's :func:`pending_capture` (the inverse view of the three human
    evaluators at ``C``) and, for each returned item, routes the operator's answer
    to the matching builder (D-2): a confirmed criterion →
    :func:`build_acceptance_confirmation`; a waived/failed scope file →
    :func:`build_scope_decision`; an approved/rejected blanket →
    :func:`build_blanket_approval`. A skip persists nothing. Returns structured
    positive, negative, and skipped counts so the command can distinguish
    "nothing outstanding" from every kind of presented decision without parsing
    prompt text."""
    pending = pending_capture(
        ticket, head_commit=head_commit, pr_files=pr_files, evidence=evidence
    )
    passed_or_approved = 0
    failed_or_rejected = 0
    skipped = 0

    for prompt in pending.unconfirmed_criteria:
        if prompts.acceptance(prompt.criterion):
            (record,) = build_confirmation_records(
                ticket,
                confirmed_criteria=(prompt.criterion,),
                manual_approval=None,
                head_commit=head_commit,
                product_id=product_id,
                operator_id=operator_id,
                now=now,
                new_id=new_id,
            )
            evidence_repo.add(record)
            passed_or_approved += 1
        else:
            skipped += 1

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
            if scope_decision == "waive":
                passed_or_approved += 1
            else:
                failed_or_rejected += 1
        else:
            skipped += 1

    if pending.human_approval_required_and_missing:
        approval_decision = prompts.approval()
        if approval_decision in ("approve", "reject"):
            (record,) = build_confirmation_records(
                ticket,
                confirmed_criteria=(),
                manual_approval=approval_decision == "approve",
                head_commit=head_commit,
                product_id=product_id,
                operator_id=operator_id,
                now=now,
                new_id=new_id,
            )
            evidence_repo.add(record)
            if approval_decision == "approve":
                passed_or_approved += 1
            else:
                failed_or_rejected += 1
        else:
            skipped += 1

    return ConfirmCaptureResult(
        passed_or_approved=passed_or_approved,
        failed_or_rejected=failed_or_rejected,
        skipped=skipped,
    )
