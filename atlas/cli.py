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

`deps` (ATLAS-39) is a thin read-mostly surface over the Phase 3 dependency
functions: `ready`, `blocked`, `critical-path`, `unlocks`, `validate`, and
`effort`. It modifies no computation module — it only calls them. The four
computation commands (ready/blocked/critical-path/unlocks) build the graph
and run `validate_graph` FIRST, refusing an invalid graph (EXIT_PRECONDITION
with the typed violations) rather than computing on it; `validate` is the
explicit form of that check. `effort` writes `estimated_effort` directly via
the ATLAS-32 setter (no graph). The Mermaid `graph` subcommand is ATLAS-37's,
added into this group later. `deps` exit codes: 0 success; 2 precondition (an
invalid graph, an unknown key, or a rejected effort). Every deps subcommand
takes `--db` and `--json`.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import networkx as nx

from atlas.core.models import PlanRunStatus
from atlas.dependencies import (
    BlockedResult,
    CriticalPath,
    GraphValidationError,
    GraphValidationFailed,
    HighRiskBlocker,
    ReadinessResult,
    UnlocksResult,
    blocked,
    build_dependency_graph,
    critical_path,
    high_risk_blockers,
    ready_tickets,
    unlocks,
    validate_graph,
)
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
from atlas.planning.staged import StagedProposalGenerator, TemplateStagedGenerator
from atlas.storage import (
    Database,
    EffortValidationError,
    TicketNotFoundError,
    TicketRepo,
)

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
    plan.add_argument(
        "--staged",
        action="store_true",
        help="generate across the three staged calls and assemble one "
        "proposal (ADR-0010); first-run only — refuses a non-empty backlog",
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
    _add_deps_parser(subcommands)
    return parser


def _add_deps_parser(subcommands: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """The `atlas deps` group (ATLAS-39) and its six subcommands. Its own
    nested subparsers (dest="deps_command", required=True) so ATLAS-37 can add
    `graph` (Mermaid) later without restructuring. Every subcommand carries
    `--db` and `--json`."""
    deps = subcommands.add_parser(
        "deps",
        help="Inspect the dependency graph: readiness, blockers, critical path",
    )
    deps_sub = deps.add_subparsers(dest="deps_command", required=True)

    def _add(name: str, help_text: str) -> argparse.ArgumentParser:
        sub: argparse.ArgumentParser = deps_sub.add_parser(name, help=help_text)
        sub.add_argument("--db", default=None, help="database URL")
        sub.add_argument(
            "--json", action="store_true", help="emit machine-readable JSON"
        )
        return sub

    _add("ready", "List the tickets that are ready to be worked on")

    blocked_parser = _add(
        "blocked", "Show what blocks a ticket (or every blocked ticket)"
    )
    # KEY and --high-risk are mutually exclusive: high_risk_blockers is a
    # graph-wide report with no per-ticket variant, so `blocked KEY --high-risk`
    # must error rather than silently ignore KEY.
    blocked_group = blocked_parser.add_mutually_exclusive_group()
    blocked_group.add_argument(
        "key", nargs="?", default=None, help="a ticket key; omit for every blocked"
    )
    blocked_group.add_argument(
        "--high-risk",
        action="store_true",
        help="graph-wide high/critical-risk blocker report (omit KEY)",
    )

    _add("critical-path", "Show the longest effort-weighted execution chain")

    unlocks_parser = _add("unlocks", "Show the tickets a ticket would unlock")
    unlocks_parser.add_argument("key", help="the ticket key")

    _add("validate", "Validate the graph; refuse an invalid one")

    effort_parser = _add("effort", "Set or clear a ticket's estimated_effort")
    effort_parser.add_argument("key", help="the ticket key")
    effort_parser.add_argument(
        "value", nargs="?", type=int, default=None, help="positive integer effort"
    )
    effort_parser.add_argument(
        "--clear", action="store_true", help="clear the estimate (set null)"
    )


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
    staged_generator: StagedProposalGenerator | None = None,
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

    # --staged selects multi-call generation (ADR-0010). The default single
    # call stays the live path; an injected generator (tests) overrides.
    if staged_generator is None and getattr(args, "staged", False):
        staged_generator = TemplateStagedGenerator()

    try:
        result = run_plan(
            repo_root=Path(args.repo).resolve(),
            database=resolved_db,
            client=client,
            identity=identity,
            similarity_threshold=args.similarity_threshold,
            now=datetime.now(UTC),
            staged_generator=staged_generator,
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


def _violation_json(violation: GraphValidationError) -> dict[str, str]:
    """A stable JSON form of one typed validation violation: its class name
    and its message (the message names the offending nodes — a cycle's full
    path, a dangling target's sources)."""
    return {"type": type(violation).__name__, "message": str(violation)}


def _print_violations(error: GraphValidationFailed) -> None:
    """Print every typed violation to stderr (collect-all, mirroring the
    aggregate). Used by both the validate-first guard and `validate`."""
    print("graph validation failed:", file=sys.stderr)
    for violation in error.violations:
        print(f"  {type(violation).__name__}: {violation}", file=sys.stderr)


def _emit(payload: object, text: str, *, as_json: bool) -> None:
    """Emit JSON to stdout when --json, else the human-readable text."""
    print(json.dumps(payload) if as_json else text)


def _deps_ready(graph: nx.DiGraph[str], *, as_json: bool) -> int:
    results: list[ReadinessResult] = ready_tickets(graph)
    keys = [result.key for result in results]
    text = "\n".join(keys) if keys else "No ready tickets."
    _emit({"ready": keys}, text, as_json=as_json)
    return EXIT_OK


def _blocked_payload(result: BlockedResult) -> dict[str, object]:
    return {
        "key": result.key,
        "is_blocked": result.is_blocked,
        "targets": [
            {"key": target.key, "code": target.code.value} for target in result.targets
        ],
    }


def _blocked_text(result: BlockedResult) -> str:
    if not result.is_blocked:
        return f"{result.key} is not blocked."
    lines = [f"{result.key} is blocked by:"]
    lines.extend(f"  {target.key} ({target.code.value})" for target in result.targets)
    return "\n".join(lines)


def _deps_blocked(
    graph: nx.DiGraph[str], args: argparse.Namespace, *, as_json: bool
) -> int:
    if args.high_risk:
        report: tuple[HighRiskBlocker, ...] = high_risk_blockers(graph)
        payload = [
            {
                "target": blocker.target,
                "risk_level": blocker.risk_level,
                "blocks": list(blocker.blocks),
                "blocked_count": blocker.blocked_count,
            }
            for blocker in report
        ]
        if report:
            text = "\n".join(
                f"{blocker.target} ({blocker.risk_level}) blocks "
                f"{blocker.blocked_count}: {', '.join(blocker.blocks)}"
                for blocker in report
            )
        else:
            text = "No high-risk blockers."
        _emit({"high_risk_blockers": payload}, text, as_json=as_json)
        return EXIT_OK

    if args.key is not None:
        result = blocked(graph, args.key)
        _emit(_blocked_payload(result), _blocked_text(result), as_json=as_json)
        return EXIT_OK

    # No KEY: every blocked ticket in the graph, key-ordered.
    all_blocked = [
        blocked(graph, key)
        for key, data in sorted(graph.nodes(data=True))
        if data.get("node_type") == "ticket" and data.get("present", True)
    ]
    blocked_only = [result for result in all_blocked if result.is_blocked]
    payload = [_blocked_payload(result) for result in blocked_only]
    text = (
        "\n".join(_blocked_text(result) for result in blocked_only)
        if blocked_only
        else "No blocked tickets."
    )
    _emit({"blocked": payload}, text, as_json=as_json)
    return EXIT_OK


def _deps_critical_path(graph: nx.DiGraph[str], *, as_json: bool) -> int:
    path: CriticalPath = critical_path(graph)
    payload = {
        "keys": list(path.keys),
        "steps": [
            {
                "key": step.key,
                "effort": step.effort,
                "cumulative_effort": step.cumulative_effort,
            }
            for step in path.steps
        ],
        "total_effort": path.total_effort,
    }
    if path.steps:
        lines = [
            f"  {step.key}  effort={step.effort}  cumulative={step.cumulative_effort}"
            for step in path.steps
        ]
        header = "Critical path (execution order):"
        footer = f"Total effort: {path.total_effort}"
        text = "\n".join([header, *lines, footer])
    else:
        text = "Critical path is empty (no non-terminal tickets)."
    _emit(payload, text, as_json=as_json)
    return EXIT_OK


def _deps_unlocks(graph: nx.DiGraph[str], key: str, *, as_json: bool) -> int:
    result: UnlocksResult = unlocks(graph, key)
    payload = {
        "key": result.key,
        "dependents": list(result.dependents),
        "count": result.count,
    }
    if result.count:
        text = f"{result.key} unlocks {result.count}: {', '.join(result.dependents)}"
    else:
        text = f"{result.key} unlocks no tickets."
    _emit(payload, text, as_json=as_json)
    return EXIT_OK


def _deps_validate(graph: nx.DiGraph[str], *, as_json: bool) -> int:
    """The explicit validate-first check: a clean graph -> EXIT_OK; a
    GraphValidationFailed -> typed violations, EXIT_PRECONDITION."""
    try:
        validate_graph(graph)
    except GraphValidationFailed as error:
        if as_json:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "violations": [
                            _violation_json(violation) for violation in error.violations
                        ],
                    }
                )
            )
        else:
            _print_violations(error)
        return EXIT_PRECONDITION
    _emit({"ok": True, "violations": []}, "Graph is valid.", as_json=as_json)
    return EXIT_OK


