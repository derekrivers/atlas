"""ATLAS-27: the `atlas apply` pipeline.

Full apply end to end, the gap-1 atomicity + post-commit recovery, and the
AT-2/AT-4/AT-5/AT-6 shadows. Every test drives apply with an injected
confirm callback and writes renders to a tmp planning dir (the repo's
docs/planning/ stays empty). PlanRuns are created by the real plan
pipeline (so the proposal is persisted exactly as production does).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from planner_fakes import FAKE_IDENTITY, FakePlannerClient
from test_plan_pipeline import (
    INBOX_PATH,
    INBOX_STUB,
    NOW,
    PLAN_MD,
    PRODUCT_MD,
    _epic,
    _ticket,
    fixture_repo,
    fixture_repo_with_inbox,
    fresh_db,
    git,
    make_repo,
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
    ApplyResult,
    ConflictRefusalError,
    NoProposedPlanError,
    StalePlanError,
    UnsupportedDiffError,
    run_apply,
)
from atlas.planning.ingestion import collect_input_documents
from atlas.planning.pipeline import run_plan, run_stubs_only_plan
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


def apply(
    repo: Path,
    database: Database,
    pdir: Path,
    confirm: Callable[[Any], ApplyDecision] = confirmed,
    add_only: bool = False,
) -> ApplyResult:
    return run_apply(
        repo_root=repo,
        database=database,
        now=APPLY_NOW,
        confirm=confirm,
        planning_dir=pdir,
        add_only=add_only,
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


def stubs_only_plan_with_inbox(tmp_path: Path) -> tuple[Path, Database]:
    """A proposed stubs-only PlanRun over the same inbox fixture (ATLAS-153):
    no model, so the stub anchors to a seeded backlog epic instead of the
    model's new_epic:0."""
    repo = make_repo(
        tmp_path,
        {
            "PRODUCT.md": PRODUCT_MD,
            "docs/atlas/plan.md": PLAN_MD,
            INBOX_PATH: INBOX_STUB.replace(
                'epic_ref: "new_epic:0"', 'epic_ref: "ATLAS-E1"'
            ),
        },
    )
    database = fresh_db(tmp_path)
    product = ProductRepo(database).get_by_key("ATLAS")
    assert product is not None
    EpicRepo(database).add(Epic(**_epic_model_kwargs(product.id, key="ATLAS-E1")))
    run_stubs_only_plan(repo_root=repo, database=database, now=NOW)
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


@pytest.mark.parametrize("mode", ["generative", "stubs_only"])
@pytest.mark.parametrize("mutate", ["change", "add", "remove"])
def test_staleness_covers_inbox(tmp_path: Path, mutate: str, mode: str) -> None:
    # AT-8: an inbox stub changed, added, or removed between plan and apply
    # reads as stale. Wrong answer: an inbox change slips past the re-check.
    # A stubs-only PlanRun (ATLAS-153) pins the same corpus + inbox domain,
    # so the re-check must refuse identically in both modes.
    if mode == "generative":
        repo, database = plan_then_with_inbox(tmp_path)
    else:
        repo, database = stubs_only_plan_with_inbox(tmp_path)
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


# --- deterministic inbox-stub promotion: apply materialises it (ATLAS-146) ---


def test_ac2_apply_materialises_the_promoted_ticket(tmp_path: Path) -> None:
    # AC-2: the promoted ADD becomes a stored ticket with a monotonic key under
    # its epic, and the stub is retired. Red: no promotion → the fixture ticket
    # is never stored (the title lookup finds nothing).
    repo, database = plan_then_with_inbox(tmp_path)

    result = apply(repo, database, planning_dir(tmp_path))
    assert result.outcome == "applied"

    tickets = TicketRepo(database).list()
    promoted = [t for t in tickets if t.title == "Follow-up from ATLAS-9"]
    assert len(promoted) == 1
    ticket = promoted[0]
    assert ticket.key is not None and ticket.key.startswith("ATLAS-")
    # Anchored to the epic the model proposed at new_epic:0.
    epics = EpicRepo(database).list()
    assert ticket.epic_id == epics[0].id
    # The consumed stub is retired by the existing lifecycle (unchanged).
    inbox = repo / "docs" / "planning" / "inbox"
    assert not (inbox / "ATLAS-9-1.md").exists()
    assert (inbox / "processed" / "ATLAS-9-1.md").exists()


# --- promotion dedup: apply collapses too, at the confirm gate (ATLAS-151) ---


def test_apply_collapses_stub_reemission_and_mints_once(tmp_path: Path) -> None:
    # apply reconstructs plan's promotion identity positionally — the trailing
    # len(inbox) proposal tickets — which is sound because the AT-5 staleness
    # re-check pins the apply-time inbox to plan time (test_staleness_covers_
    # inbox above is the load-bearing guarantee). The collapse line reaches the
    # operator's confirm diff (the eyeball check the 149/150 mint slipped
    # past), and one stub mints exactly ONE ticket. Red (pre-fix): two mints —
    # the ATLAS-149/150 and 155/158 duplicate-mint shape.
    repo = fixture_repo_with_inbox(tmp_path)
    database = fresh_db(tmp_path)
    reemission = _ticket(
        title="Follow-up from ATLAS-9",
        objective="Investigate the retry seam.",
        source_anchor=f"{INBOX_PATH}#follow-up-from-atlas-9",
    )
    run_plan(
        repo_root=repo,
        database=database,
        client=FakePlannerClient(proposal_json(tickets=[_ticket(), reemission])),
        identity=FAKE_IDENTITY,
        now=NOW,
    )

    confirmed_diffs = []

    def confirm(diff: Any) -> ApplyDecision:
        confirmed_diffs.append(diff)
        return ApplyDecision.CONFIRMED

    result = apply(repo, database, planning_dir(tmp_path), confirm)

    assert result.outcome == "applied"
    # The operator SAW the collapse at the confirm gate.
    assert [note.absorbed for note in confirmed_diffs[0].collapses] == ["new:1"]
    # Exactly one ticket minted for the stub, plus the model's own ticket —
    # never a duplicate pair.
    tickets = TicketRepo(database).list()
    assert len([t for t in tickets if t.title == "Follow-up from ATLAS-9"]) == 1
    assert len(tickets) == 2


