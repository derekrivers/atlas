---
title: "Retirement target-exists handling: a stub whose processed/ twin already exists must not silently survive the inbox"
objective: "Apply's stub retirement never leaves an active-inbox stub behind because a same-named file already sits in processed/; the tree state is either resolved deterministically or refused loudly."
context: "Found at the ATLAS-159 gate: retirement's move (apply.py, target-exists skip) declines to overwrite an existing processed/<name>.md and leaves the active-inbox copy in place — where every subsequent plan re-reads and re-promotes it, a duplicate-mint hazard of exactly the F-4 genus the collapse pre-pass was built against (and the collapse only guards within one proposal, not across plan runs). ATLAS-159 made the same-basename-in-both-places state a typed plan-time error, so the front door now refuses the symptom; this ticket fixes the producer. Design choice at the plan gate: on collision, either (a) refuse the whole apply pre-confirmation with a typed error naming the collision (fail-closed, symmetric with the plan-time check — likely the right answer), or (b) retire under a deterministic disambiguated name. The gate rules; the render deliberately does not."
ticket_type: "bug"
epic_ref: "ATLAS-E3"
acceptance_criteria:
  - "Post-fix, no code path exists in which apply completes CONFIRMED while a promoted stub remains in the active inbox; proven by a fixture with a pre-existing processed/ twin."
  - "The ruled branch is implemented with a typed error or deterministic rename, each named in the change; the plan-time collision check from ATLAS-159 and this apply-time behaviour agree (one fixture exercises both seams)."
  - "Seeded regression: pre-fix, the collision fixture leaves the stub in the inbox and the next plan re-promotes it; post-fix it cannot."
  - "Negative: normal retirement (no twin) is byte-identical (existing retirement tests pass unmodified)."
non_goals:
  - "No change to retire-on-reject semantics (ATLAS-152 — named, not touched), promotion, or the collapse pre-pass."
test_requirements:
  - "Apply-level fixtures, real retirement (no mocks of the move), ATLAS_LIVE_TESTS=0, seeds `assert 1 == 2` (B011)."
definition_of_done:
  - "All acceptance criteria evidenced by named tests; full gate sweep green; the routed-follow-up pointer updated in the same change."
---

# Retirement collision handling

A retired stub leaves the inbox, or the apply says why it can't.
