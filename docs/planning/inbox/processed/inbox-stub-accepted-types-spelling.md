---
title: "Accepted-types spelling: admit the live canceled and duplicate state types for rejected"
objective: "Preflight C2 passes on the rejected mappings: validate_against_states accepts the live board's canceled and duplicate Linear state types for TicketStatus.REJECTED."
context: "Known gap flagged for follow-up (not folded in) by ATLAS-148: _ACCEPTED_TYPES admits only 'cancelled' for rejected, while the live board reports type 'canceled' (US spelling) for the Canceled state and type 'duplicate' for the Duplicate state — pm-engine-and-linear-sync.md documents both under the state-map completeness table. The sync tick does not run this validation and is unaffected, but preflight C2 fails on the rejected mappings the moment the operator adds the documented Duplicate entry (cd8e7c95-8a25-48ad-b0ef-19e00f000e70) to LINEAR_STATE_MAP — which the apply-and-cancel disposition for duplicate mints depends on to pull board Duplicates to rejected with full semantic honesty."
ticket_type: "bug"
epic_ref: "ATLAS-E6"
acceptance_criteria:
  - "_ACCEPTED_TYPES for rejected admits {'cancelled', 'canceled', 'duplicate'}; a unit test proves validate_against_states passes a state map containing the documented Canceled (type canceled) and Duplicate (type duplicate) entries."
  - "The contradiction filter still rejects: a completed-type state mapped to rejected, and a started-type state mapped to rejected, both still raise LinearStatusMapError (negative tests)."
  - "No other row of the accepted-types table changes; existing ownership tests pass unmodified."
  - "The 'Known gap, flagged for follow-up' paragraph in pm-engine-and-linear-sync.md is resolved in the same change (the doc describes the admitted spellings, not the gap)."
non_goals:
  - "No LINEAR_STATE_MAP value changes and no board edits — the operator adds the Duplicate env entry out of band, as documented."
  - "No general spelling normalisation layer; the fix is the one table row the live board contradicts."
test_requirements:
  - "Unit tests over validate_against_states with fixture workflow-state payloads (no live calls, ATLAS_LIVE_TESTS=0)."
definition_of_done:
  - "All acceptance criteria evidenced by named tests; full gate sweep green; pm-engine-and-linear-sync.md's gap paragraph updated in the same change (doc linter green)."
---

# Accepted-types spelling

The board speaks US English; the contradiction filter should understand
it. One row learns the two live spellings so C2 stops failing on
mappings the design doc already blesses.