# --- ATLAS-109: add-only apply (skip MODIFY / PROPOSE_ARCHIVE) ---------------


def _seed_counter(database: Database, *, tickets: int = 0, epics: int = 0) -> None:
    """Advance the key counter to match a directly-seeded backlog, so a new ADD
    mints the next free key instead of colliding with a seeded one."""
    with database.session() as session:
        if tickets:
            KeyCounterRepo(database).reserve(session, "ATLAS", tickets)
        if epics:
            KeyCounterRepo(database).reserve(session, "ATLAS-E", epics)
        session.commit()


def test_ac1_add_only_applies_add_and_skips_modify(tmp_path: Path) -> None:
    # AC-1: a MODIFY + ADD diff under add_only raises no UnsupportedDiffError,
    # materialises the ADD, and leaves the existing ticket byte-unchanged.
    # Red (pre-bypass): the MODIFY refusal raises before anything applies.
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    product = ProductRepo(database).get_by_key("ATLAS")
    assert product is not None
    epic = Epic(**_epic_model_kwargs(product.id, key="ATLAS-E1"))
    EpicRepo(database).add(epic)
    existing = Ticket(**_ticket_model_kwargs(product.id, epic.id, key="ATLAS-1"))
    TicketRepo(database).add(existing)
    _seed_counter(database, tickets=1, epics=1)
    original_title = existing.title
    proposal = {
        "epics": [_epic(key="ATLAS-E1")],
        "tickets": [
            _ticket(key="ATLAS-1", epic_ref="ATLAS-E1", title="Restated"),  # MODIFY
            _ticket(
                epic_ref="ATLAS-E1",
                title="Brand new capability",
                objective="new work.",
                source_anchor="docs/atlas/plan.md#new",
            ),  # ADD
        ],
        "dependencies": [],
        "planner_notes": [],
    }
    _add_proposed_plan_run(database, repo, product.id, proposal)

    result = apply(repo, database, planning_dir(tmp_path), add_only=True)

    assert result.outcome == "applied"
    tickets = {t.key: t for t in TicketRepo(database).list()}
    # The ADD minted at the next counter key.
    assert "ATLAS-2" in tickets
    assert tickets["ATLAS-2"].title == "Brand new capability"
    # The MODIFY was skipped: the existing ticket is byte-unchanged.
    assert tickets["ATLAS-1"].title == original_title


def test_ac2_add_only_skips_propose_archive(tmp_path: Path) -> None:
    # AC-2: an existing ticket the proposal omits (PROPOSE_ARCHIVE) survives in
    # BOTH the store and the render after an add-only apply — no removals.
    # Red: default apply drops the omitted ticket from the render set.
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    product = ProductRepo(database).get_by_key("ATLAS")
    assert product is not None
    epic = Epic(**_epic_model_kwargs(product.id, key="ATLAS-E1"))
    EpicRepo(database).add(epic)
    keep = Ticket(**_ticket_model_kwargs(product.id, epic.id, key="ATLAS-1"))
    omitted = Ticket(
        **(
            _ticket_model_kwargs(product.id, epic.id, key="ATLAS-2")
            | {"title": "Legacy thing", "source_anchor": "docs/atlas/plan.md#legacy"}
        )
    )
    TicketRepo(database).add(keep)
    TicketRepo(database).add(omitted)
    _seed_counter(database, tickets=2, epics=1)
    proposal = {
        "epics": [_epic(key="ATLAS-E1")],
        "tickets": [
            _ticket(key="ATLAS-1", epic_ref="ATLAS-E1"),  # echoed unchanged
            _ticket(
                epic_ref="ATLAS-E1",
                title="Brand new capability",
                objective="new work.",
                source_anchor="docs/atlas/plan.md#new",
            ),  # ADD
        ],  # ATLAS-2 omitted -> PROPOSE_ARCHIVE
        "dependencies": [],
        "planner_notes": [],
    }
    _add_proposed_plan_run(database, repo, product.id, proposal)
    pdir = planning_dir(tmp_path)

    result = apply(repo, database, pdir, add_only=True)

    assert result.outcome == "applied"
    # Present in the store...
    assert "ATLAS-2" in {t.key for t in TicketRepo(database).list()}
    # ...and in the render (add-only removes nothing).
    rendered = parse_document(Ticket, (pdir / "tickets.yaml").read_text(), "tickets")
    assert "ATLAS-2" in {t.key for t in rendered}


def test_ac3_add_only_still_refuses_conflict(tmp_path: Path) -> None:
    # ATLAS-110 repoint (ruling b): this test formerly pinned the now-inverted
    # ATLAS-109 contract (a frozen-source MODIFY CONFLICT refuses under
    # add-only). ATLAS-110 skips those; the surviving add-only refusal is an
    # *identity* CONFLICT — here a duplicate echoed key (would_have_been is
    # None). Two proposal tickets both echo ATLAS-1 -> one identity CONFLICT.
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    product = ProductRepo(database).get_by_key("ATLAS")
    assert product is not None
    epic = Epic(**_epic_model_kwargs(product.id, key="ATLAS-E1"))
    EpicRepo(database).add(epic)
    # A non-frozen (planned) ticket, so the CONFLICT is purely identity, not
    # frozen-source: only the duplicate-echoed-key ambiguity refuses.
    TicketRepo(database).add(
        Ticket(**_ticket_model_kwargs(product.id, epic.id, key="ATLAS-1"))
    )
    proposal = {
        "epics": [_epic(key="ATLAS-E1")],
        "tickets": [
            _ticket(key="ATLAS-1", epic_ref="ATLAS-E1", title="Claimant one"),
            _ticket(key="ATLAS-1", epic_ref="ATLAS-E1", title="Claimant two"),
        ],
        "dependencies": [],
        "planner_notes": [],
    }
    _add_proposed_plan_run(database, repo, product.id, proposal)

    with pytest.raises(ConflictRefusalError, match="ATLAS-1"):
        apply(repo, database, planning_dir(tmp_path), add_only=True)


