"""The PR completion validator (ATLAS-77) — the Phase 7 outer-loop aggregation,
per docs/atlas/verification-engine.md "Verdict and completion" (the "PR completion"
note).

:func:`evaluate_pr` answers one question for a PR at its head commit ``C``: are ALL
the tickets it closes complete at ``C``? It is pure glue one level up from
:func:`atlas.verification.evaluate_ticket`: it calls ``evaluate_ticket`` for each
ticket the PR closes — passing every ticket the SAME PR file list, head commit, and
evidence set (``evaluate_ticket`` does the per-ticket filtering internally: scope
against each ticket's own ``relevant_docs``/``source_anchor``, human-tier evidence
by ``ticket_id``) — and folds the per-ticket verdicts into a PR verdict via the
shared :func:`atlas.verification.completion.fold_statuses` rule (D5): any ticket
FAILED -> FAILED; else (non-empty and) every ticket PASSED -> PASSED; else PENDING.
An EMPTY close-set -> PENDING (never a vacuous PASS over no tickets).

It does NOT resolve WHICH tickets a PR closes (no parsing of "Closes ATLAS-XX", no
GitHub I/O — the verify CLI's / Phase 8's job; the ticket set is a parameter), does
NOT persist anything, does NOT generate ids or timestamps, and does NOT touch
storage/evidence/github. The per-ticket breakdown is ordered by ``ticket.key`` (D6)
so the result is stable regardless of the caller's input order.

PURE and layer-faithful (D7): this module imports ``atlas.core`` and its
``atlas.verification`` siblings (``evaluate_ticket`` / ``TicketVerification``, the
shared ``fold_statuses``) ONLY — never ``atlas.evidence``, ``atlas.storage``,
``atlas.pm``, or ``atlas.github``. Like the per-check evaluators and
``evaluate_ticket`` it builds on, invalidity and absence are DATA, not exceptions:
:func:`evaluate_pr` NEVER raises, for any input (empty tickets, empty evidence,
empty pr_files included).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from atlas.core.enums import EvidenceStatus
from atlas.core.models import Evidence, Ticket
from atlas.verification.completion import (
    TicketVerification,
    evaluate_ticket,
    fold_statuses,
)


@dataclass(frozen=True)
class PRVerification:
    """The composed PR-level verdict (D3).

    Frozen: an immutable record. ``head_commit`` is the commit the verdict was
    computed at (every per-ticket check is pinned to it); ``status`` is the verdict
    folded over the per-ticket verdicts (D4); ``tickets`` is the per-ticket
    breakdown, each a :class:`TicketVerification`, ordered by ``ticket.key`` (D6).

    Minimal by design: there is no ``pr_number`` — the CLI associates this result
    with its PR (the validator never resolves which PR or which tickets it closes).
    """

    head_commit: str
    status: EvidenceStatus
    tickets: tuple[TicketVerification, ...]


def evaluate_pr(
    tickets: Sequence[Ticket],
    *,
    pr_files: Sequence[str],
    head_commit: str,
    evidence: Sequence[Evidence],
) -> PRVerification:
    """Compose the PR verdict over the tickets it closes (never raises).

    For each ticket — in ``ticket.key`` order (D6), so a scrambled input yields the
    same ordered breakdown — calls :func:`evaluate_ticket` with the one shared
    ``pr_files``/``head_commit``/``evidence``; ``evaluate_ticket`` filters each to
    the ticket's own identity (scope, ticket-scoped human evidence), so the same
    inputs evaluate each ticket independently. Folds the per-ticket verdicts via the
    shared :func:`fold_statuses` rule (D4/D5): any ticket FAILED -> FAILED; else all
    PASSED -> PASSED; else PENDING. An empty ticket set -> PENDING, never a vacuous
    PASS over no tickets.

    Args:
        tickets: the tickets the PR closes (resolving WHICH tickets is the CLI's /
            Phase 8's job — this validator does not parse PR bodies or call GitHub).
        pr_files: the PR's changed-file paths, shared across all tickets (resolving
            them is the CLI's / Phase 8's job).
        head_commit: the PR head commit every check is pinned to (a parameter —
            resolving it is the CLI's / Phase 8's job); recorded on the result.
        evidence: pre-loaded Evidence records, shared across all tickets (loading is
            the CLI's job; this module never touches storage). ``evaluate_ticket``
            filters it per ticket.
    """
    verifications = tuple(
        evaluate_ticket(
            ticket,
            pr_files=pr_files,
            head_commit=head_commit,
            evidence=evidence,
        )
        for ticket in sorted(tickets, key=lambda t: t.key)
    )
    return PRVerification(
        head_commit=head_commit,
        status=fold_statuses(v.status for v in verifications),
        tickets=verifications,
    )
