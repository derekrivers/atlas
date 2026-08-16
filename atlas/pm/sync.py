"""PM-Engine ticket synchronisation (ATLAS-42, extended by
ATLAS-45/-118/-119/-120/-148).

One idempotent sync pass (:func:`sync_tick`) wiring the ATLAS-41 boundary
primitives into steps 1-5 of the ``pm-engine-and-linear-sync.md`` "Sync loop":
pull status Linear -> Atlas, push owned definitions Atlas -> Linear, promote
at most one lease-guarded, revalidated dependency-ready ticket to
``Ready for Agent`` (:mod:`atlas.pm.admission_sync`),
scan tagged comments into inbox proposal stubs, and *nothing else crossing*.
Step 1's read is BATCHED (ATLAS-148): one paginated, project-scoped
:meth:`LinearClient.fetch_project_issues` call replaces the per-ticket
``fetch_issue`` loop, and issues join to tickets by ``external_linear_id``
ONLY — never title, never identifier — so one tick costs ``ceil(n / 250)``
pull requests instead of one per ticket. Step 4 (ATLAS-45, scoped by
ATLAS-148) is the follow-up scan: per synced ticket in an
``ACTIVE_COMMENT_SCAN_STATUSES`` state (the documented active-state set —
parked ``needs_human_decision`` and terminal statuses are not scanned), read
its Linear comments (the read-only :meth:`LinearClient.fetch_comments`) and
write one inbox stub per comment tagged ``atlas:proposed-follow-up`` to
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
``Ticket.last_observed_linear_state_id``). Step 5 has three detection clauses plus
the DRAFT-lesson extraction remainder. Dwell-breach
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
operator owns the move. ATLAS-99 adds the extraction remainder for the
review-cycle and dwell-breach clauses: newly appended `DebtItem`s trigger the
lesson extractor, which calls an LLM over a bounded evidence bundle and persists
DRAFT `Lesson` rows for operator review only.
The remaining work — the recurring scheduler (ATLAS-50) and the follow-up
CONSUMER (ATLAS-122: plan reads the committed inbox, apply moves processed
stubs) — is deliberately NOT here.

Directionality is structural, not conventional. The pull reads only the
issue's state id (:func:`status_from_issue`) and writes only ``status``; the
push sends only :func:`definition_payload` (title + description — priority and
labels are owned-but-deferred; since ATLAS-164 the description is widened with
the ticket's rendered context pack at push time, still the same single owned
key) and the client rejects any unowned key. So a
Linear-side edit of an Atlas-owned field is never pulled, and an Atlas status
change is mechanically incapable of being pushed *through the definition path*.
Sanctioned workflow writes go through :meth:`LinearClient.set_state` only:
create-time state assertion for newly minted issues, readiness promotion,
verified completion, and review-cycling's route to Needs Human. None of those
adds ``stateId`` to the owned definition payload, and the update path never
writes workflow state.

Idempotency rests on the sync cursor ``Ticket.linear_synced_at``: a definition
is re-pushed only while ``updated_at > linear_synced_at`` (or it has never
synced), and the cursor is stamped to the pushed ``updated_at`` — so a second
tick over an unchanged, successfully embedded Linear description produces zero
writes (no redundant status set, no re-push). A missed or interrupted tick
costs latency only (D5: push to Linear first, then stamp; a failed push is
retried next tick). An enumerated context-pack render failure is the one
intentional unstamped success: it pushes definition-only, leaves
``linear_synced_at`` behind, and retries until the full embed succeeds.

Layering: this is ``atlas.pm``, above ``atlas.storage``/``atlas.linear``/
``atlas.core`` in the import spine — it needs ``TicketRepo``, so it cannot live
in ``atlas.linear`` (below storage). It performs no I/O of its own beyond the
injected ``LinearClient``; tests drive it with the in-memory fake, so CI runs
with no network and no secrets.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from uuid import UUID, uuid4

import networkx as nx

from atlas.context.lesson_retrieval import retrieve_lessons
from atlas.context.pack import ContextBudgetExceededError, build_context_pack
from atlas.core.anchors import IngestionError, SourceDocument
from atlas.core.enums import ActorType
from atlas.core.models.adr import ADRStatus, ArchitectureDecisionRecord
from atlas.core.models.debt_item import AnomalyType, DebtItem
from atlas.core.models.lesson import Lesson
from atlas.core.models.pm_sync_receipt import PmSyncReceipt, PmSyncReceiptResult
from atlas.core.models.ticket import (
    Ticket,
    TicketStatus,
    TicketTransitionOwner,
    ci_pending_transition_owner,
)
from atlas.dependencies.blockers import blocked
from atlas.dependencies.graph import build_dependency_graph
from atlas.dependencies.validation import TERMINAL_STATUSES
from atlas.learning import (
    ExtractionTrigger,
    LessonModelClient,
    extract_lesson_for_ticket,
)
from atlas.linear.client import (
    LinearAPIError,
    LinearClient,
    LinearIssue,
    LinearRateLimitError,
)
from atlas.linear.ownership import (
    PACK_HEADER_PREFIX,
    LinearStatusMap,
    compose_embedded_description,
    definition_payload,
    status_from_issue,
)
from atlas.pm.admission_sync import (
    AdmissionSyncHooks,
    AdmissionSyncOutcome,
    AdmissionSyncResult,
    admit_one_ready,
)
from atlas.pm.agent_runs import reconstruct_agent_runs
from atlas.pm.completion import complete_verified
from atlas.pm.protected_lanes import (
    ProtectedLaneRegistryLoadResult,
    load_packaged_protected_lane_registry,
)
from atlas.storage.db import Database
from atlas.storage.repositories import (
    ADRRepo,
    ContextPackRepo,
    DebtItemRepo,
    LessonRepo,
    PmSyncReceiptRepo,
    ProductRepo,
    TicketRepo,
)

# Attribution for system-observed anomalies (data-model §6.1): the PM Engine
# writes DebtItems from deterministic observation, so created_by_type is
# ``system`` and created_by_id names the writer (matches the §6.1 example).
# One definition for the system-actor id, mirroring planning's CREATED_BY.
CREATED_BY = "pm-engine"
RECEIPT_EXCEPTION_TYPE_MAX_LEN = 200


class SyncReceiptPersistenceError(RuntimeError):
    """Persisting the PM sync receipt failed at the local completion boundary."""


class MalformedLinearPullError(RuntimeError):
    """The batched Linear project pull returned an unusable board shape."""


def _utcnow() -> datetime:
    """Sample a timezone-aware completion instant for direct tick callers."""

    return datetime.now(UTC)


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

# The statuses whose tickets step 4 comment-scans (ATLAS-148; the documented
# active-state set in pm-engine-and-linear-sync.md "Sync loop"). These are the
# states in which an agent is or was just working the ticket, so a tagged
# follow-up comment can appear; everything else — pre-dispatch work no agent
# has touched (backlog/planned/blocked), a parked ``needs_human_decision``
# awaiting the operator, and terminal statuses — is excluded, which is what
# makes the tick's comment-scan budget O(active) instead of O(board).
ACTIVE_COMMENT_SCAN_STATUSES: frozenset[TicketStatus] = frozenset(
    {
        TicketStatus.READY_FOR_AGENT,
        TicketStatus.IN_PROGRESS,
        TicketStatus.PR_OPEN,
        TicketStatus.REVIEW_REQUIRED,
        TicketStatus.CHANGES_REQUESTED,
    }
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

# The enumerated pack-render failure classes the embed path degrades on
# (ATLAS-164 D-2, enumerated-exceptions-only): the fail-closed token budget
# (``ContextBudgetExceededError``), the ingestion/anchor family
# (``IngestionError`` covers ``UnknownDocumentError``/``UnknownAnchorError``
# and the documents loader's ``DirtyInputError``), and the retriever
# precondition ``ValueError``s (``select_adrs``/``select_related_tickets`` on
# a missing graph node). Each degrades that ticket's push to definition-only
# with one typed ``PACK_RENDER_FAILURE`` DebtItem; anything NOT listed here
# still propagates — a bug crashes the tick loudly (the scheduler's
# create-on-crash absorbs it), it is never eaten by a blanket handler.
PACK_RENDER_FAILURE_TYPES: tuple[type[Exception], ...] = (
    ContextBudgetExceededError,
    IngestionError,
    ValueError,
)


@dataclass
class _PackInputs:
    """The already-loaded shared inputs ``build_context_pack`` takes, loaded once
    per tick by :class:`_PackInputLoader` (mirrors the CLI's ``_ContextInputs``
    minus the ticket and ticket-specific lesson retrieval)."""

    graph: nx.DiGraph[str]
    documents: list[SourceDocument]
    accepted_adrs: list[ArchitectureDecisionRecord]


class _PackInputLoader:
    """The lazily-invoked pack-inputs seam (ATLAS-164).

    The import spine places ``atlas.pm`` BELOW ``atlas.planning``, so the tick
    cannot import the corpus collectors — the ``documents`` provider is a
    callable built by the CLI (which may import ``atlas.planning``) and
    injected through :class:`~atlas.pm.scheduler.TickConfig`. Everything else
    shared (graph projection, accepted ADRs) loads inside ``atlas.pm``, all
    layer-legal and DB-only. Lessons are retrieved per ticket so ADR-0009's
    ACTIVE-only filter is enforced at query time against that ticket's facets.

    Lazy and once-per-tick: nothing loads until the FIRST push that will
    actually embed calls :meth:`load`, so a no-op tick loads nothing and stays
    byte-identical in requests (the ATLAS-148 bound); every input is local —
    zero Linear calls on any path. A provider failure of an enumerated
    :data:`PACK_RENDER_FAILURE_TYPES` class (e.g. a dirty corpus's
    ``DirtyInputError``) is remembered and re-raised per embed attempt —
    logged once here, and every embedding push this tick degrades to
    definition-only (D-2, tick-level); a non-enumerated provider failure
    propagates and crashes the tick loudly."""

    def __init__(
        self, db: Database, documents: Callable[[], list[SourceDocument]]
    ) -> None:
        self._db = db
        self._documents = documents
        self._inputs: _PackInputs | None = None
        self._error: Exception | None = None

    def load(self) -> _PackInputs:
        if self._error is not None:
            raise self._error
        if self._inputs is None:
            try:
                documents = self._documents()
            except PACK_RENDER_FAILURE_TYPES as error:
                self._error = error
                logger.warning(
                    "linear-sync: pack-input documents failed to load (%s); "
                    "every embed this tick degrades to definition-only: %s",
                    type(error).__name__,
                    error,
                )
                raise
            self._inputs = _PackInputs(
                graph=build_dependency_graph(self._db),
                documents=documents,
                accepted_adrs=[
                    adr
                    for adr in ADRRepo(self._db).list()
                    if adr.status == ADRStatus.ACCEPTED
                ],
            )
        return self._inputs

    def lessons_for(self, ticket: Ticket) -> list[Lesson]:
        return retrieve_lessons(ticket, self._db)


class SyncDecisionClassification(StrEnum):
    """CLI presentation class for per-ticket sync decisions."""

    ROUTINE = "routine"
    NOTABLE = "notable"


@dataclass(frozen=True)
class SyncDecision:
    """One per-ticket decision for CLI presentation; not a persisted metric."""

    phase: str
    ticket_key: str
    outcome: str
    reason: str
    classification: SyncDecisionClassification = SyncDecisionClassification.NOTABLE


@dataclass
class SyncResult:
    """Per-tick counters — the structured observability D2 asks for. Pure
    totals; no I/O. ``unmapped`` counts every observed unmapped state this
    tick; ``anomalies_logged`` counts state observations that were new
    out-of-ownership transitions and therefore appended a DebtItem. Those are
    transitions into an unmapped state plus mapped CI-pending lifecycle edges
    the generic pull is not authorised to mirror. A persisting observation is
    deduplicated by ``last_observed_linear_state_id`` and does not increment
    ``anomalies_logged`` again.
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
    ticket. Create-state assertion failures count as anomalies too: the issue is
    already created and joined, so the tick logs the failed assertion and
    continues without adding a DebtItem enum member. ``completed`` (ATLAS-131)
    counts every ``set_state(Done)`` route this
    tick -- the verified-completion step moves a ``review_required`` ticket whose
    persisted verdict is PASSED to ``Done``. Like ``promoted`` and
    ``routed_to_human`` it counts route attempts: the route fires idempotently each
    tick until the pull reconciles the ticket out of ``review_required``, so a
    not-yet-reconciled ticket increments it every tick. The three pack-embedding
    counters (ATLAS-164) split each definition push's context-pack outcome:
    ``packs_embedded`` counts pushes whose description carried a rendered pack,
    ``packs_truncated`` the subset whose pack tail hit the D-1 pin (a truncated
    pack still counts as embedded), and ``pack_render_failures`` the embed
    attempts that hit an enumerated render failure (D-2) — each of those also
    appended one ``PACK_RENDER_FAILURE`` DebtItem. Definition-push failures
    degrade to definition-only and leave the cursor unstamped; repair-mode
    failures write nothing. ``agent_runs_reconstructed`` and
    ``agent_runs_updated`` count the local AgentRun reconstruction step
    (ATLAS-166): rows inserted for newly observed dispatch cycles, and existing
    rows filled in when later handoff/evidence becomes observable.
    ``draft_lessons_filed`` (ATLAS-99) counts DRAFT lesson rows extracted from
    completion/failure transitions and newly logged review-cycle or dwell-breach
    failure-analysis events. ``packs_repaired`` (ATLAS-169) counts
    operator-invoked repair-mode updates that re-embedded a full context pack
    into an already-stamped Linear description that lacked the pack header.
    ``push_decisions`` and ``repair_pack_decisions`` are per-ticket presentation
    details for one-shot CLI output; they do not change the counter meanings."""

    status_pulled: int = 0
    status_unchanged: int = 0
    missing_issues: int = 0
    unmapped: int = 0
    anomalies_logged: int = 0
    pushed_created: int = 0
    pushed_updated: int = 0
    push_skipped: int = 0
    packs_embedded: int = 0
    packs_truncated: int = 0
    pack_render_failures: int = 0
    packs_repaired: int = 0
    agent_runs_reconstructed: int = 0
    agent_runs_updated: int = 0
    promoted: int = 0
    admitted: int = 0
    held: int = 0
    over_capacity: int = 0
    stale: int = 0
    indeterminate: int = 0
    completed: int = 0
    follow_ups_stubbed: int = 0
    dwell_breaches: int = 0
    routed_to_human: int = 0
    review_cycles_logged: int = 0
    stale_blocks: int = 0
    draft_lessons_filed: int = 0
    push_decisions: list[SyncDecision] = field(default_factory=list)
    repair_pack_decisions: list[SyncDecision] = field(default_factory=list)
    admission_decisions: list[AdmissionSyncResult] = field(default_factory=list)

    def safe_admission_summaries(self, *, verbose: bool) -> tuple[str, ...]:
        """Return bounded admission details suitable for operator output."""

        return tuple(
            detail.safe_summary
            for detail in self.admission_decisions
            if verbose or not detail.routine
        )


SYNC_RESULT_COUNTER_NAMES: tuple[str, ...] = (
    "status_pulled",
    "status_unchanged",
    "missing_issues",
    "unmapped",
    "anomalies_logged",
    "pushed_created",
    "pushed_updated",
    "push_skipped",
    "packs_embedded",
    "packs_truncated",
    "pack_render_failures",
    "packs_repaired",
    "agent_runs_reconstructed",
    "agent_runs_updated",
    "promoted",
    "admitted",
    "held",
    "over_capacity",
    "stale",
    "indeterminate",
    "completed",
    "follow_ups_stubbed",
    "dwell_breaches",
    "routed_to_human",
    "review_cycles_logged",
    "stale_blocks",
    "draft_lessons_filed",
)


def sync_result_is_empty(result: SyncResult) -> bool:
    counters = [
        result.status_pulled,
        result.status_unchanged,
        result.missing_issues,
        result.unmapped,
        result.anomalies_logged,
        result.pushed_created,
        result.pushed_updated,
        result.push_skipped,
        result.packs_embedded,
        result.packs_truncated,
        result.pack_render_failures,
        result.packs_repaired,
        result.agent_runs_reconstructed,
        result.agent_runs_updated,
        result.promoted,
        result.admitted,
        result.held,
        result.over_capacity,
        result.stale,
        result.indeterminate,
        result.completed,
        result.follow_ups_stubbed,
        result.dwell_breaches,
        result.routed_to_human,
        result.review_cycles_logged,
        result.stale_blocks,
        result.draft_lessons_filed,
    ]
    return all(counter == 0 for counter in counters)


@dataclass
class _ReceiptContext:
    """Mutable receipt inputs collected by the sync body as it runs."""

    result: SyncResult = field(default_factory=SyncResult)
    pull_board: list[Ticket] = field(default_factory=list)
    fetched_issues: list[LinearIssue] = field(default_factory=list)


def _canonical_hash(payload: object) -> str:
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _status_map_fingerprint(status_map: LinearStatusMap) -> str:
    return _canonical_hash(status_map.snapshot())


def _fetched_board_fingerprint(issues: list[LinearIssue]) -> str:
    """Fingerprint the bounded board observation fields used by sync.

    Titles, descriptions and comment bodies are deliberately excluded. The
    receipt needs enough provenance to prove which board state was observed
    without storing raw Linear payload material.
    """

    payload = [
        {
            "id": issue.id,
            "identifier": issue.identifier,
            "state_id": issue.state_id,
            "state_name": issue.state_name,
            "state_type": issue.state_type,
        }
        for issue in sorted(issues, key=lambda item: item.id)
    ]
    return _canonical_hash(payload)


def _validate_fetched_board(issues: list[LinearIssue]) -> None:
    """Reject malformed board pulls before they can look successful."""

    if not bool(getattr(issues, "complete", True)):
        raise MalformedLinearPullError(
            "Linear project pull did not reach a complete pagination boundary"
        )
    if tuple(getattr(issues, "pagination_gaps", ())):
        raise MalformedLinearPullError(
            "Linear project pull returned a discontinuous pagination chain"
        )
    seen: set[str] = set()
    for index, issue in enumerate(issues):
        if not isinstance(issue.id, str) or not issue.id.strip():
            raise MalformedLinearPullError(
                f"Linear project pull returned issue[{index}] without a stable id"
            )
        if issue.id in seen:
            raise MalformedLinearPullError(
                f"Linear project pull returned duplicate issue id {issue.id!r}"
            )
        seen.add(issue.id)


def _sync_result_counters(result: SyncResult) -> dict[str, int]:
    return {name: int(getattr(result, name)) for name in SYNC_RESULT_COUNTER_NAMES}


def _apply_admission_result(result: SyncResult, admission: AdmissionSyncResult) -> None:
    """Project one bounded admission outcome into counters and safe detail."""

    result.admission_decisions.append(admission)
    if admission.outcome is AdmissionSyncOutcome.ADMITTED:
        result.admitted += 1
        # Compatibility for existing PM reports and receipts while operator
        # output moves to the precise Phase-15 term.
        result.promoted += 1
    elif admission.outcome is AdmissionSyncOutcome.HELD:
        result.held += 1
    elif admission.outcome is AdmissionSyncOutcome.OVER_CAPACITY:
        result.over_capacity += 1
    elif admission.outcome is AdmissionSyncOutcome.STALE:
        result.stale += 1
    else:
        result.indeterminate += 1


def _sanitized_error_summary(error: BaseException) -> str:
    """Return bounded diagnostic metadata without exception message content.

    Linear transport exceptions may embed response bodies, GraphQL payloads or
    credentials in ``str(error)``. Receipts therefore persist only a sanitized
    exception type plus one code selected from this closed local allow-list.
    """

    if isinstance(error, MalformedLinearPullError):
        code = "malformed_linear_pull"
    elif isinstance(error, (KeyboardInterrupt, SystemExit)):
        code = "tick_cancelled"
    elif isinstance(error, LinearRateLimitError):
        code = "linear_rate_limited"
    elif isinstance(error, LinearAPIError):
        code = "linear_api_failure"
    else:
        code = "unexpected_failure"

    cls = type(error)
    qualified_type = f"{cls.__module__}.{cls.__qualname__}"
    safe_type = re.sub(r"[^A-Za-z0-9_.]+", "_", qualified_type)
    return f"{safe_type[:RECEIPT_EXCEPTION_TYPE_MAX_LEN]}; code={code}"


def _product_identity(
    db: Database, pull_board: list[Ticket]
) -> tuple[UUID | None, str | None]:
    product_repo = ProductRepo(db)
    product_ids = {ticket.product_id for ticket in pull_board}
    if len(product_ids) == 1:
        product_id = next(iter(product_ids))
        product = product_repo.get(product_id)
        if product is not None:
            return product.id, product.key

    products = product_repo.list()
    if len(products) == 1:
        product = products[0]
        return product.id, product.key
    return None, None


def _classify_successful_receipt(
    result: SyncResult,
) -> PmSyncReceiptResult:
    if (
        result.pack_render_failures > 0
        or result.unmapped > 0
        or result.missing_issues > 0
        or result.anomalies_logged > 0
        or result.stale > 0
        or result.indeterminate > 0
    ):
        return PmSyncReceiptResult.PARTIAL
    if result.pushed_created > 0 or result.pushed_updated > 0:
        return PmSyncReceiptResult.SUCCESS_DEFINITION_CHANGED
    if any(
        counter > 0
        for counter in (
            result.status_pulled,
            result.promoted,
            result.admitted,
            result.completed,
            result.routed_to_human,
            result.agent_runs_reconstructed,
            result.agent_runs_updated,
            result.follow_ups_stubbed,
            result.dwell_breaches,
            result.review_cycles_logged,
            result.stale_blocks,
            result.draft_lessons_filed,
        )
    ):
        return PmSyncReceiptResult.SUCCESS_STATUS_ONLY
    return PmSyncReceiptResult.SUCCESS_ZERO_ACTION


def _record_sync_receipt(
    *,
    db: Database,
    status_map: LinearStatusMap,
    project_id: str,
    started_at: datetime,
    finished_at: datetime,
    receipt_result: PmSyncReceiptResult,
    context: _ReceiptContext,
    error: BaseException | None = None,
) -> None:
    product_id, product_key = _product_identity(db, context.pull_board)
    try:
        PmSyncReceiptRepo(db).record(
            PmSyncReceipt(
                id=uuid4(),
                product_id=product_id,
                product_key=product_key,
                linear_project_id=project_id,
                started_at=started_at,
                finished_at=finished_at,
                status_map_fingerprint=_status_map_fingerprint(status_map),
                fetched_board_fingerprint=_fetched_board_fingerprint(
                    context.fetched_issues
                ),
                fetched_board_issue_count=len(context.fetched_issues),
                result=receipt_result,
                counters=_sync_result_counters(context.result),
                error_summary=(
                    None if error is None else _sanitized_error_summary(error)
                ),
                created_by_type=ActorType.SYSTEM,
                created_by_id=CREATED_BY,
            )
        )
    except Exception as receipt_error:
        raise SyncReceiptPersistenceError(
            "PM sync receipt persistence failed; tick success cannot be reported"
        ) from receipt_error


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


def _ci_pending_ownership_item(
    ticket: Ticket,
    issue: LinearIssue,
    mapped: TicketStatus,
    owner: TicketTransitionOwner | None,
    now: datetime,
) -> DebtItem:
    """Build the anomaly for a mapped but unauthorised CI-pending edge.

    A generic Linear observation proves neither an Atlas-owned CI result nor a
    valid arbitrary entry.  The sole edge it may mirror is the agent-owned
    ``pr_open -> ci_pending`` handoff; every other edge touching CI-pending is
    held unchanged until a trusted owner-specific seam performs it.
    """

    ownership = (
        "reserved to Atlas's trusted CI reconciler"
        if owner is TicketTransitionOwner.ATLAS
        else "not present in the CI-pending ownership table"
    )
    return DebtItem(
        id=uuid4(),
        product_id=ticket.product_id,
        ticket_id=ticket.id,
        anomaly_type=AnomalyType.OUT_OF_OWNERSHIP_TRANSITION,
        summary=(
            f"Linear state {issue.state_id!r} maps to {mapped.value!r}, but "
            f"{ticket.status.value!r} -> {mapped.value!r} is {ownership}; "
            "generic pull left status unchanged"
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
) -> DebtItem | None:
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
        return None  # this status carries no dwell horizon; never breaches
    if ticket.status_entered_at is None:
        return None  # unknown entry time: skip, never a false breach
    if now - ticket.status_entered_at < horizon:
        return None  # still inside the horizon
    if debt.logged_since(ticket.id, AnomalyType.DWELL_BREACH, ticket.status_entered_at):
        return None  # this episode already logged one breach; not one per tick
    item = debt.record(_dwell_breach_item(ticket, horizon, now))
    result.dwell_breaches += 1
    logger.info(
        "linear-sync: dwell breach for %s in %s (entered %s, horizon %s); "
        "DebtItem logged, status unchanged",
        ticket.key,
        ticket.status.value,
        ticket.status_entered_at.isoformat(),
        horizon,
    )
    return item


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
) -> DebtItem | None:
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
        return None  # not cycling (e.g. already routed and reconciled into Needs Human)
    if ticket.review_cycle_count <= REVIEW_CYCLE_THRESHOLD:
        return None  # at or under threshold; routing at 3 would be the wrong answer
    if ticket.external_linear_id is None:
        return None  # no Linear join: nothing to route (mirrors promote_ready)
    if ticket.status_entered_at is None:
        return None  # no episode boundary for dedup; unreachable when count > threshold
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
        return None  # this episode already logged its note; not one per tick
    item = debt.record(_review_cycle_item(ticket, now))
    result.review_cycles_logged += 1
    logger.info(
        "linear-sync: review-cycle DebtItem logged for %s (%d round trips)",
        ticket.key,
        ticket.review_cycle_count,
    )
    return item


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

    Skips, in order: a ticket with no Linear join (nothing to read) and a ticket
    outside ``ACTIVE_COMMENT_SCAN_STATUSES`` (ATLAS-148) — only a ticket an agent
    is or was just working can grow a tagged follow-up, so pre-dispatch work, a
    parked ``needs_human_decision``, and terminal statuses are not scanned (this
    is what keeps the tick's comment-scan cost O(active), not O(board)). Then per
    comment: skip an untagged body and a comment id already stubbed (the dedup;
    the wrong answer re-stubs it every tick). A newly-seen tagged comment is
    written at the next free index for the ticket key, and
    ``seen_ids``/``max_index`` are advanced so a second tagged comment this same
    tick lands at the next index without a re-read."""

    if ticket.external_linear_id is None:
        return  # not joined to a Linear issue; no comments to scan
    if ticket.status not in ACTIVE_COMMENT_SCAN_STATUSES:
        return  # not in an agent-active state; no follow-up can appear (ATLAS-148)
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


