"""ATLAS-26: `atlas plan` CLI exit codes and wiring.

Drives main() with an injected fake client and an in-memory database — no
real API call. Asserts the documented exit codes (0 success, 1 recorded
failure, 2 clean-exit precondition) and that `python -m atlas` resolves.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from planner_fakes import FAKE_IDENTITY, FakePlannerClient, RaisingPlannerClient
from test_apply import (
    _add_proposed_plan_run,
    _epic_model_kwargs,
    _seed_counter,
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
from atlas.core.models import Epic, Ticket
from atlas.planning.pipeline import run_plan
from atlas.storage import EpicRepo, PlanRunRepo, ProductRepo, TicketRepo


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
        "Add-only: skipping 1 MODIFY, 1 PROPOSE_ARCHIVE, and 0 frozen-source "
        "CONFLICT entries" in out
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
        "Add-only: skipping 0 MODIFY, 0 PROPOSE_ARCHIVE, and 1 frozen-source "
        "CONFLICT entry" in out
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