def test_ac4_default_path_still_refuses_modify(tmp_path: Path) -> None:
    # AC-4: with add_only=False (the default) a MODIFY diff still refuses.
    # The default apply contract does not move.
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    product = ProductRepo(database).get_by_key("ATLAS")
    assert product is not None
    epic = Epic(**_epic_model_kwargs(product.id, key="ATLAS-E1"))
    EpicRepo(database).add(epic)
    TicketRepo(database).add(
        Ticket(**_ticket_model_kwargs(product.id, epic.id, key="ATLAS-1"))
    )
    proposal = {
        "epics": [_epic(key="ATLAS-E1")],
        "tickets": [_ticket(key="ATLAS-1", epic_ref="ATLAS-E1", title="Renamed")],
        "dependencies": [],
        "planner_notes": [],
    }
    _add_proposed_plan_run(database, repo, product.id, proposal)

    with pytest.raises(UnsupportedDiffError):
        apply(repo, database, planning_dir(tmp_path), add_only=False)


def test_ac5_add_only_reports_skips(tmp_path: Path) -> None:
    # AC-5: the apply result names the skipped MODIFY and PROPOSE_ARCHIVE
    # entries. Red: the skip properties are absent (AttributeError) before D-3.
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    product = ProductRepo(database).get_by_key("ATLAS")
    assert product is not None
    epic = Epic(**_epic_model_kwargs(product.id, key="ATLAS-E1"))
    EpicRepo(database).add(epic)
    TicketRepo(database).add(
        Ticket(**_ticket_model_kwargs(product.id, epic.id, key="ATLAS-1"))
    )
    TicketRepo(database).add(
        Ticket(
            **(
                _ticket_model_kwargs(product.id, epic.id, key="ATLAS-2")
                | {
                    "title": "Legacy thing",
                    "source_anchor": "docs/atlas/plan.md#legacy",
                }
            )
        )
    )
    _seed_counter(database, tickets=2, epics=1)
    proposal = {
        "epics": [_epic(key="ATLAS-E1")],
        "tickets": [
            _ticket(key="ATLAS-1", epic_ref="ATLAS-E1", title="Restated"),  # MODIFY
            _ticket(
                epic_ref="ATLAS-E1",
                title="Brand new capability",
                objective="new work.",
                source_anchor="docs/atlas/plan.md#new",
            ),  # ADD
        ],  # ATLAS-2 omitted -> PROPOSE_ARCHIVE
        "dependencies": [],
        "planner_notes": [],
    }
    _add_proposed_plan_run(database, repo, product.id, proposal)

    result = apply(repo, database, planning_dir(tmp_path), add_only=True)

    assert result.outcome == "applied"
    assert {e.identity for e in result.skipped_modify} == {"ATLAS-1"}
    assert {e.identity for e in result.skipped_archive} == {"ATLAS-2"}


def test_ac6_add_only_preserves_the_human_gate(tmp_path: Path) -> None:
    # AC-6: add-only still calls confirm; a REJECTED decision persists nothing.
    # Red: seed `assert 1 == 2` on the "nothing persisted" checks.
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    product = ProductRepo(database).get_by_key("ATLAS")
    assert product is not None
    epic = Epic(**_epic_model_kwargs(product.id, key="ATLAS-E1"))
    EpicRepo(database).add(epic)
    TicketRepo(database).add(
        Ticket(**_ticket_model_kwargs(product.id, epic.id, key="ATLAS-1"))
    )
    _seed_counter(database, tickets=1, epics=1)
    proposal = {
        "epics": [_epic(key="ATLAS-E1")],
        "tickets": [
            _ticket(key="ATLAS-1", epic_ref="ATLAS-E1", title="Restated"),  # MODIFY
            _ticket(
                epic_ref="ATLAS-E1",
                title="Brand new capability",
                objective="new work.",
                source_anchor="docs/atlas/plan.md#new",
            ),  # ADD
        ],
        "dependencies": [],
        "planner_notes": [],
    }
    _add_proposed_plan_run(database, repo, product.id, proposal)
    confirm_calls = 0

    def spy(diff: object) -> ApplyDecision:
        nonlocal confirm_calls
        confirm_calls += 1
        return ApplyDecision.REJECTED

    pdir = planning_dir(tmp_path)
    result = apply(repo, database, pdir, confirm=spy, add_only=True)

    assert confirm_calls == 1  # the gate was consulted
    assert result.outcome == "rejected"
    # No ADD minted, no renders: nothing persisted before CONFIRMED.
    assert {t.key for t in TicketRepo(database).list()} == {"ATLAS-1"}
    assert not pdir.exists() or not list(pdir.iterdir())