def _record_lesson_citation_feedback(ticket: Ticket, db: Database) -> int:
    """Record successful reuse for lessons in ``ticket``'s latest stored pack.

    The pack is optional because pack persistence is not part of every render
    path. When no stored pack is known, completion still proceeds and there is no
    citation feedback to apply.
    """
    pack = ContextPackRepo(db).latest_for_ticket(ticket.id)
    if pack is None or not pack.historical_lessons:
        return 0
    cited_lessons = LessonRepo(db).record_ticket_citation(
        lesson_ids=pack.historical_lessons,
        ticket_id=ticket.id,
    )
    if cited_lessons:
        logger.info(
            "linear-sync: recorded citation feedback for %s on %d lesson(s)",
            ticket.key,
            len(cited_lessons),
        )
    return len(cited_lessons)


def _pull(
    ticket: Ticket,
    tickets: TicketRepo,
    db: Database,
    debt: DebtItemRepo,
    issues_by_id: Mapping[str, LinearIssue],
    status_map: LinearStatusMap,
    result: SyncResult,
    now: datetime,
    lesson_client: LessonModelClient | None,
) -> Ticket:
    """Step 1 (Linear -> Atlas): mirror an owned mapped status onto a
    non-terminal ticket, and log an out-of-ownership anomaly for unmapped
    states or CI-pending lifecycle edges this generic observation cannot own.
    Returns the possibly-updated ticket so the push step sees the post-pull
    status (a status pulled into a frozen state freezes the push).

    ``issues_by_id`` is the tick's ONE batched, project-scoped pull
    (:meth:`LinearClient.fetch_project_issues`, ATLAS-148), keyed by issue id —
    this function makes no Linear call of its own. The join is by
    ``external_linear_id`` ONLY (never title or identifier): a ticket whose id
    is absent from the map — issue deleted, or moved out of the configured
    project and so out of the poll scope — takes the issue-missing path and is
    left unchanged."""

    if ticket.external_linear_id is None:
        return ticket  # not yet joined to a Linear issue; nothing to pull
    if ticket.status.value in TERMINAL_STATUSES:
        return ticket  # terminal work is closed; do not poll it
    issue = issues_by_id.get(ticket.external_linear_id)
    if issue is None:
        # The join target is gone (or left the project's poll scope). That is a
        # distinct anomaly (not an out-of-ownership *transition*) and out of
        # ATLAS-118's narrowed scope; here we only avoid crashing and leave
        # status unchanged.
        result.missing_issues += 1
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
    owner = ci_pending_transition_owner(ticket.status, mapped)
    if TicketStatus.CI_PENDING in {ticket.status, mapped} and (
        owner is not TicketTransitionOwner.AGENT
    ):
        # The generic Linear pull may mirror the agent-owned handoff only.
        # Atlas-owned exits require the trusted CI reconciliation seam delivered
        # by ATLAS-256; an observed mapped board state cannot impersonate it.
        # Invalid entries/exits likewise fail closed.  Observation-transition
        # dedup matches the existing unmapped-state anomaly contract.
        if transitioned:
            debt.record(_ci_pending_ownership_item(ticket, issue, mapped, owner, now))
            result.anomalies_logged += 1
            logger.info(
                "linear-sync: out-of-ownership CI-pending transition %s -> %s "
                "for %s; DebtItem logged, status unchanged",
                ticket.status.value,
                mapped.value,
                ticket.key,
            )
        else:
            logger.info(
                "linear-sync: out-of-ownership CI-pending transition %s -> %s "
                "for %s persists; status unchanged, no new DebtItem",
                ticket.status.value,
                mapped.value,
                ticket.key,
            )
        return ticket
    updated = tickets.apply_linear_status(
        ticket.key, mapped, now=now, created_by_id=CREATED_BY
    )
    result.status_pulled += 1
    logger.info(
        "linear-sync: pulled %s -> %s for %s",
        ticket.status.value,
        mapped.value,
        ticket.key,
    )
    if mapped is TicketStatus.DONE:
        _record_lesson_citation_feedback(updated, db)
        lesson = extract_lesson_for_ticket(
            updated,
            db=db,
            client=lesson_client,
            now=now,
            trigger=ExtractionTrigger.DONE,
        )
        if lesson is not None:
            result.draft_lessons_filed += 1
    elif mapped is TicketStatus.REJECTED:
        lesson = extract_lesson_for_ticket(
            updated,
            db=db,
            client=lesson_client,
            now=now,
            trigger=ExtractionTrigger.REJECTED,
        )
        if lesson is not None:
            result.draft_lessons_filed += 1
    return updated


