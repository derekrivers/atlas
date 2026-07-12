---
title: "Durable stub anchors: promote against processed/ paths and repair the 14 dangling live anchors"
objective: "Stub-minted tickets carry source_anchors that remain valid after stub retirement, and the 14 live tickets whose anchors dangled when apply moved their stubs to processed/ echo cleanly through gate 4."
context: "Structural defect found at ATLAS-153 delivery: promote_inbox_stubs anchors each minted ticket to the stub's active-inbox path, and apply then retires the stub to inbox/processed/ — so every stub-minted ticket's anchor dangles the moment its own apply completes. Fourteen live tickets (ATLAS-109/110/147-158) carry such anchors today; a live stubs-only run echoes them verbatim and fails gate 4 as a typed recorded failure (honest, but it blocks the door ATLAS-153 built). The forward fix is one line: promotion anchors to the processed/ path, which is known at promotion time and is the file's durable home. The repair of the 14 is a design ruling, because ten of them are frozen (done/rejected), planning cannot modify frozen tickets (spec §4), and no other sanctioned anchor writer exists. Present the fork at the plan gate; the operator rules there: (a) a scoped one-time repair exception outside planning, documented in the debt register per the ATLAS-007M bootstrap-exception precedent; (b) gate 4 relaxed for keyed verbatim echoes, on the principle that an unchanged echo asserts no new anchor claim — a permanent semantics change, wider than the incident; (c) repair only the four unfrozen tickets via a normal MODIFY apply and exempt frozen echoes from gate 4 — a narrower hybrid. Do not implement any branch before the gate rules."
ticket_type: "bug"
epic_ref: "ATLAS-E3"
acceptance_criteria:
  - "promote_inbox_stubs anchors minted tickets to the inbox/processed/ path; a promotion-then-retirement round trip leaves a resolvable anchor, proven by a test that runs apply's retirement and re-validates the anchor."
  - "The ratified repair branch (from the gate fork) is implemented for all 14 affected tickets, each named in the change; after repair, a live-shaped fixture echoing all 14 passes gate 4."
  - "Seeded regression: pre-fix, a stubs-only echo of a retired-stub ticket fails gate 4; post-fix it passes."
  - "Negative: a genuinely dangling anchor (file absent from both inbox/ and processed/) still fails gate 4 — the gate keeps its teeth."
  - "The runbook's known-constraint note from ATLAS-153 is deleted in the same change (deletion over annotation)."
non_goals:
  - "No change to retirement itself, gate 4's behaviour for unkeyed tickets, the depends_on contract, or the collapse pre-pass."
  - "No general anchor-migration tooling — this repairs one named defect."
test_requirements:
  - "Fixture-driven, ATLAS_LIVE_TESTS=0, seeds `assert 1 == 2` (B011); the 14-ticket live shape is a named fixture; the round-trip test exercises real promotion and real retirement, not mocks of either."
definition_of_done:
  - "All acceptance criteria evidenced by named tests; full gate sweep green; spec §2.1's promotion text and the debt register (if branch (a) is ruled) updated in the same change."
---

# Durable stub anchors

A ticket's anchor should outlive the ceremony that minted it.
