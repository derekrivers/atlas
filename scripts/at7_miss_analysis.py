#!/usr/bin/env python
"""Offline AT-7 miss analysis — FREE, no API call.

Reads a saved staged proposal and the roadmap, then breaks the AT-7 result into:
  - which hand-written roadmap tickets were COVERED (a proposed ticket's
    source_anchor exactly matched), and
  - which were MISSED, with the roadmap heading each missed ticket lives under,
    plus the proposed anchors that landed NEAR it (same document, different
    heading) — the signal that distinguishes an adjacent-anchor undercount (the
    planner was right, the metric was strict) from a genuine coverage gap (the
    planner never proposed this work).

Run AFTER generating a saved proposal with staged_coverage.py:
    uv run python scripts/at7_miss_analysis.py
    uv run python scripts/at7_miss_analysis.py --proposal /tmp/full_staged_proposal.json
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tests"))

DEFAULT_PROPOSAL = Path("/tmp/full_staged_proposal.json")
ROADMAP = "docs/atlas/implementation-roadmap.md"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proposal", type=Path, default=DEFAULT_PROPOSAL)
    args = parser.parse_args()

    if not args.proposal.exists():
        raise SystemExit(
            f"no saved proposal at {args.proposal}; "
            "generate one first with staged_coverage.py"
        )

    from acceptance_metrics import enumerate_roadmap_tickets

    from atlas.planning.proposal import Proposal

    proposal = Proposal.model_validate_json(args.proposal.read_text(encoding="utf-8"))
    roadmap_text = (REPO_ROOT / ROADMAP).read_text(encoding="utf-8")
    hand_written = enumerate_roadmap_tickets(roadmap_text, path=ROADMAP)

    # The set of anchors the planner actually emitted.
    proposed_anchors = {t.source_anchor for t in proposal.tickets if t.source_anchor}

    # Group proposed anchors by document, to detect "near misses" (right doc,
    # wrong heading) vs. "absent" (planner never touched that document/area).
    proposed_by_doc: dict[str, set[str]] = defaultdict(set)
    for a in proposed_anchors:
        doc = a.split("#", 1)[0]
        proposed_by_doc[doc].add(a)

    covered = []
    missed = []
    for ticket in hand_written:
        anchor = getattr(ticket, "source_anchor", None) or getattr(
            ticket, "anchor", None
        )
        key = getattr(ticket, "key", None) or getattr(ticket, "title", "?")
        if anchor in proposed_anchors:
            covered.append((key, anchor))
        else:
            doc = anchor.split("#", 1)[0] if anchor else "?"
            near = sorted(proposed_by_doc.get(doc, set()))
            # "near" = the planner DID anchor into this document, just a different
            # heading → likely an adjacent-anchor undercount, not a real gap.
            missed.append((key, anchor, near))

    total = len(hand_written)
    print(
        f"=== AT-7 breakdown: {len(covered)}/{total} covered "
        f"({len(covered) / total:.1%}) ===\n"
    )

    adjacent = [m for m in missed if m[2]]  # planner hit the same doc elsewhere
    genuine = [m for m in missed if not m[2]]  # planner never touched this doc

    print(f"MISSED: {len(missed)} total")
    print(
        f"  - likely adjacent-anchor undercount (planner anchored into the "
        f"same doc, different heading): {len(adjacent)}"
    )
    print(
        f"  - likely genuine gap (planner never anchored into this doc): "
        f"{len(genuine)}\n"
    )

    if adjacent:
        print("--- LIKELY ADJACENT (metric undercount; planner probably right) ---")
        for key, anchor, near in adjacent:
            print(f"  roadmap: {key}")
            print(f"    wanted anchor: {anchor}")
            print(
                f"    planner anchored nearby: {', '.join(near[:5])}"
                + (" ..." if len(near) > 5 else "")
            )
        print()

    if genuine:
        print("--- LIKELY GENUINE GAPS (planner proposed nothing in this doc) ---")
        for key, anchor, _ in genuine:
            print(f"  roadmap: {key}  (wanted: {anchor})")
        print()

    # The adjusted-ceiling estimate: if every "adjacent" miss is in fact a correct
    # proposal undercounted by the metric, coverage would be:
    optimistic = (len(covered) + len(adjacent)) / total
    print(
        f"=== floor (exact-anchor): {len(covered) / total:.1%} | "
        f"optimistic ceiling (if all adjacent misses are correct): "
        f"{optimistic:.1%} ==="
    )
    print(
        "  true coverage is somewhere in this band; inspect the adjacent list "
        "to judge how many are genuinely correct."
    )


if __name__ == "__main__":
    main()
