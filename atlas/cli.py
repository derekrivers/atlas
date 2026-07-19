"""The `atlas` CLI (ATLAS-26, ATLAS-27).

Two subcommands: `plan` composes the proposer pipeline and persists a
PlanRun (never writes `docs/planning/`); `apply` loads the latest proposed
PlanRun, confirms with the operator, and atomically writes the renders +
finalises the PlanRun. Dependencies (database, client, clock) are
injectable so tests drive the CLI with a fake client and an in-memory
database and make zero real API calls.

`plan` exit codes: 0 PlanRun proposed; 1 recorded failure (PlanRun failed);
2 clean-exit precondition (dirty tree, missing product/key, model error; in
--stubs-only also an empty inbox or a malformed committed stub, ATLAS-153).

`apply` exit codes: 0 applied; 1 operator rejected (PlanRun rejected); 2
refusal/precondition (no proposed plan, stale plan, dirty tree,
unsupported diff/CONFLICT, or no way to confirm).

`deps` (ATLAS-39, ATLAS-37) is a thin read-mostly surface over the Phase 3
dependency functions: `ready`, `blocked`, `critical-path`, `unlocks`,
`validate`, `effort`, and `graph`. It modifies no computation module — it only
calls them. The five computation commands (ready/blocked/critical-path/unlocks/
graph) build the graph and run `validate_graph` FIRST, refusing an invalid
graph (EXIT_PRECONDITION with the typed violations) rather than computing on it;
`validate` is the explicit form of that check. `graph` (ATLAS-37) prints an
ADVISORY Mermaid analysis view to stdout — readiness/blocker/critical-path
overlays — and writes NO file; it is NOT the canonical docs/planning/roadmap.mmd
(that render is `atlas apply`'s, ATLAS-27). `effort` writes `estimated_effort`
directly via the ATLAS-32 setter (no graph). `deps` exit codes: 0 success; 2
precondition (an invalid graph, an unknown key, or a rejected effort). Every
deps subcommand takes `--db` and `--json`.

`pm report` (ATLAS-47) is the read side of the Phase 4 PM Engine: a PURE READER
that renders the five `pm-engine-and-linear-sync.md` "Delivery metrics" —
throughput, historical cycle time per state (from the transition log,
ATLAS-126), ready-queue depth, anomaly counts, and dwell breaches — plus the
ATLAS-167 DRAFT lesson queue as markdown, or as structured JSON with `--json`.
It computes everything from stored tickets, DebtItems, status transitions, and
Lessons (`atlas.pm.build_delivery_report`); it makes no Linear call and writes
nothing, so it runs with no network and no secrets.
`datetime.now(UTC)` is read only at this boundary and passed into the pure
builder.

`pm sync` (ATLAS-50) is the write side: the recurring scheduler that calls
`sync_tick` on a cadence (default 60s), recording one `TickFailure` on a
crashing tick and continuing (create-on-crash), with graceful SIGTERM/SIGINT
shutdown and a `--once` mode. It builds the real injection from the environment
(`LinearGraphQLClient`, `LinearStatusMap.from_env`, `LINEAR_TEAM_ID`); the loop
logic lives in `atlas.pm.scheduler` and is CI-tested with a fake client and an
injected clock, so the end-to-end round-trip against real Linear is the
operator-run live milestone (ADR-0008). `pm` exit codes: 0 success; 2
precondition (`sync` only — missing Linear creds, an unset team id, or a
missing/malformed status map).

`context` (ATLAS-58) is the Phase 5 read surface over the pure `atlas.context`
functions: `render <KEY> [--budget N] [--json]`, `validate <KEY>`, `show <KEY>`.
A shared loader turns a bare `<KEY>` into the five already-loaded inputs the pure
builder/validator take — the ticket (`TicketRepo.get_by_key`), the global
dependency graph (`build_dependency_graph`, the full-backlog projection), the
input documents re-ingested from HEAD every invocation (`collect_input_documents`
plus the committed `processed/` stubs via `collect_processed_documents`, so
stub-minted anchors resolve and staleness is real), the ACCEPTED ADRs, and the
lessons — and the three
commands are thin wrappers over it. Everything is TRANSIENT: a pack is built
in-memory and printed; nothing is persisted (no `ContextPackRepo`, no
`atlas/storage/` writes) — pack persistence is deferred to the PM promotion gate's
own ticket. `validate` exits non-zero when the pack is invalid so it is scriptable
as a gate; over-budget, ticket-not-found, a dirty input tree, and an unresolvable
anchor are each a clean one-line CLI error (`EXIT_PRECONDITION`), never a
traceback. `context` exit codes: 0 success (and a valid `validate`); 2 precondition
(any loader/build failure, or an invalid `validate`).

`evidence` (ATLAS-67) is the Phase 6 surface over the evidence pipeline:
`pull --pr N --repo OWNER/REPO` (the write side) and `list`/`show` (the read
side). `pull` resolves the PR head SHA once via `fetch_pull_request`, then
fetches + normalises + ingests all three sources — CI checks (workflow + check
runs, pinned to the head SHA), PR reviews (pinned to each review's own commit),
and a per-PR documentation record (touched `docs/` paths) — through the existing
`atlas.evidence.ingest_*` paths and the append-only `EvidenceRepo`, whose
ATLAS-61 guard makes every persisted row commit-pinned system-tier. The
pipeline-driving helper is `GitHubClient`-Protocol-typed and takes the client as
a parameter, so tests inject the fake and it runs with no network; production
builds the live `GitHubRESTClient` (token from `GITHUB_TOKEN`) inside `pull`. A
single `datetime.now(UTC)` is captured per `pull` and threaded into every
ingest. `list` filters `EvidenceRepo.list()` by `--commit`/`--type` in Python
and orders by `(created_at, id)`; `show` prints one record by id. A malformed
`--repo`, a missing product, a missing token, an unknown PR / transport failure,
a non-UUID id, and an unknown id are each a clean one-line `EXIT_PRECONDITION`,
never a traceback; no token is ever printed. `evidence` exit codes: 0 success; 2
precondition. NOTE: `pull --repo` is the GitHub `OWNER/REPO` slug, not the
repo-root path that `plan`/`context` `--repo` mean.

`lessons report`, `lessons search <query>`, and `lessons show <LESSON_ID>` are
the Learning System's pure read side. `report` renders lesson analytics
(category/status and tag grouping, ACTIVE citation counts, pattern candidates,
DRAFT promotion backlog age, and dwell-breach rows) as markdown or JSON.
`search` scans ACTIVE Lessons by title and tag tokens, with optional tag
filtering, for deterministic organisational memory lookup. `show` prints the
full stored lesson record the operator reads before ruling at the promotion
gate, with `--json` for machine consumers. They write nothing and make no LLM
call. `lessons extract <KEY>` remains the explicit operator-request write side
for generating one DRAFT lesson. `lessons playbook <tag>` drafts canonical-doc
Markdown from ACTIVE lessons under one tag onto a new review branch, with no
commit or automatic PR creation.

`verify` (ATLAS-80) is the Phase 7 entry point that makes the verification engine
usable: `verify --pr N --repo OWNER/REPO` verifies every ticket the PR closes,
RECORDS the verdict, and REPORTS it. It mirrors `evidence pull`'s GitHub-client +
`--repo` + `GITHUB_TOKEN` construction, resolves the head commit C and changed
files from GitHub, resolves the close-set from the PR (the `(ATLAS-NN)` key in the
title is the primary source, OP-C/R1; `--tickets` overrides), loads each Ticket
and the stored evidence, runs the PURE `atlas.verification.evaluate_pr`, PERSISTS
one append-only VerificationCheck row per check (OP-B; every run appends a fresh
set, never mutating prior rows), and renders a human or `--json` report. It is
NON-interactive and writes NO Evidence (OP-A): the interactive
operator-confirmation capture (writing human-tier acceptance/scope/approval
evidence pinned to C) is the OP-3 follow-on, so acceptance/scope/human checks
report PENDING here until it lands — honest and expected, not a bug. EXIT-CODE
CONTRACT (R2): a produced report is EXIT_OK for ANY verdict (PASSED / PENDING /
FAILED) — because OP-A makes PENDING the normal state, a verdict-based exit code
would make `verify` "fail" constantly; only a precondition (malformed `--repo`,
missing token, unknown PR / transport, a cold database) is EXIT_PRECONDITION,
never a traceback. A future `--strict` mode (FAILED -> nonzero, for CI gating) is
a follow-up — do not script `atlas verify && merge` expecting it to block on
FAILED today. Like `evidence`, `verify --repo` is the GitHub `OWNER/REPO` slug.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

import networkx as nx
from sqlalchemy.exc import OperationalError

from atlas.context import (
    DEFAULT_TOKEN_BUDGET,
    ContextBudgetExceededError,
    ContextPackValidation,
    build_context_pack,
    validate_context_pack,
)
from atlas.core.anchors import IngestionError
from atlas.core.models import PlanRunStatus
from atlas.core.models.context_pack import ContextPack
from atlas.core.models.evidence import Evidence
from atlas.core.models.ticket import Ticket
from atlas.dependencies import (
    BlockedResult,
    CriticalPath,
    GraphValidationFailed,
    HighRiskBlocker,
    ReadinessResult,
    UnlocksResult,
    blocked,
    build_dependency_graph,
    critical_path,
    high_risk_blockers,
    ready_tickets,
    render_graph,
    render_graph_json,
    unlocks,
    validate_graph,
)
from atlas.dependencies.views import (
    blocked_payload,
    critical_path_payload,
    high_risk_blockers_payload,
    unlocks_payload,
    violation_json,
)
from atlas.evidence import drive_evidence_pull, evidence_summary
from atlas.github import (
    GitHubAPIError,
    GitHubClient,
    GitHubRESTClient,
    MissingGitHubTokenError,
)
from atlas.learning import (
    DEFAULT_LESSON_SCHEDULER_INTERVAL_SECONDS,
    ExtractionTrigger,
    LessonSchedulerConfig,
    LessonSchedulerResult,
    NoActiveLessonsForTagError,
    PlaybookGenerationError,
    PlaybookGitError,
    build_lessons_report,
    draft_playbook_branch,
    extract_lesson_for_ticket,
    lesson_search_results_json,
    lessons_report_json,
    render_lesson_search_results,
    render_lessons_report_markdown,
    run_lesson_scheduler,
    search_lessons,
)
from atlas.learning.views import lesson_review_row, lesson_show_record
from atlas.linear.client import (
    PROJECT_ID_ENV,
    TEAM_ID_ENV,
    LinearClient,
    LinearGraphQLClient,
    MissingLinearTokenError,
)
from atlas.linear.ownership import LinearStatusMap, LinearStatusMapError
from atlas.linear.preflight import ModelProbe, PreflightReport, run_preflight
from atlas.orchestration import (
    ConfirmPrompts,
    ContextInputs,
    ContextNotFoundError,
    build_tick_config,
    capture_ticket,
    load_context_inputs,
    resolve_github_client,
    resolve_pr_context,
    run_verify,
)
from atlas.orchestration.pr_context import (
    parse_tickets_flag as _parse_tickets_flag,
)
from atlas.planning.apply import (
    ApplyDecision,
    ApplyError,
    is_existing_dependency_add,
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
    PRODUCT_KEY,
    PlanPreconditionError,
    PlanResult,
    format_plan_diff,
    run_plan,
    run_stubs_only_plan,
)
from atlas.planning.progress import (
    STAGE_ASSEMBLY,
    STAGE_DEPENDENCIES,
    STAGE_EPICS,
    STAGE_TICKETS,
    PlanProgress,
)
from atlas.planning.promotion import StubPromotionError
from atlas.planning.reconciler import DEFAULT_SIMILARITY_THRESHOLD, PlanDiff
from atlas.planning.staged import StagedProposalGenerator, TemplateStagedGenerator
from atlas.pm import (
    DEFAULT_INTERVAL_SECONDS,
    SyncDecisionClassification,
    SyncResult,
    build_delivery_report,
    render_markdown,
    report_json,
    run_scheduler,
    sync_result_is_empty,
)
from atlas.pm.sync import SyncDecision
from atlas.storage import (
    AgentRunRepo,
    Database,
    DebtItemRepo,
    EffortValidationError,
    EvidenceRepo,
    LessonNotFoundError,
    LessonRepo,
    LessonStateError,
    LessonValidationError,
    ProductRepo,
    TicketNotFoundError,
    TicketRepo,
    TicketStatusTransitionRepo,
    TickFailureRepo,
)
from atlas.storage.preconditions import SchemaDriftError, assert_schema_at_head
from atlas.verification import (
    CheckOutcome,
    PRVerification,
    parse_close_set,
    pr_verification_json,
)

EXIT_OK = 0
EXIT_RECORDED_FAILURE = 1
EXIT_PRECONDITION = 2


def _add_verbose_flag(
    parser: argparse.ArgumentParser, *, default: bool | object = argparse.SUPPRESS
) -> None:
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=default,
        help="enable INFO logging for this invocation",
    )


def _configure_logging(verbose: bool) -> None:
    if not verbose:
        return
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s:%(name)s:%(message)s",
    )
    logging.getLogger().setLevel(logging.INFO)


def _routine_skip_breakdown(decisions: Iterable[SyncDecision]) -> str:
    status_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    prefix = "status not pushable ("
    for decision in decisions:
        if (
            decision.classification != SyncDecisionClassification.ROUTINE
            or decision.outcome != "skipped"
        ):
            continue
        reason = decision.reason
        if reason.startswith(prefix) and reason.endswith(")"):
            status_counts[reason[len(prefix) : -1]] += 1
        else:
            reason_counts[reason] += 1

    parts: list[str] = []
    if status_counts:
        statuses = ", ".join(
            f"{reason}={count}"
            for reason, count in sorted(
                status_counts.items(), key=lambda item: (-item[1], item[0])
            )
        )
        parts.append(f"not pushable: {statuses}")
    parts.extend(
        f"{reason}={count}"
        for reason, count in sorted(
            reason_counts.items(), key=lambda item: (-item[1], item[0])
        )
    )
    return "; ".join(parts)


def _format_push_skipped(result: SyncResult) -> str:
    formatted = f"push_skipped={result.push_skipped}"
    breakdown = _routine_skip_breakdown(result.push_decisions)
    if breakdown:
        formatted = f"{formatted} ({breakdown})"
    return formatted


def _decision_is_visible(decision: SyncDecision, *, verbose: bool) -> bool:
    return verbose or decision.classification != SyncDecisionClassification.ROUTINE


def _format_sync_result(result: SyncResult, *, verbose: bool = False) -> str:
    pushes = result.pushed_created + result.pushed_updated
    prefix = "no work performed" if sync_result_is_empty(result) else "completed"
    lines = [
        (
            f"pm sync: {prefix}; "
            f"pushes={pushes} "
            f"pushed_created={result.pushed_created} "
            f"pushed_updated={result.pushed_updated} "
            f"embeds={result.packs_embedded} "
            f"status_pulls={result.status_pulled} "
            f"status_unchanged={result.status_unchanged} "
            f"anomalies_logged={result.anomalies_logged} "
            f"unmapped_observations={result.unmapped} "
            f"{_format_push_skipped(result)}"
        )
    ]
    for decision in result.push_decisions:
        if not _decision_is_visible(decision, verbose=verbose):
            continue
        lines.append(
            f"{decision.phase} {decision.outcome} {decision.ticket_key}: "
            f"{decision.reason}"
        )
    return "\n".join(lines)


def _format_repair_pack_result(result: SyncResult, *, verbose: bool = False) -> str:
    if result.packs_repaired == 0 and not result.repair_pack_decisions:
        prefix = "no repair candidates"
    elif result.packs_repaired == 0:
        prefix = "no packs repaired"
    else:
        prefix = "completed"
    summary = f"pm sync repair-packs: {prefix}; packs_repaired={result.packs_repaired}"
    breakdown = _routine_skip_breakdown(result.repair_pack_decisions)
    if breakdown:
        summary = f"{summary} ({breakdown})"
    lines = [summary]
    for decision in result.repair_pack_decisions:
        if not _decision_is_visible(decision, verbose=verbose):
            continue
        lines.append(
            f"{decision.phase} {decision.outcome} {decision.ticket_key}: "
            f"{decision.reason}"
        )
    return "\n".join(lines)


def _format_lesson_scheduler_result(result: LessonSchedulerResult | None) -> str:
    attempted = 0 if result is None else result.attempted
    extracted = 0 if result is None else result.extracted
    declined = 0 if result is None else result.declined_as_not_notable
    failed = 0 if result is None else result.failed
    prefix = "no work performed" if attempted == 0 else "completed"
    return (
        f"lessons schedule: {prefix}; "
        f"attempted={attempted} "
        f"extracted={extracted} "
        f"declined-as-not-notable={declined} "
        f"failed={failed}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="atlas", description="Atlas planning engine CLI"
    )
    _add_verbose_flag(parser, default=False)
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
    # Generation-mode selection: --staged reshapes generation, --stubs-only
    # skips it entirely — combining them is meaningless, so argparse refuses
    # (ATLAS-153).
    plan_mode = plan.add_mutually_exclusive_group()
    plan_mode.add_argument(
        "--staged",
        action="store_true",
        help="generate across the three staged calls and assemble one "
        "proposal (ADR-0010); seeds a non-empty backlog to re-plan it "
        "(ATLAS-144)",
    )
    plan_mode.add_argument(
        "--stubs-only",
        action="store_true",
        help="mint the committed inbox stubs without a model call: the "
        "proposal is the verbatim keyed backlog echo plus the promoted "
        "stubs (ATLAS-153); needs no ANTHROPIC_API_KEY; an empty inbox "
        "is a precondition failure (exit 2)",
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
    apply.add_argument(
        "--add-only",
        action="store_true",
        help="apply ADD entries only; skip MODIFY and PROPOSE_ARCHIVE entries "
        "(CONFLICT still refuses). Leaves the existing backlog untouched.",
    )
    apply.add_argument("--db", default=None, help="database URL")
    apply.add_argument(
        "--repo",
        default=".",
        help="repository root to apply against (default: current directory)",
    )
    _add_deps_parser(subcommands)
    _add_pm_parser(subcommands)
    _add_context_parser(subcommands)
    _add_evidence_parser(subcommands)
    _add_verify_parser(subcommands)
    _add_confirm_parser(subcommands)
    _add_preflight_parser(subcommands)
    _add_lessons_parser(subcommands)
    return parser


def _add_deps_parser(subcommands: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """The `atlas deps` group (ATLAS-39, ATLAS-37) and its seven subcommands.
    Its own nested subparsers (dest="deps_command", required=True). Every
    subcommand carries `--db` and `--json`."""
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

    _add(
        "graph",
        "Advisory Mermaid view (stdout) with ready/blocked/critical overlays",
    )

    effort_parser = _add("effort", "Set or clear a ticket's estimated_effort")
    effort_parser.add_argument("key", help="the ticket key")
    effort_parser.add_argument(
        "value", nargs="?", type=int, default=None, help="positive integer effort"
    )
    effort_parser.add_argument(
        "--clear", action="store_true", help="clear the estimate (set null)"
    )


def _add_pm_parser(subcommands: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """The `atlas pm` group (ATLAS-47, ATLAS-50) and its sub-subcommands. Mirrors
    the `deps` shape: its own nested subparsers (dest="pm_command", required=True).
    `report` (the read side) carries `--db` and `--json`; `sync` (the write side,
    the recurring scheduler) carries the cadence flags instead of `--json`."""
    pm = subcommands.add_parser(
        "pm",
        help="PM Engine: delivery metrics (report) and the Linear sync loop (sync)",
    )
    pm_sub = pm.add_subparsers(dest="pm_command", required=True)

    report = pm_sub.add_parser(
        "report",
        help="Delivery metrics as markdown (read-only; --json for structured output)",
    )
    report.add_argument("--db", default=None, help="database URL")
    report.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )

    # `sync` (ATLAS-50): the recurring scheduler. No `--json` (it is a long-running
    # loop, not a one-shot read); `--once` runs exactly one tick; `--repair-packs`
    # adds the ATLAS-169 one-shot repair sweep and also runs exactly one tick;
    # `--interval` owns the cadence; `--inbox-dir` is the follow-up inbox
    # sync_tick writes stubs to; `--repo` (ATLAS-164) is the repo root the
    # pack-inputs provider re-ingests documents from (the repo-root sense,
    # mirroring `plan`/`context` — NOT the GitHub OWNER/REPO slug
    # `evidence`/`verify` mean).
    sync = pm_sub.add_parser(
        "sync",
        help="Run the recurring Linear sync loop (--once runs a single tick)",
    )
    _add_verbose_flag(sync)
    sync.add_argument("--db", default=None, help="database URL")
    sync.add_argument(
        "--once",
        action="store_true",
        help="run exactly one tick and exit (no cadence)",
    )
    sync.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL_SECONDS,
        help=f"seconds between ticks (default {DEFAULT_INTERVAL_SECONDS})",
    )
    sync.add_argument(
        "--inbox-dir",
        default="docs/planning/inbox",
        help="follow-up inbox directory (default docs/planning/inbox)",
    )
    sync.add_argument(
        "--repo",
        default=".",
        help="repository root to ingest pack documents from (default: current dir)",
    )
    sync.add_argument(
        "--repair-packs",
        action="store_true",
        help=(
            "run one sync tick with the operator-invoked repair sweep for "
            "already-stamped Linear descriptions missing the embedded pack header"
        ),
    )


def _add_context_parser(subcommands: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """The `atlas context` group (ATLAS-58) and its three subcommands. Mirrors
    the `deps`/`pm` shape: its own nested subparsers (dest="context_command",
    required=True). Every subcommand takes a `KEY` positional plus `--db`,
    `--repo` (the loader re-ingests documents from HEAD, so it needs the repo
    root), and `--json`; `render` additionally takes `--budget`."""
    context = subcommands.add_parser(
        "context",
        help="Context Renderer: render/validate/show a ticket's context pack",
    )
    context_sub = context.add_subparsers(dest="context_command", required=True)

    def _add(name: str, help_text: str) -> argparse.ArgumentParser:
        sub: argparse.ArgumentParser = context_sub.add_parser(name, help=help_text)
        sub.add_argument("key", help="the ticket key")
        sub.add_argument("--db", default=None, help="database URL")
        sub.add_argument(
            "--repo",
            default=".",
            help="repository root to ingest documents from (default: current dir)",
        )
        sub.add_argument(
            "--json", action="store_true", help="emit machine-readable JSON"
        )
        return sub

    render = _add("render", "Build a ticket's context pack and print its markdown")
    render.add_argument(
        "--budget",
        type=int,
        default=DEFAULT_TOKEN_BUDGET,
        help="token budget for the compression ladder "
        f"(default {DEFAULT_TOKEN_BUDGET})",
    )

    _add("validate", "Validate a ticket's context pack; non-zero when invalid")
    _add("show", "Print a human summary of a ticket's context pack")


def _add_evidence_parser(subcommands: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """The `atlas evidence` group (ATLAS-67) and its three subcommands. Mirrors
    the `deps`/`pm`/`context` shape: its own nested subparsers
    (dest="evidence_command", required=True). `pull` drives the live pipeline for
    one PR (fetch -> normalise -> ingest all three sources); `list`/`show` read
    the stored rows back. Every subcommand takes `--db` and `--json`.

    NOTE: `pull --repo` is the GitHub `OWNER/REPO` slug (which repository to poll),
    NOT the repo-root path that `plan`/`context` `--repo` means -- a different
    surface, a different meaning."""
    evidence = subcommands.add_parser(
        "evidence",
        help="Evidence System: pull CI/review/docs evidence for a PR, list, show",
    )
    evidence_sub = evidence.add_subparsers(dest="evidence_command", required=True)

    pull = evidence_sub.add_parser(
        "pull",
        help="Fetch + normalise + ingest CI/review/docs evidence for one PR",
    )
    pull.add_argument(
        "--pr", type=int, required=True, help="the pull request number to pull"
    )
    pull.add_argument(
        "--repo",
        required=True,
        help="the GitHub repository as OWNER/REPO (not a path)",
    )
    pull.add_argument("--db", default=None, help="database URL")
    pull.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    list_parser = evidence_sub.add_parser(
        "list", help="List the stored evidence rows (newest-pinned ordering)"
    )
    list_parser.add_argument(
        "--commit", default=None, help="filter to one commit SHA (exact match)"
    )
    list_parser.add_argument(
        "--type",
        default=None,
        help="filter to one evidence_type (e.g. test_result, pr_review)",
    )
    list_parser.add_argument("--db", default=None, help="database URL")
    list_parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )

    show = evidence_sub.add_parser("show", help="Show one stored evidence record")
    show.add_argument("evidence_id", help="the evidence id (a UUID)")
    show.add_argument("--db", default=None, help="database URL")
    show.add_argument("--json", action="store_true", help="emit machine-readable JSON")


def _add_verify_parser(subcommands: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """The `atlas verify` command (ATLAS-80): verify + record + report for one PR.

    PR-centric — it verifies every ticket the PR closes. Resolves the head commit
    and changed files from GitHub (mirroring `evidence pull`'s client + `--repo
    OWNER/REPO` + `GITHUB_TOKEN` construction), runs the pure `evaluate_pr`,
    PERSISTS one append-only VerificationCheck row per check (OP-B), and renders a
    human + JSON report. NON-interactive and writes NO Evidence (OP-A): the
    interactive operator-confirmation capture is the OP-3 follow-on, so
    acceptance/scope/human checks report PENDING until it lands.

    NOTE: `--repo` is the GitHub `OWNER/REPO` slug (like `evidence pull`), not the
    repo-root path that `plan`/`context` `--repo` mean.

    EXIT-CODE CONTRACT (R2): a produced report is EXIT_OK for ANY verdict
    (PASSED / PENDING / FAILED) — because OP-A makes PENDING the normal state, a
    verdict-based exit code would make `verify` "fail" constantly. Only a
    precondition (malformed `--repo`, missing token, unknown PR / transport, a
    cold database) is EXIT_PRECONDITION. A future `--strict` mode (FAILED ->
    nonzero, for CI gating) is a follow-up; do NOT script `atlas verify && merge`
    expecting it to block on FAILED today."""
    verify = subcommands.add_parser(
        "verify",
        help="Verify a PR (verify + record + report); EXIT_OK on any verdict",
    )
    verify.add_argument(
        "--pr", type=int, required=True, help="the pull request number to verify"
    )
    verify.add_argument(
        "--repo",
        required=True,
        help="the GitHub repository as OWNER/REPO (not a path)",
    )
    verify.add_argument(
        "--tickets",
        default=None,
        help="override the close-set: a comma-separated list of ATLAS keys "
        "(e.g. ATLAS-72,ATLAS-73); without it the keys are parsed from the PR",
    )
    verify.add_argument("--db", default=None, help="database URL")
    verify.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )


def _add_confirm_parser(subcommands: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """The `atlas confirm` command (ATLAS-133, OP-3.2): interactive capture of the
    operator's human-tier confirmations for a PR.

    Sibling to `verify`, NOT a flag on it (OP-3.2a): `verify` is contractually
    read-only / non-interactive / EXIT_OK-on-any-verdict (ATLAS-80); `confirm`
    WRITES human-tier MANUAL_APPROVAL Evidence and is interactive — a separate
    contract. It resolves the PR head commit, files, and close-set EXACTLY as
    `verify` does (the same helpers, verify untouched), then for each ticket walks
    the operator through every still-pending acceptance criterion, out-of-scope
    file, and blanket-approval requirement at the head commit ``C`` and persists
    their decisions as the human-tier Evidence the three human evaluators match.

    It writes RECORDS ONLY (D-5): no verdict, no VerificationCheck rows, no ticket
    transition — the next `atlas verify` recomputes the verdict from the new
    Evidence, and the next PM tick (ATLAS-131) moves a now-PASSED ticket. There is
    NO blanket confirm-all flag (D-4): a blanket confirm defeats the operator gate
    (ADR-0008), so the operator decides each item, and with neither an injected
    prompt seam nor a TTY the command refuses rather than auto-confirm.

    NOTE: `--repo` is the GitHub `OWNER/REPO` slug (like `verify` / `evidence
    pull`), not a repo-root path.

    EXIT-CODE CONTRACT (D-6): a completed session — even one where the operator
    skips every item — is EXIT_OK. Every setup failure (malformed `--repo`,
    missing operator identity, missing token, unknown PR / transport, a cold
    database, no `ATLAS` product, or no TTY without an injected prompt seam) is a
    clean one-line EXIT_PRECONDITION, never a traceback. No secret is printed."""
    confirm = subcommands.add_parser(
        "confirm",
        help="Interactively capture operator confirmations for a PR (writes "
        "human-tier evidence; EXIT_OK on a completed session)",
    )
    confirm.add_argument(
        "--pr", type=int, required=True, help="the pull request number to confirm"
    )
    confirm.add_argument(
        "--repo",
        required=True,
        help="the GitHub repository as OWNER/REPO (not a path)",
    )
    confirm.add_argument(
        "--tickets",
        default=None,
        help="override the close-set: a comma-separated list of ATLAS keys "
        "(e.g. ATLAS-72,ATLAS-73); without it the keys are parsed from the PR",
    )
    confirm.add_argument(
        "--operator",
        default=None,
        help="the operator id recorded on each confirmation; falls back to the "
        "ATLAS_OPERATOR_ID environment variable (no anonymous human-tier writes)",
    )
    confirm.add_argument("--db", default=None, help="database URL")


def _make_confirm(
    assume_yes: bool, add_only: bool = False
) -> Callable[[PlanDiff], ApplyDecision]:
    """Confirmation policy (operator ruling): --yes pre-confirms; otherwise
    an interactive y/N prompt; with neither a TTY nor --yes, refuse rather
    than assume consent.

    In add-only mode (ATLAS-109, D-3) the gate additionally surfaces which
    entries add-only will decline, so the operator sees a backlog-diverging
    re-plan before confirming — the skip is never silent."""

    def confirm(diff: PlanDiff) -> ApplyDecision:
        print(format_plan_diff(diff))
        if add_only:
            counts = diff.counts
            # At this point (post the run_apply refusal check) any surviving
            # CONFLICT is frozen-source: hard identity/tie conflicts have
            # already refused, so counts["CONFLICT"] is the skipped-conflict
            # count (ATLAS-110, D-3).
            frozen_conflicts = counts["CONFLICT"]
            # ATLAS-111 (D-2): existing↔existing dependency ADDs add-only will
            # scope out. Computed from the diff via the same discriminator the
            # ApplyResult.skipped_dependency property uses (no ApplyResult exists
            # at the gate yet) — same value by construction.
            skipped_deps = sum(
                1 for entry in diff.entries if is_existing_dependency_add(entry)
            )
            total = (
                counts["MODIFY"]
                + counts["PROPOSE_ARCHIVE"]
                + frozen_conflicts
                + skipped_deps
            )
            print(
                f"Add-only: skipping {counts['MODIFY']} MODIFY, "
                f"{counts['PROPOSE_ARCHIVE']} PROPOSE_ARCHIVE, "
                f"{frozen_conflicts} frozen-source CONFLICT, and "
                f"{skipped_deps} existing-to-existing dependency "
                f"entr{'y' if total == 1 else 'ies'}"
                "; the existing backlog is left untouched."
            )
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
        assert_schema_at_head(resolved_db)
    except SchemaDriftError as error:
        print(error, file=sys.stderr)
        return EXIT_PRECONDITION
    try:
        result = run_apply(
            repo_root=Path(args.repo).resolve(),
            database=resolved_db,
            now=datetime.now(UTC),
            confirm=_make_confirm(args.yes, args.add_only),
            add_only=args.add_only,
        )
    except (DirtyInputError, ApplyError) as error:
        print(error, file=sys.stderr)
        return EXIT_PRECONDITION
    except GraphValidationFailed as error:
        # ATLAS-111 (F-2): a projected-graph refusal (e.g. a dependency cycle) is
        # a precondition failure, not a recorded rejection — apply.py's
        # validate_graph runs before the commit seam, so the DB and docs are
        # untouched. Mirror _deps_validate: print the typed violations and return
        # EXIT_PRECONDITION, never a raw traceback with Python exit 1 (which
        # collides with EXIT_RECORDED_FAILURE, the rejection code).
        _print_violations(error)
        return EXIT_PRECONDITION

    if result.outcome == "applied":
        print(f"Applied. PlanRun {result.plan_run.id} finalised to applied.")
        return EXIT_OK
    if result.outcome == "rejected":
        print("Plan rejected; no renders written.", file=sys.stderr)
        return EXIT_RECORDED_FAILURE
    print("Apply not confirmed; no changes made.", file=sys.stderr)
    return EXIT_PRECONDITION


def _format_plan_progress(event: PlanProgress) -> str | None:
    """Map one staged-generation progress event to its operator-facing line, or
    None for an unrecognised stage. Pure: the 'X/3' numbering is cosmetic and
    lives here (not in the event), and a '(retry N)' suffix renders only on a
    real retry (attempt > 0), never '(retry 0)' on the first try."""
    if event.stage == STAGE_EPICS:
        return "Stage 1/3 · epics — generating…"
    if event.stage == STAGE_TICKETS:
        retry = ""
        if event.attempt:
            reason = f" — {event.reason}" if event.reason else ""
            retry = f" (retry {event.attempt}{reason})"
        return (
            f"Stage 2/3 · tickets — epic {event.index}/{event.total}: "
            f"{event.detail}{retry}"
        )
    if event.stage == STAGE_DEPENDENCIES:
        return "Stage 3/3 · dependencies — generating…"
    if event.stage == STAGE_ASSEMBLY:
        return "Stage 3/3 · assembling proposal…"
    return None


def _render_plan_progress(event: PlanProgress) -> None:
    """Print one staged-generation progress line to stderr (the CLI owns
    presentation). Stdout — the §2.4 diff and the persisted-PlanRun line — is
    untouched, so anything parsing it is unaffected."""
    line = _format_plan_progress(event)
    if line is not None:
        print(line, file=sys.stderr)


def _plan_command(
    args: argparse.Namespace,
    *,
    database: Database | None,
    client: PlannerClient | None,
    identity: ModelIdentity | None,
    staged_generator: StagedProposalGenerator | None = None,
) -> int:
    resolved_db = database if database is not None else Database(args.db)
    try:
        assert_schema_at_head(resolved_db)
    except SchemaDriftError as error:
        print(error, file=sys.stderr)
        return EXIT_PRECONDITION

    # --stubs-only invokes no PlannerClient (ATLAS-153): the client — and
    # with it ANTHROPIC_API_KEY — is neither constructed nor required, so
    # the branch sits before the client bootstrap.
    if getattr(args, "stubs_only", False):
        try:
            result = run_stubs_only_plan(
                repo_root=Path(args.repo).resolve(),
                database=resolved_db,
                similarity_threshold=args.similarity_threshold,
                now=datetime.now(UTC),
            )
        except (DirtyInputError, PlanPreconditionError, StubPromotionError) as error:
            print(error, file=sys.stderr)
            return EXIT_PRECONDITION
        return _print_plan_result(result)

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
            on_progress=_render_plan_progress,
        )
    except (
        DirtyInputError,
        PlanPreconditionError,
        ModelCallError,
        StubPromotionError,
    ) as error:
        # StubPromotionError joins the clean-exit set (ATLAS-153, gate-
        # authorised): a malformed committed stub is the same fail-closed
        # posture as an uncommitted one, and previously escaped the
        # generative path as a raw traceback.
        print(error, file=sys.stderr)
        return EXIT_PRECONDITION

    return _print_plan_result(result)


def _print_plan_result(result: PlanResult) -> int:
    """The plan presentation both entry paths share (ATLAS-153): a recorded
    failure to stderr (exit 1), else the §2.4 diff and the persisted-PlanRun
    line to stdout (exit 0) — byte-identical to the pre-ATLAS-153 output."""
    if result.status is PlanRunStatus.FAILED:
        print("Plan failed (recorded):", file=sys.stderr)
        print(result.failure_reason, file=sys.stderr)
        return EXIT_RECORDED_FAILURE

    if result.diff is not None:
        print(format_plan_diff(result.diff))
    print(f"PlanRun {result.plan_run.id} persisted at status proposed.")
    return EXIT_OK


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
        payload = high_risk_blockers_payload(report)
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
        _emit(blocked_payload(result), _blocked_text(result), as_json=as_json)
        return EXIT_OK

    # No KEY: every blocked ticket in the graph, key-ordered.
    all_blocked = [
        blocked(graph, key)
        for key, data in sorted(graph.nodes(data=True))
        if data.get("node_type") == "ticket" and data.get("present", True)
    ]
    blocked_only = [result for result in all_blocked if result.is_blocked]
    payload = [blocked_payload(result) for result in blocked_only]
    text = (
        "\n".join(_blocked_text(result) for result in blocked_only)
        if blocked_only
        else "No blocked tickets."
    )
    _emit({"blocked": payload}, text, as_json=as_json)
    return EXIT_OK


def _deps_critical_path(graph: nx.DiGraph[str], *, as_json: bool) -> int:
    path: CriticalPath = critical_path(graph)
    payload = critical_path_payload(path)
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
    payload = unlocks_payload(result)
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
                            violation_json(violation) for violation in error.violations
                        ],
                    }
                )
            )
        else:
            _print_violations(error)
        return EXIT_PRECONDITION
    _emit({"ok": True, "violations": []}, "Graph is valid.", as_json=as_json)
    return EXIT_OK


def _deps_graph(graph: nx.DiGraph[str], *, as_json: bool) -> int:
    """Print the advisory Mermaid analysis view (ATLAS-37) to stdout. Writes NO
    file — docs/planning/roadmap.mmd is `atlas apply`'s (ADR-0007). Runs after
    the validate-first gate, so it never renders an invalid graph."""
    if as_json:
        print(json.dumps(render_graph_json(graph)))
    else:
        # render_graph already terminates with a newline; end="" avoids a
        # spurious trailing blank line so stdout equals the rendered text.
        print(render_graph(graph), end="")
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
    """Route `atlas deps <subcommand>`. The five computation commands (ready/
    blocked/critical-path/unlocks/graph) build the graph and validate FIRST,
    refusing an invalid one; `validate` is the explicit form; `effort` writes
    directly without a graph."""
    resolved_db = database if database is not None else Database(args.db)
    as_json = args.json

    if args.deps_command == "effort":
        return _deps_effort(args, resolved_db, as_json=as_json)

    if args.deps_command == "validate":
        return _deps_validate(build_dependency_graph(resolved_db), as_json=as_json)

    # ready / blocked / critical-path / unlocks / graph: validate-first, never
    # compute on an invalid graph (a cycle must refuse, not loop or emit a
    # partial render).
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
        if args.deps_command == "graph":
            return _deps_graph(graph, as_json=as_json)
    except ValueError as error:
        # An unknown/non-ticket key from blocked/unlocks: a clean precondition
        # exit, not a traceback.
        print(error, file=sys.stderr)
        return EXIT_PRECONDITION

    return EXIT_PRECONDITION  # unreachable: deps subparser is required


def _pm_report(resolved_db: Database, *, as_json: bool) -> int:
    """Render the delivery metrics, draft lesson queue, and agent-run
    sections. A pure reader: it builds the report from stored tickets,
    DebtItems, the transition log, tick failures, AgentRuns, and DRAFT
    lessons and emits it, writing nothing and making no Linear call.
    `datetime.now(UTC)` is read only here and passed into the pure builder so
    every metric is deterministic under test."""
    report = build_delivery_report(
        TicketRepo(resolved_db),
        DebtItemRepo(resolved_db),
        TickFailureRepo(resolved_db),
        TicketStatusTransitionRepo(resolved_db),
        AgentRunRepo(resolved_db),
        now=datetime.now(UTC),
        lesson_repo=LessonRepo(resolved_db),
    )
    if as_json:
        print(json.dumps(report_json(report)))
    else:
        print(render_markdown(report))
    return EXIT_OK


def _install_shutdown_handlers(shutdown: threading.Event) -> None:
    """Install graceful-shutdown handlers (GAP 1). SIGTERM/SIGINT set the event;
    `run_scheduler` consults it only AFTER the in-flight tick returns, so a signal
    finishes the current tick and then stops — never abandoning a tick mid-write.
    Installed here, at the CLI entry, NOT in the loop, so the scheduler stays a
    pure, signal-free, unit-testable function."""

    def _handle(signum: int, frame: object) -> None:
        shutdown.set()

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)


def _pm_sync(args: argparse.Namespace, resolved_db: Database) -> int:
    """Run the recurring sync scheduler (ATLAS-50). Builds the live injection,
    installs the shutdown handlers, and drives `run_scheduler` (default 60s
    cadence, or one tick with `--once`). A missing credential / team id / status
    map is a clean EXIT_PRECONDITION, mirroring `plan`'s missing-key handling;
    otherwise it loops until a shutdown signal (then stops after the current
    tick) and returns EXIT_OK."""

    try:
        assert_schema_at_head(resolved_db)
        config = build_tick_config(args, resolved_db)
    except (MissingLinearTokenError, LinearStatusMapError, PlannerClientError) as error:
        print(error, file=sys.stderr)
        return EXIT_PRECONDITION
    except SchemaDriftError as error:
        print(error, file=sys.stderr)
        return EXIT_PRECONDITION

    shutdown = threading.Event()
    _install_shutdown_handlers(shutdown)
    result = run_scheduler(
        config,
        interval=args.interval,
        once=args.once or args.repair_packs,
        shutdown=shutdown,
    )
    if args.repair_packs:
        print(
            _format_repair_pack_result(
                result or SyncResult(), verbose=bool(getattr(args, "verbose", False))
            )
        )
    elif args.once:
        print(
            _format_sync_result(
                result or SyncResult(), verbose=bool(getattr(args, "verbose", False))
            )
        )
    return EXIT_OK


def _pm_command(args: argparse.Namespace, *, database: Database | None) -> int:
    """Route `atlas pm <subcommand>`: `report` (read side, ATLAS-47) and `sync`
    (the recurring scheduler, ATLAS-50)."""
    resolved_db = database if database is not None else Database(args.db)
    if args.pm_command == "report":
        return _pm_report(resolved_db, as_json=args.json)
    if args.pm_command == "sync":
        return _pm_sync(args, resolved_db)
    return EXIT_PRECONDITION  # unreachable: pm subparser is required


def _build_pack(inputs: ContextInputs, *, budget: int) -> ContextPack:
    """Build the (transient) pack from the loaded inputs. A thin pass-through to
    the pure builder; its ``ContextBudgetExceededError`` / ``IngestionError``
    (an unresolvable ``source_anchor``) propagate to ``_context_command``, which
    maps them to a clean ``EXIT_PRECONDITION`` (D6)."""
    return build_context_pack(
        inputs.ticket,
        graph=inputs.graph,
        documents=inputs.documents,
        accepted_adrs=inputs.accepted_adrs,
        lessons=inputs.lessons,
        budget=budget,
    )


def _context_render(inputs: ContextInputs, args: argparse.Namespace) -> int:
    """`render <KEY> [--budget N] [--json]` (D3): build the pack and print its
    ``rendered_markdown`` (default) or the full ``ContextPack`` as JSON."""
    pack = _build_pack(inputs, budget=args.budget)
    _emit(pack.model_dump(mode="json"), pack.rendered_markdown, as_json=args.json)
    return EXIT_OK


def _validation_text(result: ContextPackValidation) -> str:
    """The human form of a validation result: the verdict, the anchor-check
    depth, and one failure per line (D4)."""
    head = "valid" if result.valid else "INVALID"
    lines = [f"{head} (anchor_check_depth={result.anchor_check_depth})"]
    lines.extend(f"  {failure}" for failure in result.failures)
    return "\n".join(lines)


def _context_validate(inputs: ContextInputs, args: argparse.Namespace) -> int:
    """`validate <KEY>` (D4): build the pack, run the slug-level validator (the
    CLI has the ticket), print the result, and exit non-zero when invalid so the
    command is scriptable as a gate."""
    pack = _build_pack(inputs, budget=DEFAULT_TOKEN_BUDGET)
    result = validate_context_pack(
        pack,
        documents=inputs.documents,
        lessons=inputs.validation_lessons,
        ticket=inputs.ticket,
    )
    payload = {
        "valid": result.valid,
        "failures": list(result.failures),
        "anchor_check_depth": result.anchor_check_depth,
    }
    _emit(payload, _validation_text(result), as_json=args.json)
    return EXIT_OK if result.valid else EXIT_PRECONDITION


# The renderer's fixed section order (context-renderer.md "Rendered structure").
# `show` lists the present sections by matching these canonical titles, never by
# scraping every ``## `` line — a verbatim doc-section body can itself contain
# ``## `` headings, which must not be mistaken for pack sections.
_PACK_SECTION_TITLES: tuple[str, ...] = (
    "Objective",
    "Constraints",
    "Acceptance Criteria",
    "Non-goals",
    "Relevant Docs",
    "ADRs",
    "Related Tickets",
    "Lessons",
    "Risks",
    "Test Commands",
    "Definition of Done",
)


def _show_summary_text(pack: ContextPack) -> str:
    """A human summary of the pack — distinct from render's raw markdown (D5):
    the sections present, list counts, token estimate, which rungs fired, and the
    recorded-SHA count. Read-style; carries no rendered_markdown body."""
    headers = {
        line[3:].strip()
        for line in pack.rendered_markdown.splitlines()
        if line.startswith("## ")
    }
    sections = [title for title in _PACK_SECTION_TITLES if title in headers]
    compression = ", ".join(pack.compression_applied) or "none"
    return "\n".join(
        [
            f"Context pack for {pack.title}",
            f"Sections: {', '.join(sections)}",
            f"Acceptance criteria: {len(pack.acceptance_criteria)}",
            f"Relevant docs: {len(pack.relevant_docs)}",
            f"ADRs: {len(pack.relevant_adrs)}",
            f"Related tickets: {len(pack.related_tickets)}",
            f"Lessons: {len(pack.historical_lessons)}",
            f"Token estimate: {pack.token_estimate}",
            f"Compression applied: {compression}",
            f"Input doc SHAs: {len(pack.input_doc_shas)}",
        ]
    )


def _context_show(inputs: ContextInputs, args: argparse.Namespace) -> int:
    """`show <KEY>` (D5): build the pack and print a human summary (not the raw
    markdown). ``--json`` mirrors render's full dump."""
    pack = _build_pack(inputs, budget=DEFAULT_TOKEN_BUDGET)
    _emit(pack.model_dump(mode="json"), _show_summary_text(pack), as_json=args.json)
    return EXIT_OK


def _context_command(args: argparse.Namespace, *, database: Database | None) -> int:
    """Route `atlas context <subcommand>` (ATLAS-58). Load the five inputs once
    (D2), then dispatch. Every failure is a clean one-line `EXIT_PRECONDITION`
    (D6): ticket-not-found and a dirty input tree at load time, an unresolvable
    anchor or an over-budget pack at build time — never a traceback."""
    resolved_db = database if database is not None else Database(args.db)
    repo_root = Path(args.repo).resolve()
    try:
        inputs = load_context_inputs(args.key, repo_root, resolved_db)
        if args.context_command == "render":
            return _context_render(inputs, args)
        if args.context_command == "validate":
            return _context_validate(inputs, args)
        if args.context_command == "show":
            return _context_show(inputs, args)
    except (
        ContextNotFoundError,
        DirtyInputError,
        ContextBudgetExceededError,
        IngestionError,
    ) as error:
        print(error, file=sys.stderr)
        return EXIT_PRECONDITION
    return EXIT_PRECONDITION  # unreachable: context subparser is required


def _evidence_pull(
    args: argparse.Namespace,
    resolved_db: Database,
    *,
    github_client: GitHubClient | None,
) -> int:
    """`evidence pull --pr N --repo OWNER/REPO` (D1/D2/D5). Resolve the product,
    build the LIVE client (unless one is injected for tests), drive the pipeline,
    and print a per-source count. Every precondition -- a malformed `--repo`, no
    product, a missing token, an unknown PR / transport failure -- is a clean
    one-line `EXIT_PRECONDITION`, never a traceback (D7); no secret is printed."""
    owner, sep, repo = args.repo.partition("/")
    if not (owner and sep and repo) or "/" in repo:
        print("--repo must be OWNER/REPO (e.g. acme/atlas).", file=sys.stderr)
        return EXIT_PRECONDITION

    product = ProductRepo(resolved_db).get_by_key(PRODUCT_KEY)
    if product is None:
        print(
            f"no {PRODUCT_KEY!r} product in the database; bootstrap the product "
            "before pulling evidence (setup gap).",
            file=sys.stderr,
        )
        return EXIT_PRECONDITION

    client = github_client
    if client is None:
        try:
            client = GitHubRESTClient()  # reads GITHUB_TOKEN at construction
        except MissingGitHubTokenError as error:
            print(error, file=sys.stderr)
            return EXIT_PRECONDITION

    try:
        result = drive_evidence_pull(
            client,
            owner,
            repo,
            args.pr,
            evidence_repo=EvidenceRepo(resolved_db),
            product_id=product.id,
            now=datetime.now(UTC),
        )
    except GitHubAPIError as error:
        print(error, file=sys.stderr)
        return EXIT_PRECONDITION

    persisted = [*result.checks, *result.reviews, *result.docs]
    payload = {
        "checks": len(result.checks),
        "reviews": len(result.reviews),
        "docs": len(result.docs),
        "total": len(persisted),
        "evidence_ids": [str(record.id) for record in persisted],
    }
    text = "\n".join(
        [
            f"Pulled evidence for {owner}/{repo} PR #{args.pr}:",
            f"  checks:  {len(result.checks)}",
            f"  reviews: {len(result.reviews)}",
            f"  docs:    {len(result.docs)}",
            f"  total:   {len(persisted)}",
        ]
    )
    _emit(payload, text, as_json=args.json)
    return EXIT_OK


def _evidence_row_text(record: Evidence) -> str:
    """One human-readable `evidence list` row: id, type, status, short SHA, summary."""
    short_sha = (record.commit_sha or "")[:12]
    return (
        f"{record.id}  {record.evidence_type.value:<22} "
        f"{record.status.value:<14} {short_sha:<12}  {record.summary}"
    )


def _evidence_list(args: argparse.Namespace, resolved_db: Database) -> int:
    """`evidence list [--commit SHA] [--type T]` (D4). Filter ``EvidenceRepo.list()``
    in Python (no new storage verb this ticket): ``--commit`` matches
    ``commit_sha`` and ``--type`` matches ``evidence_type`` exactly. Order
    deterministically by ``(created_at, id)``."""
    rows = EvidenceRepo(resolved_db).list()
    if args.commit is not None:
        rows = [record for record in rows if record.commit_sha == args.commit]
    if args.type is not None:
        rows = [record for record in rows if record.evidence_type.value == args.type]
    rows = sorted(rows, key=lambda record: (record.created_at, str(record.id)))

    payload = [evidence_summary(record) for record in rows]
    text = (
        "\n".join(_evidence_row_text(record) for record in rows)
        if rows
        else "No evidence."
    )
    _emit(payload, text, as_json=args.json)
    return EXIT_OK


def _evidence_show_text(record: Evidence) -> str:
    """A human field summary for `evidence show` (D1/D7): the record's identifying
    and trust fields, one per line. Never prints a token (there is none on an
    Evidence row); ``raw_payload`` is summarised by size, surfaced verbatim only
    under ``--json``."""
    return "\n".join(
        [
            f"id:              {record.id}",
            f"product_id:      {record.product_id}",
            f"ticket_id:       {record.ticket_id}",
            f"evidence_type:   {record.evidence_type.value}",
            f"status:          {record.status.value}",
            f"summary:         {record.summary}",
            f"commit_sha:      {record.commit_sha}",
            f"external_run_id: {record.external_run_id}",
            f"payload_hash:    {record.payload_hash}",
            f"source_uri:      {record.source_uri}",
            f"created_by:      {record.created_by_type.value}:{record.created_by_id}",
            f"created_at:      {record.created_at.isoformat()}",
            f"raw_payload:     {len(record.raw_payload)} key(s)",
        ]
    )


def _evidence_show(args: argparse.Namespace, resolved_db: Database) -> int:
    """`evidence show EVIDENCE_ID` (D1). A non-UUID id and an unknown id are each
    a clean ``EXIT_PRECONDITION`` (no traceback); ``--json`` dumps the full
    record (``raw_payload`` included)."""
    try:
        evidence_id = UUID(args.evidence_id)
    except ValueError:
        print(f"not a valid evidence id: {args.evidence_id!r}", file=sys.stderr)
        return EXIT_PRECONDITION

    record = EvidenceRepo(resolved_db).get(evidence_id)
    if record is None:
        print(f"no evidence with id {evidence_id}", file=sys.stderr)
        return EXIT_PRECONDITION

    _emit(
        record.model_dump(mode="json"),
        _evidence_show_text(record),
        as_json=args.json,
    )
    return EXIT_OK


def _evidence_command(
    args: argparse.Namespace,
    *,
    database: Database | None,
    github_client: GitHubClient | None,
) -> int:
    """Route `atlas evidence <subcommand>` (ATLAS-67): `pull` (write side -- drive
    the pipeline for one PR) and `list`/`show` (read side). `github_client` is
    injected by tests; production builds the live `GitHubRESTClient` inside
    `pull`.

    ATLAS-130: a cold (never-migrated) database raises ``OperationalError: no
    such table`` from the first repository access — ``ProductRepo.get_by_key``
    in `pull`, ``EvidenceRepo.list``/``.get`` in `list`/`show` — which without
    this guard escapes as a raw SQLAlchemy traceback (D7 violation). The catch
    wraps the WHOLE dispatch deliberately, not just the first DB access: at this
    stage an ``OperationalError`` anywhere in an evidence command almost always
    means a schema problem — a fully cold DB, or a partially-migrated one whose
    ``evidence`` table is still absent at ingest-time ``EvidenceRepo.add`` (the
    `products`-exists-but-`evidence`-missing case). Wrapping only the probe would
    leave that write-time ``no such table: evidence`` path an unguarded
    traceback. A constraint/duplicate failure raises ``IntegrityError`` (a
    different class), so it is NOT caught here; a rare transient operational
    fault on a migrated DB is the one mislabel, and it still exits cleanly. The
    binding is omitted so no raw SQLAlchemy text leaks. The inner
    ``MissingGitHubTokenError``/``GitHubAPIError`` handlers `return`, so they
    propagate as values and the outer ``except`` never sees them."""
    resolved_db = database if database is not None else Database(args.db)
    try:
        if args.evidence_command == "pull":
            return _evidence_pull(args, resolved_db, github_client=github_client)
        if args.evidence_command == "list":
            return _evidence_list(args, resolved_db)
        if args.evidence_command == "show":
            return _evidence_show(args, resolved_db)
    except OperationalError:
        print(
            "database is not initialised (no such table); run the database "
            "migrations before using `atlas evidence`.",
            file=sys.stderr,
        )
        return EXIT_PRECONDITION
    return EXIT_PRECONDITION  # unreachable: evidence subparser is required


def _verify_check_text(outcome: CheckOutcome) -> str:
    """One per-check line in the human report (D4): status, type, gating flag,
    evidence ids, and the evaluator's reason (which already reads e.g. 'awaiting
    system-tier evidence at C' for a PENDING machine check)."""
    evidence_ids = ", ".join(str(eid) for eid in outcome.evidence_ids) or "—"
    gate = "required" if outcome.required else "optional"
    return (
        f"    [{outcome.status.value.upper():<14}] "
        f"{outcome.check_type.value:<20} ({gate})  evidence: {evidence_ids}\n"
        f"      {outcome.reason}"
    )


def _verify_report_text(
    pr: PRVerification,
    key_by_id: dict[UUID, str],
    *,
    repo: str,
    pr_number: int,
    unknown_keys: list[str],
) -> str:
    """The human report (D4): a PR verdict headline at C, then per ticket (by
    key) its verdict and per-check breakdown, plus the OP-A honesty note and any
    skipped unknown keys / empty-close-set explanation. Never a traceback (D5)."""
    lines = [
        f"Verification for {repo} PR #{pr_number} at {pr.head_commit}",
        f"PR verdict: {pr.status.value.upper()}",
    ]
    if not pr.tickets:
        lines.append(
            "  No tickets resolved from this PR (empty close-set); PENDING — "
            "nothing to verify (a PR is expected to close at least one ATLAS-NN "
            "ticket; name them with --tickets if the convention was not followed)."
        )
    for tv in pr.tickets:
        key = key_by_id.get(tv.ticket_id, str(tv.ticket_id))
        lines.append(f"  {key}: {tv.status.value.upper()}")
        lines.extend(_verify_check_text(outcome) for outcome in tv.checks)
    if unknown_keys:
        lines.append(
            "  Skipped (no such ticket in the database): " + ", ".join(unknown_keys)
        )
    lines.append(
        "  Note (OP-A): acceptance / scope / human_approval report PENDING here "
        "until the interactive operator-confirmation capture lands (OP-3 "
        "follow-on) — no operator confirmations exist yet. This is honest and "
        "expected, not a bug; the machine checks (tests / lint / documentation) "
        "are evaluated against system-tier evidence at this commit."
    )
    return "\n".join(lines)


def _verify_command(
    args: argparse.Namespace,
    *,
    database: Database | None,
    github_client: GitHubClient | None,
) -> int:
    """`atlas verify --pr N --repo OWNER/REPO` (ATLAS-80): verify + record + report.

    Mirrors `evidence pull`'s client construction (`--repo OWNER/REPO`, the live
    `GitHubRESTClient` from `GITHUB_TOKEN` unless one is injected for tests).
    Resolves the head commit and changed files from GitHub, the close-set from the
    PR (`--tickets` override else `parse_close_set` over the title/body, OP-C/R1),
    loads each Ticket and the evidence, runs the pure `evaluate_pr`, PERSISTS one
    append-only VerificationCheck row per check (OP-B), and renders the report.

    EXIT-CODE CONTRACT (R2): a produced report is EXIT_OK for ANY verdict; only a
    precondition (malformed `--repo`, missing token, unknown PR / transport, a
    cold database) is EXIT_PRECONDITION — never a traceback (D5). No secret is
    printed. A cold (never-migrated) database raises OperationalError from the
    first repo access; the guard maps it to a clean precondition, mirroring
    `evidence`'s ATLAS-130 handling (no raw SQLAlchemy text leaks)."""
    resolved_db = database if database is not None else Database(args.db)

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
        context = resolve_pr_context(args.repo, args.pr, client)
    except GitHubAPIError as error:
        print(error, file=sys.stderr)
        return EXIT_PRECONDITION

    close_set: tuple[str, ...]
    if args.tickets is not None:
        close_set = _parse_tickets_flag(args.tickets)
    else:
        close_set = parse_close_set(
            context.pull_request.get("title"), context.pull_request.get("body")
        )

    try:
        result = run_verify(context, close_set, resolved_db)
    except OperationalError:
        print(
            "database is not initialised (no such table); run the database "
            "migrations before using `atlas verify`.",
            file=sys.stderr,
        )
        return EXIT_PRECONDITION

    payload = pr_verification_json(result.verification)
    text = _verify_report_text(
        result.verification,
        result.key_by_id,
        repo=f"{context.owner}/{context.repo}",
        pr_number=args.pr,
        unknown_keys=result.unknown_keys,
    )
    _emit(payload, text, as_json=args.json)
    return EXIT_OK


def _make_confirm_prompts() -> ConfirmPrompts:
    """The stdin :class:`ConfirmPrompts` default (production, D-3).

    Reads the operator's rulings from ``input()`` — the same register as
    :func:`_make_confirm`. The exact wording is non-contractual: tests inject a
    scripted seam and never parse this output. Only called once the command has
    confirmed a TTY exists (D-4), so the prompts never block a headless run."""

    class _StdinConfirmPrompts:
        def acceptance(self, criterion: str) -> bool:
            answer = input(f"Confirm acceptance criterion: {criterion}  [y/N] ")
            return answer.strip().lower() == "y"

        def scope(self, path: str) -> Literal["waive", "fail", "skip"]:
            answer = (
                input(f"Out-of-scope file {path}:  [w]aive / [f]ail / [s]kip  ")
                .strip()
                .lower()
            )
            if answer in ("w", "waive"):
                return "waive"
            if answer in ("f", "fail"):
                return "fail"
            return "skip"

        def approval(self) -> Literal["approve", "reject", "skip"]:
            answer = (
                input("Blanket-approve this PR?  [a]pprove / [r]eject / [s]kip  ")
                .strip()
                .lower()
            )
            if answer in ("a", "approve"):
                return "approve"
            if answer in ("r", "reject"):
                return "reject"
            return "skip"

    return _StdinConfirmPrompts()


def _confirm_command(
    args: argparse.Namespace,
    *,
    database: Database | None = None,
    github_client: GitHubClient | None = None,
    prompts: ConfirmPrompts | None = None,
    now: datetime | None = None,
    new_id: Callable[[], UUID] | None = None,
) -> int:
    """`atlas confirm --pr N --repo OWNER/REPO` (ATLAS-133, OP-3.2): capture the
    operator's human-tier confirmations for a PR and persist them as Evidence.

    Resolves the PR head commit ``C``, changed files, and close-set EXACTLY as
    `_verify_command` does — the same helpers, `verify` untouched (D-1) — so a
    confirmation pins the same commit `verify` later evaluates against. Per ticket
    in the close-set it calls OP-3.1's :func:`pending_capture` and, for each still-
    pending item, prompts the operator (the injected :class:`ConfirmPrompts` seam,
    else the stdin default) and routes the answer to the matching OP-3.1 builder,
    persisting the record via :meth:`EvidenceRepo.add`. The CLI owns NO record
    shape (keys / hashes / tier / commit pin are all the builders'); it only
    decides which builder an answer calls (D-2).

    RECORDS ONLY (D-5): no `evaluate_pr`, no VerificationCheck rows, no ticket
    transition. EXIT-CODE CONTRACT (D-6): a completed session (even all-skip) →
    EXIT_OK; every setup failure → a clean one-line EXIT_PRECONDITION, never a
    traceback, no secret printed."""
    resolved_db = database if database is not None else Database(args.db)

    owner, sep, repo = args.repo.partition("/")
    if not (owner and sep and repo) or "/" in repo:
        print("--repo must be OWNER/REPO (e.g. acme/atlas).", file=sys.stderr)
        return EXIT_PRECONDITION

    # Operator identity (OP-3.2c) — resolved BEFORE any I/O so a miss writes
    # nothing: no anonymous human-tier evidence.
    operator_id = args.operator or os.environ.get("ATLAS_OPERATOR_ID")
    if not operator_id:
        print(
            "confirm needs an operator id: pass --operator ID or set "
            "ATLAS_OPERATOR_ID (no anonymous human-tier writes).",
            file=sys.stderr,
        )
        return EXIT_PRECONDITION

    # The prompt seam (D-3/D-4) — also resolved before any I/O. With no injected
    # seam and no TTY, refuse rather than auto-confirm (a blanket confirm defeats
    # the operator gate, ADR-0008).
    if prompts is None:
        if not sys.stdin.isatty():
            print(
                "confirm is interactive and needs a terminal; no TTY is "
                "available and no prompt seam was injected.",
                file=sys.stderr,
            )
            return EXIT_PRECONDITION
        prompts = _make_confirm_prompts()

    try:
        client = resolve_github_client(github_client)
    except MissingGitHubTokenError as error:
        print(error, file=sys.stderr)
        return EXIT_PRECONDITION

    try:
        context = resolve_pr_context(args.repo, args.pr, client)
    except GitHubAPIError as error:
        print(error, file=sys.stderr)
        return EXIT_PRECONDITION

    close_set: tuple[str, ...]
    if args.tickets is not None:
        close_set = _parse_tickets_flag(args.tickets)
    else:
        close_set = parse_close_set(
            context.pull_request.get("title"), context.pull_request.get("body")
        )

    clock = now if now is not None else datetime.now(UTC)
    mint = new_id if new_id is not None else uuid4

    try:
        product = ProductRepo(resolved_db).get_by_key(PRODUCT_KEY)
        if product is None:
            print(
                f"no {PRODUCT_KEY!r} product in the database; bootstrap the "
                "product before confirming (setup gap).",
                file=sys.stderr,
            )
            return EXIT_PRECONDITION

        ticket_repo = TicketRepo(resolved_db)
        evidence_repo = EvidenceRepo(resolved_db)
        tickets: list[Ticket] = []
        unknown_keys: list[str] = []
        for key in close_set:
            ticket = ticket_repo.get_by_key(key)
            if ticket is None:
                unknown_keys.append(key)
                continue
            tickets.append(ticket)

        evidence = evidence_repo.list()  # snapshot at C; loaded once (mirrors verify)
        recorded = 0
        for ticket in tickets:
            recorded += capture_ticket(
                ticket,
                prompts=prompts,
                head_commit=context.head_commit,
                pr_files=context.pr_files,
                evidence=evidence,
                product_id=product.id,
                operator_id=operator_id,
                evidence_repo=evidence_repo,
                now=clock,
                new_id=mint,
            )
    except OperationalError:
        print(
            "database is not initialised (no such table); run the database "
            "migrations before using `atlas confirm`.",
            file=sys.stderr,
        )
        return EXIT_PRECONDITION

    print(
        f"Recorded {recorded} operator confirmation(s) for "
        f"{context.owner}/{context.repo} PR #{args.pr} at {context.head_commit}."
    )
    if unknown_keys:
        print("  Skipped (no such ticket in the database): " + ", ".join(unknown_keys))
    return EXIT_OK


def _add_preflight_parser(subcommands: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """The `atlas preflight` command (ATLAS-136; F3 + A1 + A2): an
    operator-invoked, read-only check the operator runs *before* dispatching
    agents, to catch the silent no-dispatch traps the Phase-8 review surfaced.

    It builds the live Linear client, status map, and project id from the
    environment exactly as `pm sync` does (`LinearGraphQLClient()`,
    `LinearStatusMap.from_env()`, `LINEAR_PROJECT_ID`), reads the WORKFLOW.md
    front matter, and renders the ordered findings.

    EXIT-CODE CONTRACT (deliberately NOT `confirm`'s EXIT_OK-on-completion): a
    SETUP failure (missing `LINEAR_API_KEY`, missing/malformed
    `LINEAR_STATE_MAP`, or an unset `LINEAR_PROJECT_ID`) is a clean one-line
    EXIT_PRECONDITION (2); a completed run that produced one or more FAILING
    findings is EXIT_RECORDED_FAILURE (1); a run whose only non-pass is a
    SKIPPED check (C6 could not run — no binary, unauthenticated, timeout, or an
    unparseable model) is also EXIT_PRECONDITION (2); an all-pass run is EXIT_OK
    (0). The operator must distinguish "I misconfigured the environment" or "the
    model check couldn't run" from "Linear isn't set up yet." This command is
    NOT a gate on `pm sync`/dispatch in this ticket — wiring it as a hard
    precondition is its own ticket (OP-4)."""

    preflight = subcommands.add_parser(
        "preflight",
        help="Operator preflight: check Linear states, status map, and project "
        "against the WORKFLOW.md contract before dispatch (read-only)",
    )
    preflight.add_argument(
        "--workflow-md",
        default="WORKFLOW.md",
        help="path to the Symphony WORKFLOW.md contract (default: WORKFLOW.md)",
    )
    preflight.add_argument(
        "--allow-assignee",
        action="store_true",
        help="acknowledge a set LINEAR_ASSIGNEE (otherwise a set assignee is a "
        "failing finding, as it narrows Symphony's poll)",
    )
    preflight.add_argument(
        "--check-model",
        action="store_true",
        help="also run C6: probe the pinned Codex model for reachability "
        "(opt-in — makes a live, billable, auth-requiring model call; C1-C5 "
        "run offline without it)",
    )
    preflight.add_argument(
        "--model-probe-timeout",
        type=float,
        default=60.0,
        metavar="SECONDS",
        help="timeout for the C6 model probe (default: 60); a timeout is a skip "
        "(EXIT_PRECONDITION), never a rejection",
    )


def _add_lessons_parser(subcommands: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """The `atlas lessons` group: reporting, extraction, scheduler, and gates."""

    lessons = subcommands.add_parser(
        "lessons",
        help=(
            "Learning System: report, search, extract, playbook, review, show, "
            "and promote lessons"
        ),
    )
    lessons_sub = lessons.add_subparsers(dest="lessons_command", required=True)

    search = lessons_sub.add_parser(
        "search",
        help=(
            "Search ACTIVE lessons by title/tag keyword tokens "
            "(read-only; --json for structured output)"
        ),
    )
    search.add_argument("query", nargs="+", help="keyword query")
    search.add_argument(
        "--tag",
        default=None,
        help="filter to lessons carrying this exact tag before keyword matching",
    )
    search.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON array"
    )
    search.add_argument("--db", default=None, help="database URL")

    report = lessons_sub.add_parser(
        "report",
        help=("Lesson analytics as markdown (read-only; --json for structured output)"),
    )
    report.add_argument("--db", default=None, help="database URL")
    report.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )

    review = lessons_sub.add_parser(
        "review",
        help="List DRAFT lessons awaiting promotion, or stale ACTIVE lessons",
    )
    review.add_argument(
        "--stale",
        action="store_true",
        help="list ACTIVE lessons included in 10+ post-action context packs "
        "with zero citation/re-confirmation signal",
    )
    review.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )
    review.add_argument("--db", default=None, help="database URL")

    show = lessons_sub.add_parser(
        "show",
        help="Show one stored lesson record",
    )
    show.add_argument("lesson_id", help="lesson UUID to show")
    show.add_argument("--db", default=None, help="database URL")
    show.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    promote = lessons_sub.add_parser(
        "promote",
        help="Promote a DRAFT lesson to ACTIVE with operator confidence",
    )
    promote.add_argument("lesson_id", type=UUID, help="lesson UUID to promote")
    promote.add_argument(
        "--confidence",
        type=float,
        required=True,
        help="operator confidence from 0.0 to 1.0 inclusive",
    )
    promote.add_argument("--db", default=None, help="database URL")

    reject = lessons_sub.add_parser(
        "reject",
        help="Reject a DRAFT lesson and retain it as ARCHIVED",
    )
    reject.add_argument("lesson_id", type=UUID, help="lesson UUID to reject")
    reject.add_argument("--db", default=None, help="database URL")

    archive = lessons_sub.add_parser(
        "archive",
        help="Archive a DRAFT or ACTIVE lesson without deleting it",
    )
    archive.add_argument("lesson_id", type=UUID, help="lesson UUID to archive")
    archive.add_argument("--db", default=None, help="database URL")

    merge = lessons_sub.add_parser(
        "merge",
        help="Merge a DRAFT lesson into an existing ACTIVE lesson",
    )
    merge.add_argument("draft_lesson_id", type=UUID, help="DRAFT lesson UUID")
    merge.add_argument(
        "--into",
        dest="target_lesson_id",
        type=UUID,
        required=True,
        help="ACTIVE target lesson UUID",
    )
    merge.add_argument("--db", default=None, help="database URL")

    extract = lessons_sub.add_parser(
        "extract",
        help="Extract one DRAFT lesson for a ticket key",
    )
    extract.add_argument("key", help="the ticket key")
    extract.add_argument("--db", default=None, help="database URL")

    playbook = lessons_sub.add_parser(
        "playbook",
        help="Draft a playbook from ACTIVE lessons under a tag on a new branch",
    )
    playbook.add_argument("tag", help="lesson tag to draft into a playbook")
    playbook.add_argument(
        "--repo",
        default=".",
        help="repository root for the playbook branch (default: current directory)",
    )
    playbook.add_argument("--db", default=None, help="database URL")

    schedule = lessons_sub.add_parser(
        "schedule",
        help="Run the recurring lesson extraction scheduler",
    )
    _add_verbose_flag(schedule)
    schedule.add_argument("--db", default=None, help="database URL")
    schedule.add_argument(
        "--once",
        action="store_true",
        help="run exactly one poll cycle and exit (no cadence)",
    )
    schedule.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_LESSON_SCHEDULER_INTERVAL_SECONDS,
        help="seconds between poll cycles "
        f"(default {DEFAULT_LESSON_SCHEDULER_INTERVAL_SECONDS})",
    )


def _lessons_command(
    args: argparse.Namespace,
    *,
    database: Database | None,
    client: PlannerClient | None,
) -> int:
    """Route `atlas lessons` report, extraction, scheduler, and gate commands."""
    resolved_db = database if database is not None else Database(args.db)
    if args.lessons_command == "search":
        results = search_lessons(
            LessonRepo(resolved_db).list(),
            " ".join(args.query),
            tag=args.tag,
        )
        if args.json:
            print(json.dumps(lesson_search_results_json(results)))
        else:
            print(render_lesson_search_results(results))
        return EXIT_OK
    if args.lessons_command == "report":
        report = build_lessons_report(
            LessonRepo(resolved_db),
            DebtItemRepo(resolved_db),
            TicketRepo(resolved_db),
            now=datetime.now(UTC),
        )
        if args.json:
            print(json.dumps(lessons_report_json(report)))
        else:
            print(render_lessons_report_markdown(report))
        return EXIT_OK
    if args.lessons_command == "review":
        return _lessons_review(args, resolved_db)
    if args.lessons_command == "show":
        return _lessons_show(args, resolved_db)
    if args.lessons_command == "playbook":
        return _lessons_playbook(args, resolved_db, client=client)
    if args.lessons_command in {"extract", "schedule"}:
        return _lessons_extract_or_schedule(args, resolved_db, client=client)

    repo = LessonRepo(resolved_db)
    now = datetime.now(UTC)
    try:
        if args.lessons_command == "promote":
            lesson = repo.promote(
                args.lesson_id,
                confidence=args.confidence,
                now=now,
            )
            print(
                f"Promoted lesson {lesson.id} to ACTIVE "
                f"(confidence: {lesson.confidence})."
            )
            return EXIT_OK
        if args.lessons_command == "reject":
            lesson = repo.reject(args.lesson_id, now=now)
            print(f"Rejected lesson {lesson.id}; status is ARCHIVED.")
            return EXIT_OK
        if args.lessons_command == "archive":
            lesson = repo.archive(args.lesson_id, now=now)
            print(f"Archived lesson {lesson.id}.")
            return EXIT_OK
        if args.lessons_command == "merge":
            draft, target = repo.merge(
                args.draft_lesson_id,
                args.target_lesson_id,
                now=now,
            )
            print(
                f"Merged DRAFT lesson {draft.id} into ACTIVE lesson {target.id}; "
                f"draft status is ARCHIVED."
            )
            return EXIT_OK
    except (LessonNotFoundError, LessonStateError, LessonValidationError) as error:
        print(error, file=sys.stderr)
        return EXIT_PRECONDITION

    return EXIT_PRECONDITION


def _lesson_show_value(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) if value else "-"
    return str(value)


def _lesson_show_text(record: dict[str, object]) -> str:
    fields = [
        "id",
        "title",
        "category",
        "status",
        "confidence",
        "tags",
        "problem",
        "solution",
        "outcome",
        "source_ticket",
        "related_tickets",
        "related_adr_ids",
        "created_by",
        "created_at",
        "updated_at",
    ]
    return "\n".join(
        f"{field + ':':<17} {_lesson_show_value(record[field])}" for field in fields
    )


def _lessons_show(args: argparse.Namespace, resolved_db: Database) -> int:
    """Show one stored lesson for the ADR-0009 promotion gate.

    The operator-facing id is the canonical dashed UUID printed by
    ``lessons review``. Bad ids, unknown ids, and never-migrated databases are
    clean preconditions, mirroring ``evidence show``.
    """
    try:
        lesson_id = UUID(args.lesson_id)
    except ValueError:
        print(f"not a valid lesson id: {args.lesson_id!r}", file=sys.stderr)
        return EXIT_PRECONDITION

    try:
        lesson = LessonRepo(resolved_db).get(lesson_id)
        if lesson is None:
            print(f"no lesson with id {lesson_id}", file=sys.stderr)
            return EXIT_PRECONDITION
        ticket_keys_by_id = {
            ticket.id: ticket.key for ticket in TicketRepo(resolved_db).list()
        }
    except OperationalError:
        print(
            "database is not initialised (no such table); run the database "
            "migrations before using `atlas lessons show`.",
            file=sys.stderr,
        )
        return EXIT_PRECONDITION

    record = lesson_show_record(lesson, ticket_keys_by_id)
    _emit(record, _lesson_show_text(record), as_json=args.json)
    return EXIT_OK


def _review_line(row: dict[str, object]) -> str:
    prefix = f"{row['id']}  {row['created_at']}  source={row['source_ticket']}"
    if "context_pack_count" in row:
        prefix = (
            f"{prefix}  packs={row['context_pack_count']}  "
            f"last_operator_action={row['last_operator_action_at']}"
        )
    return f"{prefix}  {row['title']}"


def _lessons_review(args: argparse.Namespace, resolved_db: Database) -> int:
    """List DRAFT lessons or stale ACTIVE lessons for operator review."""
    repo = LessonRepo(resolved_db)
    ticket_keys_by_id = {
        ticket.id: ticket.key for ticket in TicketRepo(resolved_db).list()
    }

    if args.stale:
        reviews = repo.list_stale_active()
        rows = [
            lesson_review_row(
                review.lesson,
                ticket_keys_by_id,
                context_pack_count=review.context_pack_count,
            )
            for review in reviews
        ]
        text = (
            "\n".join(["Stale ACTIVE lessons:", *[_review_line(row) for row in rows]])
            if rows
            else "No stale ACTIVE lessons."
        )
        _emit({"stale_lessons": rows}, text, as_json=args.json)
        return EXIT_OK

    drafts = repo.list_drafts()
    rows = [lesson_review_row(lesson, ticket_keys_by_id) for lesson in drafts]
    text = (
        "\n".join(["DRAFT lessons:", *[_review_line(row) for row in rows]])
        if rows
        else "No DRAFT lessons."
    )
    _emit({"draft_lessons": rows}, text, as_json=args.json)
    return EXIT_OK


def _lessons_extract_or_schedule(
    args: argparse.Namespace,
    resolved_db: Database,
    *,
    client: PlannerClient | None,
) -> int:
    """Route lesson extraction and the recurring extraction scheduler."""
    try:
        assert_schema_at_head(resolved_db)
    except SchemaDriftError as error:
        print(error, file=sys.stderr)
        return EXIT_PRECONDITION

    lesson_client = client
    if lesson_client is None:
        try:
            lesson_client = AnthropicPlannerClient()
        except PlannerClientError as error:
            print(error, file=sys.stderr)
            return EXIT_PRECONDITION
    if args.lessons_command == "schedule":
        shutdown = threading.Event()
        _install_shutdown_handlers(shutdown)
        result = run_lesson_scheduler(
            LessonSchedulerConfig(
                db=resolved_db,
                tickets=TicketRepo(resolved_db),
                debt_items=DebtItemRepo(resolved_db),
                client=lesson_client,
            ),
            interval=args.interval,
            once=args.once,
            shutdown=shutdown,
        )
        if args.once:
            print(_format_lesson_scheduler_result(result))
        return EXIT_OK
    ticket = TicketRepo(resolved_db).get_by_key(args.key)
    if ticket is None:
        print(f"no ticket with key {args.key!r}", file=sys.stderr)
        return EXIT_PRECONDITION
    lesson = extract_lesson_for_ticket(
        ticket,
        db=resolved_db,
        client=lesson_client,
        now=datetime.now(UTC),
        trigger=ExtractionTrigger.OPERATOR_REQUEST,
        force=True,
    )
    if lesson is None:
        print(f"Lesson extraction failed for {ticket.key}; see logs.", file=sys.stderr)
        return EXIT_RECORDED_FAILURE
    print(
        f"Extracted DRAFT lesson {lesson.id} for {ticket.key} "
        f"(confidence: {lesson.confidence})."
    )
    return EXIT_OK


def _lessons_playbook(
    args: argparse.Namespace,
    resolved_db: Database,
    *,
    client: PlannerClient | None,
) -> int:
    """Draft a playbook branch from ACTIVE lessons under one tag."""

    lesson_client = client
    if lesson_client is None:
        try:
            lesson_client = AnthropicPlannerClient()
        except PlannerClientError as error:
            print(error, file=sys.stderr)
            return EXIT_PRECONDITION
    try:
        result = draft_playbook_branch(
            LessonRepo(resolved_db),
            args.tag,
            client=lesson_client,
            repo_root=Path(args.repo).resolve(),
            now=datetime.now(UTC),
        )
    except NoActiveLessonsForTagError as error:
        print(error, file=sys.stderr)
        return EXIT_PRECONDITION
    except (PlaybookGenerationError, PlaybookGitError) as error:
        print(error, file=sys.stderr)
        return EXIT_PRECONDITION

    print(f"Drafted playbook {result.path.as_posix()} on branch {result.branch_name}.")
    print(
        "Review the file, commit it, push the branch, and open a PR for "
        "operator review and merge."
    )
    return EXIT_OK


def _format_preflight_report(report: PreflightReport) -> str:
    """Render the findings as a loud, line-per-finding block; the CLI sets the
    exit code from `report.ok`."""

    lines = ["Atlas preflight:"]
    for finding in report.findings:
        if finding.skipped:
            mark = "SKIP"
        elif finding.ok:
            mark = "PASS"
        else:
            mark = "FAIL"
        lines.append(f"  [{mark}] {finding.check_id}: {finding.message}")
    if not report.ok:
        summary = "one or more checks FAILED — resolve before dispatch"
    elif report.skipped:
        summary = "all runnable checks passed, but a check was SKIPPED (see above)"
    else:
        summary = "all checks passed"
    lines.append(f"=> {summary}")
    return "\n".join(lines)


def _preflight_command(
    args: argparse.Namespace,
    *,
    linear_client: LinearClient | None = None,
    model_probe: ModelProbe | None = None,
) -> int:
    """Route `atlas preflight` (ATLAS-136). Builds the live injection from env
    (`linear_client` is injectable for tests), runs `run_preflight` (including
    the opt-in C6 model probe when `--check-model` is passed), prints the
    findings, and returns the exit code by the D2 precedence (fail > skip >
    pass)."""

    try:
        client = (
            linear_client if linear_client is not None else LinearGraphQLClient()
        )  # raises MissingLinearTokenError without a key
        status_map = LinearStatusMap.from_env()  # raises LinearStatusMapError if unset
        team_id = os.environ.get(TEAM_ID_ENV)
        if not team_id:
            raise MissingLinearTokenError(
                f"{TEAM_ID_ENV} is not set; preflight needs the Linear team id to "
                "fetch the team's workflow states (team-scoped, ATLAS-148)"
            )
        project_id = os.environ.get(PROJECT_ID_ENV)
        if not project_id:
            raise MissingLinearTokenError(
                f"{PROJECT_ID_ENV} is not set; preflight needs the Linear project "
                "id (a UUID, not the slug) to resolve the project"
            )
    except (MissingLinearTokenError, LinearStatusMapError) as error:
        print(error, file=sys.stderr)
        return EXIT_PRECONDITION

    report = run_preflight(
        workflow_md_path=Path(args.workflow_md),
        client=client,
        status_map=status_map,
        team_id=team_id,
        project_id=project_id,
        allow_assignee=args.allow_assignee,
        check_model=args.check_model,
        model_probe=model_probe,
        model_probe_timeout=args.model_probe_timeout,
    )
    print(_format_preflight_report(report))
    # Exit precedence (D2): a failing finding dominates (EXIT_RECORDED_FAILURE);
    # else a skipped C6 (the model check could not run) is EXIT_PRECONDITION —
    # consistent with the setup-failure use of that code above; else all-pass.
    if not report.ok:
        return EXIT_RECORDED_FAILURE
    if report.skipped:
        return EXIT_PRECONDITION
    return EXIT_OK


def main(
    argv: list[str] | None = None,
    *,
    database: Database | None = None,
    client: PlannerClient | None = None,
    identity: ModelIdentity | None = None,
    staged_generator: StagedProposalGenerator | None = None,
    github_client: GitHubClient | None = None,
    linear_client: LinearClient | None = None,
    model_probe: ModelProbe | None = None,
) -> int:
    """Entry point. ``database``/``client``/``identity``/``staged_generator``/
    ``github_client``/``linear_client``/``model_probe`` are injectable for
    tests; production builds them from the environment (and the C6 model probe
    shells out to Codex)."""
    args = build_parser().parse_args(argv)
    _configure_logging(bool(getattr(args, "verbose", False)))
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
    if args.command == "pm":
        return _pm_command(args, database=database)
    if args.command == "context":
        return _context_command(args, database=database)
    if args.command == "evidence":
        return _evidence_command(args, database=database, github_client=github_client)
    if args.command == "verify":
        return _verify_command(args, database=database, github_client=github_client)
    if args.command == "confirm":
        return _confirm_command(args, database=database, github_client=github_client)
    if args.command == "preflight":
        return _preflight_command(
            args, linear_client=linear_client, model_probe=model_probe
        )
    if args.command == "lessons":
        return _lessons_command(args, database=database, client=client)
    return EXIT_PRECONDITION  # unreachable: subparser is required


if __name__ == "__main__":
    raise SystemExit(main())
