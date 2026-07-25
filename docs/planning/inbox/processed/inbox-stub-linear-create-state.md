---
title: "Assert the mapped Linear state when Atlas creates an issue"
objective: "Stop newly created Linear issues from inheriting the team's default workflow state. On first sync, Atlas must assert the state its status map assigns to the ticket's Atlas status, so a ticket that is `planned` in Atlas is `planned` on the board rather than whatever Linear defaults to."
context: "Observed live twice on 2026-07-24. `_CREATE_MUTATION` in atlas/linear/client.py sends no `stateId`, so Linear assigns the team default — which on this workspace is the Needs Human state. For a dependency-READY ticket the damage self-corrects: `promote_ready` immediately writes Ready for Agent. For a BLOCKED ticket nothing corrects it, and the next tick's pull imports the default state back into Atlas as `needs_human_decision` — which is neither pushable nor promotable, so `is_ready` fails its status condition permanently and the ticket is stranded with no automatic recovery. ATLAS-196 hit exactly this: minted `planned`, created into Needs Human, pulled back as `needs_human_decision`, and required a manual Linear move before `promote_ready` would touch it even after its blocker completed. Pre-ruled decisions (operator-ratified in reviewer session 2026-07-25; land them, do not relitigate): D-1 the fix asserts state on the CREATE path only, immediately after a confirmed `create_issue`, using the existing sanctioned `LinearClient.set_state`; the GraphQL create mutation and `OWNED_LINEAR_INPUT_KEYS` are NOT changed — `stateId` is a workflow value, not an owned definition field. D-2 the UPDATE path is untouched: after creation Linear owns workflow state and Atlas pulls it, so asserting state on every definition push would overwrite operator moves. Atlas writes state only at creation and at its two existing sanctioned points (`promote_ready`, `complete_verified`). D-3 `_push` does not currently receive the status map; thread `status_map: LinearStatusMap` from its call site in the tick, where it is already in scope. D-4 resolve the target state through the existing `status_map.state_id_for(...)` so a missing or ambiguous mapping fails closed at the existing load-time guard rather than silently skipping the assertion. D-5 partial-failure behaviour is explicit: if `create_issue` succeeds and the state assertion then fails, the join key is still written (an orphaned Linear issue is worse than a mis-stated one) and the failure is logged and counted as an anomaly; the ticket is left recoverable by the next tick, never half-linked. D-6 no bulk correction of existing mis-stated tickets — the 53 tickets currently in `needs_human_decision` are a separate operator-gated triage, and this ticket must not write to any pre-existing issue."
ticket_type: tech_debt
epic_ref: "ATLAS-E6"
risk_level: medium
component: pm
acceptance_criteria:
- "On first sync of a pushable ticket, the created Linear issue is left in the state the status map assigns to that ticket's Atlas status, asserted by test against a fake client that records its create and set_state calls in order."
- "A blocked `planned` ticket that is NOT dependency-ready is created in the mapped `planned` state and is NOT promoted; a following pull imports `planned`, not the team default. This reproduces the ATLAS-196 stranding and proves it no longer occurs."
- "A dependency-ready ticket still reaches Ready for Agent in the same tick: creation asserts the mapped state and `promote_ready` then moves it, with no regression to existing promotion tests."
- "The update path writes no state: a definition update to an existing issue issues no set_state call, asserted by test."
- "Partial failure is covered: a create that succeeds followed by a failing state assertion still writes the join key, logs, and counts an anomaly, leaving no orphaned issue — asserted by test."
non_goals:
- "No change to the GraphQL create mutation or OWNED_LINEAR_INPUT_KEYS; stateId stays out of the owned definition. No state writes on the update path. No bulk correction, sweep, or repair of existing tickets in any status — the needs_human_decision cohort is a separate operator-gated triage. No change to promote_ready, complete_verified, the pull path, or the status map's shape. No change to PUSHABLE_STATUSES or to readiness conditions."
test_requirements:
- "Fixture-driven with the existing fake/recording Linear client; ATLAS_LIVE_TESTS=0; seeded defects use assert 1 == 2 (B011). Call ORDER matters — assert create precedes set_state and that the join key is written in both the happy and partial-failure paths."
definition_of_done:
- "All acceptance criteria evidenced by named tests; full gate sweep green with ATLAS_LIVE_TESTS=0; enumeration pins unchanged; PR title carries the minted key."
---

# A created issue inherits a state nobody chose

`promote_ready` rescues the ready ones. Nothing rescues the blocked ones.
