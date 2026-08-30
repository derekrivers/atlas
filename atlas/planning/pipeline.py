"""`atlas plan` pipeline composition (ATLAS-26), spec §2.1.

Composes the proposer half into one deterministic flow:

    product pre-flight -> ingest (HEAD) -> load backlog (storage) ->
    render prompt -> generate (client) -> parse -> gates -> reconcile ->
    persist PlanRun -> diff

No argparse, no printing, no SDK: every side-effecting dependency
(database, model client, clock) is injected, so the whole flow is
testable against a fake client and an in-memory database. Key
assignment and render writes are `atlas apply`'s (ATLAS-27); this
command persists a PlanRun and prints the diff, nothing else.

`run_stubs_only_plan` (ATLAS-153) is the second entry path: the same
ingest -> gates -> reconcile -> PlanRun flow with generation replaced by
pure code (the verbatim keyed backlog echo plus deterministic inbox-stub
promotion) — zero model calls, generation_stages == [] as the mode marker.

Failure contract (gap 1) — the dividing line is the provenance chain:
- before raw output exists (dirty tree, missing product, no documents,
  model-call error): a typed exception, clean exit, no PlanRun;
- once raw output exists (malformed JSON = gate 1, or gates 2-7): a
  PlanRun is recorded — inserted at `proposed`, finalised to `failed`
  with a machine-readable reason (spec §6) — carrying the full
  provenance chain including raw_output_hash, so a failed run is as
  auditable as a successful one.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import ValidationError as PydanticValidationError

from atlas.core.anchors import AnchorIndex
from atlas.core.models import (
    PlanRun,
    PlanRunStatus,
    Ticket,
)
from atlas.core.models.dependency import DependencyType
from atlas.core.models.planner_call_telemetry import (
    PlannerExecutionParameters,
    PlannerIdentity,
    PlanningExecutionIdentity,
)
from atlas.planning.client import (
    ModelIdentity,
    PlannerCallRequest,
    PlannerClient,
    TruncatedOutputError,
    invoke_planner_call,
    planner_telemetry_identity,
)
from atlas.planning.gates import GateFailure, run_gates
from atlas.planning.ingestion import (
    collect_batch_manifest_documents,
    collect_inbox_documents,
    collect_input_documents,
    collect_processed_documents,
    durable_alias_documents,
)
from atlas.planning.progress import ProgressCallback
from atlas.planning.promotion import promote_inbox_stubs
from atlas.planning.proposal import (
    Proposal,
    ProposalDependency,
    ProposalEpic,
    ProposalError,
    ProposalTicket,
    parse_proposal,
)
from atlas.planning.reconciler import (
    DEFAULT_SIMILARITY_THRESHOLD,
    FROZEN_STATUSES,
    Backlog,
    PlanDiff,
    reconcile,
)
from atlas.planning.renderer import (
    RenderedPrompt,
    render_planner_prompt,
    resolve_prompt_template_artifact,
)
from atlas.planning.seed import render_backlog_yaml
from atlas.planning.staged import (
    STAGE_DEPENDENCIES_VERSION,
    STAGE_EPICS_VERSION,
    STAGE_TICKETS_VERSION,
    StageContext,
    StagedGenerationError,
    StagedProposalGenerator,
    StageGateFailureError,
    StageRecord,
    StageTruncatedError,
    composite_prompt_hash,
    composite_prompt_version,
)
from atlas.planning.stub_integrity import validate_inbox_batch_integrity
from atlas.storage import (
    Database,
    EpicRepo,
    KeyCounterRepo,
    PlanRunRepo,
    ProductRepo,
    TicketDependencyRepo,
    TicketRepo,
)

# Single-product Atlas in Milestone 1 (ADR-0009): plan resolves the
# backlog's product by this key.
PRODUCT_KEY = "ATLAS"
TICKET_PREFIX = "ATLAS"

# The committed follow-up inbox sync_tick (ATLAS-45) writes stubs to; plan reads
# it as a separate input source (ATLAS-122). Planning-local default — it must
# match the producer's (atlas/pm/sync.py) and apply's (atlas/planning/apply.py).
DEFAULT_INBOX_DIR = Path("docs/planning/inbox")

# Stubs-only provenance sentinels (ATLAS-153): the mode makes no model call
# and renders no prompt, so the non-null provenance columns carry pinned
# constants. The load-bearing mode marker is generation_stages == [] — zero
# stages is unreachable on any generative path (single-call stores one
# record, staged stores three), so a reader distinguishes the modes from the
# stored record alone. prompt_hash is the SHA-256 of the empty string (no
# prompt was rendered); raw_output_hash is over the constructed proposal's
# canonical JSON, keeping the input_doc_shas -> raw_output_hash -> proposal
# audit chain deterministic and meaningful.
STUBS_ONLY_PROVIDER = "none"
STUBS_ONLY_MODEL = "stubs-only"
STUBS_ONLY_PROMPT_VERSION = "stubs-only"


class PlanPreconditionError(RuntimeError):
    """A clean-exit precondition failed before any model output existed."""


class ProductNotFoundError(PlanPreconditionError):
    """No product row to attribute the PlanRun to (setup gap)."""


class NoInputDocumentsError(PlanPreconditionError):
    """The §2.1 input set is empty (wrong repo root, or nothing tracked)."""


class EmptyInboxError(PlanPreconditionError):
    """``--stubs-only`` found no committed inbox stubs: nothing to mint.

    A clean-exit precondition failure, never an empty-diff PlanRun
    (ATLAS-153): the mode exists to mint stubs, so an empty inbox means the
    operator ran it against the wrong state — the message names the inbox so
    the fix (commit stubs, or run a generative plan) is obvious."""


class BacklogEchoError(PlanPreconditionError):
    """The stored backlog cannot be re-stated as a §3.11 proposal
    (ATLAS-153). Unreachable for a backlog minted through the proposal
    contract (its bounds were enforced at gate 1); fail-closed with the
    offending key rather than a raw validation traceback if state ever
    drifts out from under that assumption."""


class StubEpicRefError(PlanPreconditionError):
    """A stub promoted in a stubs-only run declares an ``epic_ref`` that is
    not an existing epic key (ATLAS-153). The generative path leaves
    ``new_epic:<n>`` refs to the parse-time bounds check and the gates; a
    stubs-only run has no parse stage and no model to create epics, so the
    ref could only mis-anchor to an echoed epic by positional accident or
    crash the reconciler out of bounds — refuse it typed, before the gates."""


class StagedReplanUnsupportedError(PlanPreconditionError):
    """The staged path was handed a backlog it cannot seed as full-state
    (ATLAS-144). Staged generation batches stage 2 per epic, so an epic-less
    ticket (``epic_id is None`` — a ``tech_debt`` ticket) has no batch to be
    re-stated in: seeding it is not expressible in the current stage grammar.
    Rather than silently omit it — which the reconciler would read as
    ``PROPOSE_ARCHIVE``, the exact data loss re-plan seeding exists to prevent —
    the pipeline refuses honestly (clean exit, no PlanRun) before generation.
    Epic-attached tickets seed and re-plan normally; only the epic-less case
    refuses. Seeding epic-less tickets is a named follow-up (an unassigned
    stage-2 batch), not this ticket."""


@dataclass(frozen=True)
class PlanResult:
    """The outcome of a plan run. ``status`` is PROPOSED on success or
    FAILED for a recorded gate/parse failure; clean-exit precondition
    failures raise instead of returning."""

    status: PlanRunStatus
    plan_run: PlanRun
    diff: PlanDiff | None
    failure_reason: str | None


@dataclass(frozen=True)
class _Generated:
    """The generation step's product, single-call or staged: the raw output
    the pipeline hashes and parses, plus the prompt provenance. ``failure_reason``
    is set when generation itself produced a recordable failure (truncation,
    or a staged protocol break) — raw output exists, so a PlanRun is recorded
    rather than a clean exit."""

    raw_output: str
    prompt_version: str
    prompt_hash: str
    # Per-stage generation provenance (ATLAS-105, §5.3): the staged path's
    # per-call records, or the single-call path's degenerate one-stage list.
    generation_stages: list[dict[str, str]]
    failure_reason: str | None


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _stage_payload(records: Sequence[StageRecord]) -> list[dict[str, str]]:
    """The persisted ``generation_stages`` shape (ATLAS-105, §5.3): each
    ATLAS-104 ``StageRecord`` as a plain JSON object, in call order. The
    ``stage`` string is copied verbatim, so the stored value byte-matches the
    composite-hash input and a future audit can tie the two together."""
    return [
        {
            "stage": record.stage,
            "prompt_version": record.prompt_version,
            "prompt_hash": record.prompt_hash,
            "raw_output_hash": record.raw_output_hash,
        }
        for record in records
    ]


def _next_key_hint(database: Database) -> str:
    """Informational hint for the prompt; the model never assigns keys.
    Sourced from the key authority's high-water mark (ATLAS-25)."""
    marks = KeyCounterRepo(database).high_water_marks()
    return f"{TICKET_PREFIX}-{marks.get(TICKET_PREFIX, 0) + 1}"


