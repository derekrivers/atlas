#!/usr/bin/env python
"""Offline AT-7 analysis (ATLAS-112) — FREE, no API call.

Reads a saved staged proposal and the roadmap and reports the two AT-7
coverage metrics side by side, so the operator can decide which one the AT-7
bar should use:

  - EXACT-ANCHOR coverage (the original ATLAS-29 metric): a hand-written
    roadmap ticket is covered iff a proposed ticket's source_anchor equals
    its roadmap anchor exactly. This measures anchoring-convention agreement.
  - CONTENT coverage (ATLAS-112): a roadmap ticket is covered iff some
    proposed ticket's title is similar enough to the roadmap title, document-
    independent. This measures work coverage. Threshold is recorded, not tuned.

For each exact-anchor MISS it also reports the corrected adjacency split —
"adjacent" now means a planner ticket anchored to a heading NEAR the wanted
heading within the same document (proximity in the heading index), not the
defeated "any anchor in the same document" test that flagged every roadmap-doc
miss as adjacent (a false 100% ceiling).

Finally it prints a FALSIFIABLE verdict on the ATLAS-112 finding: whether the
exact-anchor misses are predominantly design-doc-anchored Phase 4-8 tickets
(work present, anchored off the roadmap). The verdict can read CONTRADICTED —
the tool can prove the finding wrong, not only confirm it.

This is operator/dev tooling, not part of the Atlas system, and makes no model
call. Run against a proposal saved by staged_coverage.py:
    uv run python scripts/at7_miss_analysis.py
    uv run python scripts/at7_miss_analysis.py --proposal /tmp/full_staged_proposal.json
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tests"))

DEFAULT_PROPOSAL = Path("/tmp/full_staged_proposal.json")
ROADMAP = "docs/atlas/implementation-roadmap.md"
PHASE_4_8 = frozenset({4, 5, 6, 7, 8})

_PHASE_RE = re.compile(r"^#\s+Phase\s+(\d+)\b")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
_FENCE_RE = re.compile(r"^\s*```")


def _phase_by_slug(roadmap_text: str) -> dict[str, int]:
    """Map each heading slug in the roadmap to the Phase number it sits under,
    so a missed ticket's anchor can be attributed to a phase. Fence-aware."""
    from acceptance_metrics import slugify

    by_slug: dict[str, int] = {}
    current_phase: int | None = None
    in_fence = False
    for line in roadmap_text.splitlines():
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        phase = _PHASE_RE.match(line)
        if phase:
            current_phase = int(phase.group(1))
        heading = _HEADING_RE.match(line)
        if heading and current_phase is not None:
            by_slug[slugify(heading.group(2))] = current_phase
    return by_slug


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proposal", type=Path, default=DEFAULT_PROPOSAL)
    args = parser.parse_args()

    if not args.proposal.exists():
        raise SystemExit(
            f"no saved proposal at {args.proposal}; "
            "generate one first with staged_coverage.py (an API run)"
        )

    from acceptance_metrics import (
        anchor_coverage,
        content_coverage,
        enumerate_roadmap_tickets,
        heading_index,
        is_adjacent_anchor,
    )

    from atlas.planning import reconciler
    from atlas.planning.proposal import Proposal

    proposal = Proposal.model_validate_json(args.proposal.read_text(encoding="utf-8"))
    roadmap_text = (REPO_ROOT / ROADMAP).read_text(encoding="utf-8")
    hand_written = enumerate_roadmap_tickets(roadmap_text, path=ROADMAP)
    total = len(hand_written)

    proposed_anchors = {t.source_anchor for t in proposal.tickets if t.source_anchor}
    phase_by_slug = _phase_by_slug(roadmap_text)
    roadmap_headings = heading_index(roadmap_text)

    # --- both metrics, side by side -----------------------------------------
    exact = anchor_coverage(
        [t.source_anchor for t in proposal.tickets if t.source_anchor],
        roadmap_text,
        path=ROADMAP,
    )
    content = content_coverage(proposal.tickets, roadmap_text, path=ROADMAP)

    print(
        f"proposal: {len(proposal.epics)} epics, {len(proposal.tickets)} tickets, "
        f"{len(proposal.dependencies)} dependencies "
        f"({args.proposal})\n"
    )
    print("=== AT-7 coverage, two metrics (ATLAS-112) ===")
    print(f"  EXACT-ANCHOR (anchoring-convention agreement): {exact:.1%}")
    print(
        f"  CONTENT      (work coverage, threshold "
        f"{content.threshold}):           {content.fraction:.1%}"
    )
    print(
        "  exact-anchor is a strict floor; content is document-independent. "
        "The gap between them is the anchoring-convention signal.\n"
    )

    # --- exact-anchor misses, corrected adjacency ---------------------------
    covered = [t for t in hand_written if t.anchor in proposed_anchors]
    missed = [t for t in hand_written if t.anchor not in proposed_anchors]

    adjacent = []
    genuine = []
    for ticket in missed:
        near = sorted(
            anchor
            for anchor in proposed_anchors
            if is_adjacent_anchor(ticket.anchor, anchor, roadmap_headings)
        )
        (adjacent if near else genuine).append((ticket, near))

    print(f"=== exact-anchor breakdown: {len(covered)}/{total} covered ===")
    print(f"MISSED: {len(missed)} total")
    print(
        f"  - adjacent (planner ticket anchored to a NEAR heading in the same "
        f"doc — metric undercount): {len(adjacent)}"
    )
    print(
        f"  - not adjacent (different-document anchoring or absent work): "
        f"{len(genuine)}\n"
    )
    # The old tool reported floor+adjacent as an "optimistic ceiling" and, with
    # the defeated same-doc test, that ceiling was a false 100%. With the
    # proximity rule it is a real, usually-small number.
    ceiling = (len(covered) + len(adjacent)) / total if total else 1.0
    print(
        f"  corrected adjacent-ceiling (floor + genuine adjacency): {ceiling:.1%} "
        f"(was a false 100% under the same-doc test)\n"
    )

    # --- falsifiable verdict on the ATLAS-112 finding -----------------------
    # The finding: exact-anchor misses are predominantly Phase 4-8 tickets whose
    # WORK is present but anchored to a design doc, not the roadmap epic heading.
    design_doc_phase48 = 0
    miss_rows = []
    for ticket in missed:
        slug = ticket.anchor.split("#", 1)[1] if "#" in ticket.anchor else ""
        phase = phase_by_slug.get(slug)
        # best content match for this missed ticket, and where it is anchored.
        best_score = 0.0
        best_doc: str | None = None
        for proposed_ticket in proposal.tickets:
            score = reconciler.similarity(ticket.title, "", proposed_ticket.title, "")
            if score > best_score:
                best_score = score
                best_doc = (proposed_ticket.source_anchor or "").split("#", 1)[0]
        work_present = best_score >= content.threshold
        off_roadmap = best_doc is not None and best_doc != ROADMAP
        is_design_doc_phase48 = phase in PHASE_4_8 and work_present and off_roadmap
        if is_design_doc_phase48:
            design_doc_phase48 += 1
        miss_rows.append((ticket.key, phase, work_present, best_doc, best_score))

    predominant = missed and design_doc_phase48 > len(missed) / 2
    verdict = "CONFIRMED" if predominant else "CONTRADICTED"
    print("=== ATLAS-112 finding verdict (falsifiable) ===")
    print(
        f"  {design_doc_phase48}/{len(missed)} exact-anchor misses are "
        f"design-doc-anchored Phase 4-8 tickets (work present, anchored "
        f"off-roadmap)."
    )
    print(
        f"  Finding {verdict}: the exact-anchor misses are "
        f"{'predominantly' if predominant else 'NOT predominantly'} "
        f"design-doc-anchored Phase 4-8 work.\n"
    )
    if miss_rows:
        print("  per-miss detail (key | phase | work-present | best-match doc):")
        for key, phase, work_present, best_doc, best_score in miss_rows:
            phase_str = f"P{phase}" if phase is not None else "P?"
            doc = (best_doc or "?").replace("docs/atlas/", "")
            print(
                f"    {key:<10} {phase_str:<3} "
                f"{'yes' if work_present else 'no ':<3} "
                f"{doc}  (score {best_score:.2f})"
            )
        print()

    # --- run-to-run spread: honest single-capture limitation ----------------
    print("=== run-to-run spread ===")
    print(
        "  Only one capture is durably on disk (this proposal). The "
        "documented Run A (82.6% exact-anchor) was never saved and is not "
        "offline-reproducible without an API call (not spent here)."
    )
    print(
        "  Exact-anchor spread is therefore the documented swing only "
        "(82.6% vs this capture); content-coverage spread needs a second "
        "saved capture — a free byproduct of the next staged run, scored by "
        "this same tool."
    )


if __name__ == "__main__":
    main()