def test_ac7_smoke_unblock_partial_replan_add_only(tmp_path: Path) -> None:
    # AC-7 (integration, Smoke B Phase 1): a re-plan of a populated store with a
    # promoted inbox stub yields M MODIFY + A PROPOSE_ARCHIVE + one fixture ADD.
    # --add-only mints exactly the fixture ADD (next counter key), retires its
    # inbox stub, and leaves the M+A existing tickets intact in store and render.
    # Red: without the MODIFY bypass, apply refuses and the fixture never mints.
    repo = fixture_repo_with_inbox(tmp_path)
    database = fresh_db(tmp_path)
    product = ProductRepo(database).get_by_key("ATLAS")
    assert product is not None
    epic = Epic(**_epic_model_kwargs(product.id, key="ATLAS-E1"))
    EpicRepo(database).add(epic)
    modify_me = Ticket(**_ticket_model_kwargs(product.id, epic.id, key="ATLAS-1"))
    archive_me = Ticket(
        **(
            _ticket_model_kwargs(product.id, epic.id, key="ATLAS-2")
            | {"title": "Legacy thing", "source_anchor": "docs/atlas/plan.md#legacy"}
        )
    )
    TicketRepo(database).add(modify_me)
    TicketRepo(database).add(archive_me)
    _seed_counter(database, tickets=2, epics=1)
    original_title = modify_me.title
    # A new epic at new_epic:0 (the stub's home), the existing epic echoed, and
    # ATLAS-1 restated (MODIFY); ATLAS-2 omitted (PROPOSE_ARCHIVE). The pipeline
    # appends the committed inbox stub as the fixture ADD (epic_ref new_epic:0).
    proposal = proposal_json(
        epics=[
            _epic(
                title="Follow-up epic",
                objective="home for the stub.",
                source_anchor="docs/atlas/plan.md#backlog",
            ),
            _epic(key="ATLAS-E1"),
        ],
        tickets=[_ticket(key="ATLAS-1", epic_ref="ATLAS-E1", title="Restated")],
    )
    run_plan(
        repo_root=repo,
        database=database,
        client=FakePlannerClient(proposal),
        identity=FAKE_IDENTITY,
        now=NOW,
    )
    pdir = planning_dir(tmp_path)

    result = apply(repo, database, pdir, add_only=True)

    assert result.outcome == "applied"
    tickets = {t.key: t for t in TicketRepo(database).list()}
    # Exactly the fixture ADD minted, at the next counter key.
    promoted = [t for t in tickets.values() if t.title == "Follow-up from ATLAS-9"]
    assert len(promoted) == 1
    assert promoted[0].key == "ATLAS-3"
    # The M+A existing tickets are intact (skipped, not applied/archived).
    assert tickets["ATLAS-1"].title == original_title
    assert "ATLAS-2" in tickets
    rendered = {
        t.key
        for t in parse_document(Ticket, (pdir / "tickets.yaml").read_text(), "tickets")
    }
    assert {"ATLAS-1", "ATLAS-2", "ATLAS-3"} <= rendered
    # The consumed inbox stub is retired.
    inbox = repo / "docs" / "planning" / "inbox"
    assert not (inbox / "ATLAS-9-1.md").exists()
    assert (inbox / "processed" / "ATLAS-9-1.md").exists()
    # Skips reported (D-3): exactly the one MODIFY and the one PROPOSE_ARCHIVE.
    assert {e.identity for e in result.skipped_modify} == {"ATLAS-1"}
    assert {e.identity for e in result.skipped_archive} == {"ATLAS-2"}


# --- ATLAS-110: add-only skips frozen-source CONFLICTs ----------------------
#
# The discriminator is DiffEntry.would_have_been: a frozen-source
# MODIFY/PROPOSE_ARCHIVE conflict carries it (add-only skips — the frozen
# entity is never touched anyway); a duplicate-echoed-key or similarity-tie
# conflict leaves it None (an identity ambiguity that can implicate an ADD, so
# it refuses in every mode). Default (non-add-only) behaviour is unchanged: any
# CONFLICT still refuses.


def test_atlas110_ac1_add_only_skips_frozen_modify_conflict(tmp_path: Path) -> None:
    # AC-1: a frozen-source MODIFY CONFLICT (would_have_been=MODIFY) + an ADD,
    # under add_only, applies the ADD, does NOT raise, and leaves the frozen
    # ticket byte-unchanged. Red on pre-ATLAS-110 code: ConflictRefusalError.
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    product = ProductRepo(database).get_by_key("ATLAS")
    assert product is not None
    epic = Epic(**_epic_model_kwargs(product.id, key="ATLAS-E1"))
    EpicRepo(database).add(epic)
    frozen = Ticket(
        **(
            _ticket_model_kwargs(product.id, epic.id, key="ATLAS-1")
            | {"status": "in_progress"}
        )
    )
    TicketRepo(database).add(frozen)
    _seed_counter(database, tickets=1, epics=1)
    original_title = frozen.title
    proposal = {
        "epics": [_epic(key="ATLAS-E1")],
        "tickets": [
            # Restating a frozen ticket with a changed title -> frozen-source
            # MODIFY CONFLICT (would_have_been=MODIFY).
            _ticket(key="ATLAS-1", epic_ref="ATLAS-E1", title="Restated frozen"),
            # A genuinely new ticket -> ADD.
            _ticket(
                epic_ref="ATLAS-E1",
                title="Brand new capability",
                objective="new work.",
                source_anchor="docs/atlas/plan.md#new",
            ),
        ],
        "dependencies": [],
        "planner_notes": [],
    }
    _add_proposed_plan_run(database, repo, product.id, proposal)
    pdir = planning_dir(tmp_path)

    result = apply(repo, database, pdir, add_only=True)

    assert result.outcome == "applied"
    tickets = {t.key: t for t in TicketRepo(database).list()}
    # The frozen ticket is byte-unchanged: title, status, and updated_at intact.
    assert tickets["ATLAS-1"].title == original_title
    assert tickets["ATLAS-1"].status.value == "in_progress"
    assert tickets["ATLAS-1"].updated_at == frozen.updated_at
    # The ADD minted at the next counter key.
    assert "ATLAS-2" in tickets
    # The render keeps the frozen ticket with its original title.
    rendered = {
        t.key: t
        for t in parse_document(Ticket, (pdir / "tickets.yaml").read_text(), "tickets")
    }
    assert rendered["ATLAS-1"].title == original_title
    # The skipped frozen-source conflict is reported (D-3).
    assert {e.identity for e in result.skipped_conflict} == {"ATLAS-1"}


