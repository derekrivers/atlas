---
title: "Review queue view"
objective: >-
  Render the tickets awaiting operator review with their verification checks
  and the two acceptance gates made explicit, so the operator sees what is
  waiting and what would block accepting it.
context: >-
  Pre-ruled decisions (operator-ratified, reviewer session 2026-07-26): D-1
  has_system_evidence and has_pr_merged_evidence are rendered as EXPLICIT,
  prominent pass or fail states rather than as flags in a row of metadata.
  Those two gates are what actually strand tickets — the Phase 10 closure
  report's incident ledger is largely a record of that — and burying them
  reproduces the failure the view exists to prevent. D-2 the checks matrix
  covers all seven VerificationCheckType values and renders a check that has
  never run as distinct from one that passed and one that is not applicable;
  an absent check must never read as a pass. D-3 the queue is READ-ONLY and
  carries no approve, reject, request-changes or retry control, not even
  disabled: writes are deferred to the writeable API phase and a greyed-out
  button is a promise this phase does not keep. D-4 the API's order is
  preserved; the view does not re-rank. D-5 an empty queue is a normal,
  expected state — it was empty at Phase 10 close — and renders the shared
  empty state.
ticket_type: feature
epic_ref: ATLAS-E13
risk_level: low
component: operator-ui
relevant_docs:
- "docs/atlas/operator-ui.md"
- "docs/atlas/operator-api.md"
depends_on:
- "inbox-stub-ui-ci-pipeline.md"
acceptance_criteria:
- "Each queue item renders its verdict, its checks across all seven check types, and both acceptance gates as explicit pass or fail, asserted by an end-to-end spec."
- "A check that has never run renders distinctly from passed and from not-applicable, asserted by component test over all three."
- "No approve, reject, request-changes or retry control exists in any state, asserted by a test over interactive elements."
- "An empty queue renders the shared empty state, asserted against the seeded empty queue."
- "Rows link to ticket detail and the API's order is preserved, asserted by test."
non_goals:
- "Read-only: no writes, no mutations, no authentication, no Linear or GitHub writes. No pagination, no bespoke error envelope, no parallel enum copies. No changes to Python domain models or storage. Do not implement or pre-empt any other queued Operator UI ticket. Never write to docs/planning/ (ADR-0007)."
test_requirements:
- "Vitest browser-mode component tests for rendering logic and a @playwright/test end-to-end spec where the ticket names one; the end-to-end suite runs against a real `atlas api serve` over a seeded store, never against mocked responses. ATLAS_LIVE_TESTS=0 for the Python gate sweep; seeded Python defects use assert 1 == 2 (B011)."
definition_of_done:
- "All acceptance criteria evidenced by named tests; the full Python gate sweep and the operator-UI pipeline both green; canonical docs updated in the same change where behaviour diverges from them; PR title carries the ticket key in the form (ATLAS-NN)."
---

# Review queue view

Minted from the reviewer session of 2026-07-26; the D-x decisions in
`context` are operator-ratified. Land them; do not relitigate.