def _extract_pm_failure_lesson(
    *,
    ticket: Ticket,
    item: DebtItem,
    db: Database,
    lesson_client: LessonModelClient | None,
    result: SyncResult,
    now: datetime,
) -> None:
    """Run lesson extraction for one newly logged PM failure-analysis event."""

    lesson = extract_lesson_for_ticket(
        ticket,
        db=db,
        client=lesson_client,
        now=now,
        trigger=ExtractionTrigger.PM_FAILURE_ANALYSIS,
        failure_event=item,
        force=True,
    )
    if lesson is not None:
        result.draft_lessons_filed += 1


def _pack_render_failure_item(
    ticket: Ticket,
    error: Exception,
    now: datetime,
    *,
    summary_posture: str,
) -> DebtItem:
    """Build the ``PACK_RENDER_FAILURE`` DebtItem for a definition push whose
    context pack failed to render (ATLAS-164 D-2).

    System-attributed and append-only (data-model §6.1), exactly like the other
    observation items: ``product_id``/``ticket_id`` come from the pushed ticket,
    the type is ``PACK_RENDER_FAILURE``, and ``observed_at``/``created_at`` are
    the injected tick clock. The summary names the ticket key, the failure
    class, and the degradation/repair posture; the specific error text lives in
    the accompanying ``logger.warning``."""

    return DebtItem(
        id=uuid4(),
        product_id=ticket.product_id,
        ticket_id=ticket.id,
        anomaly_type=AnomalyType.PACK_RENDER_FAILURE,
        summary=(
            f"context pack render failed for {ticket.key} "
            f"({type(error).__name__}); {summary_posture}"
        ),
        observed_at=now,
        created_by_type=ActorType.SYSTEM,
        created_by_id=CREATED_BY,
        created_at=now,
    )


