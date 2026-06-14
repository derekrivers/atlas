"""The `atlas` CLI (ATLAS-26).

Milestone 1 exposes a single subcommand, `plan`: it composes the proposer
pipeline and persists a PlanRun (it never writes `docs/planning/` or
assigns keys — that is `atlas apply`, ATLAS-27). The command wires the
real dependencies (database, Anthropic client, clock) and delegates to
`atlas.planning.pipeline.run_plan`; the client and database are injectable
so tests drive the CLI with a fake client and an in-memory database and
make zero real API calls.

Exit codes (gap 1 failure contract):
- 0: a PlanRun was persisted at `proposed`; the diff is printed.
- 1: a recorded failure — a PlanRun was finalised to `failed` (malformed
     model output, or a gate failure); the reason is printed.
- 2: a clean-exit precondition — dirty input tree, missing product,
     missing API key, or a model-call error; no PlanRun exists.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from atlas.core.models import PlanRunStatus
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
from atlas.planning.reconciler import DEFAULT_SIMILARITY_THRESHOLD
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
    return parser


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
    return EXIT_PRECONDITION  # unreachable: subparser is required


if __name__ == "__main__":
    raise SystemExit(main())