def _gate_failure_reason(failures: list[GateFailure]) -> str:
    return json.dumps(
        {
            "stage": "gates",
            "failures": [
                {"gate": f.gate, "code": f.code, "reason": f.reason} for f in failures
            ],
        }
    )


def run_plan(
    *,
    repo_root: Path,
    database: Database,
    client: PlannerClient,
    identity: ModelIdentity,
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    now: datetime,
    prompts_dir: Path | None = None,
    staged_generator: StagedProposalGenerator | None = None,
    inbox_dir: Path = DEFAULT_INBOX_DIR,
    on_progress: ProgressCallback | None = None,
) -> PlanResult:
    """Run the full `atlas plan` pipeline once (spec §2.1).

    Generation is single-call by default. When ``staged_generator`` is
    supplied (``atlas plan --staged``), generation runs across the three
    bounded staged calls and assembles one §3.11 envelope (ADR-0010); the
    parse → gates → reconcile → PlanRun path downstream is identical.

    ``on_progress`` is an optional staged-generation progress callback
    (additive observability): the staged path emits one event per stage
    boundary and per epic through it, and the CLI renders them to stderr. It is
    a no-op on the single-call path (no events fire) and when ``None``, so the
    public signature stays back-compatible.
    """
    # Pre-flight: product attribution (clean exit, no PlanRun).
    product = ProductRepo(database).get_by_key(PRODUCT_KEY)
    if product is None:
        raise ProductNotFoundError(
            f"no {PRODUCT_KEY!r} product in the database; bootstrap the "
            "product before planning (setup gap, not a plan failure)"
        )

    # Ingest from HEAD; a dirty/untracked input set raises DirtyInputError.
    documents = collect_input_documents(repo_root)
    if not documents:
        raise NoInputDocumentsError(
            f"no planner input documents found under {repo_root}; is the "
            "repo root correct and are the documents committed?"
        )
    # The committed follow-up inbox is a SEPARATE source (ATLAS-122): merged into
    # the planner input — anchor index, document payload, and input_doc_shas —
    # so stubs are visible to the planner, their headings are valid source_anchor
    # choices, and their provenance is recorded. The corpus globs stay pure (the
    # inbox subset is identifiable by its <inbox_dir>/ path prefix). An empty
    # inbox is a no-op; an uncommitted stub raises DirtyInputError (the gate).
    inbox_documents = collect_inbox_documents(repo_root, inbox_dir)
    manifest_documents = collect_batch_manifest_documents(repo_root, inbox_dir)
    all_documents = documents + inbox_documents
    # Retired stubs and durable aliases (ATLAS-159): processed/ headings join
    # the anchor index (never the prompt payload) so a stub-minted ticket's
    # durable anchor resolves after retirement, and each ACTIVE stub is
    # indexed at its future processed/ path too so the anchor promotion mints
    # resolves in this very run. Real files are pinned; aliases are not (the
    # alias is the already-pinned inbox blob at its future address).
    processed_documents = collect_processed_documents(repo_root, inbox_dir)
    anchor_index = AnchorIndex.build(
        all_documents
        + processed_documents
        + durable_alias_documents(inbox_documents, processed_documents)
    )
    input_doc_shas = {
        doc.path: doc.sha
        for doc in all_documents + processed_documents + manifest_documents
    }

    # Current backlog from operational state (the database; ADR-0006).
    epics = EpicRepo(database).list()
    tickets = TicketRepo(database).list()
    dependencies = TicketDependencyRepo(database).list()
    backlog = Backlog(epics=epics, tickets=tickets, dependencies=dependencies)
    validate_inbox_batch_integrity(
        repo_root=repo_root,
        inbox_documents=inbox_documents,
        manifest_documents=manifest_documents,
        backlog=backlog,
    )
    backlog_keys = {epic.key for epic in epics} | {ticket.key for ticket in tickets}
    frozen = [ticket.key for ticket in tickets if ticket.status in FROZEN_STATUSES]

    # Generate: single-call by default, or the staged sequence (ADR-0010).
    # Both yield one raw output the rest of the pipeline hashes and parses.
    document_payload = [
        {"path": doc.path, "sha": doc.sha, "content": doc.content}
        for doc in all_documents
    ]
    # The valid-anchor list (ATLAS-111): both paths render it so the model
    # SELECTS source_anchor from the index rather than constructing a slug.
    # Derived from the same anchor_index gate 4 validates against, so the
    # prompt's list and the gate's validator cannot drift.
    valid_anchors = anchor_index.anchor_choices()
    planner_identity, execution_parameters = planner_telemetry_identity(identity)
    if staged_generator is None:
        backlog_yaml = render_backlog_yaml(
            epics=epics,
            tickets=tickets,
            dependencies=dependencies,
            projection="full",
        )
        next_key_hint = _next_key_hint(database)
        # Freeze the exact selected template before minting the durable
        # post-preflight execution identity or invoking a provider.
        template = resolve_prompt_template_artifact(
            prompts_dir=prompts_dir,
            stage="single",
        )
        execution_identity = PlanningExecutionIdentity(execution_id=uuid4())
        generated = _generate_single_call(
            client=client,
            execution_identity=execution_identity,
            planner_identity=planner_identity,
            execution_parameters=execution_parameters,
            product_key=product.key,
            documents=document_payload,
            valid_anchors=valid_anchors,
            backlog_yaml=backlog_yaml,
            frozen=frozen,
            next_key_hint=next_key_hint,
            prompt_version=template.prompt_version,
            prompts_dir=prompts_dir,
        )
    else:
        # Re-plan seeding (ATLAS-144): the staged templates now carry the
        # current backlog, so a non-empty store re-plans. The one shape the
        # per-epic stage grammar cannot seed is an epic-less ticket — refuse
        # before generation rather than omit it (== PROPOSE_ARCHIVE, D-3).
        epic_keys_by_id = {epic.id: epic.key for epic in epics}
        epicless = [
            ticket.key
            for ticket in tickets
            if ticket.epic_id is None or ticket.epic_id not in epic_keys_by_id
        ]
        if epicless:
            raise StagedReplanUnsupportedError(
                "the staged path cannot seed epic-less ticket(s) "
                f"{sorted(epicless)} (no epic to batch under): stage 2 batches "
                "per epic, so there is no batch to re-state them in, and omitting "
                "them would archive them. Re-plan without --staged, or attach "
                "them to an epic (an unassigned stage-2 batch is a tracked "
                "follow-up)."
            )
        # The guard above proved every ticket has a resolvable epic_id, so the
        # lookup is total (the assert makes that legible to the type checker).
        seed_tickets_by_epic: dict[str, list[Ticket]] = {}
        for ticket in tickets:
            assert ticket.epic_id is not None
            seed_tickets_by_epic.setdefault(epic_keys_by_id[ticket.epic_id], []).append(
                ticket
            )
        # All released staged artifacts are resolved before the first call.
        # Dynamic ticket-batch input identities are derived later, but an
        # unknown template can never surface after an earlier provider call.
        resolve_prompt_template_artifact(
            version=STAGE_EPICS_VERSION,
            prompts_dir=prompts_dir,
            stage="epics",
        )
        resolve_prompt_template_artifact(
            version=STAGE_TICKETS_VERSION,
            prompts_dir=prompts_dir,
            stage="tickets.preflight",
        )
        resolve_prompt_template_artifact(
            version=STAGE_DEPENDENCIES_VERSION,
            prompts_dir=prompts_dir,
            stage="dependencies",
        )
        current_epics_seed = render_backlog_yaml(epics=epics, projection="epics")
        current_tickets_seed_by_epic = {
            key: render_backlog_yaml(tickets=epic_tickets, projection="tickets")
            for key, epic_tickets in seed_tickets_by_epic.items()
        }
        execution_identity = PlanningExecutionIdentity(execution_id=uuid4())
        generated = _generate_staged(
            staged_generator,
            client=client,
            execution_identity=execution_identity,
            planner_identity=planner_identity,
            execution_parameters=execution_parameters,
            product_key=product.key,
            documents=document_payload,
            valid_anchors=valid_anchors,
            current_epic_keys=frozenset(epic_keys_by_id.values()),
            current_epics_seed=current_epics_seed,
            current_tickets_seed_by_epic=current_tickets_seed_by_epic,
            prompts_dir=prompts_dir,
            on_progress=on_progress,
        )

    raw_output_hash = _sha256(generated.raw_output)
    provenance: dict[str, Any] = {
        "product_id": product.id,
        "input_doc_shas": input_doc_shas,
        "model_provider": identity.provider,
        "model_name": identity.model,
        "model_parameters": dict(identity.parameters),
        "prompt_version": generated.prompt_version,
        "prompt_hash": generated.prompt_hash,
        "similarity_threshold": similarity_threshold,
        "raw_output_hash": raw_output_hash,
        "generation_stages": generated.generation_stages,
    }

    # Truncation (single-call or per-stage) is a recorded failure carrying the
    # full provenance chain, named honestly so it is not a confusing parse error.
    if generated.failure_reason is not None:
        return _record_failed(database, provenance, now, generated.failure_reason)

    # Parse (gate 1, the parser's): a recorded failure with raw_output_hash.
    try:
        proposal = parse_proposal(generated.raw_output)
    except ProposalError as error:
        reason = json.dumps({"stage": "parse", "error": str(error)})
        return _record_failed(database, provenance, now, reason)

    # Deterministic inbox-stub promotion (ATLAS-146): inject one ADD ticket per
    # committed inbox stub into the parsed proposal before the gates run, so a
    # promoted ticket is validated and reconciled exactly like a model-emitted
    # one. Pure code, no model call. A committed stub with missing/invalid
    # front-matter is a fail-closed StubPromotionError at plan time (like
    # DirtyInputError), never a silent skip.
    tickets_before_promotion = len(proposal.tickets)
    proposal = promote_inbox_stubs(proposal, inbox_documents, backlog, anchor_index)
    # Promotion identity is POSITIONAL (ATLAS-151): promote_inbox_stubs appends
    # its tickets at the tail, so the injected set is exactly this index range.
    # Threaded into reconcile so a model re-emission of a committed stub
    # (same source_anchor) collapses into the promotion ticket instead of
    # minting a duplicate ADD. apply reconstructs the same set (AT-5 pins the
    # inbox between plan and apply), so both diffs collapse identically.
    promotion_indices = frozenset(
        range(tickets_before_promotion, len(proposal.tickets))
    )

    # Gates 2-7: a recorded failure carrying the full GateFailure list.
    failures = run_gates(
        proposal, current_backlog_keys=backlog_keys, anchor_index=anchor_index
    )
    if failures:
        return _record_failed(database, provenance, now, _gate_failure_reason(failures))

    # Reconcile against the backlog and persist at proposed. The validated
    # proposal is stored so apply (ATLAS-27) can materialise the backlog.
    diff = reconcile(
        proposal,
        backlog,
        similarity_threshold=similarity_threshold,
        promotion_indices=promotion_indices,
    )
    plan_run = PlanRun(
        id=uuid4(),
        status=PlanRunStatus.PROPOSED,
        proposal=proposal.model_dump(mode="json"),
        diff_summary=diff.as_summary(),
        failure_reason=None,
        approved_by=None,
        created_at=now,
        applied_at=None,
        **provenance,
    )
    PlanRunRepo(database).add(plan_run)
    return PlanResult(
        status=PlanRunStatus.PROPOSED,
        plan_run=plan_run,
        diff=diff,
        failure_reason=None,
    )