def _render_embedded_description(
    ticket: Ticket,
    pack_inputs: _PackInputLoader,
    debt: DebtItemRepo,
    result: SyncResult,
    now: datetime,
    *,
    failure_posture: str = "definition_push",
) -> str | None:
    """Render ``ticket``'s context pack and compose the embedded description
    (ATLAS-164), or ``None`` when the push must degrade to definition-only.

    The D-2 seam: an enumerated :data:`PACK_RENDER_FAILURE_TYPES` failure —
    from the once-per-tick input load or from this ticket's render — appends
    one ``PACK_RENDER_FAILURE`` DebtItem, warns with the key and error, bumps
    the counter, and returns ``None``. In the definition-push path the caller
    pushes today's exact definition-only payload and leaves the cursor
    unstamped; in repair mode the caller writes nothing. A non-enumerated
    exception propagates: a bug crashes the tick loudly, never eaten. The D-1
    truncation lives in the pure composer; only the counters are read here."""

    try:
        inputs = pack_inputs.load()
        pack = build_context_pack(
            ticket,
            graph=inputs.graph,
            documents=inputs.documents,
            accepted_adrs=inputs.accepted_adrs,
            lessons=pack_inputs.lessons_for(ticket),
        )
    except PACK_RENDER_FAILURE_TYPES as error:
        definition_push = failure_posture == "definition_push"
        posture = (
            "pushed definition-only; cursor unstamped"
            if definition_push
            else "repair skipped; no Linear write"
        )
        debt.record(
            _pack_render_failure_item(
                ticket,
                error,
                now,
                summary_posture=posture,
            )
        )
        result.pack_render_failures += 1
        if definition_push:
            logger.warning(
                "linear-sync: context pack render failed for %s (%s: %s); "
                "pushing definition-only; cursor unstamped",
                ticket.key,
                type(error).__name__,
                error,
            )
        else:
            logger.warning(
                "linear-sync: context pack repair render failed for %s (%s: %s); "
                "no Linear write",
                ticket.key,
                type(error).__name__,
                error,
            )
        return None
    composed = compose_embedded_description(ticket, pack)
    result.packs_embedded += 1
    if composed.pack_truncated:
        result.packs_truncated += 1
        logger.info(
            "linear-sync: embedded pack truncated at the pinned limit for %s "
            "(full pack: atlas context render %s)",
            ticket.key,
            ticket.key,
        )
    return composed.description


