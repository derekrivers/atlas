---
title: "Collapse pre-pass anchor normalization: old-spelling stub re-emissions still collapse"
objective: "A model re-emission citing a stub's active-inbox anchor spelling collapses into the promotion ticket exactly as a durable-spelling citation does."
context: "ATLAS-159 moved promotion anchors to the durable inbox/processed/ path, which narrowed ATLAS-151's collapse rule: the pre-pass matches on anchor equality, and a model re-emission citing the OLD active-inbox spelling (docs/planning/inbox/<name>.md#slug) no longer equals the promotion ticket's durable spelling (docs/planning/inbox/processed/<name>.md#slug), so it escapes collapse and would mint a duplicate. The narrowing was kept loud, not silent: ATLAS-159 added a dedicated boundary test pinning the escape and routed this ~3-line fix (owner: this stub). Fix: normalize both spellings to a canonical form before the equality check in the collapse pre-pass — inbox/<name> and inbox/processed/<name> are the same stub identity. Only the pre-pass comparison normalizes; stored anchors, gate 4 resolution, and the index are untouched."
ticket_type: "bug"
epic_ref: "ATLAS-E3"
acceptance_criteria:
  - "A keyless model ticket citing the active-inbox spelling of a promotion ticket's stub collapses into the promotion ticket; proven by flipping the ATLAS-159 boundary test from pinning the escape to pinning the collapse (deletion over annotation — the escape test is replaced, its replacement named)."
  - "Durable-spelling re-emissions still collapse (existing ATLAS-151 tests pass unmodified)."
  - "Negative: a ticket citing a genuinely different stub's anchor (either spelling) is not collapsed."
  - "Normalization applies only in the collapse pre-pass equality; stored anchors and gate-4 resolution are byte-identical (existing gate/promotion tests pass unmodified)."
non_goals:
  - "No change to promotion anchor assignment, gate 4, the anchor index, or collapse behaviour for non-stub anchors."
test_requirements:
  - "Reconciler-level fixtures, ATLAS_LIVE_TESTS=0, seeds `assert 1 == 2` (B011); both spellings appear as named fixtures."
definition_of_done:
  - "All acceptance criteria evidenced by named tests; full gate sweep green; the ATLAS-159 completion report's follow-up 1 pointer in the debt register (if present) updated to this ticket."
---

# Collapse anchor normalization

Two spellings, one stub, one ticket.