def _echo_backlog_proposal(backlog: Backlog) -> Proposal:
    """Re-state the current backlog verbatim as a keyed §3.11 proposal
    (ATLAS-153): every epic and ticket echoed field-for-field with its real
    key (``epic_ref`` resolved to the owning epic's key), every
    ticket-to-ticket edge echoed with its stored reason. Pure code, no model
    (ADR-0005). A verbatim echo reconciles to a no-op — no MODIFY, no
    PROPOSE_ARCHIVE, and no frozen CONFLICT (the reconciler skips no-change
    matches before its frozen check) — so the only diff entries a stubs-only
    run produces are the promotion's own ADDs.

    Edge filters mirror the reconciler's backlog read (reconciler.py,
    "Dependency edges"): ticket-to-ticket edges only, dangling rows skipped.
    Milestone-1 storage is ``depends_on``-only (the proposal contract
    enforced it at mint), so the echo asserts rather than filters on type.
    """
    epic_key_by_id = {epic.id: epic.key for epic in backlog.epics}
    ticket_key_by_id = {ticket.id: ticket.key for ticket in backlog.tickets}

    try:
        epics = [
            ProposalEpic(
                key=epic.key,
                title=epic.title,
                description=epic.description,
                objective=epic.objective,
                priority=epic.priority,
                risk_level=epic.risk_level,
                source_anchor=epic.source_anchor,
            )
            for epic in backlog.epics
        ]
        tickets = [
            ProposalTicket(
                key=ticket.key,
                epic_ref=(
                    epic_key_by_id.get(ticket.epic_id)
                    if ticket.epic_id is not None
                    else None
                ),
                title=ticket.title,
                objective=ticket.objective,
                context=ticket.context,
                ticket_type=ticket.ticket_type,
                risk_level=ticket.risk_level,
                priority=ticket.priority,
                source_anchor=ticket.source_anchor,
                relevant_docs=list(ticket.relevant_docs),
                tags=list(ticket.tags),
                component=ticket.component,
                acceptance_criteria=list(ticket.acceptance_criteria),
                non_goals=list(ticket.non_goals),
                test_requirements=list(ticket.test_requirements),
                implementation_notes=list(ticket.implementation_notes),
                documentation_requirements=list(ticket.documentation_requirements),
                definition_of_done=list(ticket.definition_of_done),
            )
            for ticket in backlog.tickets
        ]
    except PydanticValidationError as error:
        raise BacklogEchoError(
            "the stored backlog cannot be re-stated as a proposal (a field "
            "violates the §3.11 contract the backlog was minted under): "
            f"{error.errors()[0].get('msg')} — repair the store before a "
            "stubs-only run"
        ) from error

    dependencies = []
    for dependency in backlog.dependencies:
        if dependency.target_entity_type != "ticket":
            continue  # M1 reconciles ticket-to-ticket edges only (§3.11)
        source_key = ticket_key_by_id.get(dependency.source_ticket_id)
        target_key = ticket_key_by_id.get(dependency.target_entity_id)
        if source_key is None or target_key is None:
            continue  # dangling rows are ATLAS-19's validator's concern
        dependencies.append(
            ProposalDependency(
                source=source_key,
                target=target_key,
                dependency_type=DependencyType.DEPENDS_ON,
                reason=dependency.reason,
            )
        )

    return Proposal(
        epics=epics,
        tickets=tickets,
        dependencies=dependencies,
        planner_notes=[
            "stubs-only run (ATLAS-153): verbatim keyed backlog echo plus "
            "deterministic inbox-stub promotion; no model call"
        ],
    )