def _push(
    ticket: Ticket,
    tickets: TicketRepo,
    client: LinearClient,
    team_id: str,
    project_id: str,
    status_map: LinearStatusMap,
    pack_inputs: _PackInputLoader,
    debt: DebtItemRepo,
    result: SyncResult,
    now: datetime,
) -> str | None:
    """Step 2 (Atlas -> Linear): push the owned definition for a pushable
    ticket whose cursor says it changed. Push first, then stamp (D5).

    Every definition push EMBEDS the ticket's rendered context pack beneath the
    definition fields (ATLAS-164, gate assumption A-1: all ``PUSHABLE_STATUSES``,
    create and update paths alike) — ``description`` stays the single owned key,
    widened in content per the ATLAS-143 precedent. A render failure degrades
    THIS ticket's push to today's exact definition-only payload
    (:func:`_render_embedded_description`, D-2) and leaves the definition cursor
    unstamped: the next tick retries the full embed once the render condition
    clears. On a first-sync degraded create, only the Linear join key is recorded
    so the retry updates the same issue rather than creating a duplicate.

    ``project_id`` is the creation scope threaded alongside ``team_id`` (ATLAS-135):
    a first-sync create places the issue in the configured Linear project so it is
    visible to Symphony's project-scoped poll. It is used ONLY on the create path
    (the project is set once, at creation); the ``update_issue`` re-push below never
    carries it -- a project move is not a definition update.

    On first sync, creation also asserts the Linear workflow state mapped to the
    ticket's current Atlas status. That assertion is create-only: updates never
    write state, preserving Linear/operator ownership after the issue exists."""

    if ticket.status not in PUSHABLE_STATUSES:
        result.push_skipped += 1
        result.push_decisions.append(
            SyncDecision(
                phase="push",
                ticket_key=ticket.key,
                outcome="skipped",
                reason=f"status not pushable ({ticket.status.value})",
                classification=SyncDecisionClassification.ROUTINE,
            )
        )
        return None
    if not _definition_changed(ticket):
        result.push_skipped += 1
        result.push_decisions.append(
            SyncDecision(
                phase="push",
                ticket_key=ticket.key,
                outcome="skipped",
                reason="cursor already stamped",
                classification=SyncDecisionClassification.ROUTINE,
            )
        )
        return None
    definition = definition_payload(ticket)
    embedded = _render_embedded_description(ticket, pack_inputs, debt, result, now)
    degraded = embedded is None
    if embedded is not None:
        # Widen the owned description in place: definition fields first, then
        # the delimited pack (compose_embedded_description). The D-2 fallback
        # IS the unwidened definition_payload above.
        definition["description"] = embedded
    if ticket.external_linear_id is None:
        target_state_id = status_map.state_id_for(ticket.status)
        # First sync: create the issue and write back the join key. A full embed
        # stamps immediately after the confirmed create to shrink the
        # non-idempotent create-retry window (tracked in
        # docs/tech-debt/debt-register.md); a degraded create records only the
        # join key so the next tick updates this issue with the full embed.
        issue = client.create_issue(definition, team_id=team_id, project_id=project_id)
        if degraded:
            tickets.mark_external_linear_id(ticket.key, issue.id)
        else:
            tickets.mark_definition_pushed(
                ticket.key,
                synced_at=ticket.updated_at,
                external_linear_id=issue.id,
            )
        result.pushed_created += 1
        if degraded:
            logger.info(
                "linear-sync: created Linear issue %s for %s with definition-only "
                "description; cursor unstamped",
                issue.id,
                ticket.key,
            )
        else:
            logger.info(
                "linear-sync: created Linear issue %s for %s", issue.id, ticket.key
            )
        try:
            client.set_state(issue.id, target_state_id)
        except Exception as error:
            result.anomalies_logged += 1
            result.push_decisions.append(
                SyncDecision(
                    phase="push",
                    ticket_key=ticket.key,
                    outcome="state assertion failed",
                    reason=(
                        f"created Linear issue {issue.id} but could not assert "
                        f"{ticket.status.value} state {target_state_id!r}: {error}"
                    ),
                )
            )
            logger.exception(
                "linear-sync: created Linear issue %s for %s but failed to "
                "assert mapped %s state %s; join key retained",
                issue.id,
                ticket.key,
                ticket.status.value,
                target_state_id,
            )
        else:
            logger.info(
                "linear-sync: asserted mapped %s state %s for new Linear issue %s (%s)",
                ticket.status.value,
                target_state_id,
                issue.id,
                ticket.key,
            )
        return issue.id
    # update_issue is idempotent, so a stamp lost to a crash only re-pushes the
    # same definition next tick — safe to re-run. With an embedded pack the
    # retry is no longer byte-identical (a fresh render mints a new
    # pack_id/rendered_at — gate assumption A-5) but remains a plain overwrite.
    client.update_issue(ticket.external_linear_id, definition)
    result.pushed_updated += 1
    if degraded:
        logger.info(
            "linear-sync: pushed definition-only for %s; cursor unstamped",
            ticket.key,
        )
    else:
        tickets.mark_definition_pushed(ticket.key, synced_at=ticket.updated_at)
        logger.info("linear-sync: pushed definition for %s", ticket.key)
    return ticket.external_linear_id


