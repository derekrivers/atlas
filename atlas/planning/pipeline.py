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

import yaml

from atlas.core.anchors import AnchorIndex
from atlas.core.models import (
    Epic,
    PlanRun,
    PlanRunStatus,
    Ticket,
    TicketDependency,
)
from atlas.planning.client import ModelIdentity, PlannerClient, TruncatedOutputError
from atlas.planning.gates import GateFailure, run_gates
from atlas.planning.ingestion import (
    collect_inbox_documents,
    collect_input_documents,
)
from atlas.planning.proposal import Proposal, ProposalError, parse_proposal
from atlas.planning.reconciler import (
    DEFAULT_SIMILARITY_THRESHOLD,
    FROZEN_STATUSES,
    Backlog,
    PlanDiff,
    reconcile,
)
from atlas.planning.renderer import RenderedPrompt, render_planner_prompt
from atlas.planning.staged import (
    StageContext,
    StagedGenerationError,
    StagedProposalGenerator,
    StageRecord,
    StageTruncatedError,
    composite_prompt_hash,
    composite_prompt_version,
)
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


class PlanPreconditionError(RuntimeError):
    """A clean-exit precondition failed before any model output existed."""


class ProductNotFoundError(PlanPreconditionError):
    """No product row to attribute the PlanRun to (setup gap)."""


class NoInputDocumentsError(PlanPreconditionError):
    """The §2.1 input set is empty (wrong repo root, or nothing tracked)."""


class StagedReplanUnsupportedError(PlanPreconditionError):
    """The staged path was selected against a non-empty backlog. The
    ATLAS-103 templates carry no current-backlog seeding, so a staged
    re-plan would emit a partial-state proposal that archives everything it
    omits. Refuse honestly (clean exit, no PlanRun) rather than seed badly:
    staged generation is first-run only until re-plan seeding lands
    (ADR-0010; the capability is preserved via echoed keys)."""


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


def _backlog_yaml(
    epics: list[Epic], tickets: list[Ticket], dependencies: list[TicketDependency]
) -> str | None:
    """A YAML view of the current backlog for the prompt; None when the
    backlog is empty (first run). Prompt-only — not a planning render."""
    if not (epics or tickets or dependencies):
        return None
    sections = []
    for plural, entries in (
        ("epics", epics),
        ("tickets", tickets),
        ("dependencies", dependencies),
    ):
        if entries:
            payload = {plural: [entry.model_dump(mode="json") for entry in entries]}
            sections.append(yaml.safe_dump(payload, sort_keys=False))
    return "\n".join(sections)


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
) -> PlanResult:
    """Run the full `atlas plan` pipeline once (spec §2.1).

    Generation is single-call by default. When ``staged_generator`` is
    supplied (``atlas plan --staged``), generation runs across the three
    bounded staged calls and assembles one §3.11 envelope (ADR-0010); the
    parse → gates → reconcile → PlanRun path downstream is identical.
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
    all_documents = documents + inbox_documents
    anchor_index = AnchorIndex.build(all_documents)

    # Current backlog from operational state (the database; ADR-0006).
    epics = EpicRepo(database).list()
    tickets = TicketRepo(database).list()
    dependencies = TicketDependencyRepo(database).list()
    backlog = Backlog(epics=epics, tickets=tickets, dependencies=dependencies)
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
    if staged_generator is None:
        generated = _generate_single_call(
            client=client,
            product_key=product.key,
            documents=document_payload,
            valid_anchors=valid_anchors,
            backlog_yaml=_backlog_yaml(epics, tickets, dependencies),
            frozen=frozen,
            next_key_hint=_next_key_hint(database),
            prompts_dir=prompts_dir,
        )
    else:
        if epics or tickets or dependencies:
            raise StagedReplanUnsupportedError(
                "the staged path is first-run only (ADR-0010): the current "
                "backlog is non-empty, and the staged templates carry no "
                "re-emission seeding, so a staged proposal would archive "
                "every omitted item; re-run without --staged"
            )
        generated = _generate_staged(
            staged_generator,
            client=client,
            product_key=product.key,
            documents=document_payload,
            valid_anchors=valid_anchors,
            prompts_dir=prompts_dir,
        )

    raw_output_hash = _sha256(generated.raw_output)
    provenance: dict[str, Any] = {
        "product_id": product.id,
        "input_doc_shas": anchor_index.input_doc_shas,
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

    # Gates 2-7: a recorded failure carrying the full GateFailure list.
    failures = run_gates(
        proposal, current_backlog_keys=backlog_keys, anchor_index=anchor_index
    )
    if failures:
        return _record_failed(database, provenance, now, _gate_failure_reason(failures))

    # Reconcile against the backlog and persist at proposed. The validated
    # proposal is stored so apply (ATLAS-27) can materialise the backlog.
    diff = reconcile(proposal, backlog, similarity_threshold=similarity_threshold)
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
    product_key: str,
    documents: list[dict[str, str]],
    valid_anchors: list[dict[str, str]],
    backlog_yaml: str | None,
    frozen: list[str],
    next_key_hint: str,
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
        prompts_dir=prompts_dir,
    )
    try:
        raw_output = client.generate(rendered.text)
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
    product_key: str,
    documents: list[dict[str, str]],
    valid_anchors: list[dict[str, str]],
    prompts_dir: Path | None,
) -> _Generated:
    """The staged generation path (ADR-0010): three bounded calls assembled
    into one §3.11 envelope. raw_output is the assembled JSON (its hash is the
    provenance link, §5.3); prompt_version/prompt_hash are composites over the
    per-stage records. A per-stage truncation or protocol break is a recorded
    failure naming the stage (§5.4). ``valid_anchors`` (ATLAS-111) is the
    select-from list the epics and tickets stages render."""
    context = StageContext(
        product_key=product_key,
        documents=documents,
        prompts_dir=prompts_dir,
        valid_anchors=valid_anchors,
    )
    try:
        result = staged_generator.generate(client=client, context=context)
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
    per entry (type, key/new:<n>, title, anchor; MODIFY before/after)."""
    counts = diff.counts
    summary = "Plan diff: " + ", ".join(
        f"{entry_type} {counts[entry_type]}"
        for entry_type in ("ADD", "MODIFY", "PROPOSE_ARCHIVE", "CONFLICT")
    )
    lines = [summary]
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
