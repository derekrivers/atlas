"""Drive the acceptance chain for one pull request (ATLAS-040M/067M).

The merge remains an operator action. This script first proves the PR head is
current with main, then requires evidence, human confirmations, a PASSED
verification at that exact head, and a second live freshness check before it
pauses for the merge. After the operator merges, it independently verifies
GitHub's merged state, records the merged proof, and observes the Atlas store
until the managed PM cadence establishes Done.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple

from sqlalchemy.exc import OperationalError

from atlas.github import GitHubAPIError, MissingGitHubTokenError
from atlas.orchestration import (
    PRIntegrationAssessment,
    PRIntegrationEligibility,
    PRIntegrationStatus,
    assess_pr_integration,
    resolve_github_client,
    resolve_pr_context,
)
from atlas.storage import Database, TicketRepo
from atlas.verification import parse_close_set

REPO_ROOT = Path(__file__).resolve().parents[1]
OPERATOR_ENV = "ATLAS_OPERATOR_ID"
DEFAULT_COMPLETION_TIMEOUT_SECONDS = 300.0
DEFAULT_COMPLETION_POLL_SECONDS = 5.0
Command = tuple[str, ...]
RunCommand = Callable[..., subprocess.CompletedProcess[str]]
ResolveAssessment = Callable[[str, int], PRIntegrationAssessment]


class FreshnessSnapshot(NamedTuple):
    owner: str
    repo: str
    pr_number: int
    head_ref: str
    head_sha: str
    head_repository: str
    base_ref: str
    base_sha: str
    base_repository: str


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


def _require_passed_verdict(result: subprocess.CompletedProcess[str]) -> str:
    """Return the verified head, failing closed unless the verdict is PASSED."""

    try:
        payload = json.loads(result.stdout or "")
    except json.JSONDecodeError as error:
        raise RuntimeError(
            "Pre-merge verification did not emit valid JSON; merge is blocked."
        ) from error
    if not isinstance(payload, dict) or payload.get("status") != "passed":
        status = payload.get("status") if isinstance(payload, dict) else None
        raise RuntimeError(_non_passing_verdict_message(payload, status))
    head_commit = payload.get("head_commit")
    if not isinstance(head_commit, str) or not head_commit.strip():
        raise RuntimeError(
            "Pre-merge verification has no valid head_commit; merge is blocked."
        )
    return head_commit


def _non_passing_verdict_message(payload: Any, status: Any) -> str:
    lines = [
        f"Pre-merge verification is {status or 'unknown'}, not passed; "
        "merge is blocked."
    ]
    if not isinstance(payload, dict):
        return "\n".join(lines)

    blockers = _blocking_check_lines(payload)
    if blockers:
        lines.append("Blocking verification checks:")
        lines.extend(blockers)
    else:
        lines.append(
            "No structured blocking_checks were supplied by atlas verify; rerun "
            "the current JSON verifier to inspect the check breakdown."
        )
    return "\n".join(lines)


def _blocking_check_lines(payload: dict[str, Any]) -> list[str]:
    raw_blockers = payload.get("blocking_checks")
    if not isinstance(raw_blockers, list):
        return []

    lines: list[str] = []
    fallback_head = _string_value(payload.get("head_commit"), "unknown head")
    for raw in raw_blockers:
        if not isinstance(raw, dict):
            continue
        ticket = _ticket_label(raw)
        check_type = _string_value(raw.get("check_type"), "unknown_check")
        status = _string_value(raw.get("status"), "unknown")
        head = _string_value(raw.get("head_commit"), fallback_head)
        reason = _string_value(raw.get("reason"), "no typed reason supplied")
        lines.append(f"- {ticket} {check_type} {status} at {head}: {reason}")
    return lines


def _ticket_label(check: dict[str, Any]) -> str:
    ticket_key = check.get("ticket_key")
    if isinstance(ticket_key, str) and ticket_key.strip():
        return ticket_key.strip()
    ticket_id = check.get("ticket_id")
    if isinstance(ticket_id, str) and ticket_id.strip():
        return f"ticket {ticket_id.strip()}"
    return "unknown ticket"


def _string_value(value: Any, fallback: str) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else fallback


def _repo_parts(repo: str) -> tuple[str, str]:
    owner, _separator, name = repo.partition("/")
    return owner, name


def _assess_current_head(repo: str, pr: int) -> PRIntegrationAssessment:
    owner, name = _repo_parts(repo)
    client = resolve_github_client(None)
    return assess_pr_integration(client, owner, name, pr)


def _snapshot(assessment: PRIntegrationAssessment) -> FreshnessSnapshot:
    return FreshnessSnapshot(
        owner=assessment.owner,
        repo=assessment.repo,
        pr_number=assessment.pr_number,
        head_ref=assessment.head_ref,
        head_sha=assessment.head_sha,
        head_repository=assessment.head_repository,
        base_ref=assessment.base_ref,
        base_sha=assessment.base_sha,
        base_repository=assessment.base_repository,
    )


def _rebase_prepare_command(assessment: PRIntegrationAssessment) -> str:
    return (
        f"atlas pr rebase prepare --pr {assessment.pr_number} "
        f"--repo {assessment.owner}/{assessment.repo}"
    )


def _eligible_for_operator_rebase(assessment: PRIntegrationAssessment) -> bool:
    return (
        assessment.eligibility is PRIntegrationEligibility.ELIGIBLE
        and assessment.integration_status
        in {
            PRIntegrationStatus.BEHIND,
            PRIntegrationStatus.DIVERGED,
            PRIntegrationStatus.CONFLICTED,
        }
    )


def _assessment_state(assessment: PRIntegrationAssessment) -> str:
    return (
        f"integration_status: {assessment.integration_status.value}; "
        f"eligibility: {assessment.eligibility.value}; "
        f"ancestry: {assessment.ancestry.value}; "
        f"mergeability: {assessment.mergeability.value}"
    )


def _freshness_failure(
    assessment: PRIntegrationAssessment,
    *,
    prefix: str,
) -> str:
    lines = [
        f"{prefix}: {_assessment_state(assessment)}.",
        (
            "PR head: "
            f"{assessment.head_repository} {assessment.head_ref}@{assessment.head_sha}."
        ),
        (
            "PR base: "
            f"{assessment.base_repository} {assessment.base_ref}@{assessment.base_sha}."
        ),
    ]
    if _eligible_for_operator_rebase(assessment):
        lines.append(f"Recovery: {_rebase_prepare_command(assessment)}")
    return "\n".join(lines)


def _resolve_freshness_assessment(
    repo: str,
    pr: int,
    *,
    resolve_assessment: ResolveAssessment,
    prefix: str,
) -> PRIntegrationAssessment:
    try:
        return resolve_assessment(repo, pr)
    except (GitHubAPIError, MissingGitHubTokenError) as error:
        raise RuntimeError(
            f"{prefix}: integration_status: indeterminate; {error}."
        ) from error


def _require_initial_current(
    repo: str,
    pr: int,
    *,
    resolve_assessment: ResolveAssessment,
) -> FreshnessSnapshot:
    assessment = _resolve_freshness_assessment(
        repo,
        pr,
        resolve_assessment=resolve_assessment,
        prefix="Freshness gate failed",
    )
    if assessment.integration_status is not PRIntegrationStatus.CURRENT:
        raise RuntimeError(
            _freshness_failure(assessment, prefix="Freshness gate failed")
        )
    snapshot = _snapshot(assessment)
    print(
        "Freshness gate: current "
        f"{snapshot.head_repository} {snapshot.head_ref}@{snapshot.head_sha} "
        f"on {snapshot.base_repository} {snapshot.base_ref}@{snapshot.base_sha}."
    )
    return snapshot


def _identity_mismatches(
    initial: FreshnessSnapshot,
    live: FreshnessSnapshot,
) -> list[str]:
    mismatches: list[str] = []
    if live.owner != initial.owner or live.repo != initial.repo:
        mismatches.append(
            "repository changed "
            f"from {initial.owner}/{initial.repo} to {live.owner}/{live.repo}"
        )
    if live.pr_number != initial.pr_number:
        mismatches.append(
            f"PR number changed from #{initial.pr_number} to #{live.pr_number}"
        )
    if live.head_ref != initial.head_ref:
        mismatches.append(
            f"head branch changed from {initial.head_ref} to {live.head_ref}"
        )
    if live.head_repository != initial.head_repository:
        mismatches.append(
            "head repository changed "
            f"from {initial.head_repository} to {live.head_repository}"
        )
    if live.base_ref != initial.base_ref:
        mismatches.append(
            f"base branch changed from {initial.base_ref} to {live.base_ref}"
        )
    if live.base_repository != initial.base_repository:
        mismatches.append(
            "base repository changed "
            f"from {initial.base_repository} to {live.base_repository}"
        )
    return mismatches


def _require_pre_merge_freshness(
    initial: FreshnessSnapshot,
    verified_head: str,
    live: PRIntegrationAssessment,
) -> None:
    if live.integration_status is not PRIntegrationStatus.CURRENT:
        raise RuntimeError(
            _freshness_failure(live, prefix="Freshness restart required")
        )

    live_snapshot = _snapshot(live)
    failures = _identity_mismatches(initial, live_snapshot)
    if live_snapshot.head_sha != initial.head_sha:
        failures.append(
            f"PR head moved from {initial.head_sha} to {live_snapshot.head_sha}"
        )
    if verified_head != initial.head_sha:
        failures.append(
            "verification evaluated "
            f"{verified_head}, not initial head {initial.head_sha}"
        )
    if live_snapshot.head_sha != verified_head:
        failures.append(
            f"live head {live_snapshot.head_sha} is not verified head {verified_head}"
        )
    if live_snapshot.base_sha != initial.base_sha:
        failures.append(
            f"base moved from {initial.base_sha} to {live_snapshot.base_sha}"
        )

    if failures:
        raise RuntimeError(
            "Freshness restart required before merge: "
            + "; ".join(failures)
            + ". Rerun the acceptance spine from evidence at the current PR head."
        )


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


def _positive_finite_seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a numeric value") from error
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("must be finite and greater than zero")
    return seconds


def _observe_managed_completion(
    keys: Sequence[str],
    *,
    timeout_seconds: float,
    poll_seconds: float,
    read_statuses: Callable[[Sequence[str]], list[tuple[str, str]]],
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
) -> tuple[bool, list[tuple[str, str]]]:
    deadline = monotonic() + timeout_seconds
    while True:
        statuses = read_statuses(keys)
        if statuses and all(status == "done" for _, status in statuses):
            return True, statuses

        remaining = deadline - monotonic()
        if remaining <= 0:
            return False, statuses
        sleep(min(poll_seconds, remaining))


def drive(
    args: argparse.Namespace,
    *,
    environ: Mapping[str, str] = os.environ,
    run_command: RunCommand = subprocess.run,
    pause: Callable[[str], str] = input,
    resolve_assessment: ResolveAssessment = _assess_current_head,
    resolve_context: Callable[[str, int], Any] = _merged_context,
    read_statuses: Callable[[Sequence[str]], list[tuple[str, str]]] = _ticket_statuses,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
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
    verify_json = (*verify, "--json")

    try:
        initial_freshness = _require_initial_current(
            repo,
            args.pr,
            resolve_assessment=resolve_assessment,
        )
        _run_step(
            "1/4",
            "Pull evidence",
            evidence,
            resume=_command_text(evidence),
            run_command=run_command,
        )
        _run_step(
            "2/4",
            "Confirm acceptance (interactive)",
            confirm,
            resume=_command_text(confirm),
            run_command=run_command,
        )
        pre_merge = _run_step(
            "3/4",
            "Verify frozen PR head",
            verify_json,
            resume=_command_text(verify_json),
            run_command=run_command,
            capture=True,
        )
        verified_head = _require_passed_verdict(pre_merge)
        print(f"Pre-merge verdict: passed at head {verified_head}.")
        live_freshness = _resolve_freshness_assessment(
            repo,
            args.pr,
            resolve_assessment=resolve_assessment,
            prefix="Freshness restart required",
        )
        _require_pre_merge_freshness(
            initial_freshness,
            verified_head,
            live_freshness,
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
        if context.head_commit != verified_head:
            print(
                f"Merge gate failed: verified head {verified_head} does not match "
                f"merged PR head {context.head_commit}; post-merge actions are "
                "blocked.",
                file=sys.stderr,
            )
            return 1
        print(f"Merge verified by GitHub at head {context.head_commit}.")

        _run_step(
            "4/4",
            "Verify merged PR",
            verify,
            resume=_command_text(verify),
            run_command=run_command,
        )
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
        complete, statuses = _observe_managed_completion(
            keys,
            timeout_seconds=args.completion_timeout_seconds,
            poll_seconds=args.completion_poll_seconds,
            read_statuses=read_statuses,
            monotonic=monotonic,
            sleep=sleep,
        )
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
    if not complete:
        print(
            "Closure incomplete: post-merge PM completion is still pending after "
            f"{args.completion_timeout_seconds:g} seconds.",
            file=sys.stderr,
        )
        return 1
    print("Closure complete: all PR-linked tickets are done.")
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
    parser.add_argument(
        "--completion-timeout-seconds",
        type=_positive_finite_seconds,
        default=DEFAULT_COMPLETION_TIMEOUT_SECONDS,
        help="read-only completion observation window (default: 300)",
    )
    parser.add_argument(
        "--completion-poll-seconds",
        type=_positive_finite_seconds,
        default=DEFAULT_COMPLETION_POLL_SECONDS,
        help="read-only completion polling interval (default: 5)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    return drive(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
