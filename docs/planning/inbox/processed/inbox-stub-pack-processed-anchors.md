---
title: "Context-pack anchor resolution covers processed stubs: packs render for stub-minted tickets"
objective: "A context pack for a stub-minted ticket resolves its source_anchor against the processed/ set instead of failing on a corpus-only index."
context: "Routed from the ATLAS-159 completion report as a pre-existing gap: the context-pack path builds its anchor index over the canonical corpus only, so a stub-minted ticket's source_anchor (now durably inbox/processed/<name>.md#slug after ATLAS-159) does not resolve there, and pack rendering for such tickets fails or degrades. With every hand-authored ticket now entering via stubs, stub-minted tickets are the growth class — and pack embedding is the Phase 8 tail's next milestone, so this gap sits directly on the autonomy path. Fix: the pack path's document collection includes the committed processed/ set (reuse ATLAS-159's collect_processed_documents; same fail-closed contract), so pack anchor resolution and gate 4 agree on what resolves. Scope check at the plan gate: confirm whether pack VALIDATION shares the same index (likely yes via context/validation.py) and cover both if so."
ticket_type: "bug"
epic_ref: "ATLAS-E7"
acceptance_criteria:
  - "A pack rendered for a ticket anchored at inbox/processed/<name>.md#slug resolves the anchor and includes the stub content in the pack's source excerpt; proven with a stub-minted fixture ticket."
  - "Seeded regression: pre-fix, the same fixture fails or degrades on anchor resolution; post-fix it renders."
  - "Negative: a genuinely dangling anchor still fails pack validation — resolution gains the processed/ set, not leniency."
  - "Corpus-anchored tickets render byte-identically (existing pack tests pass unmodified)."
non_goals:
  - "No pack format, embedding, or Linear-description changes (Phase 8 pack-embedding territory — named, not entered); no index changes outside the pack/validation path."
test_requirements:
  - "Pack-level fixtures, ATLAS_LIVE_TESTS=0, seeds `assert 1 == 2` (B011)."
definition_of_done:
  - "All acceptance criteria evidenced by named tests; full gate sweep green; the routed-follow-up pointer updated in the same change."
---

# Packs see processed stubs

What gate 4 can resolve, a pack can cite.
