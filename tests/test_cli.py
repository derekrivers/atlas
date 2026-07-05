"""ATLAS-26: `atlas plan` CLI exit codes and wiring.

Drives main() with an injected fake client and an in-memory database — no
real API call. Asserts the documented exit codes (0 success, 1 recorded
failure, 2 clean-exit precondition) and that `python -m atlas` resolves.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from planner_fakes import FAKE_IDENTITY, FakePlannerClient, RaisingPlannerClient
from test_apply import (
    _add_proposed_plan_run,
    _epic_model_kwargs,
    _seed_counter,
    _seed_two_existing_tickets,
    _ticket_model_kwargs,
)
from test_plan_pipeline import (
    NOW,
    _epic,
    _ticket,
    fixture_repo,
    fixture_repo_with_inbox,
    fresh_db,
    proposal_json,
)

from atlas.cli import (
    EXIT_OK,
    EXIT_PRECONDITION,
    EXIT_RECORDED_FAILURE,
    main,
)
from atlas.core.models import Epic, Ticket, TicketDependency
from atlas.planning.pipeline import run_plan
from atlas.storage import (
    EpicRepo,
    PlanRunRepo,
    ProductRepo,
    TicketDependencyRepo,
    TicketRepo,
)


def plan_argv(repo: Path) -> list[str]:
    return ["plan", "--repo", str(repo)]


def test_plan_success_returns_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    code = main(
        plan_argv(repo),
        database=database,
        client=FakePlannerClient(proposal_json()),
        identity=FAKE_IDENTITY,
    )
    assert code == EXIT_OK
    out = capsys.readouterr().out
    assert "Plan diff:" in out
    assert "persisted at status proposed" in out
    assert len(PlanRunRepo(database).list()) == 1


def test_recorded_failure_returns_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    code = main(
        plan_argv(repo),
        database=database,
        client=FakePlannerClient("not json"),
        identity=FAKE_IDENTITY,
    )
    assert code == EXIT_RECORDED_FAILURE
    assert "Plan failed" in capsys.readouterr().err


def test_missing_product_returns_two(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path, with_product=False)
    code = main(
        plan_argv(repo),
        database=database,
        client=FakePlannerClient(proposal_json()),
        identity=FAKE_IDENTITY,
    )
    assert code == EXIT_PRECONDITION


def test_model_error_returns_two(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    code = main(
        plan_argv(repo),
        database=database,
        client=RaisingPlannerClient(),
        identity=FAKE_IDENTITY,
    )
    assert code == EXIT_PRECONDITION
    assert PlanRunRepo(database).list() == []


def test_similarity_threshold_flag_forwarded(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    main(
        [*plan_argv(repo), "--similarity-threshold", "0.5"],
        database=database,
        client=FakePlannerClient(proposal_json()),
        identity=FAKE_IDENTITY,
    )
    assert PlanRunRepo(database).list()[0].similarity_threshold == 0.5


def test_no_subcommand_errors() -> None:
    with pytest.raises(SystemExit):
        main([])


def test_apply_yes_proceeds(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    main(
        plan_argv(repo),
        database=database,
        client=FakePlannerClient(proposal_json()),
        identity=FAKE_IDENTITY,
    )
    # apply writes to the repo's docs/planning; use a throwaway cwd repo so
    # the real tree is untouched — here the fixture repo is itself tmp.
    code = main(["apply", "--repo", str(repo), "--yes"], database=database)
    assert code == EXIT_OK
    assert (repo / "docs" / "planning" / "tickets.yaml").exists()


def test_apply_add_only_threads_and_reports_skips(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # ATLAS-109: `atlas apply --add-only --yes` applies the fixture ADD, skips
    # MODIFY + PROPOSE_ARCHIVE, leaves the backlog intact, and surfaces the D-3
    # skip banner at the confirmation gate.
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

    code = main(
        ["apply", "--repo", str(repo), "--yes", "--add-only"], database=database
    )

    assert code == EXIT_OK
    out = capsys.readouterr().out
    # D-3: the gate names what add-only declined.
    assert (
        "Add-only: skipping 1 MODIFY, 1 PROPOSE_ARCHIVE, 0 frozen-source "
        "CONFLICT, and 0 existing-to-existing dependency entries" in out
    )
    tickets = {t.key: t for t in TicketRepo(database).list()}
    # Fixture ADD minted; existing MODIFY/ARCHIVE tickets intact (not applied).
    assert any(t.title == "Follow-up from ATLAS-9" for t in tickets.values())
    assert tickets["ATLAS-1"].title == original_title
    assert "ATLAS-2" in tickets


def test_apply_add_only_banner_names_frozen_conflict_count(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # ATLAS-110 (D-3, AC-6): the confirmation banner names the frozen-source
    # CONFLICT skip count alongside the MODIFY/ARCHIVE counts. A restated frozen
    # ticket yields one frozen-source MODIFY CONFLICT; add-only skips + reports.
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    product = ProductRepo(database).get_by_key("ATLAS")
    assert product is not None
    epic = Epic(**_epic_model_kwargs(product.id, key="ATLAS-E1"))
    EpicRepo(database).add(epic)
    TicketRepo(database).add(
        Ticket(
            **(
                _ticket_model_kwargs(product.id, epic.id, key="ATLAS-1")
                | {"status": "in_progress"}
            )
        )
    )
    _seed_counter(database, tickets=1, epics=1)
    proposal = {
        "epics": [_epic(key="ATLAS-E1")],
        "tickets": [
            _ticket(key="ATLAS-1", epic_ref="ATLAS-E1", title="Restated frozen"),
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

    code = main(
        ["apply", "--repo", str(repo), "--yes", "--add-only"], database=database
    )

    assert code == EXIT_OK
    out = capsys.readouterr().out
    assert (
        "Add-only: skipping 0 MODIFY, 0 PROPOSE_ARCHIVE, 1 frozen-source "
        "CONFLICT, and 0 existing-to-existing dependency entry" in out
    )


def test_apply_without_tty_or_yes_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    main(
        plan_argv(repo),
        database=database,
        client=FakePlannerClient(proposal_json()),
        identity=FAKE_IDENTITY,
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    code = main(["apply", "--repo", str(repo)], database=database)
    assert code == EXIT_PRECONDITION
    assert not (repo / "docs" / "planning").exists()


def test_apply_no_proposed_plan_refused(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    code = main(["apply", "--repo", str(repo), "--yes"], database=database)
    assert code == EXIT_PRECONDITION


def test_python_m_atlas_resolves() -> None:
    # The module entry point imports and shows help without error.
    result = subprocess.run(
        [sys.executable, "-m", "atlas", "--help"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parent.parent,
    )
    assert result.returncode == 0
    assert "plan" in result.stdout


# --- ATLAS-111: dep-ADD scoping banner + graph-refusal exit truthfulness ------


def test_apply_add_only_banner_names_existing_dependency_count(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # ATLAS-111 (D-2, AC-5): the confirmation banner names the existing↔existing
    # dependency skip count alongside the MODIFY/ARCHIVE/CONFLICT counts. A
    # hallucinated edge between two existing non-frozen tickets yields one
    # existing-to-existing dependency skip; add-only reports it at the gate.
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
            _ticket(
                epic_ref="ATLAS-E1",
                title="Brand new capability",
                objective="new work.",
                source_anchor="docs/atlas/plan.md#new",
            ),
        ],
        "dependencies": [
            {
                "source": "ATLAS-1",
                "target": "ATLAS-2",
                "dependency_type": "depends_on",
                "reason": "Hallucinated ordering.",
            }
        ],
        "planner_notes": [],
    }
    _add_proposed_plan_run(database, repo, product.id, proposal)

    code = main(
        ["apply", "--repo", str(repo), "--yes", "--add-only"], database=database
    )

    assert code == EXIT_OK
    out = capsys.readouterr().out
    # The two existing tickets the proposal omits are 2 PROPOSE_ARCHIVE (skipped
    # and kept by add-only); the hallucinated edge between them is the 1
    # existing-to-existing dependency skip this AC pins.
    assert (
        "Add-only: skipping 0 MODIFY, 2 PROPOSE_ARCHIVE, 0 frozen-source "
        "CONFLICT, and 1 existing-to-existing dependency entr" in out
    )


def _seed_dangling_dependency_plan(tmp_path: Path) -> tuple[Path, object]:
    """Seed a store whose projected post-apply graph is invalid (a dependency
    row targeting a no-longer-stored ticket), with a proposal that merely echoes
    the backlog (empty diff). Apply must refuse at validate_graph BEFORE the
    commit — the ATLAS-40 backstop, mirrored from
    test_apply_refuses_invalid_graph_and_writes_nothing."""
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    product = ProductRepo(database).get_by_key("ATLAS")
    assert product is not None
    epic = Epic(**_epic_model_kwargs(product.id, key="ATLAS-E1"))
    EpicRepo(database).add(epic)
    ticket = Ticket(**_ticket_model_kwargs(product.id, epic.id, key="ATLAS-1"))
    TicketRepo(database).add(ticket)
    TicketDependencyRepo(database).add(
        TicketDependency(
            id=uuid4(),
            source_ticket_id=ticket.id,
            target_entity_type="ticket",
            target_entity_id=uuid4(),  # a target that is not stored: dangling
            dependency_type="depends_on",  # type: ignore[arg-type]
            reason="depends on a target that no longer exists",
            created_by_type="agent",  # type: ignore[arg-type]
            created_by_id="planner",
            created_at=NOW,
        )
    )
    _seed_counter(database, tickets=1, epics=1)
    proposal = {
        "epics": [_epic(key="ATLAS-E1")],
        "tickets": [_ticket(key="ATLAS-1", epic_ref="ATLAS-E1")],
        "dependencies": [],
        "planner_notes": [],
    }
    _add_proposed_plan_run(database, repo, product.id, proposal)
    return repo, database


def test_apply_graph_failure_returns_precondition_not_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # AC-6 / AC-7 (F-2): a GraphValidationFailed from apply is a precondition
    # refusal — EXIT_PRECONDITION (2) with the typed violations printed, NEVER a
    # raw traceback (which pre-ATLAS-111 escaped with Python exit 1, colliding
    # with the rejection code EXIT_RECORDED_FAILURE).
    repo, database = _seed_dangling_dependency_plan(tmp_path)

    code = main(["apply", "--repo", str(repo), "--yes"], database=database)  # type: ignore[arg-type]

    assert code == EXIT_PRECONDITION
    err = capsys.readouterr().err
    assert "graph validation failed:" in err
    # Nothing written: the refusal lands before the commit seam.
    assert not (repo / "docs" / "planning" / "tickets.yaml").exists()


def test_apply_rejection_returns_recorded_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # AC-7: an operator rejection at the gate returns EXIT_RECORDED_FAILURE (1) —
    # the code phase_1.sh maps to "operator rejected". With the F-2 graph catch
    # in place, exit 1 is now unambiguously a rejection (a graph crash is 2).
    repo = fixture_repo(tmp_path)
    database = fresh_db(tmp_path)
    main(
        plan_argv(repo),
        database=database,
        client=FakePlannerClient(proposal_json()),
        identity=FAKE_IDENTITY,
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *a, **k: "n")

    code = main(["apply", "--repo", str(repo)], database=database)

    assert code == EXIT_RECORDED_FAILURE
    assert not (repo / "docs" / "planning" / "tickets.yaml").exists()
