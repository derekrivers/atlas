---
title: "Ticket detail: dependencies and readiness tab"
objective: >-
  Render one ticket's blockers, reverse dependencies and full readiness
  verdict, so the operator sees every reason a ticket is not dispatchable
  rather than the first one.
context: >-
  Pre-ruled decisions (operator-ratified, reviewer session 2026-07-26): D-1
  EVERY not-ready reason is rendered, not just the first. The readiness
  predicate collects all failing conditions deliberately, and a view that
  shows one turns a complete diagnosis back into a guessing game. D-2 each
  NotReadyCode is rendered as human text alongside the machine code — wrong
  status, dependency not done, ADR not accepted, no acceptance criteria,
  dangling target — with the offending target and status when the API supplies
  them. D-3 blockers and blocked-by entries link to their own ticket detail
  routes, making the graph walkable one hop at a time; this is the per-ticket
  walk and does not pre-empt the whole-graph view, which is its own ticket
  behind its own API route. D-4 a dangling target is displayed as the defect
  it is rather than silently dropped. D-5 readiness is advisory in the same
  sense the dependency engine intends: the view never implies it can dispatch.
ticket_type: feature
epic_ref: ATLAS-E13
risk_level: low
component: operator-ui
relevant_docs:
- "docs/atlas/operator-ui.md"
- "docs/atlas/operator-api.md"
depends_on:
- "inbox-stub-ui-ticket-detail.md"
acceptance_criteria:
- "The readiness verdict and every not-ready reason render, with human text for each code plus the offending target and status where supplied, asserted against a seeded ticket carrying more than one failing reason."
- "Blockers and blocked-by entries render as working links to their own detail routes, asserted by an end-to-end spec that walks one hop in each direction."
- "A ready ticket renders the ready verdict with no reason list, asserted against the seed."
- "A dangling dependency target renders visibly as a defect rather than being omitted, asserted against a seeded dangling edge."
- "No control in the tab implies dispatch or a status change."
non_goals:
- "Read-only: no writes, no mutations, no authentication, no Linear or GitHub writes. No pagination, no bespoke error envelope, no parallel enum copies. No changes to Python domain models or storage. Do not implement or pre-empt any other queued Operator UI ticket. Never write to docs/planning/ (ADR-0007)."
test_requirements:
- "Vitest browser-mode component tests for rendering logic and a @playwright/test end-to-end spec where the ticket names one; the end-to-end suite runs against a real `atlas api serve` over a seeded store, never against mocked responses. ATLAS_LIVE_TESTS=0 for the Python gate sweep; seeded Python defects use assert 1 == 2 (B011)."
definition_of_done:
- "All acceptance criteria evidenced by named tests; the full Python gate sweep and the operator-UI pipeline both green; canonical docs updated in the same change where behaviour diverges from them; PR title carries the ticket key in the form (ATLAS-NN)."
---

# Ticket detail: dependencies and readiness tab

Minted from the reviewer session of 2026-07-26; the D-x decisions in
`context` are operator-ratified. Land them; do not relitigate.