def run_stubs_only_plan(
    *,
    repo_root: Path,
    database: Database,
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    now: datetime,
    inbox_dir: Path = DEFAULT_INBOX_DIR,
) -> PlanResult:
    """Run `atlas plan --stubs-only` once (ATLAS-153): an apply-ready PlanRun
    from committed inbox stubs alone — the second entry path beside
    :func:`run_plan`, generation untouched.

    The proposal is built by pure code — the verbatim keyed backlog echo
    (:func:`_echo_backlog_proposal`) plus the promoted stubs
    (:func:`promote_inbox_stubs`, called exactly as the generative path calls
    it) — and flows through the SAME gates, the SAME reconciler (the
    ATLAS-151 collapse pre-pass runs and is trivially a no-op: every echoed
    ticket is keyed, every promoted one is promotion-injected), and persists
    the SAME PlanRun shape, so `atlas apply` consumes it unchanged —
    ``promotion_indices`` is the trailing ``len(inbox)`` range, the exact
    positional identity apply already reconstructs under the AT-5 staleness
    guarantee. No PlannerClient is constructed or called; there is no
    generative failure surface (no truncation, no parse stage), but a gate
    failure still records a FAILED PlanRun exactly as a generative run's
    would. Impossible stub contracts are rejected earlier by the planning-
    batch integrity guard, before this proposal or a PlanRun exists.

    ``input_doc_shas`` still pins corpus + inbox + processed (ATLAS-159), so
    apply's AT-5 re-check holds identically. An empty inbox is a typed
    clean-exit precondition (:class:`EmptyInboxError`) — nothing persisted,
    never an empty-diff PlanRun.
    """
    # Pre-flight: product attribution (clean exit, no PlanRun).
    product = ProductRepo(database).get_by_key(PRODUCT_KEY)
    if product is None:
        raise ProductNotFoundError(
            f"no {PRODUCT_KEY!r} product in the database; bootstrap the "
            "product before planning (setup gap, not a plan failure)"
        )

    # Ingest from HEAD; a dirty/untracked input set raises DirtyInputError.
    # The full corpus is still read — it feeds the anchor index gate 4
    # resolves against and the input_doc_shas the AT-5 re-check compares.
    documents = collect_input_documents(repo_root)
    if not documents:
        raise NoInputDocumentsError(
            f"no planner input documents found under {repo_root}; is the "
            "repo root correct and are the documents committed?"
        )
    inbox_documents = collect_inbox_documents(repo_root, inbox_dir)
    if not inbox_documents:
        raise EmptyInboxError(
            f"the committed follow-up inbox {inbox_dir.as_posix()!r} has no "
            "stubs: nothing to mint. Commit inbox stubs first, or run a "
            "generative `atlas plan` (stubs-only plans promoted stubs only)"
        )
    manifest_documents = collect_batch_manifest_documents(repo_root, inbox_dir)
    # Processed stubs + durable aliases join the index exactly as on the
    # generative path (ATLAS-159): the verbatim backlog echo re-states every
    # stub-minted ticket's durable anchor, and gate 4 must resolve it here too.
    processed_documents = collect_processed_documents(repo_root, inbox_dir)
    anchor_index = AnchorIndex.build(
        documents
        + inbox_documents
        + processed_documents
        + durable_alias_documents(inbox_documents, processed_documents)
    )
    input_doc_shas = {
        doc.path: doc.sha
        for doc in (
            documents + inbox_documents + processed_documents + manifest_documents
        )
    }

    # Current backlog from operational state (the database; ADR-0006).
    epics = EpicRepo(database).list()
    tickets = TicketRepo(database).list()
    dependencies = TicketDependencyRepo(database).list()
    backlog = Backlog(epics=epics, tickets=tickets, dependencies=dependencies)
    validate_inbox_batch_integrity(
        repo_root=repo_root,
        inbox_documents=inbox_documents,
        manifest_documents=manifest_documents,
        backlog=backlog,
    )
    backlog_keys = {epic.key for epic in epics} | {ticket.key for ticket in tickets}

    # The stubs-only proposal: verbatim keyed echo + deterministic promotion,
    # positionally identical to the generative path's injection (ATLAS-151).
    proposal = _echo_backlog_proposal(backlog)
    tickets_before_promotion = len(proposal.tickets)
    proposal = promote_inbox_stubs(proposal, inbox_documents, backlog, anchor_index)
    promotion_indices = frozenset(
        range(tickets_before_promotion, len(proposal.tickets))
    )

    # No model, no parse stage: the parse-time epic_ref bounds check never
    # runs here, so a stub's new_epic:<n> ref — meaningless without a model
    # to create epics — must be refused typed instead (StubEpicRefError).
    # Promotion already failed closed on an existing-key ref naming no
    # backlog epic, so only placeholder refs can reach this.
    epic_keys = {epic.key for epic in epics}
    for index in sorted(promotion_indices):
        ref = proposal.tickets[index].epic_ref
        if ref is not None and ref not in epic_keys:
            document = inbox_documents[index - tickets_before_promotion]
            raise StubEpicRefError(
                f"inbox stub {document.path!r} declares epic_ref {ref!r}; a "
                "stubs-only run has no model to create epics, so epic_ref "
                "must name an existing epic key"
            )

    provenance: dict[str, Any] = {
        "product_id": product.id,
        "input_doc_shas": input_doc_shas,
        "model_provider": STUBS_ONLY_PROVIDER,
        "model_name": STUBS_ONLY_MODEL,
        "model_parameters": {},
        "prompt_version": STUBS_ONLY_PROMPT_VERSION,
        "prompt_hash": _sha256(""),
        "similarity_threshold": similarity_threshold,
        "raw_output_hash": _sha256(
            json.dumps(proposal.model_dump(mode="json"), sort_keys=True)
        ),
        "generation_stages": [],  # the zero-stage mode marker (ATLAS-153)
    }

    # Gates 2-7, unchanged: a failure records a FAILED PlanRun (spec §6).
    failures = run_gates(
        proposal, current_backlog_keys=backlog_keys, anchor_index=anchor_index
    )
    if failures:
        return _record_failed(database, provenance, now, _gate_failure_reason(failures))

    # Reconcile against the backlog and persist at proposed, exactly as the
    # generative path does — apply consumes the PlanRun unchanged.
    diff = reconcile(
        proposal,
        backlog,
        similarity_threshold=similarity_threshold,
        promotion_indices=promotion_indices,
    )
    plan_run = PlanRun(
        id=uuid4(),
        status=PlanRunStatus.PROPOSED,
        proposal=proposal.model_dump(mode="json"),
        diff_summary=diff.as_summary(),
        failure_reason=None,
        approved_by=None,
        created_at=now,
        applied_at=None,
        **provenance,
    )
    PlanRunRepo(database).add(plan_run)
    return PlanResult(
        status=PlanRunStatus.PROPOSED,
        plan_run=plan_run,
        diff=diff,
        failure_reason=None,
    )


