"""PM-Engine ticket synchronisation (ATLAS-42, extended by ATLAS-45/-118/-119/-120).

One idempotent sync pass (:func:`sync_tick`) wiring the ATLAS-41 boundary
primitives into steps 1-5 of the ``pm-engine-and-linear-sync.md`` "Sync loop":
pull status Linear -> Atlas, push owned definitions Atlas -> Linear, promote
dependency-ready tickets to ``Ready for Agent`` (:mod:`atlas.pm.promotion`),
scan tagged comments into inbox proposal stubs, and *nothing else crossing*.
Step 4 (ATLAS-45) is the follow-up scan: per synced ticket, read its Linear
comments (the read-only :meth:`LinearClient.fetch_comments`) and write one inbox
stub per comment tagged ``atlas:proposed-follow-up`` to
``docs/planning/inbox/<ticket-key>-<n>.md`` (see :func:`_scan_follow_ups`). It is
the PRODUCER only -- it surfaces follow-ups as working-tree stubs and stops;
the operator commits the inbox, and the consumer side (``atlas plan`` reading the
committed inbox as a separate input source, ``atlas apply`` moving processed
stubs) is ATLAS-122. The inbox stub is the ONE sanctioned ``docs/planning/``
write (ADR-0007), atomic and machine-written like ``atlas apply``'s renders; no
ticket is created and no Atlas/Linear state is written on the scan path. Step 1's
"log anomalies otherwise" clause is
ATLAS-118: an unmapped Linear state appends one ``OUT_OF_OWNERSHIP_TRANSITION``
``DebtItem`` per *transition* (see :func:`_pull` and the transition signal
``Ticket.last_observed_linear_state_id``). Step 5 has three clauses. Dwell-breach
(ATLAS-119): a ticket sitting in a working state past its horizon appends one
``DWELL_BREACH`` ``DebtItem`` per dwell *episode* (see :func:`_detect_dwell`,
``DWELL_HORIZONS``, and the episode boundary ``Ticket.status_entered_at``) —
report-only, it NEVER moves a ticket. Review-cycling (ATLAS-120): a ticket that
has made more than ``REVIEW_CYCLE_THRESHOLD`` ``changes_requested -> pr_open``
round trips is routed to ``Needs Human`` via the sanctioned
:meth:`LinearClient.set_state` and one ``REVIEW_CYCLE`` ``DebtItem`` is appended
as the deterministic failure-analysis note (see :func:`_detect_review_cycle`).
This is the ONE anomaly that both logs AND moves a ticket — everywhere else the
two are separate. Stale-block (ATLAS-44): a ticket stranded in ``blocked`` whose
structural blockers have all cleared (``blocked(graph, key)`` empty) appends one
``STALE_BLOCK`` ``DebtItem`` per blocked *episode* (see
:func:`_detect_stale_block`) — report-only like dwell, it surfaces a candidate to
move but NEVER routes, since the graph sees only structural blockers and the
operator owns the move. The remaining work — the recurring scheduler (ATLAS-50) and the
follow-up CONSUMER (ATLAS-122: plan reads the committed inbox, apply moves
processed stubs) — is deliberately NOT here.

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
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import networkx as nx

from atlas.core.enums import ActorType
from atlas.core.models.debt_item import AnomalyType, DebtItem
from atlas.core.models.ticket import Ticket, TicketStatus
from atlas.dependencies.blockers import blocked
from atlas.dependencies.graph import build_dependency_graph
from atlas.dependencies.validation import TERMINAL_STATUSES
from atlas.linear.client import LinearClient, LinearIssue
from atlas.linear.ownership import (
    LinearStatusMap,
    definition_payload,
    status_from_issue,
)
from atlas.pm.promotion import promote_ready
from atlas.storage.db import Database
from atlas.storage.repositories import DebtItemRepo, TicketRepo

# Attribution for system-observed anomalies (data-model §6.1): the PM Engine
# writes DebtItems from deterministic observation, so created_by_type is
# ``system`` and created_by_id names the writer (matches the §6.1 example).
# One definition for the system-actor id, mirroring planning's CREATED_BY.
CREATED_BY = "pm-engine"

# Per-status dwell horizons (ATLAS-119; pm-engine-and-linear-sync.md "Anomaly and
# dwell detection"). A ticket whose time in one of these working states exceeds
# its horizon has dwelt too long and appends one ``DWELL_BREACH`` DebtItem per
# episode. Config with these defaults (D2): a module-level map, env-overridable
# later like the status map — there is no scheduler config wiring yet. Only these
# three statuses dwell-breach; any other status has no horizon and never
# breaches (``.get`` returns ``None``).
DWELL_HORIZONS: dict[TicketStatus, timedelta] = {
    TicketStatus.IN_PROGRESS: timedelta(hours=24),
    TicketStatus.PR_OPEN: timedelta(hours=48),
    TicketStatus.REVIEW_REQUIRED: timedelta(days=7),
}

# Review-cycling threshold (ATLAS-120; pm-engine-and-linear-sync.md "Anomaly and
# dwell detection"). "More than 3 ``changes_requested -> pr_open`` round trips"
# routes the ticket to ``Needs Human`` — so a ticket is routed when its
# ``review_cycle_count`` is STRICTLY GREATER than this (count > 3 routes; count
# == 3 does not). A module-level config constant beside ``DWELL_HORIZONS``,
# env-overridable later like the status map.
REVIEW_CYCLE_THRESHOLD = 3

# The states a ticket cycles between on a ``changes_requested -> pr_open`` round
# trip. The review-cycling pass acts only while the ticket is still in one of
# these (D6/GAP B): the moment ATLAS-42's pull reconciles the routed ticket into
# ``needs_human_decision`` it leaves this set, so the pass self-clears and stops
# re-routing. Both transitions into these states stamp ``status_entered_at``, so
# a ticket here always has a non-NULL episode boundary for the log dedup.
CYCLING_STATES: frozenset[TicketStatus] = frozenset(
    {TicketStatus.CHANGES_REQUESTED, TicketStatus.PR_OPEN}
)

logger = logging.getLogger("atlas.pm.sync")

# Follow-up scan (ATLAS-45; pm-engine-and-linear-sync.md "Follow-up ingestion").
# A comment whose body CONTAINS this tag is an agent-proposed follow-up; the scan
# writes one inbox stub per such comment. Substring match (D3) — the tag may sit
# anywhere in the body.
FOLLOW_UP_TAG = "atlas:proposed-follow-up"

# The dedup key. Each stub's first line is a non-rendering HTML comment carrying
# its source comment id, kept SEPARATE from the verbatim body so a body that
# itself contains ``FOLLOW_UP_TAG`` can never be mistaken for the key. A comment
# whose id already appears in any stub under inbox/ or inbox/processed/ is skipped
# (stubbed once on first sight, then never again) — robust to a comment tagged
# late, and needing no per-ticket cursor or schema field. Failure modes
# (acceptable): a stub manually deleted from inbox/ before processing is
# re-stubbed; a verbatim body containing this exact marker line would false-dedup
# (vanishingly unlikely).
_SOURCE_COMMENT_MARKER = "atlas-source-comment-id"
_MARKER_RE = re.compile(rf"<!-- {_SOURCE_COMMENT_MARKER}: (?P<id>.+?) -->")

# The committed-inbox subdirectory ``atlas apply`` (ATLAS-122) moves processed
# stubs into. The scan reads it only to keep the dedup key set and the per-ticket
# index monotonic — it never writes there.
_PROCESSED_SUBDIR = "processed"

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
    """Per-tick counters — the structured observability D2 asks for. Pure
    totals; no I/O. ``unmapped`` counts every observed unmapped state this
    tick; ``anomalies_logged`` counts only the subset that were *transitions*
    into an unmapped state and therefore appended a DebtItem (a persisting
    unmapped state increments ``unmapped`` but not ``anomalies_logged``).
    ``dwell_breaches`` (ATLAS-119) counts the ``DWELL_BREACH`` rows appended
    this tick — one per dwell *episode*, so a ticket dwelling past its horizon
    across N ticks increments it once, not once per tick. ``routed_to_human``
    and ``review_cycles_logged`` (ATLAS-120) split the review-cycling pass the
    same way ``unmapped``/``anomalies_logged`` split step 1: ``routed_to_human``
    counts every ``set_state`` route this tick (the route fires idempotently
    each tick until the pull reconciles the ticket, so a not-yet-reconciled
    ticket increments it every tick), while ``review_cycles_logged`` counts only
    the deduped ``REVIEW_CYCLE`` rows appended — one per ``pr_open`` episode.
    ``follow_ups_stubbed`` (ATLAS-45) counts the inbox stubs written this tick —
    one per newly-seen tagged comment, so a tagged comment already stubbed under
    inbox/ or inbox/processed/ increments it zero, not once per tick.
    ``stale_blocks`` (ATLAS-44) counts the ``STALE_BLOCK`` rows appended this tick
    — one per blocked *episode*, exactly like ``dwell_breaches``, so a ticket
    stranded in ``blocked`` with its structural blockers cleared across N ticks
    increments it once, not once per tick. Report-only, like ``dwell_breaches``;
    no route counter accompanies it because the stale-block pass never moves a
    ticket."""

    status_pulled: int = 0
    status_unchanged: int = 0
    unmapped: int = 0
    anomalies_logged: int = 0
    pushed_created: int = 0
    pushed_updated: int = 0
    push_skipped: int = 0
    promoted: int = 0
    follow_ups_stubbed: int = 0
    dwell_breaches: int = 0
    routed_to_human: int = 0
    review_cycles_logged: int = 0
    stale_blocks: int = 0


def _definition_changed(ticket: Ticket) -> bool:
    """Has the definition changed since the last confirmed push? The cursor
    rule: never synced -> push; otherwise push only while ``updated_at`` is
    strictly newer than the stamped cursor."""

    if ticket.linear_synced_at is None:
        return True
    return ticket.updated_at > ticket.linear_synced_at


def _out_of_ownership_item(
    ticket: Ticket, issue: LinearIssue, now: datetime
) -> DebtItem:
    """Build the ``OUT_OF_OWNERSHIP_TRANSITION`` DebtItem for an unmapped state.

    System-attributed and append-only (data-model §6.1): ``product_id`` and
    ``ticket_id`` come from the synced ticket the anomaly was observed against
    (D3), the type is the single existing enum member (D1), and ``observed_at``
    / ``created_at`` are the tick clock. No trust tier, no status — a DebtItem
    is an operational record, not evidence."""

    return DebtItem(
        id=uuid4(),
        product_id=ticket.product_id,
        ticket_id=ticket.id,
        anomaly_type=AnomalyType.OUT_OF_OWNERSHIP_TRANSITION,
        summary=(
            f"Linear state {issue.state_id!r} for {ticket.key} does not follow "
            "the ownership table (unmapped); status left unchanged"
        ),
        observed_at=now,
        created_by_type=ActorType.SYSTEM,
        created_by_id=CREATED_BY,
        created_at=now,
    )


def _dwell_breach_item(ticket: Ticket, horizon: timedelta, now: datetime) -> DebtItem:
    """Build the ``DWELL_BREACH`` DebtItem for a ticket past its dwell horizon.

    System-attributed and append-only (data-model §6.1), exactly like the
    out-of-ownership item: ``product_id``/``ticket_id`` come from the dwelling
    ticket (D3), the type is ``DWELL_BREACH`` (D1), and ``observed_at`` /
    ``created_at`` are the injected tick clock (D3). Report-only — building or
    recording this never changes ticket state. The caller (:func:`_detect_dwell`)
    only builds this once ``status_entered_at`` is known non-NULL."""

    entered = ticket.status_entered_at.isoformat() if ticket.status_entered_at else "?"
    return DebtItem(
        id=uuid4(),
        product_id=ticket.product_id,
        ticket_id=ticket.id,
        anomaly_type=AnomalyType.DWELL_BREACH,
        summary=(
            f"{ticket.key} has dwelt in {ticket.status.value} since {entered}, "
            f"past its {horizon} horizon; status unchanged"
        ),
        observed_at=now,
        created_by_type=ActorType.SYSTEM,
        created_by_id=CREATED_BY,
        created_at=now,
    )


def _detect_dwell(
    ticket: Ticket,
    debt: DebtItemRepo,
    result: SyncResult,
    now: datetime,
) -> None:
    """Step 5 (ATLAS-119): append one ``DWELL_BREACH`` per dwell *episode* when
    ``ticket`` has sat in a horizoned working state too long. Report-only — it
    NEVER writes ticket state (that is ATLAS-120's review-cycling rule).

    Skips, in order: a status with no configured horizon (``DWELL_HORIZONS.get``
    is ``None`` — only ``in_progress``/``pr_open``/``review_required`` breach); a
    NULL ``status_entered_at`` (unknown entry time — skipped rather than guessing
    a false breach); a ticket still inside its horizon. Otherwise it logs once
    per episode: ``status_entered_at`` is the episode boundary, so a row is
    appended only when none has been logged since the ticket entered this status
    (:meth:`DebtItemRepo.logged_since`). When the status later changes,
    ``status_entered_at`` advances and a fresh episode can log again."""

    horizon = DWELL_HORIZONS.get(ticket.status)
    if horizon is None:
        return  # this status carries no dwell horizon; never breaches
    if ticket.status_entered_at is None:
        return  # unknown entry time: skip, never a false breach
    if now - ticket.status_entered_at < horizon:
        return  # still inside the horizon
    if debt.logged_since(ticket.id, AnomalyType.DWELL_BREACH, ticket.status_entered_at):
        return  # this episode already logged one breach; not one per tick
    debt.record(_dwell_breach_item(ticket, horizon, now))
    result.dwell_breaches += 1
    logger.info(
        "linear-sync: dwell breach for %s in %s (entered %s, horizon %s); "
        "DebtItem logged, status unchanged",
        ticket.key,
        ticket.status.value,
        ticket.status_entered_at.isoformat(),
        horizon,
    )


def _stale_block_item(ticket: Ticket, now: datetime) -> DebtItem:
    """Build the ``STALE_BLOCK`` DebtItem for a ticket stranded in ``blocked``
    whose structural blockers have all cleared.

    System-attributed and append-only (data-model §6.1), exactly like the dwell
    item: ``product_id``/``ticket_id`` come from the stranded ticket (D3), the
    type is ``STALE_BLOCK`` (D1), and ``observed_at`` / ``created_at`` are the
    injected tick clock — used ONLY to stamp the row, never in a detection
    comparison (the check is structural, not time-based). Report-only — building
    or recording this never changes ticket state; the engine surfaces the
    candidate and the operator decides whether to move it (the graph sees only
    structural blockers, so a non-structural ``blocked`` reason it cannot see is
    why this never routes). The caller (:func:`_detect_stale_block`) only builds
    this once ``status_entered_at`` is known non-NULL."""

    entered = ticket.status_entered_at.isoformat() if ticket.status_entered_at else "?"
    return DebtItem(
        id=uuid4(),
        product_id=ticket.product_id,
        ticket_id=ticket.id,
        anomaly_type=AnomalyType.STALE_BLOCK,
        summary=(
            f"{ticket.key} is marked blocked (since {entered}) but its structural "
            "blockers have all cleared; candidate to move, status unchanged"
        ),
        observed_at=now,
        created_by_type=ActorType.SYSTEM,
        created_by_id=CREATED_BY,
        created_at=now,
    )


def _detect_stale_block(
    ticket: Ticket,
    graph: nx.DiGraph[str],
    debt: DebtItemRepo,
    result: SyncResult,
    now: datetime,
) -> None:
    """Step 5 (ATLAS-44): append one ``STALE_BLOCK`` per blocked *episode* when
    ``ticket`` sits in ``blocked`` but its structural blockers have all cleared
    (``blocked(graph, key)`` is empty). Report-only — like :func:`_detect_dwell`
    it NEVER writes ticket state and never calls Linear (only ATLAS-120's
    review-cycling rule moves a ticket).

    This surfaces a ticket that may be ready to move but is stranded in
    ``blocked``, where :func:`promote_ready` will not touch it (it promotes only
    ``planned``/``backlog``). It deliberately does NOT route: the dependency graph
    knows only *structural* blockers, so a ticket may be ``blocked`` for a
    non-structural reason the graph cannot see — the engine reports the candidate
    and the operator decides. The inverse (structurally blocked but not marked
    ``blocked``) is out of scope: :func:`~atlas.dependencies.readiness.is_ready`
    already refuses to promote it, so it is not stranded.

    Skips, in order: a ticket not in ``blocked`` (the only status this pass
    considers — ``now`` is never used in a comparison, the check is structural); a
    NULL ``status_entered_at`` (unknown episode boundary — skipped rather than
    guessing, exactly as dwell does); a ticket whose structural blockers are still
    active (``blocked(graph, key).is_blocked`` — the wrong answer logs for a still
    blocked ticket). Otherwise it logs once per episode: ``status_entered_at`` is
    the episode boundary, so a row is appended only when none has been logged
    since the ticket entered ``blocked`` (:meth:`DebtItemRepo.logged_since`). When
    the status later changes, ``status_entered_at`` advances and a fresh stranded
    episode can log again.

    ``ticket`` is guaranteed a present ticket node in ``graph`` — both come from
    the same per-tick :class:`TicketRepo` read — so :func:`blocked` never raises
    its precondition ``ValueError`` here (the same guarantee :func:`promote_ready`
    relies on)."""

    if ticket.status is not TicketStatus.BLOCKED:
        return  # only a ticket marked blocked can be stranded in blocked
    if ticket.status_entered_at is None:
        return  # unknown entry time: skip, never a false stale-block
    if blocked(graph, ticket.key).is_blocked:
        return  # still structurally blocked; not stranded
    if debt.logged_since(ticket.id, AnomalyType.STALE_BLOCK, ticket.status_entered_at):
        return  # this episode already logged one; not one per tick
    debt.record(_stale_block_item(ticket, now))
    result.stale_blocks += 1
    logger.info(
        "linear-sync: stale block for %s (entered blocked %s, structural blockers "
        "cleared); DebtItem logged, status unchanged",
        ticket.key,
        ticket.status_entered_at.isoformat(),
    )


def _review_cycle_item(ticket: Ticket, now: datetime) -> DebtItem:
    """Build the ``REVIEW_CYCLE`` DebtItem — the deterministic failure-analysis
    note (D5). No model call (the PM Engine is deterministic) and no Linear
    comment: the note IS the row's summary, naming the round-trip count, the
    cycling pattern, and the route taken. System-attributed and append-only
    (data-model §6.1), exactly like the dwell and out-of-ownership items."""

    return DebtItem(
        id=uuid4(),
        product_id=ticket.product_id,
        ticket_id=ticket.id,
        anomaly_type=AnomalyType.REVIEW_CYCLE,
        summary=(
            f"{ticket.key} made {ticket.review_cycle_count} changes_requested -> "
            f"pr_open round trips (more than the {REVIEW_CYCLE_THRESHOLD} "
            "threshold); routed to needs_human_decision via set_state"
        ),
        observed_at=now,
        created_by_type=ActorType.SYSTEM,
        created_by_id=CREATED_BY,
        created_at=now,
    )


def _detect_review_cycle(
    ticket: Ticket,
    debt: DebtItemRepo,
    client: LinearClient,
    needs_human_state_id: str,
    result: SyncResult,
    now: datetime,
) -> None:
    """Step 5 (ATLAS-120): route a review-cycling ticket to ``Needs Human`` and
    log one ``REVIEW_CYCLE`` note. The ONE anomaly that both moves a ticket AND
    logs — everywhere else the two are separate (D6).

    Skips, in order: a ticket not in a ``CYCLING_STATES`` working state (e.g.
    already reconciled into ``needs_human_decision`` — the self-clearing guard);
    one at or under the threshold (``review_cycle_count <= REVIEW_CYCLE_THRESHOLD``
    — the wrong answer routes at 3); one with no Linear join (cannot route, like
    :func:`promote_ready`'s skip); a NULL ``status_entered_at`` (no episode
    boundary for the log dedup — unreachable when the count exceeds the
    threshold, since the increment and the ``pr_open`` entry stamp are written
    together, but guarded for a hand-seeded inconsistency).

    Route first, then log (the ordering). The route is the EXISTING sanctioned
    outbound write — ATLAS-43's :meth:`LinearClient.set_state` to the resolved
    Needs-Human state, Linear-only (ATLAS-42's next pull reconciles Atlas) and
    idempotent (``set_state`` to the same state is a no-op). It is attempted on
    EVERY tick until the pull reconciles the ticket out of the cycling states, so
    ``routed_to_human`` counts route attempts. A failed ``set_state`` raises and
    aborts the tick before the log (so a row is never written without a real
    routing attempt) and the route is retried next tick. The log is then deduped
    per ``pr_open`` episode via :meth:`DebtItemRepo.logged_since` keyed on
    ``status_entered_at`` (ATLAS-119's machinery), so a not-yet-reconciled route
    retrying across ticks logs exactly ONE ``REVIEW_CYCLE``, not one per tick."""

    if ticket.status not in CYCLING_STATES:
        return  # not cycling (e.g. already routed and reconciled into Needs Human)
    if ticket.review_cycle_count <= REVIEW_CYCLE_THRESHOLD:
        return  # at or under threshold; routing at 3 would be the wrong answer
    if ticket.external_linear_id is None:
        return  # no Linear join: nothing to route (mirrors promote_ready)
    if ticket.status_entered_at is None:
        return  # no episode boundary for dedup; unreachable when count > threshold
    # Route first: the one sanctioned move (ATLAS-43), idempotent and Linear-only.
    # A raise here aborts the tick before the log and is retried next tick.
    client.set_state(ticket.external_linear_id, needs_human_state_id)
    result.routed_to_human += 1
    logger.info(
        "linear-sync: review-cycling %s (%d round trips) routed to "
        "needs_human_decision via set_state (Linear state %s)",
        ticket.key,
        ticket.review_cycle_count,
        needs_human_state_id,
    )
    # Then log once per pr_open episode: a route retrying across ticks (not yet
    # reconciled) must not double-log. status_entered_at is the episode boundary.
    if debt.logged_since(ticket.id, AnomalyType.REVIEW_CYCLE, ticket.status_entered_at):
        return  # this episode already logged its note; not one per tick
    debt.record(_review_cycle_item(ticket, now))
    result.review_cycles_logged += 1
    logger.info(
        "linear-sync: review-cycle DebtItem logged for %s (%d round trips)",
        ticket.key,
        ticket.review_cycle_count,
    )


def _stub_index(stem: str) -> tuple[str, int] | None:
    """Parse an inbox stub stem ``<ticket-key>-<n>`` into ``(key, n)``. The key
    itself contains hyphens (``ATLAS-200``), so the index is the trailing all-
    digit segment: ``ATLAS-200-1`` -> ``("ATLAS-200", 1)``. A stem without a
    numeric tail (not one of our stubs) returns ``None``."""

    key, sep, tail = stem.rpartition("-")
    if sep and tail.isdigit():
        return key, int(tail)
    return None


def _inbox_state(inbox_dir: Path) -> tuple[set[str], dict[str, int]]:
    """Read the existing inbox once per tick for the dedup. Returns the set of
    source comment ids already stubbed (the dedup keys, scanned from the marker
    line of every stub under inbox/ AND inbox/processed/) and the highest index
    used per ticket key across BOTH directories — so a re-issued index stays
    monotonic even after ``atlas apply`` (ATLAS-122) moves a stub to processed/.
    A missing inbox (nothing produced yet) yields empties."""

    seen_ids: set[str] = set()
    max_index: dict[str, int] = {}
    for directory in (inbox_dir, inbox_dir / _PROCESSED_SUBDIR):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            parsed = _stub_index(path.stem)
            if parsed is not None:
                key, index = parsed
                max_index[key] = max(max_index.get(key, 0), index)
            match = _MARKER_RE.search(path.read_text(encoding="utf-8"))
            if match is not None:
                seen_ids.add(match.group("id"))
    return seen_ids, max_index


def _stub_content(ticket: Ticket, comment_id: str, body: str) -> str:
    """The inbox stub: the dedup marker (a non-rendering HTML comment, kept
    separate from the body), a title, the honest source reference (the ticket key
    and the Linear issue id — no fabricated URL, A3), the source comment id, and
    the verbatim comment body."""

    return (
        f"<!-- {_SOURCE_COMMENT_MARKER}: {comment_id} -->\n"
        f"# Follow-up from {ticket.key}\n"
        "\n"
        f"Source issue: {ticket.key} (Linear issue {ticket.external_linear_id})\n"
        f"Source comment: {comment_id}\n"
        "\n"
        f"{body}\n"
    )


def _write_stub(
    inbox_dir: Path, ticket: Ticket, comment_id: str, body: str, index: int
) -> None:
    """Write one inbox stub atomically (temp + ``os.replace``), mirroring
    ``atlas apply``'s render writes. This is the ONE sanctioned ``docs/planning/``
    write (ADR-0007) — the inbox's machine writer — and it writes ONLY under
    inbox/, never elsewhere in docs/planning/. The operator commits it."""

    inbox_dir.mkdir(parents=True, exist_ok=True)
    final = inbox_dir / f"{ticket.key}-{index}.md"
    temp = inbox_dir / f"{final.name}.tmp-{uuid4()}"
    temp.write_text(_stub_content(ticket, comment_id, body), encoding="utf-8")
    os.replace(temp, final)


def _scan_follow_ups(
    ticket: Ticket,
    client: LinearClient,
    inbox_dir: Path,
    seen_ids: set[str],
    max_index: dict[str, int],
    result: SyncResult,
) -> None:
    """Step 4 (ATLAS-45): read ``ticket``'s Linear comments and write one inbox
    stub per comment tagged ``atlas:proposed-follow-up`` that has not been stubbed
    before. PRODUCER only — it creates no ticket and writes no Atlas/Linear state;
    follow-ups enter the backlog solely through plan/apply (ADR-0007), and the
    consumer side is ATLAS-122.

    Skips, in order: a ticket with no Linear join (nothing to read) and a terminal
    ticket (closed work is not polled — mirroring :func:`_pull`). Then per comment:
    skip an untagged body and a comment id already stubbed (the dedup; the wrong
    answer re-stubs it every tick). A newly-seen tagged comment is written at the
    next free index for the ticket key, and ``seen_ids``/``max_index`` are advanced
    so a second tagged comment this same tick lands at the next index without a
    re-read."""

    if ticket.external_linear_id is None:
        return  # not joined to a Linear issue; no comments to scan
    if ticket.status.value in TERMINAL_STATUSES:
        return  # terminal work is closed; do not poll it (mirrors _pull)
    for comment in client.fetch_comments(ticket.external_linear_id):
        if FOLLOW_UP_TAG not in comment.body:
            continue  # not a proposed follow-up
        if comment.id in seen_ids:
            continue  # already stubbed (inbox/ or processed/); dedup holds
        index = max_index.get(ticket.key, 0) + 1
        _write_stub(inbox_dir, ticket, comment.id, comment.body, index)
        seen_ids.add(comment.id)
        max_index[ticket.key] = index
        result.follow_ups_stubbed += 1
        logger.info(
            "linear-sync: follow-up stub %s-%d written for %s (comment %s)",
            ticket.key,
            index,
            ticket.key,
            comment.id,
        )


def _pull(
    ticket: Ticket,
    tickets: TicketRepo,
    debt: DebtItemRepo,
    client: LinearClient,
    status_map: LinearStatusMap,
    result: SyncResult,
    now: datetime,
) -> Ticket:
    """Step 1 (Linear -> Atlas): mirror a mapped status onto a non-terminal
    ticket, and (ATLAS-118) log an out-of-ownership anomaly when the observed
    Linear state is unmapped. Returns the possibly-updated ticket so the push
    step sees the post-pull status (a status pulled into a frozen state freezes
    the push)."""

    if ticket.external_linear_id is None:
        return ticket  # not yet joined to a Linear issue; nothing to pull
    if ticket.status.value in TERMINAL_STATUSES:
        return ticket  # terminal work is closed; do not poll it
    issue = client.fetch_issue(ticket.external_linear_id)
    if issue is None:
        # The join target is gone. That is a distinct anomaly (not an
        # out-of-ownership *transition*) and out of ATLAS-118's narrowed scope;
        # here we only avoid crashing and leave status unchanged.
        logger.warning(
            "linear-sync: issue %s for %s not found; status left unchanged",
            ticket.external_linear_id,
            ticket.key,
        )
        return ticket
    # Transition detector (ATLAS-118): a DebtItem fires only when the observed
    # state id CHANGES, so a persisting unmapped state logs one row, not one per
    # tick, and recurrence stays meaningful. A re-occurrence (unmapped -> mapped
    # -> the same unmapped id) is a genuine new transition because the
    # intervening mapped pull moved the signal.
    transitioned = issue.state_id != ticket.last_observed_linear_state_id
    if transitioned:
        tickets.mark_linear_state_observed(ticket.key, issue.state_id)
    mapped = status_from_issue(issue, status_map)
    if mapped is None:
        # Unmapped Linear state: never guessed. Count every observation; append
        # one DebtItem only on the transition into the unmapped state (the
        # step-1 "log anomalies" clause). Logging never moves the ticket.
        result.unmapped += 1
        if transitioned:
            debt.record(_out_of_ownership_item(ticket, issue, now))
            result.anomalies_logged += 1
            logger.info(
                "linear-sync: out-of-ownership transition into unmapped Linear "
                "state %r for %s; DebtItem logged, status unchanged",
                issue.state_id,
                ticket.key,
            )
        else:
            logger.info(
                "linear-sync: unmapped Linear state %r for %s persists; status "
                "unchanged, no new DebtItem",
                issue.state_id,
                ticket.key,
            )
        return ticket
    if mapped == ticket.status:
        result.status_unchanged += 1  # set-to-same is a no-op
        return ticket
    updated = tickets.apply_linear_status(ticket.key, mapped, now=now)
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
    inbox_dir: Path,
    now: datetime,
) -> SyncResult:
    """Run one idempotent sync pass over every ticket (steps 1-5).

    Per ticket: pull a mapped status (Linear -> Atlas) — logging an
    out-of-ownership ``DebtItem`` (ATLAS-118) on a transition into an unmapped
    state — then push the owned definition (Atlas -> Linear) if the cursor says
    it changed. Pull precedes push so a status pulled into a frozen state
    freezes the same tick's push. Each Linear call is bracketed by its own DB
    commit (push-then-stamp), so an interrupted tick is safe to re-run.

    Then step 3 (ATLAS-43): project the dependency graph and promote every
    dependency-ready ticket to ``Ready for Agent`` via the sanctioned
    :meth:`LinearClient.set_state`. The graph is built AFTER the pull/push loop
    so it reflects pulled statuses and any issue step 2 just created; the
    promotion is Linear-only, so the next tick's pull reconciles Atlas (keeping
    the pull the single Atlas-status writer).

    Then step 4 (ATLAS-45): scan each synced, non-terminal ticket's Linear
    comments and write one inbox stub per comment tagged
    ``atlas:proposed-follow-up`` to ``<inbox_dir>/<ticket-key>-<n>.md``
    (:func:`_scan_follow_ups`). PRODUCER only — it surfaces follow-ups as
    working-tree stubs (the ONE sanctioned ``docs/planning/`` write, atomic;
    ADR-0007), creating no ticket and writing no Atlas/Linear state; the operator
    commits the inbox and the consumer side is ATLAS-122. The inbox is read once
    up front (:func:`_inbox_state`) for the dedup key set and the per-ticket index,
    so a tagged comment already stubbed (in inbox/ or inbox/processed/) is written
    once, not once per tick.

    Then step 5: a final pass re-reads the tickets and runs three clauses per
    ticket. Dwell-breach (ATLAS-119): append one ``DWELL_BREACH`` DebtItem per
    dwell episode for a ticket sitting in a horizoned working state past its
    horizon (:func:`_detect_dwell`) — report-only. Review-cycling (ATLAS-120):
    route a ticket over ``REVIEW_CYCLE_THRESHOLD`` round trips to ``Needs Human``
    via :meth:`LinearClient.set_state` and log one ``REVIEW_CYCLE`` note
    (:func:`_detect_review_cycle`) — the one anomaly that moves a ticket. The
    Needs-Human target state is resolved ONCE up front (like
    :func:`promote_ready`'s Ready-for-Agent resolution), so a status map missing
    a unique ``needs_human_decision`` state fails loudly even when nothing is
    cycling. Stale-block (ATLAS-44): append one ``STALE_BLOCK`` DebtItem per
    blocked episode for a ticket stranded in ``blocked`` whose structural blockers
    have all cleared (:func:`_detect_stale_block`, reusing the same ``graph``
    built for promotion above) — report-only, it NEVER moves the ticket. The pass
    runs after promotion so it sees this tick's pulled statuses and freshly
    stamped ``status_entered_at``; the graph reflects current Atlas state because
    promotion writes Linear only. Returns per-tick counters.

    ``now`` is the injected tick clock (no hidden ``datetime.now``): it stamps
    the ``observed_at``/``created_at`` of any DebtItem this tick appends and the
    ``status_entered_at`` of any status this tick pulls, so the flow stays
    deterministic under test.
    """

    result = SyncResult()
    debt = DebtItemRepo(db)
    for ticket in tickets.list():
        after_pull = _pull(ticket, tickets, debt, client, status_map, result, now)
        _push(after_pull, tickets, client, team_id, result)
    graph = build_dependency_graph(db)
    result.promoted = promote_ready(
        tickets=tickets, graph=graph, client=client, status_map=status_map
    )
    # Step 4 (ATLAS-45): the follow-up comment scan, after promotion and before
    # the step-5 anomaly passes (the loop order). The inbox is read once up front
    # for the dedup key set and per-ticket index; each newly-seen tagged comment
    # is written as one atomic inbox stub. Read-only on Linear (fetch_comments);
    # the only write is the stub under inbox/.
    seen_ids, max_index = _inbox_state(inbox_dir)
    for ticket in tickets.list():
        _scan_follow_ups(ticket, client, inbox_dir, seen_ids, max_index, result)
    # Step 5: a sibling pass after promotion, re-reading tickets so it sees this
    # tick's pulled statuses and freshly stamped ``status_entered_at`` (promotion
    # writes Linear only, so it does not perturb Atlas status). Dwell-breach
    # (ATLAS-119) is report-only; review-cycling (ATLAS-120) routes via
    # set_state. The Needs-Human target is resolved up front (the load-time
    # guard, mirroring promote_ready), before the loop, so a misconfigured map
    # fails loudly even when no ticket is over threshold.
    needs_human_state_id = status_map.state_id_for(TicketStatus.NEEDS_HUMAN_DECISION)
    for ticket in tickets.list():
        _detect_dwell(ticket, debt, result, now)
        _detect_review_cycle(ticket, debt, client, needs_human_state_id, result, now)
        _detect_stale_block(ticket, graph, debt, result, now)
    return result
