"""ATLAS-27: the `atlas apply` pipeline.

Full apply end to end, the gap-1 atomicity + post-commit recovery, and the
AT-2/AT-4/AT-5/AT-6 shadows. Every test drives apply with an injected
confirm callback and writes renders to a tmp planning dir (the repo's
docs/planning/ stays empty). PlanRuns are created by the real plan
pipeline (so the proposal is persisted exactly as production does).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from planner_fakes import FAKE_IDENTITY, FakePlannerClient
from test_plan_pipeline import (
    INBOX_STUB,
    NOW,
    _epic,
    _ticket,
    fixture_repo,
    fixture_repo_with_inbox,
    fresh_db,
    git,
    proposal_json,
)

from atlas.core.models import (
    Epic,
    PlanRun,
    PlanRunStatus,
    Ticket,
    TicketDependency,
)
from atlas.core.yaml_io import parse_document
from atlas.dependencies import DanglingTargetError, GraphValidationFailed
from atlas.planning.apply import (
    ApplyDecision,
    ConflictRefusalError,
    NoProposedPlanError,
    StalePlanError,
    UnsupportedDiffError,
    run_apply,
)
from atlas.planning.ingestion import collect_input_documents
from atlas.planning.pipeline import run_plan
from atlas.planning.proposal import Proposal
from atlas.planning.reconciler import DEFAULT_SIMILARITY_THRESHOLD, Backlog, reconcile
from atlas.storage import (
    Database,
    EpicRepo,
    KeyCounterRepo,
    PlanRunRepo,
    ProductRepo,
    TicketDependencyRepo,
    TicketRepo,
)

APPLY_NOW = datetime(2026, 6, 14, 13, tzinfo=UTC)


def confirmed(diff: object) -> ApplyDecision:
    return ApplyDecision.CONFIRMED


def rejected(diff: object) -> ApplyDecision:
    return ApplyDecision.REJECTED


def planning_dir(tmp_path: Path) -> Path:
    return tmp_path / "planning"


def plan_then(tmp_path: Path, proposal: str | None = None) -> tuple[Path, Database]:
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    run_plan(
        repo_root=repo,
        database=database,
        client=FakePlannerClient(proposal or proposal_json()),
        identity=FAKE_IDENTITY,
        now=NOW,
    )
    return repo, database


def apply(repo: Path, database: Database, pdir: Path, confirm=confirmed):  # type: ignore[no-untyped-def]
    return run_apply(
        repo_root=repo,
        database=database,
        now=APPLY_NOW,
        confirm=confirm,
        planning_dir=pdir,
    )


# --- full apply -------------------------------------------------------------


def test_full_apply_persists_keys_renders_and_finalises(tmp_path: Path) -> None:
    repo, database = plan_then(tmp_path)
    pdir = planning_dir(tmp_path)

    result = apply(repo, database, pdir)

    assert result.outcome == "applied"
    # Keys assigned from the counter.
    epics = EpicRepo(database).list()
    tickets = TicketRepo(database).list()
    assert {epic.key for epic in epics} == {"ATLAS-E1"}
    assert {ticket.key for ticket in tickets} == {"ATLAS-1"}
    # epic_ref resolved: the ticket points at the assigned epic.
    assert tickets[0].epic_id == epics[0].id
    # PlanRun finalised to applied by the operator.
    plan_run = PlanRunRepo(database).list()[0]
    assert plan_run.status is PlanRunStatus.APPLIED
    assert plan_run.approved_by == "operator"
    assert plan_run.applied_at == APPLY_NOW
    # All four renders written and format-valid (parse round-trips = linter).
    for name in ("epics.yaml", "tickets.yaml", "dependencies.yaml", "roadmap.mmd"):
        assert (pdir / name).exists()
    assert parse_document(Epic, (pdir / "epics.yaml").read_text(), "epics")[0].key == (
        "ATLAS-E1"
    )
    assert (
        parse_document(Ticket, (pdir / "tickets.yaml").read_text(), "tickets")[0].key
        == "ATLAS-1"
    )
    # Header carries both high-water marks.
    header = (pdir / "tickets.yaml").read_text().splitlines()
    assert "# ticket_key_high_water: 1" in header
    assert "# epic_key_high_water: 1" in header
    # No temp files left behind.
    assert not list(pdir.glob("*.tmp-*"))


def test_apply_carries_tags_and_component_into_stored_ticket(
    tmp_path: Path,
) -> None:
    # ATLAS-128: the proposal's free-form facets survive materialisation into
    # the stored Ticket (the only line that makes ATLAS-127's columns non-empty
    # in practice). Wrong answer: the values are dropped at materialisation.
    proposal = proposal_json(
        tickets=[_ticket(tags=["pm-engine", "linear-sync"], component="pm")]
    )
    repo, database = plan_then(tmp_path, proposal)

    apply(repo, database, planning_dir(tmp_path))

    ticket = TicketRepo(database).list()[0]
    assert ticket.tags == ["pm-engine", "linear-sync"]
    assert ticket.component == "pm"


def test_apply_carries_empty_facets_as_127_defaults(tmp_path: Path) -> None:
    # The "nothing fits" emission ([] / null) round-trips to the ATLAS-127
    # defaults on the stored Ticket — empty list, null component.
    proposal = proposal_json(tickets=[_ticket(tags=[], component=None)])
    repo, database = plan_then(tmp_path, proposal)

    apply(repo, database, planning_dir(tmp_path))

    ticket = TicketRepo(database).list()[0]
    assert ticket.tags == []
    assert ticket.component is None


def test_repo_docs_planning_stays_empty(tmp_path: Path) -> None:
    # Sanity: applying to a tmp planning dir never touches the repo's.
    repo, database = plan_then(tmp_path)
    apply(repo, database, planning_dir(tmp_path))
    assert not (repo / "docs" / "planning").exists() or not list(
        (repo / "docs" / "planning").iterdir()
    )


# --- confirmation -----------------------------------------------------------


def test_rejection_sets_rejected_and_writes_nothing(tmp_path: Path) -> None:
    repo, database = plan_then(tmp_path)
    pdir = planning_dir(tmp_path)
    result = apply(repo, database, pdir, confirm=rejected)
    assert result.outcome == "rejected"
    assert PlanRunRepo(database).list()[0].status is PlanRunStatus.REJECTED
    assert EpicRepo(database).list() == []
    assert not pdir.exists() or not list(pdir.iterdir())


def test_unconfirmable_writes_nothing(tmp_path: Path) -> None:
    repo, database = plan_then(tmp_path)
    pdir = planning_dir(tmp_path)
    result = run_apply(
        repo_root=repo,
        database=database,
        now=APPLY_NOW,
        confirm=lambda diff: ApplyDecision.UNCONFIRMABLE,
        planning_dir=pdir,
    )
    assert result.outcome == "unconfirmed"
    assert PlanRunRepo(database).latest_proposed() is not None  # untouched
    assert EpicRepo(database).list() == []


def test_no_proposed_plan_refused(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    with pytest.raises(NoProposedPlanError):
        apply(repo, database, planning_dir(tmp_path))


# --- AT-5: staleness before confirmation ------------------------------------


def test_stale_plan_refused_before_confirmation(tmp_path: Path) -> None:
    repo, database = plan_then(tmp_path)
    confirmed_called = False

    def spy(diff: object) -> ApplyDecision:
        nonlocal confirmed_called
        confirmed_called = True
        return ApplyDecision.CONFIRMED

    # Edit and commit an input doc: same tree cleanliness, different SHA.
    (repo / "PRODUCT.md").write_text("# Atlas\n\n## Vision\n\nChanged.\n", "utf-8")
    git(repo, "commit", "-aqm", "edit")

    with pytest.raises(StalePlanError):
        run_apply(
            repo_root=repo,
            database=database,
            now=APPLY_NOW,
            confirm=spy,
            planning_dir=planning_dir(tmp_path),
        )
    assert confirmed_called is False  # never confirmed a stale plan
    assert PlanRunRepo(database).latest_proposed() is not None  # untouched


# --- AT-4: frozen CONFLICT not applied --------------------------------------


def _add_proposed_plan_run(
    database: Database, repo: Path, product_id: object, proposal: dict[str, Any]
) -> PlanRun:
    shas = {doc.path: doc.sha for doc in collect_input_documents(repo)}
    plan_run = PlanRun(
        id=uuid4(),
        product_id=product_id,  # type: ignore[arg-type]
        status=PlanRunStatus.PROPOSED,
        input_doc_shas=shas,
        model_provider="fake",
        model_name="fake",
        prompt_version="planner-v1.1.0",
        prompt_hash="a" * 64,
        model_parameters={},
        similarity_threshold=DEFAULT_SIMILARITY_THRESHOLD,
        raw_output_hash="b" * 64,
        proposal=proposal,
        diff_summary={},
        created_at=NOW,
    )
    PlanRunRepo(database).add(plan_run)
    return plan_run


def test_conflict_diff_is_refused(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    product = ProductRepo(database).get_by_key("ATLAS")
    assert product is not None
    # Seed a frozen (in_progress) ticket under an epic.
    epic = Epic(**_epic_model_kwargs(product.id, key="ATLAS-E1"))
    EpicRepo(database).add(epic)
    ticket = Ticket(
        **_ticket_model_kwargs(product.id, epic.id, key="ATLAS-1")
        | {"status": "in_progress"}
    )
    TicketRepo(database).add(ticket)
    # A proposal echoing ATLAS-1 with a changed title -> MODIFY on a frozen
    # ticket -> CONFLICT at reconcile -> apply refuses.
    proposal = {
        "epics": [_epic(key="ATLAS-E1")],
        "tickets": [_ticket(key="ATLAS-1", epic_ref="ATLAS-E1", title="Renamed")],
        "dependencies": [],
        "planner_notes": [],
    }
    _add_proposed_plan_run(database, repo, product.id, proposal)
    with pytest.raises(ConflictRefusalError, match="ATLAS-1"):
        apply(repo, database, planning_dir(tmp_path))
    assert not planning_dir(tmp_path).exists()


def test_modify_diff_is_refused(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    product = ProductRepo(database).get_by_key("ATLAS")
    assert product is not None
    epic = Epic(**_epic_model_kwargs(product.id, key="ATLAS-E1"))
    EpicRepo(database).add(epic)
    ticket = Ticket(
        **_ticket_model_kwargs(product.id, epic.id, key="ATLAS-1")
    )  # status planned: not frozen, so a change is MODIFY
    TicketRepo(database).add(ticket)
    proposal = {
        "epics": [_epic(key="ATLAS-E1")],
        "tickets": [_ticket(key="ATLAS-1", epic_ref="ATLAS-E1", title="Renamed")],
        "dependencies": [],
        "planner_notes": [],
    }
    _add_proposed_plan_run(database, repo, product.id, proposal)
    with pytest.raises(UnsupportedDiffError):
        apply(repo, database, planning_dir(tmp_path))


# --- AT-2: re-reconcile after apply is empty --------------------------------


def test_at2_reapply_backlog_yields_empty_diff(tmp_path: Path) -> None:
    repo, database = plan_then(tmp_path)
    apply(repo, database, planning_dir(tmp_path))
    # Re-reconcile the same proposal against the now-persisted backlog.
    plan_run = PlanRunRepo(database).list()[0]
    proposal = Proposal.model_validate(plan_run.proposal)
    backlog = Backlog(
        epics=EpicRepo(database).list(),
        tickets=TicketRepo(database).list(),
        dependencies=TicketDependencyRepo(database).list(),
    )
    diff = reconcile(proposal, backlog)
    assert diff.is_empty  # no key churn, no ADD/ARCHIVE pairs


# --- AT-6: counter monotonic across cycles ----------------------------------


def test_at6_counter_never_reissues_across_cycles(tmp_path: Path) -> None:
    repo, database = plan_then(tmp_path)
    pdir = planning_dir(tmp_path)
    apply(repo, database, pdir)
    assert KeyCounterRepo(database).high_water_marks()["ATLAS"] == 1

    # Cycle 2: full-state proposal = the same ticket plus a new one. The
    # existing epic echoes its key (§3.11: existing items echo keys).
    proposal2 = proposal_json(
        epics=[_epic(key="ATLAS-E1")],
        tickets=[
            _ticket(epic_ref="ATLAS-E1"),
            _ticket(title="Second thing", objective="two more", epic_ref="ATLAS-E1"),
        ],
    )
    run_plan(
        repo_root=repo,
        database=database,
        client=FakePlannerClient(proposal2),
        identity=FAKE_IDENTITY,
        now=NOW,
    )
    apply(repo, database, pdir)
    keys = {ticket.key for ticket in TicketRepo(database).list()}
    assert keys == {"ATLAS-1", "ATLAS-2"}  # ATLAS-1 never reissued
    assert KeyCounterRepo(database).high_water_marks()["ATLAS"] == 2


# --- gap 1: atomicity + post-commit recovery --------------------------------


def test_atomicity_failure_at_commit_leaves_no_partial_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, database = plan_then(tmp_path)
    pdir = planning_dir(tmp_path)

    def boom(*args: object, **kwargs: object) -> dict[str, int]:
        raise RuntimeError("commit failed")

    monkeypatch.setattr("atlas.planning.apply.apply_backlog", boom)
    with pytest.raises(RuntimeError, match="commit failed"):
        apply(repo, database, pdir)

    # Fully pre-apply: PlanRun still proposed, no rows, counter unmoved, no
    # render or temp files survive.
    assert PlanRunRepo(database).latest_proposed() is not None
    assert EpicRepo(database).list() == []
    assert TicketRepo(database).list() == []
    assert KeyCounterRepo(database).high_water_marks() == {}
    assert not list(pdir.glob("*"))


def test_recovery_completes_move_after_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, database = plan_then(tmp_path)
    pdir = planning_dir(tmp_path)

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("crash during move")

    # Crash after the DB commit, during the temp -> final move.
    monkeypatch.setattr("atlas.planning.apply.os.replace", boom)
    with pytest.raises(RuntimeError, match="crash during move"):
        apply(repo, database, pdir)

    # DB is post-apply; renders are mid-swap (temps present, no finals).
    assert PlanRunRepo(database).latest_proposed() is None  # committed -> applied
    assert TicketRepo(database).list()  # rows committed
    assert list(pdir.glob("*.tmp-*"))
    assert not (pdir / "epics.yaml").exists()

    # Re-running apply repairs it idempotently from the committed state.
    monkeypatch.undo()
    with pytest.raises(NoProposedPlanError):
        apply(repo, database, pdir)
    for name in ("epics.yaml", "tickets.yaml", "dependencies.yaml", "roadmap.mmd"):
        assert (pdir / name).exists()
    assert not list(pdir.glob("*.tmp-*"))


# --- model-kwargs helpers for seeded backlogs -------------------------------


# --- ATLAS-40: apply refuses an invalid graph, writing nothing -------------


def test_apply_refuses_invalid_graph_and_writes_nothing(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    product = ProductRepo(database).get_by_key("ATLAS")
    assert product is not None
    # Seed an applied backlog whose dependency targets a no-longer-stored
    # entity: ATLAS-31 projects that as a present=False dangling node.
    epic = Epic(**_epic_model_kwargs(product.id, key="ATLAS-E1"))
    EpicRepo(database).add(epic)
    ticket = Ticket(**_ticket_model_kwargs(product.id, epic.id, key="ATLAS-1"))
    TicketRepo(database).add(ticket)
    missing_target = uuid4()
    TicketDependencyRepo(database).add(
        TicketDependency(
            id=uuid4(),
            source_ticket_id=ticket.id,
            target_entity_type="ticket",
            target_entity_id=missing_target,
            dependency_type="depends_on",  # type: ignore[arg-type]
            reason="depends on a target that no longer exists",
            created_by_type="agent",  # type: ignore[arg-type]
            created_by_id="planner",
            created_at=NOW,
        )
    )
    # A proposal that merely echoes the existing epic+ticket (empty diff):
    # render_deps still carries the seeded dangling dependency, so the
    # post-apply projection is invalid and apply must refuse BEFORE the
    # commit seam.
    proposal = {
        "epics": [_epic(key="ATLAS-E1")],
        "tickets": [_ticket(key="ATLAS-1", epic_ref="ATLAS-E1")],
        "dependencies": [],
        "planner_notes": [],
    }
    _add_proposed_plan_run(database, repo, product.id, proposal)

    with pytest.raises(GraphValidationFailed) as caught:
        apply(repo, database, planning_dir(tmp_path))
    assert any(
        isinstance(v, DanglingTargetError) and v.target == str(missing_target)
        for v in caught.value.violations
    )
    # Nothing written: no renders, and the PlanRun is still proposed (not
    # applied) because the refusal lands before the commit.
    assert not planning_dir(tmp_path).exists()
    assert PlanRunRepo(database).latest_proposed() is not None


def _epic_model_kwargs(product_id: object, *, key: str) -> dict[str, Any]:
    return {
        "id": uuid4(),
        "product_id": product_id,
        "key": key,
        "title": "Planning Engine",
        "description": "Generative planning.",
        "objective": "Plan and apply.",
        "status": "planned",
        "priority": 10,
        "risk_level": "medium",
        "source_anchor": "docs/atlas/plan.md#planning",
        "created_by_type": "agent",
        "created_by_id": "planner",
        "created_at": NOW,
        "updated_at": NOW,
    }


def _ticket_model_kwargs(
    product_id: object, epic_id: object, *, key: str
) -> dict[str, Any]:
    return {
        "id": uuid4(),
        "product_id": product_id,
        "epic_id": epic_id,
        "key": key,
        "title": "Build plan CLI",
        "objective": "atlas plan exists.",
        "context": "Phase 2.",
        "status": "planned",
        "ticket_type": "feature",
        "risk_level": "medium",
        "priority": 10,
        "relevant_docs": [],
        "acceptance_criteria": ["It composes the pipeline."],
        "non_goals": ["No apply."],
        "implementation_notes": [],
        "test_requirements": ["Pipeline tests."],
        "documentation_requirements": [],
        "definition_of_done": ["Tests pass."],
        "source_anchor": "docs/atlas/plan.md#backlog",
        "created_by_type": "agent",
        "created_by_id": "planner",
        "created_at": NOW,
        "updated_at": NOW,
    }


# --- follow-up inbox lifecycle: apply retires consumed stubs (ATLAS-122) -----


def plan_then_with_inbox(tmp_path: Path) -> tuple[Path, Database]:
    repo = fixture_repo_with_inbox(tmp_path)
    database = fresh_db(tmp_path)
    run_plan(
        repo_root=repo,
        database=database,
        client=FakePlannerClient(proposal_json()),
        identity=FAKE_IDENTITY,
        now=NOW,
    )
    return repo, database


def test_apply_retires_stubs_on_applied(tmp_path: Path) -> None:
    # AT-5: an applied plan's inbox stubs land under processed/ and are gone
    # from inbox/. Wrong answer: they linger and reappear next plan.
    repo, database = plan_then_with_inbox(tmp_path)
    inbox = repo / "docs" / "planning" / "inbox"
    assert (inbox / "ATLAS-9-1.md").exists()

    result = apply(repo, database, planning_dir(tmp_path))

    assert result.outcome == "applied"
    assert not (inbox / "ATLAS-9-1.md").exists()
    assert (inbox / "processed" / "ATLAS-9-1.md").exists()


def test_apply_retires_stubs_on_rejected(tmp_path: Path) -> None:
    # AT-6: rejected also means "considered" — its stubs move to processed/,
    # so a declined follow-up does not reappear every plan.
    repo, database = plan_then_with_inbox(tmp_path)
    inbox = repo / "docs" / "planning" / "inbox"

    result = apply(repo, database, planning_dir(tmp_path), confirm=rejected)

    assert result.outcome == "rejected"
    assert not (inbox / "ATLAS-9-1.md").exists()
    assert (inbox / "processed" / "ATLAS-9-1.md").exists()


def test_retire_is_idempotent(tmp_path: Path) -> None:
    # AT-7: a stub already in processed/ (or a re-run) is a skip, not an error.
    from atlas.planning.apply import DEFAULT_INBOX_DIR, _retire_inbox_stubs

    repo, database = plan_then_with_inbox(tmp_path)
    plan_run = PlanRunRepo(database).latest_proposed()
    assert plan_run is not None
    inbox = repo / "docs" / "planning" / "inbox"

    _retire_inbox_stubs(repo, DEFAULT_INBOX_DIR, plan_run)
    assert (inbox / "processed" / "ATLAS-9-1.md").exists()
    # Source now gone → a re-run is a skip, no error.
    _retire_inbox_stubs(repo, DEFAULT_INBOX_DIR, plan_run)
    # Target already present → a re-appeared source is left untouched, no clobber.
    (inbox / "ATLAS-9-1.md").write_text("re-appeared\n", encoding="utf-8")
    _retire_inbox_stubs(repo, DEFAULT_INBOX_DIR, plan_run)
    assert (inbox / "ATLAS-9-1.md").read_text(encoding="utf-8") == "re-appeared\n"
    assert (inbox / "processed" / "ATLAS-9-1.md").exists()


@pytest.mark.parametrize("mutate", ["change", "add", "remove"])
def test_staleness_covers_inbox(tmp_path: Path, mutate: str) -> None:
    # AT-8: an inbox stub changed, added, or removed between plan and apply
    # reads as stale. Wrong answer: an inbox change slips past the re-check.
    repo, database = plan_then_with_inbox(tmp_path)
    inbox = repo / "docs" / "planning" / "inbox"
    if mutate == "change":
        (inbox / "ATLAS-9-1.md").write_text(INBOX_STUB + "\nmore.\n", encoding="utf-8")
    elif mutate == "add":
        (inbox / "ATLAS-9-2.md").write_text(INBOX_STUB, encoding="utf-8")
    else:  # remove
        (inbox / "ATLAS-9-1.md").unlink()
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", f"mutate inbox: {mutate}")

    with pytest.raises(StalePlanError):
        apply(repo, database, planning_dir(tmp_path))


def test_unconfirmable_leaves_inbox_untouched(tmp_path: Path) -> None:
    # AT-9: nothing was decided, so the inbox is untouched (no move).
    repo, database = plan_then_with_inbox(tmp_path)
    inbox = repo / "docs" / "planning" / "inbox"

    result = run_apply(
        repo_root=repo,
        database=database,
        now=APPLY_NOW,
        confirm=lambda diff: ApplyDecision.UNCONFIRMABLE,
        planning_dir=planning_dir(tmp_path),
    )

    assert result.outcome == "unconfirmed"
    assert (inbox / "ATLAS-9-1.md").exists()
    assert not (inbox / "processed").exists()