def test_atlas110_ac2_add_only_skips_frozen_archive_conflict(tmp_path: Path) -> None:
    # AC-2: a frozen-source PROPOSE_ARCHIVE CONFLICT (would_have_been=
    # PROPOSE_ARCHIVE) + an ADD, under add_only, mints the ADD and the frozen
    # ticket survives store + render (skipped, never archived).
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    product = ProductRepo(database).get_by_key("ATLAS")
    assert product is not None
    epic = Epic(**_epic_model_kwargs(product.id, key="ATLAS-E1"))
    EpicRepo(database).add(epic)
    frozen = Ticket(
        **(
            _ticket_model_kwargs(product.id, epic.id, key="ATLAS-1")
            | {"status": "in_progress"}
        )
    )
    TicketRepo(database).add(frozen)
    _seed_counter(database, tickets=1, epics=1)
    original_title = frozen.title
    # ATLAS-1 omitted from the proposal; frozen -> PROPOSE_ARCHIVE CONFLICT.
    proposal = {
        "epics": [_epic(key="ATLAS-E1")],
        "tickets": [
            _ticket(
                epic_ref="ATLAS-E1",
                title="Brand new capability",
                objective="new work.",
                source_anchor="docs/atlas/plan.md#new",
            ),
        ],
        "dependencies": [],
        "planner_notes": [],
    }
    _add_proposed_plan_run(database, repo, product.id, proposal)
    pdir = planning_dir(tmp_path)

    result = apply(repo, database, pdir, add_only=True)

    assert result.outcome == "applied"
    tickets = {t.key: t for t in TicketRepo(database).list()}
    # The frozen ticket survived: still present, unchanged, not archived.
    assert tickets["ATLAS-1"].title == original_title
    assert tickets["ATLAS-1"].status.value == "in_progress"
    assert "ATLAS-2" in tickets
    rendered = {
        t.key
        for t in parse_document(Ticket, (pdir / "tickets.yaml").read_text(), "tickets")
    }
    assert {"ATLAS-1", "ATLAS-2"} <= rendered
    assert {e.identity for e in result.skipped_conflict} == {"ATLAS-1"}


def test_atlas110_ac4_add_only_refuses_similarity_tie_conflict(
    tmp_path: Path,
) -> None:
    # AC-4: a similarity-tie CONFLICT (would_have_been is None) refuses under
    # add_only. Two twin existing tickets + one key-less proposal item that ties
    # both at the same score -> an ambiguous match on the proposal item.
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    product = ProductRepo(database).get_by_key("ATLAS")
    assert product is not None
    epic = Epic(**_epic_model_kwargs(product.id, key="ATLAS-E1"))
    EpicRepo(database).add(epic)
    twin_a = Ticket(
        **(
            _ticket_model_kwargs(product.id, epic.id, key="ATLAS-1")
            | {
                "title": "Identical twin ticket",
                "objective": "Same words exactly.",
                "source_anchor": "docs/atlas/plan.md#a",
            }
        )
    )
    twin_b = Ticket(
        **(
            _ticket_model_kwargs(product.id, epic.id, key="ATLAS-2")
            | {
                "title": "Identical twin ticket",
                "objective": "Same words exactly.",
                "source_anchor": "docs/atlas/plan.md#b",
            }
        )
    )
    TicketRepo(database).add(twin_a)
    TicketRepo(database).add(twin_b)
    # A key-less proposal item, same content, third distinct anchor (so neither
    # key nor anchor matches and it falls through to the similarity tie).
    proposal = {
        "epics": [_epic(key="ATLAS-E1")],
        "tickets": [
            _ticket(
                epic_ref="ATLAS-E1",
                title="Identical twin ticket",
                objective="Same words exactly.",
                source_anchor="docs/atlas/plan.md#c",
            ),
        ],
        "dependencies": [],
        "planner_notes": [],
    }
    _add_proposed_plan_run(database, repo, product.id, proposal)

    with pytest.raises(ConflictRefusalError):
        apply(repo, database, planning_dir(tmp_path), add_only=True)


def test_atlas110_ac5_default_path_refuses_both_conflict_partitions(
    tmp_path: Path,
) -> None:
    # AC-5 (A-2): with add_only=False the partition is provably inert — BOTH a
    # frozen-source CONFLICT and an identity CONFLICT still refuse, exactly as
    # today. Red-first pins the default contract against any partition drift.

    # (a) frozen-source MODIFY CONFLICT under the default path -> refuses.
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    repo_a = fixture_repo(tmp_path / "a")
    database_a = fresh_db(tmp_path / "a")
    product_a = ProductRepo(database_a).get_by_key("ATLAS")
    assert product_a is not None
    epic_a = Epic(**_epic_model_kwargs(product_a.id, key="ATLAS-E1"))
    EpicRepo(database_a).add(epic_a)
    TicketRepo(database_a).add(
        Ticket(
            **(
                _ticket_model_kwargs(product_a.id, epic_a.id, key="ATLAS-1")
                | {"status": "in_progress"}
            )
        )
    )
    proposal_a = {
        "epics": [_epic(key="ATLAS-E1")],
        "tickets": [_ticket(key="ATLAS-1", epic_ref="ATLAS-E1", title="Renamed")],
        "dependencies": [],
        "planner_notes": [],
    }
    _add_proposed_plan_run(database_a, repo_a, product_a.id, proposal_a)
    with pytest.raises(ConflictRefusalError, match="ATLAS-1"):
        apply(repo_a, database_a, planning_dir(tmp_path / "a"), add_only=False)

    # (b) identity CONFLICT (duplicate echoed key) under the default path.
    repo_b = fixture_repo(tmp_path / "b")
    database_b = fresh_db(tmp_path / "b")
    product_b = ProductRepo(database_b).get_by_key("ATLAS")
    assert product_b is not None
    epic_b = Epic(**_epic_model_kwargs(product_b.id, key="ATLAS-E1"))
    EpicRepo(database_b).add(epic_b)
    TicketRepo(database_b).add(
        Ticket(**_ticket_model_kwargs(product_b.id, epic_b.id, key="ATLAS-1"))
    )
    proposal_b = {
        "epics": [_epic(key="ATLAS-E1")],
        "tickets": [
            _ticket(key="ATLAS-1", epic_ref="ATLAS-E1", title="Claimant one"),
            _ticket(key="ATLAS-1", epic_ref="ATLAS-E1", title="Claimant two"),
        ],
        "dependencies": [],
        "planner_notes": [],
    }
    _add_proposed_plan_run(database_b, repo_b, product_b.id, proposal_b)
    with pytest.raises(ConflictRefusalError, match="ATLAS-1"):
        apply(repo_b, database_b, planning_dir(tmp_path / "b"), add_only=False)


