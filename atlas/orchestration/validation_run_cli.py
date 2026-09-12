"""Execute repository-owned validation plans with governed bounded concurrency."""

from __future__ import annotations

import argparse
import hashlib
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
    CandidateIdentity,
    ValidationCommandResult,
    ValidationExecutionGroup,
    ValidationExecutionResult,
    ValidationLaneResult,
    ValidationTopologyError,
    aggregate_execution_result,
    attribute_candidate_identity,
    execution_groups_for_plan,
)
from atlas.verification.validation_plan import ValidationPlan

MAX_PARALLEL_VALIDATION_LANES = 3
CommandRunner = Callable[[Path, str], int]
CandidateIdentityReader = Callable[[Path], CandidateIdentity]

_CONTROLLED_IGNORED_PREFIXES = (
    ".hypothesis/",
    ".import_linter_cache/",
    ".mypy_cache/",
    ".pytest_cache/",
    ".ruff_cache/",
    ".venv/",
    "apps/operator-ui/coverage/",
    "apps/operator-ui/dist/",
    "apps/operator-ui/node_modules/",
    "build/",
    "dist/",
)


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


def _git_identity_command(
    repo_root: Path, *argv: str
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *argv],
        cwd=repo_root,
        capture_output=True,
        shell=False,
    )


def _read_candidate_identity(repo_root: Path) -> CandidateIdentity:
    """Read candidate and relevant input identity without changing Git state."""

    errors: list[str] = []

    def run(
        label: str, *argv: str, allowed_returncodes: tuple[int, ...] = (0,)
    ) -> subprocess.CompletedProcess[bytes] | None:
        try:
            result = _git_identity_command(repo_root, *argv)
        except OSError as error:
            errors.append(f"{label}: {type(error).__name__}: {error}")
            return None
        if result.returncode not in allowed_returncodes:
            detail = result.stderr.decode(errors="replace").strip()
            errors.append(f"{label}: git exited {result.returncode}: {detail}")
            return None
        return result

    head_result = run("HEAD", "rev-parse", "--verify", "HEAD")
    tree_result = run("HEAD tree", "rev-parse", "--verify", "HEAD^{tree}")
    index_result = run(
        "index",
        "diff",
        "--no-ext-diff",
        "--cached",
        "--quiet",
        "HEAD",
        "--",
        allowed_returncodes=(0, 1),
    )
    tracked_result = run(
        "tracked worktree",
        "diff",
        "--no-ext-diff",
        "--quiet",
        "--",
        allowed_returncodes=(0, 1),
    )
    untracked_result = run(
        "untracked inputs", "ls-files", "--others", "--exclude-standard", "-z"
    )
    index_listing = run("index fingerprint", "ls-files", "--stage", "-z")
    index_flags = run("index flags", "ls-files", "-v", "-z")
    ignored_result = run(
        "ignored inputs",
        "ls-files",
        "--others",
        "--ignored",
        "--exclude-standard",
        "--directory",
        "-z",
    )

    def oid(result: subprocess.CompletedProcess[bytes] | None) -> str | None:
        return result.stdout.decode(errors="replace").strip() if result else None

    untracked = (
        tuple(
            sorted(
                path.decode(errors="surrogateescape")
                for path in untracked_result.stdout.split(b"\0")
                if path
            )
        )
        if untracked_result is not None
        else ()
    )
    flagged_entries = (
        tuple(entry for entry in index_flags.stdout.split(b"\0") if entry)
        if index_flags is not None
        else ()
    )
    hidden_tracked = tuple(
        sorted(
            entry[2:].decode(errors="surrogateescape")
            for entry in flagged_entries
            if entry[:1] != b"H"
        )
    )
    ignored_paths = (
        tuple(
            path.decode(errors="surrogateescape")
            for path in ignored_result.stdout.split(b"\0")
            if path
        )
        if ignored_result is not None
        else ()
    )
    unexpected_ignored = tuple(
        sorted(path for path in ignored_paths if not _is_controlled_ignored(path))
    )
    return CandidateIdentity(
        head=oid(head_result),
        head_tree=oid(tree_result),
        index_clean=index_result.returncode == 0 if index_result is not None else None,
        tracked_worktree_clean=(
            tracked_result.returncode == 0 if tracked_result is not None else None
        ),
        untracked_paths=untracked,
        index_fingerprint=(
            hashlib.sha256(
                index_listing.stdout + b"\0" + index_flags.stdout
            ).hexdigest()
            if index_listing is not None and index_flags is not None
            else None
        ),
        errors=tuple(errors),
        hidden_tracked_paths=hidden_tracked,
        unexpected_ignored_paths=unexpected_ignored,
    )


def _is_controlled_ignored(path: str) -> bool:
    if path.startswith(_CONTROLLED_IGNORED_PREFIXES):
        return True
    parts = path.split("/")
    return "__pycache__" in parts or any(part.endswith(".egg-info") for part in parts)


def run_command(
    args: argparse.Namespace,
    *,
    git_runner: validation_plan_cli.GitRunner | None = None,
    command_runner: CommandRunner | None = None,
    repo_root: Path | None = None,
    checkout_head: str | None = None,
    identity_reader: CandidateIdentityReader = _read_candidate_identity,
) -> int:
    root = repo_root or Path.cwd()
    plan = validation_plan_cli.build_plan(args, git_runner=git_runner, repo_root=root)
    initial_identity = identity_reader(root)
    actual_head = checkout_head if checkout_head is not None else initial_identity.head
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
    if not initial_identity.readable:
        precondition_errors.append("initial candidate identity is unreadable")
    elif not initial_identity.clean:
        precondition_errors.append(
            "candidate inputs are not clean (index, tracked worktree, or "
            "untracked files, hidden tracked inputs, or uncontrolled ignored inputs)"
        )
    if precondition_errors:
        for error in precondition_errors:
            print(f"validation-run refused: {error}", file=sys.stderr)
        if args.json:
            print(
                json.dumps(
                    {
                        "initial_candidate_identity": initial_identity.payload(),
                        "plan": plan.payload(),
                        "precondition_errors": precondition_errors,
                        "status": "refused",
                    },
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
        else:
            print(
                "validation-run initial identity: "
                f"head={initial_identity.head or 'unreadable'} "
                f"tree={initial_identity.head_tree or 'unreadable'} "
                f"index_clean={initial_identity.index_clean} "
                f"tracked_clean={initial_identity.tracked_worktree_clean} "
                f"untracked={len(initial_identity.untracked_paths)} "
                f"hidden_tracked={len(initial_identity.hidden_tracked_paths)} "
                f"unexpected_ignored={len(initial_identity.unexpected_ignored_paths)}",
                file=sys.stderr,
            )
            for error in initial_identity.errors:
                print(f"validation-run identity read error: {error}", file=sys.stderr)
        return 1
    try:
        result = execute_plan(plan, repo_root=root, command_runner=command_runner)
    except ValidationTopologyError as error:
        print(f"validation-run refused: {error}", file=sys.stderr)
        return 1
    final_identity = identity_reader(root)
    result = attribute_candidate_identity(
        result, initial=initial_identity, final=final_identity
    )
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
    "CandidateIdentityReader",
    "CommandRunner",
    "add_parser",
    "add_parsers",
    "execute_plan",
    "run_command",
    "run_routed_command",
)
