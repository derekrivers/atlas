"""Execute repository-owned validation plans with governed bounded concurrency."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from atlas.orchestration import validation_plan_cli
from atlas.verification.validation_execution import (
    ValidationCommandResult,
    ValidationExecutionGroup,
    ValidationExecutionResult,
    ValidationLaneResult,
    ValidationTopologyError,
    aggregate_execution_result,
    execution_groups_for_plan,
)
from atlas.verification.validation_plan import ValidationPlan

MAX_PARALLEL_VALIDATION_LANES = 3
CommandRunner = Callable[[Path, str], int]


def add_parser(subcommands: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    parser = subcommands.add_parser(
        "validation-run",
        help="Calculate and execute a governed exact-candidate validation plan",
    )
    validation_plan_cli.add_plan_arguments(parser)
    parser.add_argument(
        "--json", action="store_true", help="emit canonical execution evidence JSON"
    )


def add_parsers(subcommands: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Register the read-only planner and governed executor together."""

    validation_plan_cli.add_parser(subcommands)
    add_parser(subcommands)


def _run_process(cwd: Path, command: str) -> int:
    result = subprocess.run(
        ["bash", "-o", "pipefail", "-c", command],
        cwd=cwd,
        stdout=sys.stderr,
        stderr=sys.stderr,
        shell=False,
    )
    return result.returncode


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _execute_group(
    *,
    group: ValidationExecutionGroup,
    repo_root: Path,
    command_runner: CommandRunner,
    now: Callable[[], datetime],
    monotonic: Callable[[], float],
    output_lock: threading.Lock,
) -> ValidationLaneResult:
    lane_started_at = now()
    lane_started = monotonic()
    results: list[ValidationCommandResult] = []
    for command in group.commands:
        command_started_at = now()
        command_started = monotonic()
        with output_lock:
            print(f"[validation][{group.name}] START {command}", file=sys.stderr)
        exit_code: int | None = None
        start_error: str | None = None
        try:
            exit_code = command_runner(repo_root, command)
        except Exception as error:  # fail closed while preserving other evidence
            start_error = f"{type(error).__name__}: {error}"
        command_finished = monotonic()
        command_finished_at = now()
        result = ValidationCommandResult(
            lane=group.name,
            command=command,
            exit_code=exit_code,
            started_at=_iso(command_started_at),
            finished_at=_iso(command_finished_at),
            duration_seconds=max(0.0, command_finished - command_started),
            start_error=start_error,
        )
        results.append(result)
        with output_lock:
            exit_value = "start-error" if exit_code is None else str(exit_code)
            print(
                f"[validation][{group.name}] END rc={exit_value} "
                f"seconds={result.duration_seconds:.3f} {command}",
                file=sys.stderr,
            )
    lane_finished = monotonic()
    lane_finished_at = now()
    return ValidationLaneResult(
        name=group.name,
        command_results=tuple(results),
        started_at=_iso(lane_started_at),
        finished_at=_iso(lane_finished_at),
        duration_seconds=max(0.0, lane_finished - lane_started),
    )


def execute_plan(
    plan: ValidationPlan,
    *,
    repo_root: Path,
    command_runner: CommandRunner | None = None,
    now: Callable[[], datetime] = _utc_now,
    monotonic: Callable[[], float] = time.monotonic,
) -> ValidationExecutionResult:
    """Execute one plan; callers cannot supply or alter its group topology."""

    groups = execution_groups_for_plan(plan)
    runner = command_runner or _run_process
    output_lock = threading.Lock()
    execution_started_at = now()
    execution_started = monotonic()
    lane_results: list[ValidationLaneResult] = []
    worker_count = min(MAX_PARALLEL_VALIDATION_LANES, len(groups))
    with ThreadPoolExecutor(
        max_workers=worker_count, thread_name_prefix="atlas-validation"
    ) as executor:
        futures = {
            group.name: executor.submit(
                _execute_group,
                group=group,
                repo_root=repo_root,
                command_runner=runner,
                now=now,
                monotonic=monotonic,
                output_lock=output_lock,
            )
            for group in groups
        }
        for group in groups:
            try:
                lane_results.append(futures[group.name].result())
            except Exception as error:  # an executor/lane crash is explicit evidence
                failed_at = now()
                lane_results.append(
                    ValidationLaneResult(
                        name=group.name,
                        command_results=(),
                        started_at=_iso(failed_at),
                        finished_at=_iso(failed_at),
                        duration_seconds=0.0,
                        executor_error=f"{type(error).__name__}: {error}",
                    )
                )
    execution_finished = monotonic()
    execution_finished_at = now()
    return aggregate_execution_result(
        plan=plan,
        groups=groups,
        lane_results=tuple(lane_results),
        started_at=_iso(execution_started_at),
        finished_at=_iso(execution_finished_at),
        duration_seconds=max(0.0, execution_finished - execution_started),
    )


def _checkout_head(repo_root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            shell=False,
        )
    except OSError:
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def run_command(
    args: argparse.Namespace,
    *,
    git_runner: validation_plan_cli.GitRunner | None = None,
    command_runner: CommandRunner | None = None,
    repo_root: Path | None = None,
    checkout_head: str | None = None,
) -> int:
    root = repo_root or Path.cwd()
    plan = validation_plan_cli.build_plan(args, git_runner=git_runner, repo_root=root)
    actual_head = checkout_head if checkout_head is not None else _checkout_head(root)
    precondition_errors: list[str] = []
    if plan.base is None or plan.head is None:
        precondition_errors.append("plan does not contain exact base/head identities")
    if plan.diff_verification != "verified":
        precondition_errors.append(
            f"changed-path proof is {plan.diff_verification}, not verified"
        )
    if actual_head != plan.head:
        precondition_errors.append(
            "checked-out HEAD does not match the planned candidate head"
        )
    if precondition_errors:
        for error in precondition_errors:
            print(f"validation-run refused: {error}", file=sys.stderr)
        return 1
    try:
        result = execute_plan(plan, repo_root=root, command_runner=command_runner)
    except ValidationTopologyError as error:
        print(f"validation-run refused: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(
            json.dumps(
                result.payload(),
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
        )
    else:
        print(result.human_text(), end="")
    return 0 if result.status == "passed" else 1


def run_routed_command(
    args: argparse.Namespace,
    *,
    git_runner: validation_plan_cli.GitRunner | None = None,
    repo_root: Path | None = None,
) -> int:
    """Keep top-level CLI dispatch thin while preserving the read-only plan path."""

    if args.command == "validation-plan":
        return validation_plan_cli.run_command(
            args, git_runner=git_runner, repo_root=repo_root
        )
    return run_command(args, git_runner=git_runner, repo_root=repo_root)


__all__: Sequence[str] = (
    "MAX_PARALLEL_VALIDATION_LANES",
    "CommandRunner",
    "add_parser",
    "add_parsers",
    "execute_plan",
    "run_command",
    "run_routed_command",
)
