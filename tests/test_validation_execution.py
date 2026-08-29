"""ATLAS-083M: governed parallel full-sweep execution."""

from __future__ import annotations

import argparse
import json
import subprocess
import threading
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from atlas.orchestration.validation_run_cli import execute_plan, run_command
from atlas.verification.validation_execution import (
    ValidationCommandResult,
    ValidationLaneResult,
    aggregate_execution_result,
    execution_groups_for_plan,
)
from atlas.verification.validation_plan import (
    FULL_SWEEP_COMMANDS,
    ValidationPlan,
    ValidationRegistry,
    calculate_validation_plan,
    load_registry_bytes,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = REPO_ROOT / "atlas" / "verification" / "validation_registry_v1.json"
BASE = "a" * 40
HEAD = "b" * 40


def _registry() -> ValidationRegistry:
    loaded = load_registry_bytes(REGISTRY_PATH.read_bytes())
    assert loaded.error is None
    assert loaded.registry is not None
    return loaded.registry


def _plan(*, full_sweep: bool = True) -> ValidationPlan:
    return calculate_validation_plan(
        base=BASE,
        head=HEAD,
        changed_paths=("README.md",),
        ticket_requirements=("full-sweep",) if full_sweep else ("documentation",),
        registry=_registry(),
        diff_verification="verified",
    )


def test_atlas_083m_full_sweep_has_exact_three_lane_topology() -> None:
    groups = execution_groups_for_plan(_plan())

    assert tuple(group.name for group in groups) == (
        "python",
        "static-governance",
        "operator-ui",
    )
    assert groups[0].commands == ("uv run pytest",)
    assert groups[1].commands == FULL_SWEEP_COMMANDS[1:6]
    assert groups[2].commands == FULL_SWEEP_COMMANDS[6:]


def test_atlas_083m_flattened_lanes_preserve_inventory_once_and_in_order() -> None:
    plan = _plan()
    groups = execution_groups_for_plan(plan)
    flattened = tuple(command for group in groups for command in group.commands)

    assert flattened == plan.commands == FULL_SWEEP_COMMANDS
    assert len(flattened) == len(set(flattened)) == 8


def test_atlas_083m_targeted_plan_remains_one_serial_group() -> None:
    plan = _plan(full_sweep=False)

    assert not plan.full_sweep
    assert execution_groups_for_plan(plan)[0].name == "selected"
    assert execution_groups_for_plan(plan)[0].commands == plan.commands


def test_atlas_083m_all_success_returns_complete_exact_candidate_evidence() -> None:
    result = execute_plan(_plan(), repo_root=REPO_ROOT, command_runner=lambda *_: 0)

    assert result.status == "passed"
    assert result.plan.base == BASE
    assert result.plan.head == HEAD
    assert result.missing_results == ()
    assert result.duplicate_results == ()
    assert result.unexpected_results == ()
    assert [
        command.command
        for lane in result.lane_results
        for command in lane.command_results
    ] == list(FULL_SWEEP_COMMANDS)
    assert all(
        command.exit_code == 0
        for lane in result.lane_results
        for command in lane.command_results
    )


def test_atlas_083m_failure_does_not_cancel_later_or_independent_commands() -> None:
    calls: dict[str, list[str]] = defaultdict(list)
    lock = threading.Lock()

    def runner(_cwd: Path, command: str) -> int:
        lane = next(
            group.name
            for group in execution_groups_for_plan(_plan())
            if command in group.commands
        )
        with lock:
            calls[lane].append(command)
        return 7 if command == "uv run ruff check ." else 0

    result = execute_plan(_plan(), repo_root=REPO_ROOT, command_runner=runner)

    assert result.status == "failed"
    assert calls == {
        "python": list(FULL_SWEEP_COMMANDS[:1]),
        "static-governance": list(FULL_SWEEP_COMMANDS[1:6]),
        "operator-ui": list(FULL_SWEEP_COMMANDS[6:]),
    }
    failed = next(
        command
        for lane in result.lane_results
        for command in lane.command_results
        if command.command == "uv run ruff check ."
    )
    assert failed.exit_code == 7


def test_atlas_083m_child_start_error_fails_closed_without_losing_inventory() -> None:
    attempted: list[str] = []
    lock = threading.Lock()

    def runner(_cwd: Path, command: str) -> int:
        with lock:
            attempted.append(command)
        if command == "uv run pytest":
            raise OSError("seeded start failure")
        return 0

    result = execute_plan(_plan(), repo_root=REPO_ROOT, command_runner=runner)

    assert result.status == "failed"
    assert set(attempted) == set(FULL_SWEEP_COMMANDS)
    python = result.lane_results[0].command_results[0]
    assert python.exit_code is None
    assert python.start_error == "OSError: seeded start failure"


def test_atlas_083m_missing_result_fails_closed() -> None:
    plan = _plan()
    groups = execution_groups_for_plan(plan)
    command_results = tuple(
        ValidationCommandResult(
            lane=group.name,
            command=command,
            exit_code=0,
            started_at="2026-08-29T00:00:00.000Z",
            finished_at="2026-08-29T00:00:01.000Z",
            duration_seconds=1.0,
        )
        for group in groups
        for command in group.commands
        if command != "uv run lint-imports"
    )
    lanes = tuple(
        ValidationLaneResult(
            name=group.name,
            command_results=tuple(
                result for result in command_results if result.lane == group.name
            ),
            started_at="2026-08-29T00:00:00.000Z",
            finished_at="2026-08-29T00:00:01.000Z",
            duration_seconds=1.0,
        )
        for group in groups
    )

    result = aggregate_execution_result(
        plan=plan,
        groups=groups,
        lane_results=lanes,
        started_at="2026-08-29T00:00:00.000Z",
        finished_at="2026-08-29T00:00:01.000Z",
        duration_seconds=1.0,
    )

    assert result.status == "failed"
    assert result.missing_results == (("static-governance", "uv run lint-imports"),)


def test_atlas_083m_duplicate_and_unexpected_results_fail_closed() -> None:
    baseline = execute_plan(_plan(), repo_root=REPO_ROOT, command_runner=lambda *_: 0)
    static = baseline.lane_results[1]
    duplicate = static.command_results[0]
    unexpected = ValidationCommandResult(
        lane=static.name,
        command="uv run invented-check",
        exit_code=0,
        started_at=static.started_at,
        finished_at=static.finished_at,
        duration_seconds=0.0,
    )
    lanes = (
        baseline.lane_results[0],
        ValidationLaneResult(
            name=static.name,
            command_results=(*static.command_results, duplicate, unexpected),
            started_at=static.started_at,
            finished_at=static.finished_at,
            duration_seconds=static.duration_seconds,
        ),
        baseline.lane_results[2],
    )

    result = aggregate_execution_result(
        plan=baseline.plan,
        groups=baseline.groups,
        lane_results=lanes,
        started_at=baseline.started_at,
        finished_at=baseline.finished_at,
        duration_seconds=baseline.duration_seconds,
    )

    assert result.status == "failed"
    assert result.duplicate_results == (("static-governance", "uv run ruff check ."),)
    assert result.unexpected_results == (
        ("static-governance", "uv run invented-check"),
    )


def test_atlas_083m_three_lanes_actually_start_concurrently() -> None:
    first_commands = {group.commands[0] for group in execution_groups_for_plan(_plan())}
    barrier = threading.Barrier(3)

    def runner(_cwd: Path, command: str) -> int:
        if command in first_commands:
            barrier.wait(timeout=5)
        return 0

    result = execute_plan(_plan(), repo_root=REPO_ROOT, command_runner=runner)

    assert result.status == "passed"
    assert barrier.n_waiting == 0


class ExactDiffGit:
    def __call__(
        self,
        cwd: Path,
        argv: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        assert cwd == REPO_ROOT
        assert argv[0] == "diff"
        return subprocess.CompletedProcess(
            ["git", *argv], 0, stdout="M\0README.md\0", stderr=""
        )


def test_atlas_083m_cli_runs_the_proved_exact_candidate(
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = argparse.Namespace(
        base=BASE,
        head=HEAD,
        changed_path=["README.md"],
        ticket_requirement=["full-sweep"],
        ticket_test=[],
        expect_registry_version=None,
        json=True,
    )

    assert (
        run_command(
            args,
            git_runner=ExactDiffGit(),
            command_runner=lambda _cwd, _command: 0,
            repo_root=REPO_ROOT,
            checkout_head=HEAD,
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "passed"
    assert payload["plan"]["base"] == BASE
    assert payload["plan"]["head"] == HEAD
    assert len(payload["lane_results"]) == 3


def test_atlas_083m_execution_refuses_a_checkout_head_mismatch() -> None:
    args = argparse.Namespace(
        base=BASE,
        head=HEAD,
        changed_path=["README.md"],
        ticket_requirement=["full-sweep"],
        ticket_test=[],
        expect_registry_version=None,
        json=False,
    )
    calls: list[str] = []

    def runner(_cwd: Path, command: str) -> int:
        calls.append(command)
        return 0

    assert (
        run_command(
            args,
            git_runner=ExactDiffGit(),
            command_runner=runner,
            repo_root=REPO_ROOT,
            checkout_head="c" * 40,
        )
        == 1
    )
    assert calls == []
