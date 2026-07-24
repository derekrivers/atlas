---
title: CLI disposition path for a stale proposed PlanRun
objective: Give the operator a sanctioned way to reject a proposed PlanRun that has gone stale, so a stranded proposal never requires one-shot incident code to clear.
context: 'Proposed by the ATLAS-029M agent and hit twice in one session: AT-5 staleness fires in run_apply BEFORE the confirm/decision callback, so a stale proposed PlanRun can never reach the reject path that would finalise it to REJECTED. PlanRun bede6227 (2026-07-08) sat stranded and had to be dispositioned by hard-coded repository code under gate amendment A-6. Pre-ruled decisions (operator-ratified in reviewer session 2026-07-24; land them, do not relitigate): D-1 the new path preserves explicit operator intent - it never disposes a run implicitly as a side effect of another command. D-2 the ticket must define and document inbox-stub retirement semantics for this path: apply.py currently retires stubs on BOTH applied and rejected outcomes because both mean ''considered''. Staleness arguably means the proposal was never evaluated on its merits, so the reviewer''s non-binding view is that stale-rejection should NOT retire stubs; the agent proposes the rule with its failure modes at the plan gate, and the chosen rule is documented in the owning canonical doc in the same change. D-3 no change to AT-5 staleness detection itself, and no automatic re-planning.'
ticket_type: feature
epic_ref: ATLAS-E3
risk_level: low
component: planning
acceptance_criteria:
- A stale proposed PlanRun can be dispositioned to rejected through the CLI with explicit operator intent, evidenced by a fixture test that reproduces the AT-5 stranding first.
- The chosen stub-retirement rule is enforced by test in both directions and documented in the owning canonical doc in the same change.
- A non-stale proposed run is unaffected by the new path; the normal apply flow is unchanged.
non_goals:
- No change to staleness detection or the apply sequence. No automatic disposition of stale runs. No bulk/sweep disposition of historical runs. No re-planning trigger.
test_requirements:
- Fixture-driven; ATLAS_LIVE_TESTS=0; seeded defects use assert 1 == 2 (B011).
definition_of_done:
- All acceptance criteria evidenced by named tests; full gate sweep green; PR title carries the ticket key.
---

# CLI disposition path for a stale proposed PlanRun

Minted from the reviewer session of 2026-07-24; decisions in `context` are operator-ratified.
