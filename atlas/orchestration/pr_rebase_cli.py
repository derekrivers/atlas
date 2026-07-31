"""CLI adapter for PR status and the operator rebase lane."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy.exc import OperationalError

from atlas.github import GitHubAPIError, GitHubClient, MissingGitHubTokenError
from atlas.orchestration.pr_context import resolve_github_client
from atlas.orchestration.pr_integration import (
    PRIntegrationAssessment,
    PRIntegrationStatus,
    assess_pr_integration,
    pr_integration_assessment_json,
)
from atlas.orchestration.pr_rebase import (
    GitRunner,
    PRRebaseOutcome,
    PRRebasePreconditionError,
    PRRebaseRefusal,
    PRRebaseResult,
    abort_pr_rebase,
    continue_pr_rebase,
    prepare_pr_rebase,
    publish_pr_rebase,
    run_git,
)
from atlas.storage import Database, TicketRepo

EXIT_OK = 0
EXIT_RECORDED_FAILURE = 1
EXIT_PRECONDITION = 2


def add_pr_parser(
    subcommands: argparse._SubParsersAction,  # type: ignore[type-arg]
) -> None:
    pr = subcommands.add_parser(
        "pr",
        help="Pull-request checks and operator-owned rebase lane",
    )
    pr_subcommands = pr.add_subparsers(dest="pr_command", required=True)

    status = pr_subcommands.add_parser(
        "status",
        help="Assess whether a PR's exact head contains the exact current main",
    )
    status.add_argument(
        "--pr", type=int, required=True, help="the pull request number to assess"
    )
    status.add_argument(
        "--repo",
        required=True,
        help="the GitHub repository as OWNER/REPO (not a path)",
    )
    status.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )

    rebase = pr_subcommands.add_parser(
        "rebase",
        help="Prepare, continue, publish, or abort a lease-guarded PR rebase",
    )
    rebase_sub = rebase.add_subparsers(dest="pr_rebase_command", required=True)
    prepare = rebase_sub.add_parser(
        "prepare",
        help="Prepare a detached rebase worktree for a stale Review Required PR",
    )
    prepare.add_argument(
        "--pr", type=int, required=True, help="the pull request number to rebase"
    )
    prepare.add_argument(
        "--repo",
        required=True,
        help="the GitHub repository as OWNER/REPO (not a path)",
    )
    prepare.add_argument("--db", default=None, help="database URL")
    prepare.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )

    for name, help_text in (
        ("continue", "Continue a stopped rebase after staged conflict resolution"),
        ("publish", "Publish a completed rebase with an explicit old-head lease"),
        ("abort", "Abort and remove a managed rebase worktree"),
    ):
        sub = rebase_sub.add_parser(name, help=help_text)
        sub.add_argument(
            "--workspace",
            required=True,
            help="the managed workspace path beneath .atlas/rebase-workspaces/",
        )
        sub.add_argument(
            "--json", action="store_true", help="emit machine-readable JSON"
        )


def run_pr_cli_command(
    args: argparse.Namespace,
    *,
    database: Database | None,
    github_client: GitHubClient | None,
    git_runner: GitRunner | None,
) -> int:
    if args.pr_command == "status":
        return _status_command(args, github_client=github_client)
    if args.pr_command == "rebase":
        return run_pr_rebase_cli_command(
            args,
            database=database,
            github_client=github_client,
            git_runner=git_runner,
        )
    return EXIT_PRECONDITION


def run_pr_rebase_cli_command(
    args: argparse.Namespace,
    *,
    database: Database | None,
    github_client: GitHubClient | None,
    git_runner: GitRunner | None,
) -> int:
    runner = git_runner if git_runner is not None else run_git
    try:
        result = _run(args, database, github_client, runner)
    except PRRebaseRefusal as error:
        print(error, file=sys.stderr)
        return EXIT_RECORDED_FAILURE
    except PRRebasePreconditionError as error:
        print(error, file=sys.stderr)
        return EXIT_PRECONDITION
    except OperationalError:
        print(
            "database is not initialised (no such table); run the database "
            "migrations before using `atlas pr rebase prepare`.",
            file=sys.stderr,
        )
        return EXIT_PRECONDITION

    _emit(
        _result_payload(result),
        _result_text(result),
        as_json=bool(getattr(args, "json", False)),
    )
    return _exit_code(result)


def _status_text(assessment: PRIntegrationAssessment) -> str:
    compare_status = (
        assessment.compare_status.value
        if assessment.compare_status is not None
        else "not_run"
    )
    compare_counts = (
        "not_run"
        if assessment.ahead_by is None or assessment.behind_by is None
        else f"ahead_by={assessment.ahead_by} behind_by={assessment.behind_by}"
    )
    merge_base = assessment.merge_base_sha or "not_run"
    return "\n".join(
        [
            f"PR integration for {assessment.owner}/{assessment.repo} "
            f"#{assessment.pr_number}",
            f"integration_status: {assessment.integration_status.value}",
            f"eligibility: {assessment.eligibility.value}",
            f"ancestry: {assessment.ancestry.value}",
            f"mergeability: {assessment.mergeability.value}",
            (
                f"base: {assessment.base_repository} "
                f"{assessment.base_ref}@{assessment.base_sha}"
            ),
            (
                f"head: {assessment.head_repository} "
                f"{assessment.head_ref}@{assessment.head_sha}"
            ),
            f"compare: {compare_status} {compare_counts} merge_base={merge_base}",
        ]
    )


def _status_command(
    args: argparse.Namespace,
    *,
    github_client: GitHubClient | None,
) -> int:
    owner, sep, repo = args.repo.partition("/")
    if not (owner and sep and repo) or "/" in repo:
        print("--repo must be OWNER/REPO (e.g. acme/atlas).", file=sys.stderr)
        return EXIT_PRECONDITION

    try:
        client = resolve_github_client(github_client)
    except MissingGitHubTokenError as error:
        print(error, file=sys.stderr)
        return EXIT_PRECONDITION

    try:
        assessment = assess_pr_integration(client, owner, repo, args.pr)
    except GitHubAPIError as error:
        print(error, file=sys.stderr)
        return EXIT_PRECONDITION

    _emit(
        pr_integration_assessment_json(assessment),
        _status_text(assessment),
        as_json=bool(getattr(args, "json", False)),
    )
    if assessment.integration_status is PRIntegrationStatus.CURRENT:
        return EXIT_OK
    return EXIT_RECORDED_FAILURE


def _run(
    args: argparse.Namespace,
    database: Database | None,
    github_client: GitHubClient | None,
    runner: GitRunner,
) -> PRRebaseResult:
    if args.pr_rebase_command == "prepare":
        resolved_db = database if database is not None else Database(args.db)
        return prepare_pr_rebase(
            repo_slug=args.repo,
            pr_number=args.pr,
            repo_root=Path.cwd(),
            github_client=_github_client(github_client),
            ticket_lookup=TicketRepo(resolved_db),
            git_runner=runner,
        )
    if args.pr_rebase_command == "continue":
        return continue_pr_rebase(
            workspace_path=Path(args.workspace),
            repo_root=Path.cwd(),
            git_runner=runner,
        )
    if args.pr_rebase_command == "publish":
        return publish_pr_rebase(
            workspace_path=Path(args.workspace),
            repo_root=Path.cwd(),
            github_client=_github_client(github_client),
            git_runner=runner,
        )
    if args.pr_rebase_command == "abort":
        return abort_pr_rebase(
            workspace_path=Path(args.workspace),
            repo_root=Path.cwd(),
            git_runner=runner,
        )
    raise PRRebasePreconditionError("unknown pr rebase command")


def _github_client(github_client: GitHubClient | None) -> GitHubClient:
    try:
        return resolve_github_client(github_client)
    except MissingGitHubTokenError as error:
        raise PRRebasePreconditionError(str(error)) from error


def _emit(payload: object, text: str, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(text)


def _result_payload(result: PRRebaseResult) -> dict[str, object]:
    return {
        "outcome": result.outcome.value,
        "message": result.message,
        "workspace_path": (
            None if result.workspace_path is None else str(result.workspace_path)
        ),
        "manifest_path": (
            None if result.manifest_path is None else str(result.manifest_path)
        ),
        "state": None if result.state is None else result.state.value,
        "old_head_sha": result.old_head_sha,
        "pinned_base_sha": result.pinned_base_sha,
        "merge_base_sha": result.merge_base_sha,
        "new_head_sha": result.new_head_sha,
        "branch": result.branch,
        "tickets": list(result.tickets),
        "conflict_paths": list(result.conflict_paths),
        "receipt_path": (
            None if result.receipt_path is None else str(result.receipt_path)
        ),
    }


def _result_text(result: PRRebaseResult) -> str:
    lines = [result.message, f"outcome: {result.outcome.value}"]
    if result.state is not None:
        lines.append(f"state: {result.state.value}")
    if result.workspace_path is not None:
        lines.append(f"workspace: {result.workspace_path}")
    if result.manifest_path is not None:
        lines.append(f"manifest: {result.manifest_path}")
    if result.branch is not None:
        lines.append(f"branch: {result.branch}")
    if result.old_head_sha is not None:
        lines.append(f"old_head: {result.old_head_sha}")
    if result.pinned_base_sha is not None:
        lines.append(f"pinned_base: {result.pinned_base_sha}")
    if result.merge_base_sha is not None:
        lines.append(f"merge_base: {result.merge_base_sha}")
    if result.new_head_sha is not None:
        lines.append(f"new_head: {result.new_head_sha}")
    if result.tickets:
        lines.append("tickets: " + ", ".join(result.tickets))
    if result.conflict_paths:
        lines.append("conflict_paths:")
        lines.extend(result.conflict_paths)
    if result.receipt_path is not None:
        lines.append(f"receipt: {result.receipt_path}")
    return "\n".join(lines)


def _exit_code(result: PRRebaseResult) -> int:
    if result.outcome in {
        PRRebaseOutcome.READY_TO_PUBLISH,
        PRRebaseOutcome.NOOP_CURRENT,
        PRRebaseOutcome.PUBLISHED,
        PRRebaseOutcome.ABORTED,
    }:
        return EXIT_OK
    return EXIT_RECORDED_FAILURE
