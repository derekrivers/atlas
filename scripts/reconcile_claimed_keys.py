"""Reconcile the six hand-claimed ATLAS-187..192 keys (ATLAS-029M).

The first zero-model PlanRun advances the unused ticket range to 186, then
uses normal apply assignment to mint one open epic and tickets 187..192 in one
proposal. Repository-only post-processing records the delivered shape; a second
zero-model PlanRun uses normal apply to publish the final planning renders.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from atlas.core.enums import ActorType, RiskLevel
from atlas.core.models import (
    Epic,
    EpicStatus,
    PlanRun,
    PlanRunStatus,
    Ticket,
    TicketDependency,
    TicketStatus,
)
from atlas.core.models.ticket import TicketType
from atlas.core.yaml_io import RenderHeader, parse_document, render_document
from atlas.planning.apply import DEFAULT_INBOX_DIR, ApplyDecision, run_apply
from atlas.planning.ingestion import (
    collect_inbox_documents,
    collect_input_documents,
    collect_processed_documents,
)
from atlas.planning.mermaid import render_roadmap
from atlas.planning.pipeline import _echo_backlog_proposal, _next_key_hint
from atlas.planning.proposal import Proposal, ProposalEpic, ProposalTicket
from atlas.planning.reconciler import (
    DEFAULT_SIMILARITY_THRESHOLD,
    Backlog,
    reconcile,
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

TICKET_PREFIX = "ATLAS"
PRE_ASSIGN_HIGH_WATER = 186
TARGET_HIGH_WATER = 192
CLAIMED_KEYS = tuple(f"ATLAS-{number}" for number in range(187, 193))
MINT_PLAN_RUN_ID = uuid5(NAMESPACE_URL, "atlas:namespace-reconciliation:mint:187-192")
RENDER_PLAN_RUN_ID = uuid5(
    NAMESPACE_URL, "atlas:namespace-reconciliation:render:187-192"
)
MINT_PROMPT_VERSION = "key-reconciliation-mint-v1"
RENDER_PROMPT_VERSION = "key-reconciliation-render-v1"
CREATED_BY = "operator-key-reconciliation"
EPIC_TITLE = "Operator API (Read Surface)"
EPIC_DESCRIPTION = (
    "Read-only HTTP projection surface over the review queue and ticket board; "
    "opened retroactively by the ATLAS-187..192 key reconciliation."
)
CATCH_UP_KEYS = frozenset(
    {
        "ATLAS-171",
        "ATLAS-173",
        "ATLAS-174",
        "ATLAS-175",
        "ATLAS-176",
        "ATLAS-177",
        "ATLAS-178",
    }
)
CATCH_UP_FIELDS = frozenset(
    {
        "status",
        "external_linear_id",
        "linear_synced_at",
        "last_observed_linear_state_id",
        "status_entered_at",
        "lesson_extraction_attempted_at",
    }
)


class ReconciliationError(RuntimeError):
    """The bounded repair cannot safely proceed."""


class RenderDriftError(ReconciliationError):
    """Live store and committed planning renders differ outside the incident."""


class ExistingRecordMismatchError(ReconciliationError):
    """A claimed key already exists with an incompatible identity."""


@dataclass(frozen=True)
class ClaimedTicket:
    key: str
    pr: int
    title: str
    merged_at: str
    ticket_type: TicketType
    relevant_docs: tuple[str, ...]
    source_anchor: str
    component: str


CLAIMED_TICKETS = (
    ClaimedTicket(
        "ATLAS-187",
        223,
        "atlas.api skeleton and base infrastructure",
        "2026-07-20T18:28:12+00:00",
        TicketType.INFRASTRUCTURE,
        ("ARCHITECTURE.md", "atlas/api/app.py", "atlas/api/dependencies.py"),
        "ROADMAP.md#roadmapmd",
        "api",
    ),
    ClaimedTicket(
        "ATLAS-188",
        224,
        "review-queue coordinating service",
        "2026-07-20T19:07:50+00:00",
        TicketType.FEATURE,
        ("atlas/orchestration/review_queue.py",),
        "ROADMAP.md#roadmapmd",
        "orchestration",
    ),
    ClaimedTicket(
        "ATLAS-189",
        225,
        "GET /api/reviews endpoint",
        "2026-07-20T19:27:18+00:00",
        TicketType.FEATURE,
        ("atlas/api/routers/reviews.py", "atlas/api/schemas.py"),
        "ROADMAP.md#roadmapmd",
        "api",
    ),
    ClaimedTicket(
        "ATLAS-190",
        226,
        "GET /api/tickets board endpoint",
        "2026-07-20T19:52:59+00:00",
        TicketType.FEATURE,
        ("atlas/api/routers/tickets.py", "atlas/api/schemas.py"),
        "ROADMAP.md#roadmapmd",
        "api",
    ),
    ClaimedTicket(
        "ATLAS-191",
        227,
        "extract HTTP presenters",
        "2026-07-20T20:11:32+00:00",
        TicketType.FEATURE,
        ("atlas/api/presenters.py",),
        "ROADMAP.md#roadmapmd",
        "api",
    ),
    ClaimedTicket(
        "ATLAS-192",
        228,
        "reconcile root documentation pointers",
        "2026-07-24T06:37:37+00:00",
        TicketType.DOCUMENTATION,
        ("README.md", "ROADMAP.md"),
        "README.md#atlas",
        "documentation",
    ),
)


def _bounded_gap(claimed: ClaimedTicket) -> str:
    return (
        f"Delivered by merged PR #{claimed.pr} before its Atlas key was minted. "
        "The ticket is backfilled as done; CI/review evidence is intentionally "
        "absent and will be ingested by OP-B through the normal evidence chain. "
        "No Linear issue exists or will be created for this delivered work."
    )


def _proposal_ticket(claimed: ClaimedTicket) -> ProposalTicket:
    return ProposalTicket(
        key=None,
        epic_ref="new_epic:0",
        title=claimed.title,
        objective=f"Record the delivered {claimed.title} work in Atlas.",
        context=_bounded_gap(claimed),
        ticket_type=claimed.ticket_type,
        risk_level=RiskLevel.LOW,
        priority=0,
        relevant_docs=list(claimed.relevant_docs),
        tags=["operator-api", "key-reconciliation-backfill"],
        component=claimed.component,
        acceptance_criteria=[f"Merged PR #{claimed.pr} is recorded for this ticket."],
        non_goals=[
            "Do not backfill evidence in this reconciliation.",
            "Do not create or sync a Linear issue for this delivered work.",
        ],
        implementation_notes=[
            "Backfilled after a claimed-ahead key namespace incident."
        ],
        test_requirements=[f"Use the test coverage delivered by PR #{claimed.pr}."],
        documentation_requirements=[],
        definition_of_done=[f"PR #{claimed.pr} is merged on main."],
        source_anchor=claimed.source_anchor,
    )


def _mint_proposal() -> Proposal:
    return Proposal(
        epics=[
            ProposalEpic(
                key=None,
                title=EPIC_TITLE,
                description=EPIC_DESCRIPTION,
                objective=EPIC_DESCRIPTION,
                priority=0,
                risk_level=RiskLevel.LOW,
                source_anchor="ROADMAP.md#roadmapmd",
            )
        ],
        tickets=[_proposal_ticket(claimed) for claimed in CLAIMED_TICKETS],
        dependencies=[],
        planner_notes=[
            "ATLAS-029M bounded key-namespace reconciliation; zero model calls."
        ],
    )


def _desired_ticket(existing: Ticket, claimed: ClaimedTicket, epic_id: UUID) -> Ticket:
    merged_at = datetime.fromisoformat(claimed.merged_at)
    proposal = _proposal_ticket(claimed)
    return existing.model_copy(
        update={
            "epic_id": epic_id,
            "title": proposal.title,
            "objective": proposal.objective,
            "context": proposal.context,
            "status": TicketStatus.DONE,
            "ticket_type": proposal.ticket_type,
            "risk_level": proposal.risk_level,
            "priority": proposal.priority,
            "relevant_docs": proposal.relevant_docs,
            "acceptance_criteria": proposal.acceptance_criteria,
            "non_goals": proposal.non_goals,
            "implementation_notes": proposal.implementation_notes,
            "test_requirements": proposal.test_requirements,
            "documentation_requirements": proposal.documentation_requirements,
            "definition_of_done": proposal.definition_of_done,
            "estimated_effort": None,
            "external_linear_id": None,
            "external_github_issue_id": str(claimed.pr),
            "tags": proposal.tags,
            "component": proposal.component,
            "linear_synced_at": None,
            "last_observed_linear_state_id": None,
            "status_entered_at": merged_at,
            "review_cycle_count": 0,
            "lesson_extraction_attempted_at": None,
            "source_anchor": proposal.source_anchor,
            "created_by_type": ActorType.HUMAN,
            "created_by_id": CREATED_BY,
            "created_at": merged_at,
            "updated_at": merged_at,
            "completed_at": merged_at,
        }
    )


def expected_tickets(database: Database) -> tuple[Ticket, ...]:
    epic = next(
        (
            candidate
            for candidate in EpicRepo(database).list()
            if candidate.title == EPIC_TITLE
        ),
        None,
    )
    if epic is None:
        return ()
    tickets = TicketRepo(database)
    result = []
    for claimed in CLAIMED_TICKETS:
        existing = tickets.get_by_key(claimed.key)
        if existing is None:
            return ()
        result.append(_desired_ticket(existing, claimed, epic.id))
    return tuple(result)


def _header_from_render(text: str) -> RenderHeader:
    values: dict[str, str] = {}
    for line in text.splitlines()[:5]:
        if line.startswith("# ") and ": " in line:
            name, value = line[2:].split(": ", 1)
            values[name] = value
    try:
        return RenderHeader(
            plan_run_id=values["plan_run_id"],
            prompt_version=values["prompt_version"],
            ticket_key_high_water=int(values["ticket_key_high_water"]),
            epic_key_high_water=int(values["epic_key_high_water"]),
        )
    except (KeyError, ValueError) as error:
        raise RenderDriftError(
            "committed tickets.yaml has no valid apply header"
        ) from error


def _render_set(
    database: Database, header: RenderHeader, *, omit_incident: bool = False
) -> dict[str, str]:
    epics = EpicRepo(database).list()
    tickets = TicketRepo(database).list()
    if omit_incident:
        incident_epic_ids = {epic.id for epic in epics if epic.title == EPIC_TITLE}
        epics = [epic for epic in epics if epic.id not in incident_epic_ids]
        tickets = [ticket for ticket in tickets if ticket.key not in CLAIMED_KEYS]
    dependencies = TicketDependencyRepo(database).list()
    return {
        "epics.yaml": render_document("epics", epics, header),
        "tickets.yaml": render_document("tickets", tickets, header),
        "dependencies.yaml": render_document("dependencies", dependencies, header),
        "roadmap.mmd": render_roadmap(epics, tickets, dependencies, header),
    }


def assert_no_unrelated_render_drift(repo_root: Path, database: Database) -> None:
    """A-3/A-5: admit only the enumerated post-apply operational catch-up."""
    planning = repo_root / "docs" / "planning"
    committed_epics = parse_document(
        Epic, (planning / "epics.yaml").read_text(encoding="utf-8"), "epics"
    )
    committed_tickets = parse_document(
        Ticket, (planning / "tickets.yaml").read_text(encoding="utf-8"), "tickets"
    )
    committed_dependencies = parse_document(
        TicketDependency,
        (planning / "dependencies.yaml").read_text(encoding="utf-8"),
        "dependencies",
    )
    live_epics = [
        epic for epic in EpicRepo(database).list() if epic.title != EPIC_TITLE
    ]
    live_tickets = [
        ticket
        for ticket in TicketRepo(database).list()
        if ticket.key not in CLAIMED_KEYS
    ]
    live_dependencies = TicketDependencyRepo(database).list()
    committed_epics_by_key = {epic.key: epic for epic in committed_epics}
    live_epics_by_key = {epic.key: epic for epic in live_epics}
    committed_dependencies_by_id = {
        dependency.id: dependency for dependency in committed_dependencies
    }
    live_dependencies_by_id = {
        dependency.id: dependency for dependency in live_dependencies
    }
    if (
        live_epics_by_key != committed_epics_by_key
        or live_dependencies_by_id != committed_dependencies_by_id
    ):
        raise RenderDriftError(
            "live epics or dependencies differ from committed planning renders "
            "outside the enumerated operator API epic"
        )
    committed_by_key = {ticket.key: ticket for ticket in committed_tickets}
    live_by_key = {ticket.key: ticket for ticket in live_tickets}
    if set(committed_by_key) != set(live_by_key):
        raise RenderDriftError(
            "live ticket keys differ from committed planning renders outside "
            "ATLAS-187..192"
        )
    for key, committed in committed_by_key.items():
        live = live_by_key[key]
        if key not in CATCH_UP_KEYS:
            if live != committed:
                raise RenderDriftError(
                    f"live ticket {key} differs outside the A-5 catch-up allowance"
                )
            continue
        committed_shape = committed.model_dump(exclude=CATCH_UP_FIELDS)
        live_shape = live.model_dump(exclude=CATCH_UP_FIELDS)
        if committed_shape != live_shape:
            raise RenderDriftError(
                f"live ticket {key} changes fields outside the A-5 allowance"
            )
        if live.status is not TicketStatus.DONE:
            raise RenderDriftError(
                f"live ticket {key} moved away from closure-recorded done state"
            )


def _full_renders_match(repo_root: Path, database: Database, run: PlanRun) -> bool:
    marks = KeyCounterRepo(database).high_water_marks()
    header = RenderHeader(
        plan_run_id=run.id,
        prompt_version=run.prompt_version,
        ticket_key_high_water=marks.get(TICKET_PREFIX, 0),
        epic_key_high_water=marks.get("ATLAS-E", 0),
    )
    planning = repo_root / "docs" / "planning"
    return all(
        (planning / name).read_text(encoding="utf-8") == text
        for name, text in _render_set(database, header).items()
    )


def _documents(repo_root: Path) -> dict[str, str]:
    documents = (
        collect_input_documents(repo_root)
        + collect_inbox_documents(repo_root, DEFAULT_INBOX_DIR)
        + collect_processed_documents(repo_root, DEFAULT_INBOX_DIR)
    )
    return {document.path: document.sha for document in documents}


def _backlog(database: Database) -> Backlog:
    return Backlog(
        epics=EpicRepo(database).list(),
        tickets=TicketRepo(database).list(),
        dependencies=TicketDependencyRepo(database).list(),
    )


def _add_plan_run(
    repo_root: Path,
    database: Database,
    now: datetime,
    *,
    run_id: UUID,
    prompt_version: str,
    proposal: Proposal,
) -> PlanRun:
    proposed = PlanRunRepo(database).latest_proposed()
    if proposed is not None:
        if proposed.id == run_id:
            return proposed
        raise ReconciliationError(
            f"unrelated proposed PlanRun {proposed.id} exists; reconcile it first"
        )
    backlog = _backlog(database)
    diff = reconcile(
        proposal, backlog, similarity_threshold=DEFAULT_SIMILARITY_THRESHOLD
    )
    raw = json.dumps(proposal.model_dump(mode="json"), sort_keys=True)
    product = ProductRepo(database).get_by_key("ATLAS")
    if product is None:
        raise ReconciliationError("no ATLAS product exists in the target store")
    run = PlanRun(
        id=run_id,
        product_id=product.id,
        status=PlanRunStatus.PROPOSED,
        input_doc_shas=_documents(repo_root),
        model_provider="none",
        model_name="key-reconciliation",
        prompt_version=prompt_version,
        prompt_hash=hashlib.sha256(b"").hexdigest(),
        model_parameters={},
        similarity_threshold=DEFAULT_SIMILARITY_THRESHOLD,
        raw_output_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        proposal=proposal.model_dump(mode="json"),
        generation_stages=[],
        diff_summary=diff.as_summary(),
        failure_reason=None,
        approved_by=None,
        created_at=now,
        applied_at=None,
    )
    PlanRunRepo(database).add(run)
    return run


def _run_or_resume_apply(
    repo_root: Path,
    database: Database,
    now: datetime,
    *,
    run_id: UUID,
    prompt_version: str,
    proposal: Proposal,
    add_only: bool,
) -> PlanRun:
    runs = PlanRunRepo(database)
    run = runs.get(run_id)
    if run is None:
        run = _add_plan_run(
            repo_root,
            database,
            now,
            run_id=run_id,
            prompt_version=prompt_version,
            proposal=proposal,
        )
    if run.prompt_version != prompt_version:
        raise ReconciliationError(f"PlanRun {run_id} has unexpected provenance")
    if run.status is PlanRunStatus.APPLIED:
        return run
    if run.status is not PlanRunStatus.PROPOSED:
        raise ReconciliationError(
            f"PlanRun {run_id} is {run.status.value!r}, not resumable"
        )
    result = run_apply(
        repo_root=repo_root,
        database=database,
        now=now,
        confirm=lambda _: ApplyDecision.CONFIRMED,
        add_only=add_only,
    )
    if result.outcome != "applied" or result.plan_run.id != run_id:
        raise ReconciliationError("reconciliation apply did not apply its PlanRun")
    return result.plan_run


def _validate_claimed_identity(database: Database) -> None:
    tickets = TicketRepo(database)
    found = [tickets.get_by_key(key) for key in CLAIMED_KEYS]
    present = [ticket for ticket in found if ticket is not None]
    if present and len(present) != len(CLAIMED_KEYS):
        mint = PlanRunRepo(database).get(MINT_PLAN_RUN_ID)
        if mint is None:
            raise ExistingRecordMismatchError(
                "only a subset of ATLAS-187..192 exists without the mint PlanRun"
            )
    for claimed, ticket in zip(CLAIMED_TICKETS, found, strict=True):
        if ticket is not None and (
            ticket.key != claimed.key or ticket.title != claimed.title
        ):
            raise ExistingRecordMismatchError(
                f"{claimed.key} exists with an incompatible identity"
            )


def reconcile_claimed_keys(
    repo_root: Path, database: Database, *, now: datetime
) -> tuple[str, ...]:
    render_run = PlanRunRepo(database).get(RENDER_PLAN_RUN_ID)
    if render_run is not None and render_run.status is PlanRunStatus.APPLIED:
        if not _full_renders_match(repo_root, database, render_run):
            raise RenderDriftError(
                "applied reconciliation store does not match its planning renders"
            )
        return ()

    mint_run = PlanRunRepo(database).get(MINT_PLAN_RUN_ID)
    if mint_run is None:
        assert_no_unrelated_render_drift(repo_root, database)
        _validate_claimed_identity(database)
        marks = KeyCounterRepo(database).high_water_marks()
        current = marks.get(TICKET_PREFIX, 0)
        if current > PRE_ASSIGN_HIGH_WATER:
            raise ReconciliationError(
                f"ATLAS counter is {current}; expected at most {PRE_ASSIGN_HIGH_WATER}"
            )
        KeyCounterRepo(database).advance_to(TICKET_PREFIX, PRE_ASSIGN_HIGH_WATER)
    else:
        _validate_claimed_identity(database)

    before = {ticket.key for ticket in TicketRepo(database).list()}
    _run_or_resume_apply(
        repo_root,
        database,
        now,
        run_id=MINT_PLAN_RUN_ID,
        prompt_version=MINT_PROMPT_VERSION,
        proposal=_mint_proposal(),
        add_only=True,
    )
    after = {ticket.key for ticket in TicketRepo(database).list()}
    added = tuple(key for key in CLAIMED_KEYS if key in after - before)
    if not set(CLAIMED_KEYS).issubset(after):
        raise ReconciliationError("mint PlanRun did not assign ATLAS-187..192")

    epic = next(
        (
            candidate
            for candidate in EpicRepo(database).list()
            if candidate.title == EPIC_TITLE
        ),
        None,
    )
    if epic is None:
        raise ReconciliationError("mint PlanRun did not create the operator API epic")
    EpicRepo(database).set_status(epic.key, EpicStatus.IN_PROGRESS)

    tickets = TicketRepo(database)
    for claimed in CLAIMED_TICKETS:
        existing = tickets.get_by_key(claimed.key)
        if existing is None:
            raise ReconciliationError(f"minted ticket {claimed.key} is missing")
        tickets.reconcile_claimed_record(_desired_ticket(existing, claimed, epic.id))

    _run_or_resume_apply(
        repo_root,
        database,
        now,
        run_id=RENDER_PLAN_RUN_ID,
        prompt_version=RENDER_PROMPT_VERSION,
        proposal=_echo_backlog_proposal(_backlog(database)),
        add_only=False,
    )
    if _next_key_hint(database) != "ATLAS-193":
        raise ReconciliationError("reconciliation did not protect ATLAS-193")
    return added


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--db", default=None)
    args = parser.parse_args()
    added = reconcile_claimed_keys(
        args.repo.resolve(), Database(args.db), now=datetime.now(UTC)
    )
    if added:
        print(f"Reconciled {', '.join(added)}; next key is ATLAS-193.")
    else:
        print("ATLAS-187..192 already reconciled; no changes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