def _has_context_pack_header(description: str | None) -> bool:
    """Does a Linear description already carry Atlas's embedded-pack header?"""

    return description is not None and PACK_HEADER_PREFIX in description


def _repair_pack_absent_descriptions(
    *,
    tickets: TicketRepo,
    client: LinearClient,
    issues_by_id: Mapping[str, LinearIssue],
    skipped_issue_ids: set[str],
    pack_inputs: _PackInputLoader,
    debt: DebtItemRepo,
    result: SyncResult,
    now: datetime,
) -> None:
    """Repair already-stamped pushable tickets whose Linear description lacks
    the Atlas context-pack header.

    Repair mode is operator-invoked only. It reads the descriptions already
    carried by the batched project pull, writes only matching pack-absent
    descriptions, and stamps normally after a successful full embed. Tickets
    whose definition cursor is already stale are left to the normal push retry
    path; tickets updated by the normal push earlier in this tick are skipped
    because the prefetched description map is necessarily stale for them.
    """

    for ticket in tickets.list():
        issue_id = ticket.external_linear_id
        if issue_id is None:
            result.repair_pack_decisions.append(
                SyncDecision(
                    phase="pack repair",
                    ticket_key=ticket.key,
                    outcome="skipped",
                    reason="no external id",
                )
            )
            continue
        if issue_id in skipped_issue_ids:
            result.repair_pack_decisions.append(
                SyncDecision(
                    phase="pack repair",
                    ticket_key=ticket.key,
                    outcome="skipped",
                    reason="updated earlier in this tick",
                )
            )
            continue
        if ticket.status not in PUSHABLE_STATUSES:
            result.repair_pack_decisions.append(
                SyncDecision(
                    phase="pack repair",
                    ticket_key=ticket.key,
                    outcome="skipped",
                    reason=f"status not pushable ({ticket.status.value})",
                )
            )
            continue
        if _definition_changed(ticket):
            result.repair_pack_decisions.append(
                SyncDecision(
                    phase="pack repair",
                    ticket_key=ticket.key,
                    outcome="skipped",
                    reason="definition cursor is stale",
                )
            )
            continue
        issue = issues_by_id.get(issue_id)
        if issue is None:
            result.repair_pack_decisions.append(
                SyncDecision(
                    phase="pack repair",
                    ticket_key=ticket.key,
                    outcome="skipped",
                    reason="external id not found in project pull",
                )
            )
            continue
        if _has_context_pack_header(issue.description):
            result.repair_pack_decisions.append(
                SyncDecision(
                    phase="pack repair",
                    ticket_key=ticket.key,
                    outcome="skipped",
                    reason="header already present",
                    classification=SyncDecisionClassification.ROUTINE,
                )
            )
            continue

        embedded = _render_embedded_description(
            ticket,
            pack_inputs,
            debt,
            result,
            now,
            failure_posture="repair",
        )
        if embedded is None:
            result.repair_pack_decisions.append(
                SyncDecision(
                    phase="pack repair",
                    ticket_key=ticket.key,
                    outcome="skipped",
                    reason="context pack render failed",
                )
            )
            continue

        definition = definition_payload(ticket)
        definition["description"] = embedded
        client.update_issue(issue_id, definition)
        tickets.mark_definition_pushed(ticket.key, synced_at=ticket.updated_at)
        result.pushed_updated += 1
        result.packs_repaired += 1
        result.repair_pack_decisions.append(
            SyncDecision(
                phase="pack repair",
                ticket_key=ticket.key,
                outcome="repaired",
                reason="embedded pack header restored",
            )
        )
        logger.info(
            "linear-sync: repaired missing embedded context pack for %s",
            ticket.key,
        )


