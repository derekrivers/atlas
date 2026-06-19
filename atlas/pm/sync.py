"""PM-Engine ticket synchronisation (ATLAS-42).

One idempotent sync pass (:func:`sync_tick`) wiring the ATLAS-41 boundary
primitives into steps 1-3 of the ``pm-engine-and-linear-sync.md`` "Sync loop":
pull status Linear -> Atlas, push owned definitions Atlas -> Linear, promote
dependency-ready tickets to ``Ready for Agent`` (:mod:`atlas.pm.promotion`),
and *nothing else crossing*. The "log anomalies otherwise" clause of step 1 and
steps 4-5 (the anomaly/DebtItem write, the follow-up scan, the scheduler) are
ATLAS-118/-45/-50 and are deliberately NOT here.

Directionality is structural, not conventional. The pull reads only the
issue's state id (:func:`status_from_issue`) and writes only ``status``; the
push sends only :func:`definition_payload` (title + description — priority and
labels are owned-but-deferred) and the client rejects any unowned key. So a
Linear-side edit of an Atlas-owned field is never pulled, and an Atlas status
change is mechanically incapable of being pushed *through the definition path*.
The one exception is step 3's readiness promotion, which writes the ``Ready
for Agent`` state through the dedicated :meth:`LinearClient.set_state` (never
the definition push) and only that one state -- a narrow, sanctioned path that
leaves ``OWNED_LINEAR_INPUT_KEYS`` unchanged.

Idempotency rests on the sync cursor ``Ticket.linear_synced_at``: a definition
is re-pushed only while ``updated_at > linear_synced_at`` (or it has never
synced), and the cursor is stamped to the pushed ``updated_at`` — so a second
tick over an unchanged Linear produces zero writes (no redundant status set,
no re-push). A missed or interrupted tick costs latency only (D5: push to
Linear first, then stamp; a failed push is retried next tick).

Layering: this is ``atlas.pm``, above ``atlas.storage``/``atlas.linear``/
``atlas.core`` in the import spine — it needs ``TicketRepo``, so it cannot live
in ``atlas.linear`` (below storage). It performs no I/O of its own beyond the
injected ``LinearClient``; tests drive it with the in-memory fake, so CI runs
with no network and no secrets.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from atlas.core.models.ticket import Ticket, TicketStatus
from atlas.dependencies.graph import build_dependency_graph
from atlas.dependencies.validation import TERMINAL_STATUSES
from atlas.linear.client import LinearClient
from atlas.linear.ownership import (
    LinearStatusMap,
    definition_payload,
    status_from_issue,
)
from atlas.pm.promotion import promote_ready
from atlas.storage.db import Database
from atlas.storage.repositories import TicketRepo

logger = logging.getLogger("atlas.pm.sync")

# "Frozen once In Progress" (pm-engine-and-linear-sync.md "Field ownership"):
# definitions are pushed only while the ticket is pre-dispatch or Ready for
# Agent. Every status from in_progress onward (pr_open, review_required,
# changes_requested, done, rejected, needs_human_decision) is frozen.
PUSHABLE_STATUSES: frozenset[TicketStatus] = frozenset(
    {
        TicketStatus.BACKLOG,
        TicketStatus.PLANNED,
        TicketStatus.BLOCKED,
        TicketStatus.READY_FOR_AGENT,
    }
)


@dataclass
class SyncResult:
    """Per-tick counters — the structured observability D2 asks for, and what
    ATLAS-118 will read to decide anomaly writes. Pure totals; no I/O."""

    status_pulled: int = 0
    status_unchanged: int = 0
    unmapped: int = 0
    pushed_created: int = 0
    pushed_updated: int = 0
    push_skipped: int = 0
    promoted: int = 0


def _definition_changed(ticket: Ticket) -> bool:
    """Has the definition changed since the last confirmed push? The cursor
    rule: never synced -> push; otherwise push only while ``updated_at`` is
    strictly newer than the stamped cursor."""

    if ticket.linear_synced_at is None:
        return True
    return ticket.updated_at > ticket.linear_synced_at


def _pull(
    ticket: Ticket,
    tickets: TicketRepo,
    client: LinearClient,
    status_map: LinearStatusMap,
    result: SyncResult,
) -> Ticket:
    """Step 1 (Linear -> Atlas): mirror a mapped status onto a non-terminal
    ticket. Returns the possibly-updated ticket so the push step sees the
    post-pull status (a status pulled into a frozen state freezes the push)."""

    if ticket.external_linear_id is None:
        return ticket  # not yet joined to a Linear issue; nothing to pull
    if ticket.status.value in TERMINAL_STATUSES:
        return ticket  # terminal work is closed; do not poll it
    issue = client.fetch_issue(ticket.external_linear_id)
    if issue is None:
        # The join target is gone. Surfacing this as an anomaly is ATLAS-118;
        # here we only avoid crashing and leave status unchanged.
        logger.warning(
            "linear-sync: issue %s for %s not found; status left unchanged",
            ticket.external_linear_id,
            ticket.key,
        )
        return ticket
    mapped = status_from_issue(issue, status_map)
    if mapped is None:
        # Unmapped Linear state: never guessed. Counter + log only; the
        # DebtItem write is ATLAS-118 (the step-1 "log anomalies" clause).
        result.unmapped += 1
        logger.info(
            "linear-sync: unmapped Linear state %r for %s; status unchanged "
            "(anomaly write is ATLAS-118)",
            issue.state_id,
            ticket.key,
        )
        return ticket
    if mapped == ticket.status:
        result.status_unchanged += 1  # set-to-same is a no-op
        return ticket
    updated = tickets.apply_linear_status(ticket.key, mapped)
    result.status_pulled += 1
    logger.info(
        "linear-sync: pulled %s -> %s for %s",
        ticket.status.value,
        mapped.value,
        ticket.key,
    )
    return updated


def _push(
    ticket: Ticket,
    tickets: TicketRepo,
    client: LinearClient,
    team_id: str,
    result: SyncResult,
) -> None:
    """Step 2 (Atlas -> Linear): push the owned definition for a pushable
    ticket whose cursor says it changed. Push first, then stamp (D5)."""

    if ticket.status not in PUSHABLE_STATUSES or not _definition_changed(ticket):
        result.push_skipped += 1
        return
    definition = definition_payload(ticket)
    if ticket.external_linear_id is None:
        # First sync: create the issue and write back the join key. Stamped
        # immediately after the confirmed create to shrink the non-idempotent
        # create-retry window (tracked in docs/tech-debt/debt-register.md).
        issue = client.create_issue(definition, team_id=team_id)
        tickets.mark_definition_pushed(
            ticket.key,
            synced_at=ticket.updated_at,
            external_linear_id=issue.id,
        )
        result.pushed_created += 1
        logger.info("linear-sync: created Linear issue %s for %s", issue.id, ticket.key)
        return
    # update_issue is idempotent, so a stamp lost to a crash only re-pushes an
    # identical definition next tick — safe to re-run.
    client.update_issue(ticket.external_linear_id, definition)
    tickets.mark_definition_pushed(ticket.key, synced_at=ticket.updated_at)
    result.pushed_updated += 1
    logger.info("linear-sync: pushed definition for %s", ticket.key)


def sync_tick(
    *,
    tickets: TicketRepo,
    db: Database,
    client: LinearClient,
    status_map: LinearStatusMap,
    team_id: str,
) -> SyncResult:
    """Run one idempotent sync pass over every ticket (steps 1-3).

    Per ticket: pull a mapped status (Linear -> Atlas), then push the owned
    definition (Atlas -> Linear) if the cursor says it changed. Pull precedes
    push so a status pulled into a frozen state freezes the same tick's push.
    Each Linear call is bracketed by its own DB commit (push-then-stamp), so an
    interrupted tick is safe to re-run.

    Then step 3 (ATLAS-43): project the dependency graph and promote every
    dependency-ready ticket to ``Ready for Agent`` via the sanctioned
    :meth:`LinearClient.set_state`. The graph is built AFTER the pull/push loop
    so it reflects pulled statuses and any issue step 2 just created; the
    promotion is Linear-only, so the next tick's pull reconciles Atlas (keeping
    the pull the single Atlas-status writer). Returns per-tick counters.
    """

    result = SyncResult()
    for ticket in tickets.list():
        after_pull = _pull(ticket, tickets, client, status_map, result)
        _push(after_pull, tickets, client, team_id, result)
    graph = build_dependency_graph(db)
    result.promoted = promote_ready(
        tickets=tickets, graph=graph, client=client, status_map=status_map
    )
    return result
