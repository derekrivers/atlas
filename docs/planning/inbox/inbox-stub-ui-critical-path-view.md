---
title: "Critical path view"
objective: >-
  Render the graph-wide critical path in execution order with per-step and
  cumulative effort, so the operator sees the longest remaining chain without
  running the CLI.
context: >-
  Pre-ruled decisions (operator-ratified, reviewer session 2026-07-26): D-1
  the view states plainly that the critical path is ADVISORY and never gates
  dispatch. The dependency engine is explicit that it does not; a
  visualisation that omits this reads as authority and would quietly acquire
  it. D-2 the chain renders in execution order with per-step effort and the
  running cumulative total, and steps link to ticket detail. D-3 the API
  weights a null effort as 1 at compute time without mutating the node; the
  view does not re-derive, re-weight or re-order anything the API returned.
  D-4 an empty path — no non-terminal tickets — is a normal state and renders
  the shared empty state. D-5 this is a route in its own right, and the
  overview dashboard carries only the head of the chain and the total; the
  summary reuses this view's selectors rather than duplicating them.
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
- "The path renders in the API's execution order with per-step effort, running cumulative effort and the total, asserted against the seeded store."
- "The view states that the critical path is advisory and does not gate dispatch, asserted by test."
- "Each step links to its ticket detail route."
- "No effort value is recomputed, re-weighted or re-ordered client-side; rendered values equal the API response exactly, asserted by test."
- "An empty path renders the shared empty state."
non_goals:
- "Read-only: no writes, no mutations, no authentication, no Linear or GitHub writes. No pagination, no bespoke error envelope, no parallel enum copies. No changes to Python domain models or storage. Do not implement or pre-empt any other queued Operator UI ticket. Never write to docs/planning/ (ADR-0007)."
test_requirements:
- "Vitest browser-mode component tests for rendering logic and a @playwright/test end-to-end spec where the ticket names one; the end-to-end suite runs against a real `atlas api serve` over a seeded store, never against mocked responses. ATLAS_LIVE_TESTS=0 for the Python gate sweep; seeded Python defects use assert 1 == 2 (B011)."
definition_of_done:
- "All acceptance criteria evidenced by named tests; the full Python gate sweep and the operator-UI pipeline both green; canonical docs updated in the same change where behaviour diverges from them; PR title carries the ticket key in the form (ATLAS-NN)."
---

# Critical path view

Minted from the reviewer session of 2026-07-26; the D-x decisions in
`context` are operator-ratified. Land them; do not relitigate.
