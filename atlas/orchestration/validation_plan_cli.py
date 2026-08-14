"""Read-only CLI adapter for deterministic local-validation plans."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from importlib import resources
from pathlib import Path
from typing import Protocol

from atlas.verification.validation_plan import (
    ValidationRegistry,
    calculate_validation_plan,
    load_registry_bytes,
    runner_profiles_for_test_path,
)

REGISTRY_RESOURCE = "validation_registry_v1.json"
_READ_ONLY_GIT_ENV = {"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}


class GitRunner(Protocol):
    def __call__(
        self,
        cwd: Path,
        argv: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]: ...


def add_parser(subcommands: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    parser = subcommands.add_parser(
        "validation-plan",
        help="Calculate bounded local checks from exact identities and diff paths",
    )
    parser.add_argument(
        "--base", required=True, help="exact full lowercase base Git object id"
    )
    parser.add_argument(
        "--head", required=True, help="exact full lowercase head Git object id"
    )
    parser.add_argument(
        "--changed-path",
        action="append",
        default=[],
        help=(
            "repository-relative path to prove against the base...head diff "
            "(repeatable)"
        ),
    )
    parser.add_argument(
        "--ticket-requirement",
        action="append",
        default=[],
        help="registered explicit ticket validation requirement (repeatable)",
    )
    parser.add_argument(
        "--ticket-test",
        action="append",
        default=[],
        help="explicit repository-relative ticket test file (repeatable)",
    )
    parser.add_argument(
        "--expect-registry-version",
        default=None,
        help="fail closed if the caller's registry version differs",
    )
    parser.add_argument(
        "--json", action="store_true", help="emit canonical bounded JSON"
    )


def _packaged_registry() -> tuple[ValidationRegistry | None, str | None]:
    try:
        content = (
            resources.files("atlas.verification")
            .joinpath(REGISTRY_RESOURCE)
            .read_bytes()
        )
    except (FileNotFoundError, OSError):
        return None, "validation registry is unavailable"
    loaded = load_registry_bytes(content)
    return loaded.registry, loaded.error


def _run_git(
    cwd: Path,
    argv: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    if env is not None:
        merged_env.update(env)
    return subprocess.run(
        ["git", *argv],
        cwd=cwd,
        env=merged_env,
        capture_output=True,
        text=True,
        shell=False,
    )


def _parse_name_status_z(output: str) -> tuple[str, ...] | None:
    if not output:
        return ()
    fields = output.split("\0")
    if fields[-1] != "":
        return None
    fields.pop()
    paths: set[str] = set()
    index = 0
    while index < len(fields):
        status = fields[index]
        index += 1
        if not status:
            return None
        code = status[0]
        if code in {"R", "C"}:
            if not status[1:].isdigit() or index + 1 >= len(fields):
                return None
            paths.update((fields[index], fields[index + 1]))
            index += 2
        elif code in {"A", "D", "M", "T", "U", "X", "B"}:
            if len(status) != 1 or index >= len(fields):
                return None
            paths.add(fields[index])
            index += 1
        else:
            return None
    return tuple(sorted(paths))


def _discover_changed_paths(
    *,
    base: str | None,
    head: str | None,
    repo_root: Path,
    git_runner: GitRunner,
) -> tuple[str, ...] | None:
    if base is None or head is None:
        return None
    try:
        result = git_runner(
            repo_root,
            (
                "diff",
                "--name-status",
                "-z",
                "--find-renames",
                "--find-copies",
                "--no-ext-diff",
                "--no-textconv",
                base,
                head,
                "--",
            ),
            env=_READ_ONLY_GIT_ENV,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    return _parse_name_status_z(result.stdout)


def _unverified_ticket_tests(
    *,
    ticket_tests: tuple[str, ...],
    head: str | None,
    repo_root: Path,
    git_runner: GitRunner,
) -> tuple[str, ...]:
    candidates = tuple(
        sorted(
            {
                path
                for path in ticket_tests
                if runner_profiles_for_test_path(path) is not None
            }
        )
    )
    if head is None:
        return candidates
    unverified: list[str] = []
    for path in candidates:
        try:
            result = git_runner(
                repo_root,
                ("cat-file", "-t", f"{head}:{path}"),
                env=_READ_ONLY_GIT_ENV,
            )
        except Exception:
            unverified.append(path)
            continue
        if result.returncode != 0 or result.stdout.strip() != "blob":
            unverified.append(path)
    return tuple(unverified)


def run_command(
    args: argparse.Namespace,
    *,
    git_runner: GitRunner | None = None,
    repo_root: Path | None = None,
) -> int:
    registry, registry_error = _packaged_registry()
    runner = git_runner or _run_git
    root = repo_root or Path.cwd()
    preliminary = calculate_validation_plan(
        base=args.base,
        head=args.head,
        changed_paths=tuple(args.changed_path),
        ticket_requirements=tuple(args.ticket_requirement),
        ticket_tests=tuple(args.ticket_test),
        registry=registry,
        registry_error=registry_error,
        expected_registry_version=args.expect_registry_version,
        diff_verification="verified",
    )
    discovered_paths = _discover_changed_paths(
        base=preliminary.base,
        head=preliminary.head,
        repo_root=root,
        git_runner=runner,
    )
    supplied_paths = tuple(sorted(set(args.changed_path)))
    if discovered_paths is None:
        effective_paths = supplied_paths
        diff_verification = "unavailable"
    elif discovered_paths != supplied_paths:
        effective_paths = discovered_paths
        diff_verification = "mismatch"
    else:
        effective_paths = discovered_paths
        diff_verification = "verified"
    unverified_tests = _unverified_ticket_tests(
        ticket_tests=tuple(args.ticket_test),
        head=preliminary.head,
        repo_root=root,
        git_runner=runner,
    )
    plan = calculate_validation_plan(
        base=args.base,
        head=args.head,
        changed_paths=effective_paths,
        ticket_requirements=tuple(args.ticket_requirement),
        ticket_tests=tuple(args.ticket_test),
        registry=registry,
        registry_error=registry_error,
        expected_registry_version=args.expect_registry_version,
        diff_verification=diff_verification,
        unverified_ticket_tests=unverified_tests,
    )
    if args.json:
        sys.stdout.buffer.write(plan.json_bytes())
    else:
        print(plan.human_text(), end="")
    return 0


__all__ = ["GitRunner", "add_parser", "run_command"]
