#!/usr/bin/env python
"""Diagnostic: run the staged planner against the real corpus and measure AT-7
coverage, OR re-score a previously-saved proposal offline (no API call).

This is operator/dev tooling, not part of the Atlas system. It exists because
measuring AT-7 by hand was costing repeated 20-40 minute, multi-call API runs,
and a throwaway inline command lost the (expensive) proposal every time a
scoring bug fired. This script fixes that: it SAVES the proposal to disk BEFORE
scoring, so a scoring error never costs you the generated proposal, and you can
re-score the saved proposal for free as many times as you like.

The production home for this logic is ATLAS-107 (acceptance coverage for staged
generation, AT-1/AT-7 over the staged path) — a gated ticket. This script is the
working reference for that, not a replacement.

Usage:
    # Generate (one ~20-40 min staged API run), save, and score:
    ATLAS_LIVE_TESTS=1 uv run python scripts/staged_coverage.py

    # Re-score the saved proposal offline — FREE, no API call:
    uv run python scripts/staged_coverage.py --score-only

    # Generate to a custom path:
    ATLAS_LIVE_TESTS=1 uv run python scripts/staged_coverage.py --out /tmp/proposal.json
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

# The acceptance metric helpers live under tests/.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tests"))

DEFAULT_OUT = Path("/tmp/full_staged_proposal.json")
ROADMAP = "docs/atlas/implementation-roadmap.md"


def _coerce_proposal(raw_proposal):  # type: ignore[no-untyped-def]
    """run_plan stores the proposal as a dict (or model); normalise to Proposal."""
    from atlas.planning.proposal import Proposal, parse_proposal

    if raw_proposal is None or raw_proposal == {}:
        raise SystemExit(
            "no proposal on the PlanRun — the run did not reach 'proposed'"
        )
    if isinstance(raw_proposal, Proposal):
        return raw_proposal
    if isinstance(raw_proposal, dict):
        return Proposal.model_validate(raw_proposal)
    if isinstance(raw_proposal, str):
        return parse_proposal(raw_proposal)
    return Proposal.model_validate(dict(raw_proposal))


def _score(proposal, roadmap_path: str) -> None:  # type: ignore[no-untyped-def]
    from acceptance_metrics import anchor_coverage, enumerate_roadmap_tickets

    text = (REPO_ROOT / roadmap_path).read_text(encoding="utf-8")
    hand_written = enumerate_roadmap_tickets(text, path=roadmap_path)
    anchors = [t.source_anchor for t in proposal.tickets]
    coverage = anchor_coverage(anchors, text, path=roadmap_path)

    print(
        f"proposal: {len(proposal.epics)} epics, "
        f"{len(proposal.tickets)} tickets, "
        f"{len(proposal.dependencies)} dependencies"
    )
    print(
        f"=== AT-7 coverage vs {roadmap_path}: "
        f"{coverage:.1%} ({len(hand_written)} hand-written tickets) ==="
    )
    print(
        "  (conservative floor: exact-anchor matching undercounts a "
        "semantically-correct ticket anchored to an adjacent heading)"
    )


def generate_and_score(out_path: Path) -> None:
    from test_models_validation import product_kwargs

    from atlas.core.models import Product
    from atlas.planning.client import AnthropicPlannerClient
    from atlas.planning.pipeline import run_plan
    from atlas.planning.staged import TemplateStagedGenerator
    from atlas.storage.db import Database
    from atlas.storage.repositories import ProductRepo

    tmp = tempfile.mkdtemp()
    database = Database(f"sqlite:///{tmp}/atlas.db")
    database.create_all()
    ProductRepo(database).add(Product(**product_kwargs()))

    client = AnthropicPlannerClient()
    print(
        "running staged plan against the full corpus (~20-40 min, multiple calls)...",
        flush=True,
    )
    result = run_plan(
        repo_root=REPO_ROOT,
        database=database,
        client=client,
        identity=client.identity,
        now=datetime.now(UTC),
        staged_generator=TemplateStagedGenerator(),
    )
    run = result.plan_run

    print(
        f"status: {result.status} "
        f"| stages: {len(run.generation_stages)} "
        f"| failure: {run.failure_reason}"
    )

    proposal = _coerce_proposal(run.proposal)

    # SAVE FIRST — so a scoring error never costs the (expensive) proposal.
    out_path.write_text(proposal.model_dump_json(indent=2), encoding="utf-8")
    print(f"saved proposal to {out_path} (re-score offline with --score-only)")

    _score(proposal, ROADMAP)


def score_only(in_path: Path) -> None:
    from atlas.planning.proposal import Proposal

    if not in_path.exists():
        raise SystemExit(
            f"no saved proposal at {in_path}; run without --score-only to generate one"
        )
    proposal = Proposal.model_validate_json(in_path.read_text(encoding="utf-8"))
    _score(proposal, ROADMAP)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--score-only",
        action="store_true",
        help="re-score a previously-saved proposal offline (no API call)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUT,
        help=f"path to save/read the proposal (default: {DEFAULT_OUT})",
    )
    args = parser.parse_args()

    if args.score_only:
        score_only(args.out)
    else:
        generate_and_score(args.out)


if __name__ == "__main__":
    main()
