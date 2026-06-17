"""`atlas apply` pipeline (ATLAS-27), spec §2.2.

Loads the latest proposed PlanRun, refuses a stale one, takes explicit
operator confirmation, then atomically assigns keys, persists the
assigned backlog, writes the four renders, and finalises the PlanRun to
applied. Apply is the only legal writer of docs/planning/ (ADR-0006/0007).

Atomicity (gap 1): the DB commit (atlas.storage.apply_backlog) is the
single linearisation point. Render texts — including their header
high-water marks, known from assign_keys before any write — are written
to temp files, and atomically moved into place only after the commit. A
crash before the commit leaves nothing (DB rolled back, temps inert); a
crash during the post-commit move is repaired by re-running apply, which
completes the pending move from the committed state (idempotent).

Scope (operator ruling): apply materialises ADD items; PROPOSE_ARCHIVE
items are excluded from the renders; a diff containing MODIFY or CONFLICT
entries is refused (MODIFY is a follow-up; CONFLICT is AT-4 — a diff
touching a frozen ticket is not applied).

Graph validation (ATLAS-40): before the commit seam, apply projects the
post-apply backlog and runs validate_graph; a typed GraphValidationError
refuses the apply with nothing written (DB rolled back implicitly — it has
not committed — and the renders not yet materialised).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from uuid import UUID, uuid4

from atlas.core.enums import ActorType
from atlas.core.models import (
    Epic,
    EpicStatus,
    PlanRun,
    PlanRunStatus,
    Ticket,
    TicketDependency,
    TicketStatus,
)
from atlas.core.models.dependency import DependencyType
from atlas.core.yaml_io import RenderHeader, render_document
from atlas.dependencies import project_graph, validate_graph
from atlas.planning.ingestion import collect_input_documents
from atlas.planning.key_authority import (
    EPIC_PREFIX,
    TICKET_PREFIX,
    KeyAssignment,
    assign_keys,
)
from atlas.planning.mermaid import render_roadmap
from atlas.planning.proposal import Proposal
from atlas.planning.reconciler import (
    ADD,
    CONFLICT,
    MODIFY,
    PROPOSE_ARCHIVE,
    Backlog,
    PlanDiff,
    reconcile,
)
from atlas.storage import (
    ADRRepo,
    Database,
    EpicRepo,
    KeyCounterRepo,
    PlanRunRepo,
    ProductRepo,
    TicketDependencyRepo,
    TicketRepo,
    apply_backlog,
)

CREATED_BY = "planner"
RENDER_FILES = ("epics.yaml", "tickets.yaml", "dependencies.yaml", "roadmap.mmd")


class ApplyError(RuntimeError):
    """Base for clean-exit apply refusals (no writes)."""


class NoProposedPlanError(ApplyError):
    """There is no proposed PlanRun to apply."""


class StalePlanError(ApplyError):
    """Fresh ingestion no longer matches the recorded input_doc_shas."""


class UnsupportedDiffError(ApplyError):
    """The diff contains MODIFY entries; MODIFY-apply is a follow-up."""


class ConflictRefusalError(ApplyError):
    """The diff touches a frozen ticket (CONFLICT); apply refuses it (AT-4)."""


class ApplyDecision(Enum):
    CONFIRMED = auto()
    REJECTED = auto()
    UNCONFIRMABLE = auto()


@dataclass(frozen=True)
class ApplyResult:
    outcome: str  # "applied" | "rejected" | "unconfirmed"
    plan_run: PlanRun
    diff: PlanDiff


def _planning_dir(repo_root: Path, planning_dir: Path | None) -> Path:
    return planning_dir if planning_dir is not None else repo_root / "docs" / "planning"


def _temp_name(final: str, plan_run_id: UUID) -> str:
    return f"{final}.tmp-{plan_run_id}"


def _recover_pending_renders(planning_dir: Path, database: Database) -> None:
    """Complete or discard renders left by a crash (gap 1 recovery).

    A temp file survives only between the temp write and the post-commit
    move. Its PlanRun's status is the commit witness: applied -> the commit
    succeeded, so finish the move; otherwise the commit never happened, so
    discard. Idempotent — safe to run on every apply."""
    if not planning_dir.exists():
        return
    for temp in sorted(planning_dir.glob("*.tmp-*")):
        final_name, _, run_id = temp.name.rpartition(".tmp-")
        try:
            plan_run_id = UUID(run_id)
        except ValueError:
            continue
        run = PlanRunRepo(database).get(plan_run_id)
        if run is not None and run.status is PlanRunStatus.APPLIED:
            os.replace(temp, planning_dir / final_name)  # finish the move
        else:
            temp.unlink()  # commit never happened: discard


def _materialise(
    proposal: Proposal,
    diff: PlanDiff,
    assignment: KeyAssignment,
    *,
    product_id: UUID,
    current_epics: list[Epic],
    current_tickets: list[Ticket],
    now: datetime,
) -> tuple[list[Epic], list[Ticket], list[TicketDependency]]:
    """Build the new (ADD) entities with assigned keys and resolved refs."""
    epic_id_by_key = {epic.key: epic.id for epic in current_epics}
    ticket_id_by_key = {ticket.key: ticket.id for ticket in current_tickets}

    new_epics: list[Epic] = []
    for entry in diff.entries:
        if entry.kind == "epic" and entry.entry_type == ADD:
            epic_item = proposal.epics[int(entry.identity.removeprefix("new_epic:"))]
            key = assignment.resolve(entry.identity)
            epic = Epic(
                id=uuid4(),
                product_id=product_id,
                key=key,
                title=epic_item.title,
                description=epic_item.description,
                objective=epic_item.objective,
                status=EpicStatus.PLANNED,
                priority=epic_item.priority,
                risk_level=epic_item.risk_level,
                source_anchor=epic_item.source_anchor,
                created_by_type=ActorType.AGENT,
                created_by_id=CREATED_BY,
                created_at=now,
                updated_at=now,
                completed_at=None,
            )
            new_epics.append(epic)
            epic_id_by_key[key] = epic.id

    new_tickets: list[Ticket] = []
    for entry in diff.entries:
        if entry.kind == "ticket" and entry.entry_type == ADD:
            ticket_item = proposal.tickets[int(entry.identity.removeprefix("new:"))]
            key = assignment.resolve(entry.identity)
            epic_key = (
                assignment.resolve(ticket_item.epic_ref)
                if ticket_item.epic_ref is not None
                else None
            )
            ticket = Ticket(
                id=uuid4(),
                product_id=product_id,
                epic_id=epic_id_by_key.get(epic_key) if epic_key is not None else None,
                key=key,
                title=ticket_item.title,
                objective=ticket_item.objective,
                context=ticket_item.context,
                status=TicketStatus.PLANNED,
                ticket_type=ticket_item.ticket_type,
                risk_level=ticket_item.risk_level,
                priority=ticket_item.priority,
                relevant_docs=ticket_item.relevant_docs,
                acceptance_criteria=ticket_item.acceptance_criteria,
                non_goals=ticket_item.non_goals,
                implementation_notes=ticket_item.implementation_notes,
                test_requirements=ticket_item.test_requirements,
                documentation_requirements=ticket_item.documentation_requirements,
                definition_of_done=ticket_item.definition_of_done,
                source_anchor=ticket_item.source_anchor,
                created_by_type=ActorType.AGENT,
                created_by_id=CREATED_BY,
                created_at=now,
                updated_at=now,
            )
            new_tickets.append(ticket)
            ticket_id_by_key[key] = ticket.id

    new_dependencies: list[TicketDependency] = []
    for entry in diff.entries:
        if entry.kind == "dependency" and entry.entry_type == ADD:
            source_ref, _, target_ref = entry.identity.partition(" -> ")
            new_dependencies.append(
                TicketDependency(
                    id=uuid4(),
                    source_ticket_id=ticket_id_by_key[assignment.resolve(source_ref)],
                    target_entity_type="ticket",
                    target_entity_id=ticket_id_by_key[assignment.resolve(target_ref)],
                    dependency_type=DependencyType.DEPENDS_ON,
                    reason=entry.title,
                    created_by_type=ActorType.AGENT,
                    created_by_id=CREATED_BY,
                    created_at=now,
                )
            )
    return new_epics, new_tickets, new_dependencies


def _archived_keys(diff: PlanDiff, kind: str) -> set[str]:
    return {
        entry.identity
        for entry in diff.entries
        if entry.kind == kind and entry.entry_type == PROPOSE_ARCHIVE
    }


def run_apply(
    *,
    repo_root: Path,
    database: Database,
    now: datetime,
    confirm: Callable[[PlanDiff], ApplyDecision],
    planning_dir: Path | None = None,
) -> ApplyResult:
    """Run the §2.2 apply sequence once. ``confirm`` receives the diff and
    returns the operator's decision; no write happens before CONFIRMED."""
    target_dir = _planning_dir(repo_root, planning_dir)
    _recover_pending_renders(target_dir, database)

    plan_run = PlanRunRepo(database).latest_proposed()
    if plan_run is None:
        raise NoProposedPlanError(
            "no proposed PlanRun to apply; run `atlas plan` first"
        )

    # Staleness re-check BEFORE confirmation (AT-5); reuses ingestion's
    # dirty-tree + SHA machinery (a dirty tree raises DirtyInputError here).
    fresh_shas = {doc.path: doc.sha for doc in collect_input_documents(repo_root)}
    if fresh_shas != plan_run.input_doc_shas:
        raise StalePlanError(
            "the plan is stale: input documents changed since planning; "
            "re-run `atlas plan` (AT-5)"
        )

    product = ProductRepo(database).get(plan_run.product_id)
    if product is None:  # the PlanRun's product vanished (setup gap)
        raise ApplyError(f"PlanRun product {plan_run.product_id} no longer exists")

    current_epics = EpicRepo(database).list()
    current_tickets = TicketRepo(database).list()
    current_deps = TicketDependencyRepo(database).list()
    backlog = Backlog(
        epics=current_epics, tickets=current_tickets, dependencies=current_deps
    )

    proposal = Proposal.model_validate(plan_run.proposal)
    diff = reconcile(
        proposal, backlog, similarity_threshold=plan_run.similarity_threshold
    )

    # Refuse diffs this ticket does not apply (operator ruling, AT-4).
    if any(entry.entry_type == MODIFY for entry in diff.entries):
        raise UnsupportedDiffError(
            "the diff contains MODIFY entries; MODIFY application is a "
            "follow-up (ATLAS-27 applies ADD/PROPOSE_ARCHIVE; CONFLICT is "
            "refused). Re-plan or await the MODIFY-apply ticket."
        )
    conflicts = [entry for entry in diff.entries if entry.entry_type == CONFLICT]
    if conflicts:
        names = ", ".join(sorted(entry.identity for entry in conflicts))
        raise ConflictRefusalError(
            f"the diff touches frozen ticket(s) ({names}); apply refuses a "
            "diff with CONFLICT entries (AT-4)"
        )

    # Decision point: nothing is written before this returns CONFIRMED.
    decision = confirm(diff)
    if decision is ApplyDecision.UNCONFIRMABLE:
        return ApplyResult("unconfirmed", plan_run, diff)
    if decision is ApplyDecision.REJECTED:
        rejected = PlanRunRepo(database).finalize(plan_run.id, PlanRunStatus.REJECTED)
        return ApplyResult("rejected", rejected, diff)

    # Assign keys from the current counter marks (single-operator, ADR-0009).
    marks = KeyCounterRepo(database).high_water_marks()
    assignment = assign_keys(
        diff,
        ticket_high_water=marks.get(TICKET_PREFIX, 0),
        epic_high_water=marks.get(EPIC_PREFIX, 0),
    )
    new_epics, new_tickets, new_deps = _materialise(
        proposal,
        diff,
        assignment,
        product_id=plan_run.product_id,
        current_epics=current_epics,
        current_tickets=current_tickets,
        now=now,
    )

    # Full render set = current backlog minus archived, plus the new items.
    archived_epics = _archived_keys(diff, "epic")
    archived_tickets = _archived_keys(diff, "ticket")
    render_epics = [
        epic for epic in current_epics if epic.key not in archived_epics
    ] + new_epics
    render_tickets = [
        ticket for ticket in current_tickets if ticket.key not in archived_tickets
    ] + new_tickets
    render_deps = list(current_deps) + new_deps

    # Refuse to proceed on an invalid graph (ATLAS-40, dependency-engine.md
    # "Validation rules"). This runs BEFORE the apply_backlog commit seam,
    # so a GraphValidationError refusal leaves the DB and docs/planning
    # untouched. The validator reads the projected graph; ADRs are loaded
    # here only so polymorphic targets resolve rather than read as dangling.
    validate_graph(
        project_graph(
            tickets=render_tickets,
            epics=render_epics,
            adrs=ADRRepo(database).list(),
            dependencies=render_deps,
        )
    )

    header = RenderHeader(
        plan_run_id=plan_run.id,
        prompt_version=plan_run.prompt_version,
        ticket_key_high_water=assignment.ticket_high_water,
        epic_key_high_water=assignment.epic_high_water,
    )
    renders = {
        "epics.yaml": render_document("epics", render_epics, header),
        "tickets.yaml": render_document("tickets", render_tickets, header),
        "dependencies.yaml": render_document("dependencies", render_deps, header),
        "roadmap.mmd": render_roadmap(
            render_epics, render_tickets, render_deps, header
        ),
    }

    # Write temp renders, commit the DB (single commit point), then move.
    target_dir.mkdir(parents=True, exist_ok=True)
    temps = {name: target_dir / _temp_name(name, plan_run.id) for name in RENDER_FILES}
    for name, temp_path in temps.items():
        temp_path.write_text(renders[name], encoding="utf-8")
    try:
        apply_backlog(
            database,
            plan_run_id=plan_run.id,
            new_epics=new_epics,
            new_tickets=new_tickets,
            new_dependencies=new_deps,
            approved_by="operator",
            applied_at=now,
        )
    except Exception:
        for temp_path in temps.values():  # rollback: no durable file state
            temp_path.unlink(missing_ok=True)
        raise
    for name, temp_path in temps.items():
        os.replace(temp_path, target_dir / name)

    applied = PlanRunRepo(database).get(plan_run.id)
    assert applied is not None
    return ApplyResult("applied", applied, diff)