def _generate_single_call(
    *,
    client: PlannerClient,
    execution_identity: PlanningExecutionIdentity,
    planner_identity: PlannerIdentity,
    execution_parameters: PlannerExecutionParameters,
    product_key: str,
    documents: list[dict[str, str]],
    valid_anchors: list[dict[str, str]],
    backlog_yaml: str | None,
    frozen: list[str],
    next_key_hint: str,
    prompt_version: str,
    prompts_dir: Path | None,
) -> _Generated:
    """The single-call generation path (ATLAS-26/101): render the versioned
    prompt (resolving CURRENT, now planner-v1.2.0), call the model, treat a
    token-limit truncation as a recordable partial output. ``valid_anchors``
    (ATLAS-111) is the select-from list rendered into the prompt."""
    rendered = render_planner_prompt(
        {
            "product_key": product_key,
            "documents": documents,
            "valid_anchors": valid_anchors,
            "current_backlog_yaml": backlog_yaml,
            "frozen_ticket_keys": frozen,
            "next_key_hint": next_key_hint,
            "proposal_json_schema": json.dumps(Proposal.model_json_schema(), indent=2),
        },
        version=prompt_version,
        prompts_dir=prompts_dir,
        stage="single",
    )
    request = PlannerCallRequest(
        logical_call=rendered.logical_call(
            execution=execution_identity,
            planner=planner_identity,
            execution_parameters=execution_parameters,
            logical_attempt_no=0,
        )
    )
    try:
        raw_output = invoke_planner_call(
            client,
            prompt=rendered.text,
            request=request,
        ).raw_output
    except TruncatedOutputError as error:
        reason = json.dumps(
            {
                "stage": "truncation",
                "error": (
                    f"model output truncated at the token limit "
                    f"(max_tokens={error.max_tokens}); the corpus is too large "
                    "for a single proposal"
                ),
            }
        )
        return _Generated(
            raw_output=error.raw_output,
            prompt_version=rendered.prompt_version,
            prompt_hash=rendered.prompt_hash,
            generation_stages=_single_call_stages(rendered, error.raw_output),
            failure_reason=reason,
        )
    return _Generated(
        raw_output=raw_output,
        prompt_version=rendered.prompt_version,
        prompt_hash=rendered.prompt_hash,
        generation_stages=_single_call_stages(rendered, raw_output),
        failure_reason=None,
    )