def _deps_effort(
    args: argparse.Namespace, resolved_db: Database, *, as_json: bool
) -> int:
    """Set or clear `estimated_effort` via the ATLAS-32 setter (no graph).
    Exactly one of VALUE / --clear is required; a rejected effort
    (EffortValidationError) or unknown key (TicketNotFoundError) exits
    EXIT_PRECONDITION without persisting."""
    if args.clear and args.value is not None:
        print("effort takes either VALUE or --clear, not both.", file=sys.stderr)
        return EXIT_PRECONDITION
    if not args.clear and args.value is None:
        print("effort needs a VALUE (or --clear to set null).", file=sys.stderr)
        return EXIT_PRECONDITION

    effort = None if args.clear else args.value
    try:
        ticket = TicketRepo(resolved_db).set_estimated_effort(args.key, effort)
    except (EffortValidationError, TicketNotFoundError) as error:
        print(error, file=sys.stderr)
        return EXIT_PRECONDITION

    if ticket.estimated_effort is None:
        text = f"Cleared {ticket.key} estimated_effort."
    else:
        text = f"Set {ticket.key} estimated_effort to {ticket.estimated_effort}."
    _emit(
        {"key": ticket.key, "estimated_effort": ticket.estimated_effort},
        text,
        as_json=as_json,
    )
    return EXIT_OK


