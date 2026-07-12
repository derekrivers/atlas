---
title: "relevant_docs spelling repair: stored active-inbox document references move to their durable processed/ paths"
objective: "No stored ticket field references a retired active-inbox document path; pack rendering's relevant_docs retrieval finds every referenced document."
context: "Routed from ATLAS-162: the ATLAS-159 repair script rewrote source_anchor only, so stub-minted tickets' relevant_docs entries still cite docs/planning/inbox/<name>.md spellings whose files now live under processed/ — soft-skipped by the pack renderer today, degrading packs for exactly the tickets pack embedding is about to serve. Same disease as the anchor repair, same cure shape: stored-data rewrite outside planning for the frozen rows, since planning cannot modify frozen tickets (spec §4) and the affected set spans statuses. Two candidate mechanisms for the plan gate: (a) extend scripts/repair_stub_anchors.py's fail-closed pattern to relevant_docs entries (verified old-path-retired, new-path-present, per-row printed, idempotent, named-set-scoped — the ATLAS-159 branch (a) precedent, second use); (b) if ATLAS-161's normalization helper lands first and naturally covers read-time normalization of both spellings, a read-side fix may make the data rewrite unnecessary — the gate checks 161's state and rules. Enumerate the affected set live at plan time (do not trust this stub's count; the ATLAS-159 '14' went stale inside one day)."
ticket_type: "bug"
epic_ref: "ATLAS-E3"
acceptance_criteria:
  - "Post-fix, a scan of every stored ticket's relevant_docs finds zero entries whose path exists only under processed/; the scan is a named test over a live-shaped fixture."
  - "Pack rendering for a previously-degraded fixture ticket includes the formerly-skipped document; seeded regression proves the pre-fix skip."
  - "If mechanism (a) is ruled: the script refuses on any row outside the enumerated set, is idempotent, prints each rewrite, and bumps updated_at only on rewritten rows."
  - "Negative: a relevant_docs entry whose document is genuinely absent from both locations still soft-skips (or fails per the renderer's existing contract) — the repair fixes spellings, not existence."
non_goals:
  - "No renderer contract changes beyond what mechanism (b) would entail if ruled; no source_anchor changes (done); no ATLAS-161 collapse work."
test_requirements:
  - "Fixture-driven, ATLAS_LIVE_TESTS=0, seeds `assert 1 == 2` (B011)."
definition_of_done:
  - "All acceptance criteria evidenced by named tests; full gate sweep green; the ATLAS-162 follow-up routing closed with a pointer to this ticket; debt-register entry if mechanism (a) is ruled (second use of the repair exception, so noted)."
---

# relevant_docs spelling repair

The anchor repair's little sibling: same disease, same cure, smaller set.
