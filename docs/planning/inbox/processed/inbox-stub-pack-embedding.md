---
title: "Pack embedding: the rendered context pack rides the Linear issue description"
objective: "A ticket's Linear issue carries its rendered Atlas context pack in the description, so a dispatched agent reads its context from the board — no hand-authored prompt required."
context: "The Phase 8 milestone's unproven leg (roadmap seed 82; design ruling in symphony-integration.md: 'the rendered context pack is embedded in the Linear issue description'). The definition-push currently sends title plus a definition-fields description composed by render_definition_description (ownership.py), whose own comment defers pack embedding to this ticket. All prerequisites landed: F-7 anchor durability (ATLAS-159), pack rendering for every ticket class including stub-minted (ATLAS-162), and the batched push (ATLAS-148). Shape: at definition-push time, render the ticket's context pack and compose it into the pushed description beneath the definition fields, delimited so pulls and humans can distinguish pack from definition; the pack section is Atlas-owned like the rest of the description (one-directional push, per the field-ownership rules). Design decisions for the plan gate, not pre-decided here: (1) the size-overflow path — Linear descriptions have practical limits; the symphony-integration doc names a fallback (docs/planning/packs/<key>.md with a link) which does not exist yet — the gate decides build-it-now versus truncation-with-marker, with the operator ruling; (2) render-failure posture at push time — a ticket whose pack fails validation should push definition-only with a typed anomaly rather than blocking the whole tick (propose, argue, gate rules); (3) staleness — packs are corpus-derived, so when a re-push fires on definition change, the pack re-renders; whether unchanged-definition-but-changed-corpus triggers a re-push is a cost/benefit call for the gate (the batched pull budget from ATLAS-148 is the constraint to respect)."
ticket_type: "feature"
epic_ref: "ATLAS-E10"
acceptance_criteria:
  - "A pushed ticket's Linear description contains the definition fields followed by a delimited rendered pack section; proven by an emulator-level fixture asserting section order, delimiter, and pack content for a corpus-anchored and a stub-minted ticket (the ATLAS-162 class)."
  - "The pack section round-trips the pull untouched: pull-side parsing ignores it and no pack content leaks into any Atlas-owned field; proven with a pull fixture over an embedded description."
  - "The gate-ruled size-overflow behaviour is implemented with a named test at the boundary (a pack exceeding the pinned limit exercises the ruled path, not an exception)."
  - "The gate-ruled render-failure posture is implemented: a ticket whose pack cannot render pushes per the ruling with a typed, logged outcome; the tick completes for the remaining tickets."
  - "Push request count stays within the ATLAS-148 budget shape: embedding adds rendering cost, not per-ticket Linear requests; proven by extending the request-bound test."
non_goals:
  - "No Symphony-side changes, no WORKFLOW.md prompt changes, no pull-side pack consumption (agents read the description; Atlas never parses packs back)."
  - "No pack format redesign; the pack renders as ATLAS-162 left it."
  - "No re-push scheduling changes beyond what decision (3) rules."
test_requirements:
  - "Emulator/fixture-driven, ATLAS_LIVE_TESTS=0, seeds `assert 1 == 2` (B011); the size-boundary and render-failure fixtures are named; one fixture is a stub-minted ticket to pin the ATLAS-162 dependency live."
definition_of_done:
  - "All acceptance criteria evidenced by named tests; full gate sweep green; symphony-integration.md's pack-delivery section and pm-engine-and-linear-sync.md's push-field text updated in the same change; follow-ups routed with owners."
---

# Pack embedding

The ticket carries its own context; the prompt writes itself.