def test_atlas110_ac6_default_result_has_no_skipped_conflict(
    tmp_path: Path,
) -> None:
    # AC-6 companion: the skipped_conflict view is empty off the add-only path
    # (a non-add-only result never carries skips). The populated case is AC-1.
    repo, database = plan_then(tmp_path)
    result = apply(repo, database, planning_dir(tmp_path))
    assert result.outcome == "applied"
    assert result.skipped_conflict == ()


def test_atlas110_ac7_smoke_unblock_replan_over_done_backlog(
    tmp_path: Path,
) -> None:
    # AC-7 (integration, Smoke B Phase 1 unblock): a re-plan over a DONE-heavy
    # backlog drifts frozen tickets (a restated DONE ticket -> frozen MODIFY
    # CONFLICT; an omitted DONE ticket -> frozen PROPOSE_ARCHIVE CONFLICT) and
    # carries one fixture ADD. --add-only mints exactly the fixture at the next
    # counter key, skips every frozen-source conflict, and leaves all frozen
    # tickets intact in store and render. Red on pre-ATLAS-110 code: apply
    # refuses on the CONFLICT wall and the fixture never mints.
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    product = ProductRepo(database).get_by_key("ATLAS")
    assert product is not None
    epic = Epic(**_epic_model_kwargs(product.id, key="ATLAS-E1"))
    EpicRepo(database).add(epic)
    drifted_done = Ticket(
        **(
            _ticket_model_kwargs(product.id, epic.id, key="ATLAS-1")
            | {"status": "done", "title": "Shipped work"}
        )
    )
    omitted_done = Ticket(
        **(
            _ticket_model_kwargs(product.id, epic.id, key="ATLAS-2")
            | {
                "status": "done",
                "title": "Also shipped",
                "source_anchor": "docs/atlas/plan.md#shipped",
            }
        )
    )
    TicketRepo(database).add(drifted_done)
    TicketRepo(database).add(omitted_done)
    _seed_counter(database, tickets=2, epics=1)
    title_1, title_2 = drifted_done.title, omitted_done.title
    proposal = {
        "epics": [_epic(key="ATLAS-E1")],
        "tickets": [
            # ATLAS-1 restated with drift -> frozen MODIFY CONFLICT.
            _ticket(key="ATLAS-1", epic_ref="ATLAS-E1", title="Shipped work (drift)"),
            # The fixture the smoke wants minted -> ADD.
            _ticket(
                epic_ref="ATLAS-E1",
                title="Delivery-loop smoke marker",
                objective="the fixture.",
                source_anchor="docs/atlas/plan.md#fixture",
            ),
            # ATLAS-2 omitted -> frozen PROPOSE_ARCHIVE CONFLICT.
        ],
        "dependencies": [],
        "planner_notes": [],
    }
    _add_proposed_plan_run(database, repo, product.id, proposal)
    pdir = planning_dir(tmp_path)

    result = apply(repo, database, pdir, add_only=True)

    assert result.outcome == "applied"
    tickets = {t.key: t for t in TicketRepo(database).list()}
    # Exactly the fixture minted, at the next counter key.
    fixture = [t for t in tickets.values() if t.title == "Delivery-loop smoke marker"]
    assert len(fixture) == 1
    assert fixture[0].key == "ATLAS-3"
    # Both frozen DONE tickets are intact — untouched by the skipped conflicts.
    assert tickets["ATLAS-1"].title == title_1
    assert tickets["ATLAS-1"].status.value == "done"
    assert tickets["ATLAS-2"].title == title_2
    assert tickets["ATLAS-2"].status.value == "done"
    # Every frozen-source conflict is skipped and reported.
    assert {e.identity for e in result.skipped_conflict} == {"ATLAS-1", "ATLAS-2"}
    # The render carries all three (two frozen survivors + the fixture).
    rendered = {
        t.key
        for t in parse_document(Ticket, (pdir / "tickets.yaml").read_text(), "tickets")
    }
    assert {"ATLAS-1", "ATLAS-2", "ATLAS-3"} <= rendered


# --- ATLAS-111: add-only scopes dependency ADDs by endpoint -------------------


def _seed_two_existing_tickets(
    database: Database, product_id: object, epic_id: object
) -> tuple[Ticket, Ticket]:
    """ATLAS-1 and ATLAS-2, both PLANNED (non-frozen): a hallucinated edge
    between them is an existing↔existing dependency, not a frozen-source
    CONFLICT (ATLAS-110's case) and not a fixture-incident edge."""
    one = Ticket(**_ticket_model_kwargs(product_id, epic_id, key="ATLAS-1"))
    two = Ticket(
        **(
            _ticket_model_kwargs(product_id, epic_id, key="ATLAS-2")
            | {"title": "Second existing", "source_anchor": "docs/atlas/plan.md#two"}
        )
    )
    TicketRepo(database).add(one)
    TicketRepo(database).add(two)
    return one, two


_NEW_ADD_TICKET = {
    "epic_ref": "ATLAS-E1",
    "title": "Brand new capability",
    "objective": "new work.",
    "source_anchor": "docs/atlas/plan.md#new",
}


