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
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from atlas.core.models import (
    Epic,
    PlanRun,
    PlanRunStatus,
    Ticket,
    TicketDependency,
)
from atlas.planning.client import ModelIdentity, PlannerClient, TruncatedOutputError
from atlas.planning.gates import GateFailure, run_gates
from atlas.planning.ingestion import AnchorIndex, collect_input_documents
from atlas.planning.proposal import Proposal, ProposalError, parse_proposal
from atlas.planning.reconciler import (
    DEFAULT_SIMILARITY_THRESHOLD,
    FROZEN_STATUSES,
    Backlog,
    PlanDiff,
    reconcile,
)
from atlas.planning.renderer import render_planner_prompt
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


class PlanPreconditionError(RuntimeError):
    """A clean-exit precondition failed before any model output existed."""


class ProductNotFoundError(PlanPreconditionError):
    """No product row to attribute the PlanRun to (setup gap)."""


class NoInputDocumentsError(PlanPreconditionError):
    """The §2.1 input set is empty (wrong repo root, or nothing tracked)."""


@dataclass(frozen=True)
class PlanResult:
    """The outcome of a plan run. ``status`` is PROPOSED on success or
    FAILED for a recorded gate/parse failure; clean-exit precondition
    failures raise instead of returning."""

    status: PlanRunStatus
    plan_run: PlanRun
    diff: PlanDiff | None
    failure_reason: str | None


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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
) -> PlanResult:
    """Run the full `atlas plan` pipeline once (spec §2.1)."""
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
    anchor_index = AnchorIndex.build(documents)

    # Current backlog from operational state (the database; ADR-0006).
    epics = EpicRepo(database).list()
    tickets = TicketRepo(database).list()
    dependencies = TicketDependencyRepo(database).list()
    backlog = Backlog(epics=epics, tickets=tickets, dependencies=dependencies)
    backlog_keys = {epic.key for epic in epics} | {ticket.key for ticket in tickets}
    frozen = [ticket.key for ticket in tickets if ticket.status in FROZEN_STATUSES]

    # Render the versioned prompt (prompt_hash = provenance middle link).
    rendered = render_planner_prompt(
        {
            "product_key": product.key,
            "documents": [
                {"path": doc.path, "sha": doc.sha, "content": doc.content}
                for doc in documents
            ],
            "current_backlog_yaml": _backlog_yaml(epics, tickets, dependencies),
            "frozen_ticket_keys": frozen,
            "next_key_hint": _next_key_hint(database),
            "proposal_json_schema": json.dumps(Proposal.model_json_schema(), indent=2),
        },
        prompts_dir=prompts_dir,
    )

    # Model call. A network/timeout/API failure is a clean exit (no raw
    # output). A token-limit truncation IS raw output — partial — and is
    # recorded with a specific reason rather than misparsed (ATLAS-101).
    truncation_limit: int | None = None
    try:
        raw_output = client.generate(rendered.text)
    except TruncatedOutputError as error:
        raw_output = error.raw_output
        truncation_limit = error.max_tokens
    raw_output_hash = _sha256(raw_output)

    provenance: dict[str, Any] = {
        "product_id": product.id,
        "input_doc_shas": anchor_index.input_doc_shas,
        "model_provider": identity.provider,
        "model_name": identity.model,
        "model_parameters": dict(identity.parameters),
        "prompt_version": rendered.prompt_version,
        "prompt_hash": rendered.prompt_hash,
        "similarity_threshold": similarity_threshold,
        "raw_output_hash": raw_output_hash,
    }

    # Truncation (stop_reason == max_tokens): a recorded failure carrying the
    # full provenance chain, named honestly so it is not a confusing parse error.
    if truncation_limit is not None:
        reason = json.dumps(
            {
                "stage": "truncation",
                "error": (
                    f"model output truncated at the token limit "
                    f"(max_tokens={truncation_limit}); the corpus is too large "
                    "for a single proposal"
                ),
            }
        )
        return _record_failed(database, provenance, now, reason)

    # Parse (gate 1, the parser's): a recorded failure with raw_output_hash.
    try:
        proposal = parse_proposal(raw_output)
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
