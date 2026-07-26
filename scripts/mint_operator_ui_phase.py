"""Mint the Phase 11 Operator UI epic and its ticket batch in one PlanRun.

Meta work, no store ticket of its own: label it ATLAS-043M.

Why a script rather than `atlas plan --stubs-only`
--------------------------------------------------
Nineteen of the twenty-one Phase 11 stubs belong to an epic that does not
exist yet. Two independent rules make that a dead end on the sanctioned
path:

- `pipeline.run_stubs_only_plan` refuses a stub whose ``epic_ref`` names an
  epic absent from the backlog (``StubEpicRefError``): a stubs-only run has
  no model, and only a model proposes new epics.
- Gate 5 (``GATE5_ORPHAN_EPIC``) refuses an epic with no tickets, so the
  epic cannot be minted alone first and the stubs applied after.

So the epic and its tickets must enter in one proposal. That is exactly the
shape ATLAS-029M used to open ``ATLAS-E12`` alongside ATLAS-187..192
(`scripts/reconcile_claimed_keys.py`), and this script is modelled on it.

What it does NOT do
-------------------
It invents no key. The epic enters as an ADD with ``key=None`` and the
reconciler assigns ``ATLAS-E13`` from the epic counter, exactly as it
assigns ticket keys from 207 (WORKFLOW.md, "Ticket key identity"; ADR-0007).
It writes nothing to ``docs/planning/`` itself — ``run_apply`` does, and
``run_apply`` also retires the consumed stubs to ``inbox/processed/``.

The one transform, stated plainly
---------------------------------
A new epic has no key at proposal time; it is referenced positionally as
``new_epic:<index>``, and that index depends on how many epics the backlog
echo emits. The stubs on disk therefore say what they mean —
``epic_ref: ATLAS-E13`` — and this script rebinds that ref to the computed
positional identity on IN-MEMORY copies of the stub documents before
promotion. Nothing on disk changes, ``input_doc_shas`` is computed from the
real files, and promotion never reads document content beyond the
front-matter it parses. The rebind count is asserted: if it is not exactly
the number of stubs declaring the new epic, the run refuses.

After this run, the same stubs would apply unmodified through
`atlas plan --stubs-only`, because the epic then exists. That property is
the point: the script is a one-time door, not a parallel path.

Usage
-----
    uv run python scripts/mint_operator_ui_phase.py --dry-run
    uv run python scripts/mint_operator_ui_phase.py --apply
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from atlas.core.anchors import AnchorIndex, SourceDocument
from atlas.core.enums import RiskLevel
from atlas.core.models import PlanRun, PlanRunStatus
from atlas.planning.apply import DEFAULT_INBOX_DIR, ApplyDecision, run_apply
from atlas.planning.gates import run_gates
from atlas.planning.ingestion import (
    collect_inbox_documents,
    collect_input_documents,
    collect_processed_documents,
    durable_alias_documents,
)
from atlas.planning.pipeline import _echo_backlog_proposal
from atlas.planning.promotion import promote_inbox_stubs
from atlas.planning.proposal import Proposal, ProposalEpic
from atlas.planning.reconciler import DEFAULT_SIMILARITY_THRESHOLD, Backlog, reconcile
from atlas.storage import (
    Database,
    EpicRepo,
    PlanRunRepo,
    ProductRepo,
    TicketDependencyRepo,
    TicketRepo,
)
from atlas.storage.db import resolve_url

# --- Pinned identity of this one-shot mint -------------------------------

EPIC_TITLE = "Operator UI (Read Surface)"
EPIC_DESCRIPTION = (
    "Read-only browser surface over the Phase 10 operator API: the ticket "
    "board and detail, the review queue, dependency readiness and the "
    "critical path, lessons, and system status. Read-only for this phase; "
    "writes, authentication and remote binding stay with the writeable API "
    "phase. Implements Phase 11 per docs/atlas/operator-ui.md."
)
EPIC_OBJECTIVE = (
    "Give the operator a browser instrument over Atlas operational state "
    "that reads the /api/v1 projections directly, so the review queue, "
    "ticket definitions, evidence, dependency readiness and the lesson "
    "draft queue are legible without a CLI query or a database read."
)
EPIC_SOURCE_ANCHOR = "docs/atlas/operator-ui.md#operator-ui-design-phase-11"
EPIC_PRIORITY = 0
EPIC_RISK = RiskLevel.MEDIUM

# The ref the stubs carry on disk, rebound in memory to the positional
# identity the reconciler understands for a not-yet-keyed epic.
STUB_EPIC_REF = "ATLAS-E13"

PLAN_RUN_ID = uuid5(NAMESPACE_URL, "atlas:operator-ui-phase-11:mint")
PROMPT_VERSION = "operator-ui-phase-11-mint-v1"
MODEL_PROVIDER = "none"
MODEL_NAME = "operator-ui-phase-11-mint"

# Every stub this mint expects, and the epic each one declares. The batch is
# pinned by name so a stray or missing file refuses the run instead of
# silently minting a different phase.
EXPECTED_STUBS: dict[str, str] = {
    "inbox-stub-ui-scaffold.md": STUB_EPIC_REF,
    "inbox-stub-ui-theme-contract.md": STUB_EPIC_REF,
    "inbox-stub-ui-openapi-client.md": STUB_EPIC_REF,
    "inbox-stub-ui-query-layer.md": STUB_EPIC_REF,
    "inbox-stub-ui-app-shell.md": STUB_EPIC_REF,
    "inbox-stub-ui-e2e-harness.md": STUB_EPIC_REF,
    "inbox-stub-ui-ci-pipeline.md": STUB_EPIC_REF,
    "inbox-stub-ui-board-view.md": STUB_EPIC_REF,
    "inbox-stub-ui-ticket-detail.md": STUB_EPIC_REF,
    "inbox-stub-ui-ticket-evidence-tab.md": STUB_EPIC_REF,
    "inbox-stub-ui-ticket-dependencies-tab.md": STUB_EPIC_REF,
    "inbox-stub-ui-review-queue-view.md": STUB_EPIC_REF,
    "inbox-stub-ui-critical-path-view.md": STUB_EPIC_REF,
    "inbox-stub-ui-lessons-view.md": STUB_EPIC_REF,
    "inbox-stub-ui-overview-dashboard.md": STUB_EPIC_REF,
    "inbox-stub-ui-epic-grouping.md": STUB_EPIC_REF,
    "inbox-stub-ui-dependency-graph-view.md": STUB_EPIC_REF,
    "inbox-stub-ui-a11y-responsive.md": STUB_EPIC_REF,
    "inbox-stub-ui-open-source-readiness.md": STUB_EPIC_REF,
    "inbox-stub-api-epics-read.md": "ATLAS-E12",
    "inbox-stub-api-dependency-graph-read.md": "ATLAS-E12",
}

_EPIC_REF_LINE = re.compile(r"^epic_ref:[ \t]*[\"']?ATLAS-E13[\"']?[ \t]*$", re.M)


class MintError(RuntimeError):
    """The bounded mint cannot safely proceed."""


# --- Preflight -----------------------------------------------------------


def _assert_epic_absent(database: Database) -> None:
    for epic in EpicRepo(database).list():
        if epic.title == EPIC_TITLE:
            raise MintError(
                f"an epic titled {EPIC_TITLE!r} already exists ({epic.key}); "
                "this mint has already run. Apply the stubs through "
                "`atlas plan --stubs-only` instead."
            )


def _assert_no_unrelated_proposed_run(database: Database) -> None:
    proposed = PlanRunRepo(database).latest_proposed()
    if proposed is not None and proposed.id != PLAN_RUN_ID:
        raise MintError(
            f"an unrelated proposed PlanRun {proposed.id} exists; disposition "
            "it before minting (see `atlas plan --reject`)"
        )


def _assert_expected_batch(inbox_documents: list[SourceDocument]) -> None:
    found = {Path(document.path).name for document in inbox_documents}
    expected = set(EXPECTED_STUBS)
    if found != expected:
        missing = sorted(expected - found)
        extra = sorted(found - expected)
        raise MintError(
            "the active inbox does not match the pinned Phase 11 batch; "
            f"missing={missing} unexpected={extra}. Every active stub is "
            "consumed by this run, so the batch must be exactly the "
            "twenty-one Phase 11 stubs and nothing else."
        )


# --- The one transform ---------------------------------------------------


def _rebind_epic_ref(
    inbox_documents: list[SourceDocument], new_epic_ref: str
) -> list[SourceDocument]:
    """Return in-memory copies with ``ATLAS-E13`` rebound to ``new_epic:<n>``.

    Nothing on disk changes. The count is asserted against the pinned batch,
    so a stub that quietly stopped declaring the new epic refuses the run.
    """
    expected = sum(1 for ref in EXPECTED_STUBS.values() if ref == STUB_EPIC_REF)
    rebound: list[SourceDocument] = []
    changed = 0
    for document in inbox_documents:
        content, count = _EPIC_REF_LINE.subn(
            f"epic_ref: {new_epic_ref}", document.content, count=1
        )
        changed += count
        # SourceDocument is a frozen dataclass; the sha is deliberately left
        # as the on-disk value, because input_doc_shas must describe the real
        # files apply re-collects and compares against (AT-5 staleness).
        rebound.append(replace(document, content=content))
    if changed != expected:
        raise MintError(
            f"expected to rebind {expected} stub epic refs, rebound {changed}; "
            "refusing rather than minting a partially-anchored batch"
        )
    return rebound


# --- Proposal ------------------------------------------------------------


def _new_epic() -> ProposalEpic:
    return ProposalEpic(
        key=None,  # an ADD: the reconciler assigns ATLAS-E13 (ADR-0007)
        title=EPIC_TITLE,
        description=EPIC_DESCRIPTION,
        objective=EPIC_OBJECTIVE,
        priority=EPIC_PRIORITY,
        risk_level=EPIC_RISK,
        source_anchor=EPIC_SOURCE_ANCHOR,
    )


def _build(repo_root: Path, database: Database, inbox_dir: Path):
    documents = collect_input_documents(repo_root)
    if not documents:
        raise MintError(f"no planner input documents under {repo_root}")
    inbox_documents = collect_inbox_documents(repo_root, inbox_dir)
    _assert_expected_batch(inbox_documents)
    processed_documents = collect_processed_documents(repo_root, inbox_dir)

    anchor_index = AnchorIndex.build(
        documents
        + inbox_documents
        + processed_documents
        + durable_alias_documents(inbox_documents, processed_documents)
    )
    input_doc_shas = {
        doc.path: doc.sha for doc in documents + inbox_documents + processed_documents
    }

    backlog = Backlog(
        epics=EpicRepo(database).list(),
        tickets=TicketRepo(database).list(),
        dependencies=TicketDependencyRepo(database).list(),
    )
    backlog_keys = {epic.key for epic in backlog.epics} | {
        ticket.key for ticket in backlog.tickets
    }

    # Verbatim keyed echo, then the one new epic appended. Its positional
    # identity is its index in the echoed list — computed, never assumed.
    echoed = _echo_backlog_proposal(backlog)
    new_epic_index = len(echoed.epics)
    new_epic_ref = f"new_epic:{new_epic_index}"
    proposal = Proposal(
        epics=[*echoed.epics, _new_epic()],
        tickets=list(echoed.tickets),
        dependencies=list(echoed.dependencies),
        planner_notes=[
            "Phase 11 Operator UI mint (ATLAS-043M): one epic and its stub "
            "batch in one proposal, because gate 5 forbids an orphan epic. "
            "Zero model calls."
        ],
    )

    tickets_before = len(proposal.tickets)
    proposal = promote_inbox_stubs(
        proposal,
        _rebind_epic_ref(inbox_documents, new_epic_ref),
        backlog,
        anchor_index,
    )
    promotion_indices = frozenset(range(tickets_before, len(proposal.tickets)))

    failures = run_gates(
        proposal, current_backlog_keys=backlog_keys, anchor_index=anchor_index
    )
    if failures:
        raise MintError(
            "gates refused the mint proposal:\n"
            + "\n".join(f"  gate {f.gate} {f.code}: {f.reason}" for f in failures)
        )

    diff = reconcile(
        proposal,
        backlog,
        similarity_threshold=DEFAULT_SIMILARITY_THRESHOLD,
        promotion_indices=promotion_indices,
    )
    return proposal, diff, input_doc_shas, new_epic_ref


# --- Driver --------------------------------------------------------------


def _record_proposed(
    database: Database,
    proposal: Proposal,
    diff,
    input_doc_shas: dict[str, str],
    now: datetime,
) -> PlanRun:
    existing = PlanRunRepo(database).latest_proposed()
    if existing is not None and existing.id == PLAN_RUN_ID:
        return existing
    product = ProductRepo(database).get_by_key("ATLAS")
    if product is None:
        raise MintError("no ATLAS product exists in the target store")
    run = PlanRun(
        id=PLAN_RUN_ID,
        product_id=product.id,
        status=PlanRunStatus.PROPOSED,
        input_doc_shas=input_doc_shas,
        model_provider=MODEL_PROVIDER,
        model_name=MODEL_NAME,
        model_parameters={},
        prompt_version=PROMPT_VERSION,
        prompt_hash=hashlib.sha256(b"").hexdigest(),
        similarity_threshold=DEFAULT_SIMILARITY_THRESHOLD,
        raw_output_hash=hashlib.sha256(
            json.dumps(proposal.model_dump(mode="json"), sort_keys=True).encode()
        ).hexdigest(),
        generation_stages=[],
        proposal=proposal.model_dump(mode="json"),
        diff_summary=diff.as_summary(),
        failure_reason=None,
        approved_by=None,
        created_at=now,
        applied_at=None,
    )
    PlanRunRepo(database).add(run)
    return run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--inbox-dir", type=Path, default=DEFAULT_INBOX_DIR)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="build, gate and reconcile; print the diff; persist nothing",
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help="persist the proposed PlanRun and apply it after confirmation",
    )
    args = parser.parse_args(argv)

    database = Database(resolve_url(args.database_url))
    now = datetime.now(UTC)

    try:
        _assert_epic_absent(database)
        _assert_no_unrelated_proposed_run(database)
        proposal, diff, shas, new_epic_ref = _build(
            args.repo_root, database, args.inbox_dir
        )
    except MintError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2

    print(f"new epic positional ref: {new_epic_ref}")
    print(
        f"proposal: {len(proposal.epics)} epics, {len(proposal.tickets)} tickets, "
        f"{len(proposal.dependencies)} dependencies"
    )
    print("diff summary:")
    print(json.dumps(diff.as_summary(), indent=2, sort_keys=True))

    if args.dry_run:
        print("\ndry run: nothing persisted.")
        return 0

    _record_proposed(database, proposal, diff, shas, now)

    def confirm(pending) -> ApplyDecision:
        print("\nApply this diff? Type 'apply' to confirm: ", end="")
        return (
            ApplyDecision.CONFIRMED
            if input().strip() == "apply"
            else ApplyDecision.REJECTED
        )

    result = run_apply(
        repo_root=args.repo_root,
        database=database,
        now=now,
        confirm=confirm,
        inbox_dir=args.inbox_dir,
    )
    print(f"\noutcome: {result.outcome}")
    if result.outcome == "applied":
        epic = next(
            (e for e in EpicRepo(database).list() if e.title == EPIC_TITLE), None
        )
        print(f"minted epic: {epic.key if epic else '<not found>'}")
        print("stubs retired to docs/planning/inbox/processed/")
        print("review the docs/planning/ renders and commit them.")
    return 0 if result.outcome == "applied" else 1


if __name__ == "__main__":
    raise SystemExit(main())