def test_ac1_add_only_skips_existing_to_existing_dependency_add(
    tmp_path: Path,
) -> None:
    # AC-1 (the silent-mutation hole): a proposal hallucinates an ACYCLIC edge
    # between two existing non-frozen tickets, alongside one ADD ticket. add-only
    # mints the ticket but must NOT persist or render the edge — it would rewire
    # the existing graph. The acyclic case is the whole point: validate_graph
    # never fires, so pre-ATLAS-111 the edge applied SILENTLY.
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    product = ProductRepo(database).get_by_key("ATLAS")
    assert product is not None
    epic = Epic(**_epic_model_kwargs(product.id, key="ATLAS-E1"))
    EpicRepo(database).add(epic)
    _seed_two_existing_tickets(database, product.id, epic.id)
    _seed_counter(database, tickets=2, epics=1)
    proposal = {
        "epics": [_epic(key="ATLAS-E1")],
        "tickets": [_ticket(**_NEW_ADD_TICKET)],  # ADD -> new:0
        "dependencies": [
            {
                "source": "ATLAS-1",
                "target": "ATLAS-2",  # acyclic existing↔existing edge
                "dependency_type": "depends_on",
                "reason": "Hallucinated ordering.",
            }
        ],
        "planner_notes": [],
    }
    _add_proposed_plan_run(database, repo, product.id, proposal)

    pdir = planning_dir(tmp_path)
    result = apply(repo, database, pdir, add_only=True)

    assert result.outcome == "applied"
    # The ADD ticket minted at the next counter key...
    tickets = {t.key: t for t in TicketRepo(database).list()}
    assert tickets["ATLAS-3"].title == "Brand new capability"
    # ...but the existing↔existing edge is neither persisted nor rendered.
    assert TicketDependencyRepo(database).list() == []
    rendered_deps = parse_document(
        TicketDependency, (pdir / "dependencies.yaml").read_text(), "dependencies"
    )
    assert rendered_deps == []
    # AC-5: the skipped edge is reported on the result.
    assert {e.identity for e in result.skipped_dependency} == {"ATLAS-1 -> ATLAS-2"}


@pytest.mark.parametrize(
    ("edge_source", "edge_target"),
    [("new:0", "ATLAS-1"), ("ATLAS-1", "new:0")],
)
def test_ac2_add_only_applies_fixture_incident_dependency(
    tmp_path: Path, edge_source: str, edge_target: str
) -> None:
    # AC-2: an edge with a new:<idx> endpoint wires the fixture into the graph —
    # that IS the dep-ADD's job — so add-only still materialises it, with the
    # correct endpoint ids resolved. Non-vacuity: over-scoping the skip to ALL
    # dep ADDs drops this edge and this test goes red (shown in review).
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    product = ProductRepo(database).get_by_key("ATLAS")
    assert product is not None
    epic = Epic(**_epic_model_kwargs(product.id, key="ATLAS-E1"))
    EpicRepo(database).add(epic)
    existing = Ticket(**_ticket_model_kwargs(product.id, epic.id, key="ATLAS-1"))
    TicketRepo(database).add(existing)
    _seed_counter(database, tickets=1, epics=1)
    proposal = {
        "epics": [_epic(key="ATLAS-E1")],
        "tickets": [_ticket(**_NEW_ADD_TICKET)],  # ADD -> new:0 -> ATLAS-2
        "dependencies": [
            {
                "source": edge_source,
                "target": edge_target,
                "dependency_type": "depends_on",
                "reason": "Fixture wired into the graph.",
            }
        ],
        "planner_notes": [],
    }
    _add_proposed_plan_run(database, repo, product.id, proposal)

    result = apply(repo, database, planning_dir(tmp_path), add_only=True)

    assert result.outcome == "applied"
    new_ticket = {t.key: t for t in TicketRepo(database).list()}["ATLAS-2"]
    deps = TicketDependencyRepo(database).list()
    assert len(deps) == 1
    edge = deps[0]
    # new:0 -> the minted ticket id; ATLAS-1 -> the existing ticket id.
    expected_source = new_ticket.id if edge_source == "new:0" else existing.id
    expected_target = new_ticket.id if edge_target == "new:0" else existing.id
    assert edge.source_ticket_id == expected_source
    assert edge.target_entity_id == expected_target
    # A fixture-incident edge is applied, so it is never a reported skip.
    assert result.skipped_dependency == ()


def test_ac3_add_only_skips_hallucinated_cycle_among_existing(
    tmp_path: Path,
) -> None:
    # AC-3 (the live incident, pinned): a re-plan hallucinates the cycle shape
    # ATLAS-24 -> ATLAS-23 -> ATLAS-22 -> ATLAS-24 among existing tickets, plus
    # one ADD. Pre-ATLAS-111 add-only applied all three as ADDs and validate_graph
    # refused (GraphValidationFailed). Post: all three are existing↔existing and
    # skipped, so apply succeeds, mints the ticket, persists none of the cycle
    # edges, and the projected graph validates. Family precedent for the graph
    # refusal path is test_apply_refuses_invalid_graph_and_writes_nothing (a
    # dangling-node case); this fixture builds its own cycle from scratch.
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    product = ProductRepo(database).get_by_key("ATLAS")
    assert product is not None
    epic = Epic(**_epic_model_kwargs(product.id, key="ATLAS-E1"))
    EpicRepo(database).add(epic)
    for n in (22, 23, 24):
        TicketRepo(database).add(
            Ticket(
                **(
                    _ticket_model_kwargs(product.id, epic.id, key=f"ATLAS-{n}")
                    | {
                        "title": f"Existing {n}",
                        "source_anchor": f"docs/atlas/plan.md#t{n}",
                    }
                )
            )
        )
    _seed_counter(database, tickets=24, epics=1)
    cycle = [
        ("ATLAS-24", "ATLAS-23"),
        ("ATLAS-23", "ATLAS-22"),
        ("ATLAS-22", "ATLAS-24"),
    ]
    proposal = {
        "epics": [_epic(key="ATLAS-E1")],
        "tickets": [_ticket(**_NEW_ADD_TICKET)],
        "dependencies": [
            {
                "source": s,
                "target": t,
                "dependency_type": "depends_on",
                "reason": "Hallucinated cycle edge.",
            }
            for s, t in cycle
        ],
        "planner_notes": [],
    }
    _add_proposed_plan_run(database, repo, product.id, proposal)

    # Does not raise (pre-ATLAS-111 this raised GraphValidationFailed).
    result = apply(repo, database, planning_dir(tmp_path), add_only=True)

    assert result.outcome == "applied"
    tickets = {t.key: t for t in TicketRepo(database).list()}
    assert tickets["ATLAS-25"].title == "Brand new capability"
    # None of the cycle edges persisted; the projected graph is valid.
    assert TicketDependencyRepo(database).list() == []
    assert {e.identity for e in result.skipped_dependency} == {
        "ATLAS-24 -> ATLAS-23",
        "ATLAS-23 -> ATLAS-22",
        "ATLAS-22 -> ATLAS-24",
    }


