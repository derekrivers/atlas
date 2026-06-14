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
from test_plan_pipeline import fixture_repo, fresh_db, proposal_json

from atlas.cli import (
    EXIT_OK,
    EXIT_PRECONDITION,
    EXIT_RECORDED_FAILURE,
    main,
)
from atlas.storage import PlanRunRepo


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