def _deps_command(args: argparse.Namespace, *, database: Database | None) -> int:
    """Route `atlas deps <subcommand>`. The four computation commands build the
    graph and validate FIRST, refusing an invalid one; `validate` is the
    explicit form; `effort` writes directly without a graph."""
    resolved_db = database if database is not None else Database(args.db)
    as_json = args.json

    if args.deps_command == "effort":
        return _deps_effort(args, resolved_db, as_json=as_json)

    if args.deps_command == "validate":
        return _deps_validate(build_dependency_graph(resolved_db), as_json=as_json)

    # ready / blocked / critical-path / unlocks: validate-first, never compute
    # on an invalid graph (a cycle must refuse, not loop).
    graph = build_dependency_graph(resolved_db)
    try:
        validate_graph(graph)
    except GraphValidationFailed as error:
        _print_violations(error)
        return EXIT_PRECONDITION

    try:
        if args.deps_command == "ready":
            return _deps_ready(graph, as_json=as_json)
        if args.deps_command == "blocked":
            return _deps_blocked(graph, args, as_json=as_json)
        if args.deps_command == "critical-path":
            return _deps_critical_path(graph, as_json=as_json)
        if args.deps_command == "unlocks":
            return _deps_unlocks(graph, args.key, as_json=as_json)
    except ValueError as error:
        # An unknown/non-ticket key from blocked/unlocks: a clean precondition
        # exit, not a traceback.
        print(error, file=sys.stderr)
        return EXIT_PRECONDITION

    return EXIT_PRECONDITION  # unreachable: deps subparser is required


def main(
    argv: list[str] | None = None,
    *,
    database: Database | None = None,
    client: PlannerClient | None = None,
    identity: ModelIdentity | None = None,
    staged_generator: StagedProposalGenerator | None = None,
) -> int:
    """Entry point. ``database``/``client``/``identity``/``staged_generator``
    are injectable for tests; production builds them from the environment."""
    args = build_parser().parse_args(argv)
    if args.command == "plan":
        return _plan_command(
            args,
            database=database,
            client=client,
            identity=identity,
            staged_generator=staged_generator,
        )
    if args.command == "apply":
        return _apply_command(args, database=database)
    if args.command == "deps":
        return _deps_command(args, database=database)
    return EXIT_PRECONDITION  # unreachable: subparser is required


if __name__ == "__main__":
    raise SystemExit(main())