def _sync_tick_impl(
    *,
    tickets: TicketRepo,
    db: Database,
    client: LinearClient,
    status_map: LinearStatusMap,
    team_id: str,
    project_id: str,
    inbox_dir: Path,
    documents: Callable[[], list[SourceDocument]],
    now: datetime,
    repair_packs: bool = False,
    lesson_client: LessonModelClient | None = None,
    admission_hooks: AdmissionSyncHooks | None = None,
    admission_registry_provider: Callable[
        [], ProtectedLaneRegistryLoadResult
    ] = load_packaged_protected_lane_registry,
    receipt_context: _ReceiptContext | None = None,
) -> SyncResult:
    """Run one idempotent sync pass over every ticket (steps 1-5).

    Step 1's read is BATCHED (ATLAS-148): one paginated, project-scoped
    :meth:`LinearClient.fetch_project_issues` call up front (``ceil(n / 250)``
    requests for an n-issue project; skipped entirely when no joined,
    non-terminal ticket exists, so an empty board still makes zero pull
    requests) replaces the per-ticket ``fetch_issue`` loop, and the returned
    issues are joined to tickets by ``external_linear_id`` ONLY. Per ticket:
    pull a mapped status (Linear -> Atlas) from the pre-fetched map — logging
    an out-of-ownership ``DebtItem``
    (ATLAS-118) on a transition into an unmapped state — then push the owned
    definition (Atlas -> Linear) if the cursor says it changed (a first-sync
    create scopes the new issue to ``team_id`` AND ``project_id``, so it lands
    in the Symphony-polled Linear project; ATLAS-135). Every definition push
    embeds the ticket's rendered context pack beneath the definition fields
    (ATLAS-164): the pack inputs load lazily and once per tick through the
    injected ``documents`` provider (the CLI builds it — the import spine
    keeps ``atlas.planning``'s collectors out of this layer) plus DB-local
    graph/ADR/lesson reads, so a tick with no push loads NOTHING and adds zero
    requests; an enumerated render failure degrades that push to
    definition-only with one typed ``PACK_RENDER_FAILURE`` DebtItem (D-2), and
    leaves the cursor unstamped so the next tick retries the full embed once the
    render condition clears. Pull precedes push so a
    status pulled into a frozen state freezes the same tick's push. Each Linear
    write is bracketed by its own DB commit (push-then-stamp), so an
    interrupted tick is safe to re-run. An issue the push just created is not
    in this tick's pre-fetched map, which changes nothing: its ticket had no
    join key when step 1 ran, so it was never pulled in the same tick anyway.

    When ``repair_packs`` is true (operator-invoked only), a one-shot repair
    sweep follows the normal push pass: pushable tickets that are already
    cursor-stamped, have an ``external_linear_id``, and whose already-fetched
    Linear description lacks the ``ATLAS CONTEXT PACK v1`` header are re-rendered
    and re-pushed with a full embed. Successful repairs stamp normally; a board
    whose descriptions already carry the header writes nothing. The plain tick
    does not run this branch and therefore keeps the ATLAS-148 request bound.

    Then step 3 (ATLAS-249 replacing ATLAS-43's call site): acquire the
    product-scoped admission lease, build the complete coherent snapshot,
    evaluate at most one candidate, re-pull/revalidate the policy and complete
    board, persist the pre-write fence, and write only the selected issue to
    ``Ready for Agent`` through :meth:`LinearClient.set_state`. A concurrent,
    stale, over-capacity or indeterminate pass writes no second candidate. The
    state write remains Linear-only, so the next tick's pull reconciles Atlas.

    Then step 3b (ATLAS-131): verified completion. Move every ``review_required``
    ticket whose persisted Verification Engine verdict is PASSED to ``Done`` via the
    sanctioned :meth:`LinearClient.set_state` (:func:`complete_verified`). The verdict
    is composed from the append-only ``VerificationCheck`` rows ``atlas verify`` wrote
    (never by re-running the evaluators), so a required check with no row composes to
    PENDING and holds the verdict. Its Done target is resolved ONCE up front (like
    admission's Ready-for-Agent resolution), so a status map missing a
    unique ``done`` state fails loudly even when nothing is completable; the write is
    Linear-only, so the next tick's pull reconciles Atlas.

    Then step 4 (ATLAS-45, scoped by ATLAS-148): scan each synced ticket in an
    ``ACTIVE_COMMENT_SCAN_STATUSES`` state — the documented active-state set;
    parked ``needs_human_decision`` and terminal statuses are excluded — and
    write one inbox stub per comment tagged ``atlas:proposed-follow-up`` to
    ``<inbox_dir>/<ticket-key>-<n>.md`` (:func:`_scan_follow_ups`). PRODUCER
    only — it surfaces follow-ups as working-tree stubs (the ONE sanctioned
    ``docs/planning/`` write, atomic;
    ADR-0007), creating no ticket and writing no Atlas/Linear state; the operator
    commits the inbox and the consumer side is ATLAS-122. The inbox is read once
    up front (:func:`_inbox_state`) for the dedup key set and the per-ticket index,
    so a tagged comment already stubbed (in inbox/ or inbox/processed/) is written
    once, not once per tick.

    Then step 5: a final pass re-reads the tickets and runs the anomaly clauses
    per ticket. Dwell-breach (ATLAS-119): append one ``DWELL_BREACH`` DebtItem per
    dwell episode for a ticket sitting in a horizoned working state past its
    horizon (:func:`_detect_dwell`) — report-only. Review-cycling (ATLAS-120):
    route a ticket over ``REVIEW_CYCLE_THRESHOLD`` round trips to ``Needs Human``
    via :meth:`LinearClient.set_state` and log one ``REVIEW_CYCLE`` note
    (:func:`_detect_review_cycle`) — the one anomaly that moves a ticket. The
    Needs-Human target state is resolved ONCE up front (like admission's
    Ready-for-Agent resolution), so a status map missing
    a unique ``needs_human_decision`` state fails loudly even when nothing is
    cycling. Stale-block (ATLAS-44): append one ``STALE_BLOCK`` DebtItem per
    blocked episode for a ticket stranded in ``blocked`` whose structural blockers
    have all cleared (:func:`_detect_stale_block`, reusing the same ``graph``
    built for admission above) — report-only, it NEVER moves the ticket. Then
    ATLAS-99 extracts DRAFT lessons from the newly appended review-cycle and
    dwell-breach rows. The pass runs after admission so it sees this tick's
    pulled statuses and freshly stamped
    ``status_entered_at``; the graph reflects current Atlas state because
    admission writes Linear only. Returns per-tick counters.

    ``now`` is the injected tick clock (no hidden ``datetime.now``): it stamps
    the ``observed_at``/``created_at`` of any DebtItem this tick appends and the
    ``status_entered_at`` of any status this tick pulls, so the flow stays
    deterministic under test.
    """

    result = SyncResult()
    if receipt_context is not None:
        receipt_context.result = result
    debt = DebtItemRepo(db)
    # Step 1's one batched read (ATLAS-148): every issue in the configured
    # project, keyed by id — the join key. Tickets join by external_linear_id
    # ONLY; title and identifier are never consulted. Fetched lazily: a board
    # with nothing pullable (no joined, non-terminal ticket) makes ZERO pull
    # requests, exactly as the per-ticket loop it replaced did.
    pull_board = tickets.list()
    if receipt_context is not None:
        receipt_context.pull_board = pull_board
    needs_pull = any(
        ticket.external_linear_id is not None
        and ticket.status.value not in TERMINAL_STATUSES
        for ticket in pull_board
    )
    fetched_issues = client.fetch_project_issues(project_id) if needs_pull else []
    if receipt_context is not None:
        receipt_context.fetched_issues = fetched_issues
    _validate_fetched_board(fetched_issues)
    issues_by_id: dict[str, LinearIssue] = {issue.id: issue for issue in fetched_issues}
    # Pull all joined tickets first, then reconstruct AgentRuns from the local
    # transition/evidence store plus the already-fetched board descriptions
    # (ATLAS-166). The push pass runs after reconstruction so this step is
    # strictly post-pull and still adds no Linear request of its own.
    pulled_board: list[Ticket] = []
    for ticket in pull_board:
        after_pull = _pull(
            ticket,
            tickets,
            db,
            debt,
            issues_by_id,
            status_map,
            result,
            now,
            lesson_client,
        )
        pulled_board.append(after_pull)
    reconstructed = reconstruct_agent_runs(
        tickets=tickets,
        db=db,
        issue_descriptions_by_id={
            issue_id: issue.description for issue_id, issue in issues_by_id.items()
        },
        now=now,
    )
    result.agent_runs_reconstructed = reconstructed.created
    result.agent_runs_updated = reconstructed.updated
    # The lazily-invoked pack-inputs seam (ATLAS-164): nothing loads until the
    # first push that will actually embed, so a no-op tick stays byte-identical
    # in both requests and local reads.
    pack_inputs = _PackInputLoader(db, documents)
    pushed_issue_ids: set[str] = set()
    for after_pull in pulled_board:
        pushed_issue_id = _push(
            after_pull,
            tickets,
            client,
            team_id,
            project_id,
            status_map,
            pack_inputs,
            debt,
            result,
            now,
        )
        if pushed_issue_id is not None:
            pushed_issue_ids.add(pushed_issue_id)
    if repair_packs:
        _repair_pack_absent_descriptions(
            tickets=tickets,
            client=client,
            issues_by_id=issues_by_id,
            skipped_issue_ids=pushed_issue_ids,
            pack_inputs=pack_inputs,
            debt=debt,
            result=result,
            now=now,
        )
    graph = build_dependency_graph(db)
    if pull_board or ProductRepo(db).list():
        admission = admit_one_ready(
            tickets=tickets,
            db=db,
            client=client,
            status_map=status_map,
            project_id=project_id,
            initial_issues=fetched_issues,
            now=now,
            hooks=admission_hooks,
            protected_lane_registry_provider=admission_registry_provider,
        )
        _apply_admission_result(result, admission)
        # A selected, stale or ambiguous admission ends this tick at the
        # sole-write boundary.  The next ordinary pull remains the Atlas-status
        # writer and no later completion/anomaly route can become a second
        # external mutation in the same decision window.
        if admission.outcome in {
            AdmissionSyncOutcome.ADMITTED,
            AdmissionSyncOutcome.STALE,
            AdmissionSyncOutcome.INDETERMINATE,
        }:
            return result
    # Step 3b (ATLAS-131): verified completion, immediately after admission. Move
    # every review_required ticket whose persisted Verification Engine verdict is
    # PASSED to Done via the sanctioned set_state. Like admission it resolves
    # its Done target up front (the load-time guard, fired even when nothing is
    # completable) and writes Linear only, so the next tick's pull reconciles Atlas.
    result.completed = complete_verified(
        tickets=tickets, db=db, client=client, status_map=status_map
    )
    # Step 4 (ATLAS-45): the follow-up comment scan, after admission and before
    # the step-5 anomaly passes (the loop order). The inbox is read once up front
    # for the dedup key set and per-ticket index; each newly-seen tagged comment
    # is written as one atomic inbox stub. Read-only on Linear (fetch_comments);
    # the only write is the stub under inbox/.
    seen_ids, max_index = _inbox_state(inbox_dir)
    for ticket in tickets.list():
        _scan_follow_ups(ticket, client, inbox_dir, seen_ids, max_index, result)
    # Step 5: a sibling pass after admission, re-reading tickets so it sees this
    # tick's pulled statuses and freshly stamped ``status_entered_at`` (admission
    # writes Linear only, so it does not perturb Atlas status). Dwell-breach
    # (ATLAS-119) is report-only; review-cycling (ATLAS-120) routes via
    # set_state. The Needs-Human target is resolved up front (the load-time
    # guard, mirroring admission), before the loop, so a misconfigured map
    # fails loudly even when no ticket is over threshold.
    needs_human_state_id = status_map.state_id_for(TicketStatus.NEEDS_HUMAN_DECISION)
    step5_tickets = tickets.list()
    new_anomaly_items: list[DebtItem] = []
    for ticket in step5_tickets:
        dwell_item = _detect_dwell(ticket, debt, result, now)
        if dwell_item is not None:
            new_anomaly_items.append(dwell_item)
        review_item = _detect_review_cycle(
            ticket, debt, client, needs_human_state_id, result, now
        )
        if review_item is not None:
            new_anomaly_items.append(review_item)
        _detect_stale_block(ticket, graph, debt, result, now)
    tickets_by_id = {ticket.id: ticket for ticket in step5_tickets}
    for item in new_anomaly_items:
        event_ticket = tickets_by_id.get(item.ticket_id)
        if event_ticket is not None:
            _extract_pm_failure_lesson(
                ticket=event_ticket,
                item=item,
                db=db,
                lesson_client=lesson_client,
                result=result,
                now=now,
            )
    return result