def test_ac4_default_mode_refuses_modify_before_dependency_handling(
    tmp_path: Path,
) -> None:
    # AC-4: default (non-add-only) apply is byte-for-byte unchanged. A MODIFY
    # still raises UnsupportedDiffError BEFORE any dependency handling, so the
    # new endpoint scoping (add-only-only) never runs on the default path, and
    # nothing is written or persisted.
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    product = ProductRepo(database).get_by_key("ATLAS")
    assert product is not None
    epic = Epic(**_epic_model_kwargs(product.id, key="ATLAS-E1"))
    EpicRepo(database).add(epic)
    _seed_two_existing_tickets(database, product.id, epic.id)
    _seed_counter(database, tickets=2, epics=1)
    proposal = {
        "epics": [_epic(key="ATLAS-E1")],
        "tickets": [
            _ticket(key="ATLAS-1", epic_ref="ATLAS-E1", title="Restated"),  # MODIFY
        ],
        "dependencies": [
            {
                "source": "ATLAS-1",
                "target": "ATLAS-2",
                "dependency_type": "depends_on",
                "reason": "An edge that must never be reached.",
            }
        ],
        "planner_notes": [],
    }
    _add_proposed_plan_run(database, repo, product.id, proposal)

    with pytest.raises(UnsupportedDiffError):
        apply(repo, database, planning_dir(tmp_path), add_only=False)
    assert TicketDependencyRepo(database).list() == []
    assert not planning_dir(tmp_path).exists()


# --- terminal-dependency rule scoped to done: the 2026-07-08 apply block ------


def test_rejected_source_edge_does_not_block_add_only_apply(tmp_path: Path) -> None:
    # The line that actually bit (2026-07-08, PlanRun bede6227): a store-shaped
    # backlog holding a rejected ticket with a pre-existing outgoing edge to a
    # non-terminal one (ATLAS-108 rejected -> ATLAS-80 needs_human_decision),
    # plus an add-only diff of two new tickets. Pre-fix, validate_graph treated
    # the historical edge as a TerminalDependencyError and the apply that would
    # mint the new keys refused permanently — the rejected source is frozen, so
    # no sanctioned repair existed. Post-fix the edge is valid history: the
    # apply validates and mints both ADDs.
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    product = ProductRepo(database).get_by_key("ATLAS")
    assert product is not None
    epic = Epic(**_epic_model_kwargs(product.id, key="ATLAS-E1"))
    EpicRepo(database).add(epic)
    rejected_source = Ticket(
        **(
            _ticket_model_kwargs(product.id, epic.id, key="ATLAS-1")
            | {"status": "rejected", "title": "Rejected with an outgoing edge"}
        )
    )
    pending_target = Ticket(
        **(
            _ticket_model_kwargs(product.id, epic.id, key="ATLAS-2")
            | {
                "status": "needs_human_decision",
                "title": "Pending target",
                "source_anchor": "docs/atlas/plan.md#two",
            }
        )
    )
    TicketRepo(database).add(rejected_source)
    TicketRepo(database).add(pending_target)
    TicketDependencyRepo(database).add(
        TicketDependency(
            id=uuid4(),
            source_ticket_id=rejected_source.id,
            target_entity_type="ticket",
            target_entity_id=pending_target.id,
            dependency_type="depends_on",  # type: ignore[arg-type]
            reason="Pre-existing edge; the source was later rejected.",
            created_by_type="agent",  # type: ignore[arg-type]
            created_by_id="planner",
            created_at=NOW,
        )
    )
    _seed_counter(database, tickets=2, epics=1)
    proposal = {
        "epics": [_epic(key="ATLAS-E1")],
        "tickets": [
            _ticket(**_NEW_ADD_TICKET),  # ADD -> new:0
            _ticket(
                epic_ref="ATLAS-E1",
                title="Second new capability",
                objective="more new work.",
                source_anchor="docs/atlas/plan.md#new-2",
            ),  # ADD -> new:1
        ],
        "dependencies": [],
        "planner_notes": [],
    }
    _add_proposed_plan_run(database, repo, product.id, proposal)

    result = apply(repo, database, planning_dir(tmp_path), add_only=True)

    assert result.outcome == "applied"
    tickets = {t.key: t for t in TicketRepo(database).list()}
    assert tickets["ATLAS-3"].title == "Brand new capability"
    assert tickets["ATLAS-4"].title == "Second new capability"
    # The historical edge is untouched — valid history, not debris.
    edges = TicketDependencyRepo(database).list()
    assert len(edges) == 1
    assert edges[0].source_ticket_id == rejected_source.id
