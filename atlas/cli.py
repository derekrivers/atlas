"""The `atlas` CLI (ATLAS-26, ATLAS-27).

Two subcommands: `plan` composes the proposer pipeline and persists a
PlanRun (never writes `docs/planning/`); `apply` loads the latest proposed
PlanRun, confirms with the operator, and atomically writes the renders +
finalises the PlanRun. Dependencies (database, client, clock) are
injectable so tests drive the CLI with a fake client and an in-memory
database and make zero real API calls.

`plan` exit codes: 0 PlanRun proposed; 1 recorded failure (PlanRun failed);
2 clean-exit precondition (dirty tree, missing product/key, model error).

`apply` exit codes: 0 applied; 1 operator rejected (PlanRun rejected); 2
refusal/precondition (no proposed plan, stale plan, dirty tree,
unsupported diff/CONFLICT, or no way to confirm).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from atlas.core.models import PlanRunStatus
from atlas.planning.apply import (
    ApplyDecision,
    ApplyError,
    run_apply,
)
from atlas.planning.client import (
    ANTHROPIC_IDENTITY,
    AnthropicPlannerClient,
    ModelCallError,
    ModelIdentity,
    PlannerClient,
    PlannerClientError,
)
from atlas.planning.ingestion import DirtyInputError
from atlas.planning.pipeline import (
    PlanPreconditionError,
    format_plan_diff,
    run_plan,
)
from atlas.planning.reconciler import DEFAULT_SIMILARITY_THRESHOLD, PlanDiff
from atlas.storage import Database

EXIT_OK = 0
EXIT_RECORDED_FAILURE = 1
EXIT_PRECONDITION = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="atlas", description="Atlas planning engine CLI"
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    plan = subcommands.add_parser(
        "plan",
        help="Propose a backlog diff from the documents (never writes renders)",
    )
    plan.add_argument(
        "--similarity-threshold",
        type=float,
        default=DEFAULT_SIMILARITY_THRESHOLD,
        help="reconciler similarity threshold "
        f"(default {DEFAULT_SIMILARITY_THRESHOLD})",
    )
    plan.add_argument(
        "--db",
        default=None,
        help="database URL (overrides ATLAS_DATABASE_URL)",
    )
    plan.add_argument(
        "--repo",
        default=".",
        help="repository root to plan against (default: current directory)",
    )
    apply = subcommands.add_parser(
        "apply",
        help="Apply the latest proposed plan: write renders, finalise the PlanRun",
    )
    apply.add_argument(
        "--yes",
        action="store_true",
        help="pre-confirm the apply (non-interactive); without it apply prompts",
    )
    apply.add_argument("--db", default=None, help="database URL")
    apply.add_argument(
        "--repo",
        default=".",
        help="repository root to apply against (default: current directory)",
    )
    return parser


def _make_confirm(assume_yes: bool) -> Callable[[PlanDiff], ApplyDecision]:
    """Confirmation policy (operator ruling): --yes pre-confirms; otherwise
    an interactive y/N prompt; with neither a TTY nor --yes, refuse rather
    than assume consent."""

    def confirm(diff: PlanDiff) -> ApplyDecision:
        print(format_plan_diff(diff))
        if assume_yes:
            return ApplyDecision.CONFIRMED
        if not sys.stdin.isatty():
            print(
                "apply needs confirmation: re-run with --yes (no TTY available).",
                file=sys.stderr,
            )
            return ApplyDecision.UNCONFIRMABLE
        answer = input("Apply this plan? [y/N] ").strip().lower()
        return ApplyDecision.CONFIRMED if answer == "y" else ApplyDecision.REJECTED

    return confirm


def _apply_command(args: argparse.Namespace, *, database: Database | None) -> int:
    resolved_db = database if database is not None else Database(args.db)
    try:
        result = run_apply(
            repo_root=Path(args.repo).resolve(),
            database=resolved_db,
            now=datetime.now(UTC),
            confirm=_make_confirm(args.yes),
        )
    except (DirtyInputError, ApplyError) as error:
        print(error, file=sys.stderr)
        return EXIT_PRECONDITION

    if result.outcome == "applied":
        print(f"Applied. PlanRun {result.plan_run.id} finalised to applied.")
        return EXIT_OK
    if result.outcome == "rejected":
        print("Plan rejected; no renders written.", file=sys.stderr)
        return EXIT_RECORDED_FAILURE
    print("Apply not confirmed; no changes made.", file=sys.stderr)
    return EXIT_PRECONDITION


def _plan_command(
    args: argparse.Namespace,
    *,
    database: Database | None,
    client: PlannerClient | None,
    identity: ModelIdentity | None,
) -> int:
    resolved_db = database if database is not None else Database(args.db)
    if client is None:
        try:
            client = AnthropicPlannerClient()
        except PlannerClientError as error:  # missing key: clean exit
            print(error, file=sys.stderr)
            return EXIT_PRECONDITION
        identity = ANTHROPIC_IDENTITY
    assert identity is not None  # paired with client by every caller

    try:
        result = run_plan(
            repo_root=Path(args.repo).resolve(),
            database=resolved_db,
            client=client,
            identity=identity,
            similarity_threshold=args.similarity_threshold,
            now=datetime.now(UTC),
        )
    except (DirtyInputError, PlanPreconditionError, ModelCallError) as error:
        print(error, file=sys.stderr)
        return EXIT_PRECONDITION

    if result.status is PlanRunStatus.FAILED:
        print("Plan failed (recorded):", file=sys.stderr)
        print(result.failure_reason, file=sys.stderr)
        return EXIT_RECORDED_FAILURE

    if result.diff is not None:
        print(format_plan_diff(result.diff))
    print(f"PlanRun {result.plan_run.id} persisted at status proposed.")
    return EXIT_OK


def main(
    argv: list[str] | None = None,
    *,
    database: Database | None = None,
    client: PlannerClient | None = None,
    identity: ModelIdentity | None = None,
) -> int:
    """Entry point. ``database``/``client``/``identity`` are injectable for
    tests; production builds them from the environment."""
    args = build_parser().parse_args(argv)
    if args.command == "plan":
        return _plan_command(args, database=database, client=client, identity=identity)
    if args.command == "apply":
        return _apply_command(args, database=database)
    return EXIT_PRECONDITION  # unreachable: subparser is required


if __name__ == "__main__":
    raise SystemExit(main())