def sync_tick(
    *,
    tickets: TicketRepo,
    db: Database,
    client: LinearClient,
    status_map: LinearStatusMap,
    team_id: str,
    project_id: str,
    inbox_dir: Path,
    documents: Callable[[], list[SourceDocument]],
    now: datetime,
    repair_packs: bool = False,
    lesson_client: LessonModelClient | None = None,
    admission_hooks: AdmissionSyncHooks | None = None,
    admission_registry_provider: Callable[
        [], ProtectedLaneRegistryLoadResult
    ] = load_packaged_protected_lane_registry,
    completion_clock: Callable[[], datetime] = _utcnow,
) -> SyncResult:
    """Run one sync tick and append its durable PM sync receipt.

    The internal sync body preserves the existing pull/push/promote/scan/anomaly
    behavior. This wrapper makes receipt persistence part of the local
    completion boundary: a receipt write failure raises
    ``SyncReceiptPersistenceError`` and no successful result is returned. The
    deterministic logic clock ``now`` remains the tick start; ``completion_clock``
    is sampled only after the body completes or immediately before recording a
    failure/cancellation receipt.
    """

    context = _ReceiptContext()
    try:
        result = _sync_tick_impl(
            tickets=tickets,
            db=db,
            client=client,
            status_map=status_map,
            team_id=team_id,
            project_id=project_id,
            inbox_dir=inbox_dir,
            documents=documents,
            now=now,
            repair_packs=repair_packs,
            lesson_client=lesson_client,
            admission_hooks=admission_hooks,
            admission_registry_provider=admission_registry_provider,
            receipt_context=context,
        )
    except MalformedLinearPullError as error:
        finished_at = completion_clock()
        _record_sync_receipt(
            db=db,
            status_map=status_map,
            project_id=project_id,
            started_at=now,
            finished_at=finished_at,
            receipt_result=PmSyncReceiptResult.MALFORMED_PULL,
            context=context,
            error=error,
        )
        raise
    except (KeyboardInterrupt, SystemExit) as error:
        finished_at = completion_clock()
        _record_sync_receipt(
            db=db,
            status_map=status_map,
            project_id=project_id,
            started_at=now,
            finished_at=finished_at,
            receipt_result=PmSyncReceiptResult.CANCELLED,
            context=context,
            error=error,
        )
        raise
    except Exception as error:
        finished_at = completion_clock()
        _record_sync_receipt(
            db=db,
            status_map=status_map,
            project_id=project_id,
            started_at=now,
            finished_at=finished_at,
            receipt_result=PmSyncReceiptResult.FAILED,
            context=context,
            error=error,
        )
        raise

    receipt_result = _classify_successful_receipt(result)
    finished_at = completion_clock()
    _record_sync_receipt(
        db=db,
        status_map=status_map,
        project_id=project_id,
        started_at=now,
        finished_at=finished_at,
        receipt_result=receipt_result,
        context=context,
    )
    return result
