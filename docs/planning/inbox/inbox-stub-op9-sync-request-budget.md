---
title: "Sync request budget: batched pull, scoped comment scan, team-scoped states"
objective: "One sync tick over a 110-ticket board costs single-digit Linear requests instead of ~218, making the default cadence legitimate within the 2500/hour budget."
context: "2026-07-07 incident arithmetic: step 1 fetches every non-terminal ticket's issue individually (~110 requests) and step 4 comment-scans every non-terminal ticket (~108 more), so the 60-second default cadence attempts ~13,000 requests/hour against a 2,500 budget — the scheduler starves itself by design, not by accident. Separately, fetch_workflow_states queries workspace-wide (workflowStates(first: 250)), returning foreign teams' states with colliding names (two Canceled, two Done observed live), which made the state-map fix a guessing game until a per-issue probe resolved the team. This is a design change: pm-engine-and-linear-sync.md owns the sync loop contract and must land updated in the same change."
ticket_type: "feature"
epic_ref: "ATLAS-E6"
acceptance_criteria:
  - "Step 1's per-ticket fetch_issue loop is replaced by a paginated, project-scoped issues query; proven by a fake-client test counting calls: a 110-ticket board ticks with a pinned request bound (target: <= 15 total client calls for a no-op tick), and the seeded-defect form (re-adding a per-ticket fetch) breaks the bound test."
  - "The step 4 comment scan runs only for tickets in a documented active-state set (the design decision names the set and its rationale in pm-engine-and-linear-sync.md); a parked needs_human_decision ticket is not comment-scanned, proven by a fake-client test, with a negative proving an in_progress ticket still is."
  - "fetch_workflow_states is team-scoped (the team id the tick already requires); proven by a test on the query shape, and the docstring's 'workspace's workflow states' claim is corrected in the same change."
  - "pm-engine-and-linear-sync.md gains a state-map completeness section: every board state is either mapped or listed as intentionally unmapped with a one-line rationale (the operator supplies the intentional list at the plan gate; the agent encodes, never decides it)."
  - "Pull/push/promotion/completion semantics are behaviour-identical for mapped states: the existing sync test suite passes unmodified except where it asserted per-ticket request shapes."
non_goals:
  - "No client transport changes (error body, timeout, rate-limit backoff) — that is the client-hardening ticket, which this ticket assumes has landed or is in flight."
  - "No status-map VALUE changes (no new mappings beyond documenting intent); no board edits."
  - "No webhook/push-notification architecture — polling stays; this ticket only makes polling affordable."
  - "No pagination-cursor persistence or incremental updatedAt-since sync — a candidate future refinement, named here and not started."
test_requirements:
  - "All request-count assertions via the in-memory fake client (no live calls, ATLAS_LIVE_TESTS=0); the request-bound test is the milestone anchor and must be falsifiable by the seeded per-ticket-fetch defect."
definition_of_done:
  - "All acceptance criteria evidenced by named tests; full gate sweep green; pm-engine-and-linear-sync.md updated in the same change (doc linter green); a follow-up note filed for updatedAt-since incremental sync with an owner or a drop rationale."
---

# Sync request budget (OP-9)

Make the tick affordable. The loop's correctness was never in question —
its cost was: O(2N) requests per tick starves a 2,500/hour budget at any
reasonable cadence and board size. Batch the pull, scope the comment scan
to states where comments can matter, scope the states query to the team
whose ids it validates, and make every unmapped state a decision on the
record instead of a latent anomaly.
