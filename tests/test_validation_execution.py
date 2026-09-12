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
    CandidateIdentity,
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


def _clean_identity(head: str = HEAD) -> CandidateIdentity:
    return CandidateIdentity(
        head=head,
        head_tree="d" * 40,
        index_clean=True,
        tracked_worktree_clean=True,
        untracked_paths=(),
        index_fingerprint="index",
    )


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
            identity_reader=lambda _repo: _clean_identity(),
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


def _git(repo: Path, *argv: str) -> str:
    result = subprocess.run(
        ["git", *argv], cwd=repo, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _candidate_repo(tmp_path: Path) -> tuple[Path, argparse.Namespace, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "validation@example.test")
    _git(repo, "config", "user.name", "Validation Test")
    (repo / "README.md").write_text("base\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "README.md").write_text("candidate\n")
    _git(repo, "commit", "-qam", "candidate")
    head = _git(repo, "rev-parse", "HEAD")
    args = argparse.Namespace(
        base=base,
        head=head,
        changed_path=["README.md"],
        ticket_requirement=["documentation"],
        ticket_test=[],
        expect_registry_version=None,
        json=True,
    )
    return repo, args, base, head


@pytest.mark.parametrize(
    "dirty",
    ("tracked", "staged", "deletion", "rename", "type-change", "untracked"),
)
def test_atlas_102m_real_git_preflight_refuses_influential_dirty_inputs(
    tmp_path: Path, dirty: str, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, args, _base, _head = _candidate_repo(tmp_path)
    if dirty == "tracked":
        (repo / "README.md").write_text("dirty\n")
    elif dirty == "staged":
        (repo / "README.md").write_text("staged\n")
        _git(repo, "add", "README.md")
    elif dirty == "deletion":
        (repo / "README.md").unlink()
    elif dirty == "rename":
        (repo / "README.md").rename(repo / "RENAMED.md")
    elif dirty == "type-change":
        (repo / "README.md").unlink()
        (repo / "README.md").symlink_to("missing-target")
    else:
        (repo / "influential.py").write_text("raise SystemExit(1)\n")
    calls: list[str] = []

    def runner(_cwd: Path, command: str) -> int:
        calls.append(command)
        return 0

    assert (
        run_command(
            args,
            command_runner=runner,
            repo_root=repo,
        )
        == 1
    )
    assert calls == []
    assert "candidate inputs are not clean" in capsys.readouterr().err


def test_atlas_102m_real_git_clean_candidate_reports_pre_and_post_identity(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, args, _base, head = _candidate_repo(tmp_path)

    assert run_command(args, command_runner=lambda *_: 0, repo_root=repo) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "passed"
    assert payload["initial_candidate_identity"]["head"] == head
    assert payload["final_candidate_identity"] == payload["initial_candidate_identity"]
    assert payload["candidate_identity_errors"] == []


def test_atlas_102m_human_report_names_both_candidate_observations(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, args, _base, head = _candidate_repo(tmp_path)
    args.json = False

    assert run_command(args, command_runner=lambda *_: 0, repo_root=repo) == 0

    output = capsys.readouterr().out
    assert f"Initial candidate identity: head={head}" in output
    assert f"Final candidate identity: head={head}" in output


def test_atlas_102m_restored_clean_inputs_succeed_on_fresh_invocation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, args, _base, _head = _candidate_repo(tmp_path)
    (repo / "README.md").write_text("dirty\n")

    assert run_command(args, command_runner=lambda *_: 0, repo_root=repo) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "refused"

    (repo / "README.md").write_text("candidate\n")
    assert run_command(args, command_runner=lambda *_: 0, repo_root=repo) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "passed"


@pytest.mark.parametrize("change", ("head", "index", "tracked", "untracked"))
def test_atlas_102m_mid_run_input_change_fails_but_retains_command_diagnostics(
    tmp_path: Path,
    change: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo, args, _base, _head = _candidate_repo(tmp_path)

    def runner(_cwd: Path, _command: str) -> int:
        if change == "head":
            (repo / "README.md").write_text("new head\n")
            _git(repo, "commit", "-qam", "mid-run")
        elif change == "index":
            (repo / "README.md").write_text("staged mid-run\n")
            _git(repo, "add", "README.md")
        elif change == "tracked":
            (repo / "README.md").write_text("dirty mid-run\n")
        else:
            (repo / "influential.toml").write_text("enabled = true\n")
        return 0

    assert run_command(args, command_runner=runner, repo_root=repo) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "failed"
    assert payload["lane_results"][0]["command_results"][0]["exit_code"] == 0
    assert payload["candidate_identity_errors"] == [
        "candidate identity or relevant inputs changed during execution"
    ]


def test_atlas_102m_final_identity_read_failure_cannot_report_success(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, args, _base, head = _candidate_repo(tmp_path)
    clean = CandidateIdentity(
        head=head,
        head_tree=_git(repo, "rev-parse", "HEAD^{tree}"),
        index_clean=True,
        tracked_worktree_clean=True,
        untracked_paths=(),
        index_fingerprint="index",
    )
    unreadable = CandidateIdentity(
        head=None,
        head_tree=None,
        index_clean=None,
        tracked_worktree_clean=None,
        untracked_paths=(),
        index_fingerprint=None,
        errors=("HEAD: seeded read failure",),
    )
    reads = iter((clean, unreadable))

    assert (
        run_command(
            args,
            command_runner=lambda *_: 0,
            repo_root=repo,
            identity_reader=lambda _repo: next(reads),
        )
        == 1
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "failed"
    assert payload["lane_results"][0]["command_results"][0]["exit_code"] == 0
    assert (
        "final candidate identity is unreadable" in payload["candidate_identity_errors"]
    )


def test_atlas_102m_initial_identity_read_failure_refuses_before_execution(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, args, _base, _head = _candidate_repo(tmp_path)
    calls: list[str] = []
    unreadable = CandidateIdentity(
        head=None,
        head_tree=None,
        index_clean=None,
        tracked_worktree_clean=None,
        untracked_paths=(),
        index_fingerprint=None,
        errors=("HEAD: seeded read failure",),
    )

    def runner(_cwd: Path, command: str) -> int:
        calls.append(command)
        return 0

    assert (
        run_command(
            args,
            command_runner=runner,
            repo_root=repo,
            identity_reader=lambda _repo: unreadable,
        )
        == 1
    )
    assert calls == []
    assert "initial candidate identity is unreadable" in capsys.readouterr().err


def test_atlas_102m_ignored_outputs_do_not_poison_fresh_runs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, _args, _base, _head = _candidate_repo(tmp_path)
    (repo / ".gitignore").write_text(".cache/\n")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-qm", "ignore controlled cache")
    head = _git(repo, "rev-parse", "HEAD")
    base = _git(repo, "rev-parse", "HEAD^")
    args = argparse.Namespace(
        base=base,
        head=head,
        changed_path=[".gitignore"],
        ticket_requirement=["documentation"],
        ticket_test=[],
        expect_registry_version=None,
        json=True,
    )

    def generate_cache(_cwd: Path, _command: str) -> int:
        cache = repo / ".cache"
        cache.mkdir(exist_ok=True)
        (cache / "result").write_text("generated\n")
        return 0

    assert run_command(args, command_runner=generate_cache, repo_root=repo) == 0
    capsys.readouterr()
    assert run_command(args, command_runner=lambda *_: 0, repo_root=repo) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "passed"