def _single_call_stages(
    rendered: RenderedPrompt, raw_output: str
) -> list[dict[str, str]]:
    """The single-call path's degenerate one-stage list (gap 2, §5.3): one
    record so the field's meaning is uniform across paths. Its prompt_hash and
    raw_output_hash equal the run's top-level chain (a one-stage composite)."""
    return [
        {
            "stage": "single",
            "prompt_version": rendered.prompt_version,
            "prompt_hash": rendered.prompt_hash,
            "raw_output_hash": _sha256(raw_output),
        }
    ]


def _generate_staged(
    staged_generator: StagedProposalGenerator,
    *,
    client: PlannerClient,
    execution_identity: PlanningExecutionIdentity,
    planner_identity: PlannerIdentity,
    execution_parameters: PlannerExecutionParameters,
    product_key: str,
    documents: list[dict[str, str]],
    valid_anchors: list[dict[str, str]],
    current_epic_keys: frozenset[str],
    current_epics_seed: str | None,
    current_tickets_seed_by_epic: dict[str, str | None],
    prompts_dir: Path | None,
    on_progress: ProgressCallback | None = None,
) -> _Generated:
    """The staged generation path (ADR-0010): three bounded calls assembled
    into one §3.11 envelope. raw_output is the assembled JSON (its hash is the
    provenance link, §5.3); prompt_version/prompt_hash are composites over the
    per-stage records. A per-stage truncation or protocol break is a recorded
    failure naming the stage (§5.4). ``valid_anchors`` (ATLAS-111) is the
    select-from list the epics and tickets stages render. The two seed
    arguments (ATLAS-144) carry the current backlog the epics and per-epic
    tickets stages restate; both are ``None``/empty on a first run, so the
    seeded templates render their empty-backlog branch (first-run parity).
    ``current_epic_keys`` binds Stage-1 echoes before they can influence a
    provider-bound telemetry stage."""
    context = StageContext(
        execution_identity=execution_identity,
        planner_identity=planner_identity,
        execution_parameters=execution_parameters,
        product_key=product_key,
        documents=documents,
        prompts_dir=prompts_dir,
        valid_anchors=valid_anchors,
        current_epic_keys=current_epic_keys,
        current_epics_seed=current_epics_seed,
        current_tickets_seed_by_epic=current_tickets_seed_by_epic,
    )
    try:
        result = staged_generator.generate(
            client=client, context=context, on_progress=on_progress
        )
    except StageTruncatedError as error:
        reason = json.dumps(
            {
                "stage": "truncation",
                "generation_stage": error.stage,
                "error": (
                    f"staged generation truncated in stage {error.stage!r} "
                    f"(max_tokens={error.max_tokens}); the stage output exceeded "
                    "the single-call ceiling"
                ),
            }
        )
        return _Generated(
            raw_output=error.raw_output,
            prompt_version=composite_prompt_version(error.records),
            prompt_hash=composite_prompt_hash(error.records),
            generation_stages=_stage_payload(error.records),
            failure_reason=reason,
        )
    except StageGateFailureError as error:
        return _Generated(
            raw_output=error.raw_output,
            prompt_version=composite_prompt_version(error.records),
            prompt_hash=composite_prompt_hash(error.records),
            generation_stages=_stage_payload(error.records),
            failure_reason=_gate_failure_reason(list(error.failures)),
        )
    except StagedGenerationError as error:
        reason = json.dumps(
            {
                "stage": "staged_generation",
                "generation_stage": error.stage,
                "error": str(error),
            }
        )
        return _Generated(
            raw_output=error.raw_output,
            prompt_version=composite_prompt_version(error.records),
            prompt_hash=composite_prompt_hash(error.records),
            generation_stages=_stage_payload(error.records),
            failure_reason=reason,
        )
    return _Generated(
        raw_output=result.assembled_json,
        prompt_version=result.prompt_version,
        prompt_hash=result.prompt_hash,
        generation_stages=_stage_payload(result.stage_records),
        failure_reason=None,
    )


