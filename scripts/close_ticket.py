"""Drive the acceptance chain for one pull request (ATLAS-040M).

The merge remains an operator action.  This script pauses after the interactive
confirmation session, then independently refreshes the GitHub PR and refuses to
run ``atlas verify`` until GitHub reports it merged.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from sqlalchemy.exc import OperationalError

from atlas.github import GitHubAPIError, MissingGitHubTokenError
from atlas.orchestration import resolve_github_client, resolve_pr_context
from atlas.storage import Database, TicketRepo
from atlas.verification import parse_close_set

REPO_ROOT = Path(__file__).resolve().parents[1]
OPERATOR_ENV = "ATLAS_OPERATOR_ID"
Command = tuple[str, ...]
RunCommand = Callable[..., subprocess.CompletedProcess[str]]


def _command_text(command: Sequence[str]) -> str:
    return " ".join(command)


def _origin_repo(run_command: RunCommand) -> str:
    result = run_command(
        ("git", "remote", "get-url", "origin"),
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise ValueError("cannot read the origin remote; pass --repo OWNER/REPO")
    remote = result.stdout.strip().removesuffix("/")
    if remote.endswith(".git"):
        remote = remote[:-4]
    if remote.startswith("git@github.com:"):
        slug = remote.removeprefix("git@github.com:")
    elif remote.startswith("ssh://git@github.com/"):
        slug = remote.removeprefix("ssh://git@github.com/")
    elif remote.startswith("https://github.com/"):
        slug = remote.removeprefix("https://github.com/")
    else:
        raise ValueError("origin is not a GitHub remote; pass --repo OWNER/REPO")
    if len(slug.split("/")) != 2 or not all(slug.split("/")):
        raise ValueError("cannot derive OWNER/REPO from origin; pass --repo explicitly")
    return slug


def _clean_worktree(run_command: RunCommand) -> bool:
    result = run_command(
        ("git", "status", "--porcelain"),
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise ValueError("cannot inspect the Git working tree")
    return not result.stdout.strip()


def _validate_repo(repo: str) -> None:
    owner, separator, name = repo.partition("/")
    if not (owner and separator and name) or "/" in name:
        raise ValueError("--repo must be OWNER/REPO (e.g. acme/atlas)")


def _preflight(
    args: argparse.Namespace,
    *,
    environ: Mapping[str, str],
    run_command: RunCommand,
) -> tuple[str, str]:
    if not environ.get("GITHUB_TOKEN"):
        raise ValueError("GITHUB_TOKEN is not set")
    operator = args.operator or environ.get(OPERATOR_ENV)
    if not operator:
        raise ValueError(
            f"operator identity is required: pass --operator ID or set {OPERATOR_ENV}"
        )
    if not _clean_worktree(run_command):
        raise ValueError(
            "working tree is dirty; commit or stash changes before closing a ticket"
        )
    repo = args.repo or _origin_repo(run_command)
    _validate_repo(repo)
    return repo, operator


def _run_step(
    number: str,
    label: str,
    command: Command,
    *,
    resume: str,
    run_command: RunCommand,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    print(f"\n=== {number}  {label} ===")
    print(f"$ {_command_text(command)}")
    result = run_command(
        command,
        cwd=REPO_ROOT,
        text=True,
        capture_output=capture,
        check=False,
    )
    if result.returncode:
        if capture:
            if result.stdout:
                print(result.stdout, end="")
            if result.stderr:
                print(result.stderr, end="", file=sys.stderr)
        raise RuntimeError(
            f"{label} failed with exit code {result.returncode}.\nResume with: {resume}"
        )
    return result


def _summarise_sync(tick: int, result: subprocess.CompletedProcess[str]) -> None:
    lines = [
        line.strip()
        for line in (result.stdout or "").splitlines()
        if line.strip() and ("pm sync:" in line or "transition" in line.lower())
    ]
    summary = lines[-1] if lines else "completed successfully"
    print(f"Tick {tick}: {summary}")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)


def _merged_context(repo: str, pr: int) -> Any:
    client = resolve_github_client(None)
    return resolve_pr_context(repo, pr, client)


def _ticket_statuses(keys: Sequence[str]) -> list[tuple[str, str]]:
    tickets = TicketRepo(Database())
    statuses: list[tuple[str, str]] = []
    for key in keys:
        ticket = tickets.get_by_key(key)
        statuses.append((key, "not_found" if ticket is None else ticket.status.value))
    return statuses


def drive(
    args: argparse.Namespace,
    *,
    environ: Mapping[str, str] = os.environ,
    run_command: RunCommand = subprocess.run,
    pause: Callable[[str], str] = input,
    resolve_context: Callable[[str, int], Any] = _merged_context,
    read_statuses: Callable[[Sequence[str]], list[tuple[str, str]]] = _ticket_statuses,
) -> int:
    try:
        repo, operator = _preflight(
            args,
            environ=environ,
            run_command=run_command,
        )
    except ValueError as error:
        print(f"Precondition failed: {error}.", file=sys.stderr)
        return 2

    common = ("--pr", str(args.pr), "--repo", repo)
    evidence = ("uv", "run", "atlas", "evidence", "pull", *common)
    confirm = (
        "uv",
        "run",
        "atlas",
        "confirm",
        *common,
        "--operator",
        operator,
    )
    verify = ("uv", "run", "atlas", "verify", *common)
    sync = ("uv", "run", "atlas", "pm", "sync", "--once", "-v")

    try:
        _run_step(
            "1/6",
            "Pull evidence",
            evidence,
            resume=_command_text(evidence),
            run_command=run_command,
        )
        _run_step(
            "2/6",
            "Confirm acceptance (interactive)",
            confirm,
            resume=_command_text(confirm),
            run_command=run_command,
        )
        print("\n=== MERGE GATE ===")
        pause(
            f"Merge {repo} PR #{args.pr} in GitHub, then press Enter "
            "to verify the merged state: "
        )
        context = resolve_context(repo, args.pr)
        if not context.pull_request.get("merged"):
            print(
                f"Merge gate failed: {repo} PR #{args.pr} is not merged. "
                "Merge it in GitHub, then re-run this script; existing "
                "confirmations at this head commit will not be re-prompted.",
                file=sys.stderr,
            )
            return 1
        print(f"Merge verified by GitHub at head {context.head_commit}.")

        _run_step(
            "3/6",
            "Verify merged PR",
            verify,
            resume=_command_text(verify),
            run_command=run_command,
        )
        checkout = ("git", "checkout", "main")
        pull = ("git", "pull")
        _run_step(
            "4/6",
            "Checkout main",
            checkout,
            resume="git checkout main && git pull",
            run_command=run_command,
        )
        _run_step(
            "4/6",
            "Pull main",
            pull,
            resume="git pull",
            run_command=run_command,
        )
        first = _run_step(
            "5/6",
            "PM sync tick 1",
            sync,
            resume=_command_text(sync),
            run_command=run_command,
            capture=True,
        )
        _summarise_sync(1, first)
        second = _run_step(
            "6/6",
            "PM sync tick 2",
            sync,
            resume=_command_text(sync),
            run_command=run_command,
            capture=True,
        )
        _summarise_sync(2, second)
    except (GitHubAPIError, MissingGitHubTokenError, RuntimeError) as error:
        print(error, file=sys.stderr)
        return 1

    keys = parse_close_set(
        context.pull_request.get("title"),
        context.pull_request.get("body"),
    )
    if not keys:
        print(
            "Closure incomplete: the PR names no Atlas ticket, so Done cannot "
            "be established from the store.",
            file=sys.stderr,
        )
        return 1
    try:
        statuses = read_statuses(keys)
    except OperationalError:
        print(
            "Closure incomplete: ticket status could not be read because the "
            "database is not initialised.",
            file=sys.stderr,
        )
        return 1

    print("\n=== FINAL STATUS (read from Atlas store) ===")
    for key, status in statuses:
        print(f"{key}: {status}")
    if not statuses or any(status != "done" for _, status in statuses):
        print(
            "Closure incomplete: one or more tickets are not done after two sync "
            "ticks.",
            file=sys.stderr,
        )
        return 1
    print("Closure complete: all PR-linked tickets are done after two sync ticks.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Drive the canonical acceptance chain for one merged PR."
    )
    parser.add_argument("pr", type=int, help="pull request number")
    parser.add_argument(
        "--repo",
        help="GitHub repository as OWNER/REPO (default: derive from origin)",
    )
    parser.add_argument(
        "--operator",
        help=f"operator identity (default: ${OPERATOR_ENV})",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    return drive(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
