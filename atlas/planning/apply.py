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

Add-only (ATLAS-109, opt-in via ``add_only``, default OFF): applies ADD
entries only. MODIFY entries are skipped rather than refused, and
PROPOSE_ARCHIVE entries are skipped rather than removed — so the render set
keeps the full current backlog plus the new ADDs, with no removals (a
partial re-plan must never archive real tickets). CONFLICT still refuses
(AT-4, flag or no flag). With the flag absent every path above is
byte-for-byte unchanged. The skipped MODIFY/PROPOSE_ARCHIVE entries are
reported on ``ApplyResult`` so the operator sees what add-only declined.

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
from pathlib import Path, PurePosixPath
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
from atlas.planning.ingestion import (
    collect_inbox_documents,
    collect_input_documents,
)
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
    DiffEntry,
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

# The committed follow-up inbox (ATLAS-45 producer, ATLAS-122 consumer): apply
# retires the stubs that fed a plan to the processed/ subdir. The default must
# match the producer's (atlas/pm/sync.py) and plan's (atlas/planning/pipeline.py).
DEFAULT_INBOX_DIR = Path("docs/planning/inbox")
_PROCESSED_SUBDIR = "processed"


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
    # Add-only (ATLAS-109, D-3): the result names what add-only declined so a
    # backlog-diverging re-plan is loud, never silent. Empty on the default
    # path — MODIFY there refuses before a result is built, so a non-add-only
    # result never carries skips.
    add_only: bool = False

    @property
    def skipped_modify(self) -> tuple[DiffEntry, ...]:
        """MODIFY entries add-only skipped (not applied); () unless add-only."""
        if not self.add_only:
            return ()
        return tuple(e for e in self.diff.entries if e.entry_type == MODIFY)

    @property
    def skipped_archive(self) -> tuple[DiffEntry, ...]:
        """PROPOSE_ARCHIVE entries add-only skipped (not removed)."""
        if not self.add_only:
            return ()
        return tuple(e for e in self.diff.entries if e.entry_type == PROPOSE_ARCHIVE)


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
                tags=ticket_item.tags,
                component=ticket_item.component,
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


def _retire_inbox_stubs(repo_root: Path, inbox_dir: Path, plan_run: PlanRun) -> None:
    """Move the inbox stubs that fed this plan to processed/ (ATLAS-122, D2).

    Run on BOTH the applied and the rejected outcome: both mean "considered," so
    both retire the stub (it keeps a declined follow-up from reappearing every
    plan). The stubs are the ``input_doc_shas`` paths whose immediate parent is
    ``inbox_dir`` (the corpus docs and any already under ``processed/`` are left
    alone). This is a legal ``docs/planning/`` write — apply is that tree's sole
    writer (ADR-0006/0007).

    Idempotent: a missing source (already moved) or an existing target is a skip,
    not an error, so re-running apply is safe. The move is lifecycle metadata, not
    backlog state: a crash mid-move may leave a stub in ``inbox/``, which is simply
    re-read next plan (the producer's dedup + the operator absorb it) — there is no
    separate recovery path, idempotence is enough.
    """
    inbox = inbox_dir.as_posix()
    processed_dir = repo_root / inbox_dir / _PROCESSED_SUBDIR
    for path in plan_run.input_doc_shas:
        candidate = PurePosixPath(path)
        if candidate.suffix != ".md" or candidate.parent != PurePosixPath(inbox):
            continue  # a corpus doc, or already under processed/
        source = repo_root / path
        target = processed_dir / candidate.name
        if not source.exists() or target.exists():
            continue  # already moved, or the target is present: a skip
        processed_dir.mkdir(parents=True, exist_ok=True)
        os.replace(source, target)


def run_apply(
    *,
    repo_root: Path,
    database: Database,
    now: datetime,
    confirm: Callable[[PlanDiff], ApplyDecision],
    planning_dir: Path | None = None,
    inbox_dir: Path = DEFAULT_INBOX_DIR,
    add_only: bool = False,
) -> ApplyResult:
    """Run the §2.2 apply sequence once. ``confirm`` receives the diff and
    returns the operator's decision; no write happens before CONFIRMED.

    ``add_only`` (ATLAS-109, opt-in, default OFF) applies ADD entries only:
    MODIFY entries are skipped instead of refused, PROPOSE_ARCHIVE entries are
    skipped instead of removed (the render set keeps the full backlog), and
    CONFLICT still refuses. With the flag absent this is byte-for-byte the
    default apply, MODIFY refusal included."""
    target_dir = _planning_dir(repo_root, planning_dir)
    _recover_pending_renders(target_dir, database)

    plan_run = PlanRunRepo(database).latest_proposed()
    if plan_run is None:
        raise NoProposedPlanError(
            "no proposed PlanRun to apply; run `atlas plan` first"
        )

    # Staleness re-check BEFORE confirmation (AT-5); reuses ingestion's
    # dirty-tree + SHA machinery (a dirty tree raises DirtyInputError here).
    # The fresh set folds in the inbox (ATLAS-122, D3): input_doc_shas now
    # carries the inbox stubs, so a corpus-only fresh set would always mismatch
    # and refuse every apply. An added, removed, or changed inbox stub between
    # plan and apply correctly reads as stale.
    fresh_documents = collect_input_documents(repo_root) + collect_inbox_documents(
        repo_root, inbox_dir
    )
    fresh_shas = {doc.path: doc.sha for doc in fresh_documents}
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
    # Add-only skips MODIFY (D-2) rather than refusing it; the CONFLICT refusal
    # below stays unconditional either way (AT-4).
    if not add_only and any(entry.entry_type == MODIFY for entry in diff.entries):
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
        # Rejected still means "considered": retire its inbox stubs (ATLAS-122).
        _retire_inbox_stubs(repo_root, inbox_dir, plan_run)
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
    # Add-only skips PROPOSE_ARCHIVE (D-2): the archived sets are empty, so no
    # existing ticket is removed — a partial re-plan never archives real work.
    archived_epics = set() if add_only else _archived_keys(diff, "epic")
    archived_tickets = set() if add_only else _archived_keys(diff, "ticket")
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
    # Retire the inbox stubs that fed this plan, post-commit (ATLAS-122).
    _retire_inbox_stubs(repo_root, inbox_dir, plan_run)
    return ApplyResult("applied", applied, diff, add_only=add_only)