def _record_failed(
    database: Database,
    provenance: dict[str, Any],
    now: datetime,
    failure_reason: str,
) -> PlanResult:
    """Record a downstream-of-the-model failure: insert at proposed, then
    the single finalising transition to failed (spec §6). The provenance
    chain — including raw_output_hash — is preserved for audit."""
    repo = PlanRunRepo(database)
    plan_run = PlanRun(
        id=uuid4(),
        status=PlanRunStatus.PROPOSED,
        diff_summary={},
        failure_reason=None,
        approved_by=None,
        created_at=now,
        applied_at=None,
        **provenance,
    )
    repo.add(plan_run)
    finalised = repo.finalize(
        plan_run.id, PlanRunStatus.FAILED, failure_reason=failure_reason
    )
    return PlanResult(
        status=PlanRunStatus.FAILED,
        plan_run=finalised,
        diff=None,
        failure_reason=failure_reason,
    )


def format_plan_diff(diff: PlanDiff) -> str:
    """The §2.4 diff presentation: a counts summary line, then one block
    per entry (type, key/new:<n>, title, anchor; MODIFY before/after).
    Promotion-dedup collapses (ATLAS-151) render one line each right after
    the summary — they surface at BOTH gates (`atlas plan` output and
    apply's confirm), the exact eyeball check the 149/150 mint slipped
    past. The summary line carries a COLLAPSE count only when nonzero, so
    the collapse-free presentation is byte-identical to before."""
    counts = diff.counts
    summary = "Plan diff: " + ", ".join(
        f"{entry_type} {counts[entry_type]}"
        for entry_type in ("ADD", "MODIFY", "PROPOSE_ARCHIVE", "CONFLICT")
    )
    if diff.collapses:
        summary += f", COLLAPSE {len(diff.collapses)}"
    lines = [summary]
    for note in diff.collapses:
        lines.append(
            f"  {'COLLAPSE':<15} {'ticket':<10} {note.absorbed}"
            f"  {note.absorbed_title!r} absorbed into promotion ticket "
            f"{note.survivor} ({note.anchor})"
        )
    for entry in diff.entries:
        anchor = f" ({entry.anchor})" if entry.anchor else ""
        lines.append(
            f"  {entry.entry_type:<15} {entry.kind:<10} {entry.identity}"
            f"  {entry.title!r}{anchor}"
        )
        for name, (before, after) in entry.changes.items():
            lines.append(f"      {name}: {before!r} -> {after!r}")
        if entry.would_have_been is not None:
            lines.append(
                f"      would have been {entry.would_have_been}; {entry.reason}"
            )
    return "\n".join(lines)
