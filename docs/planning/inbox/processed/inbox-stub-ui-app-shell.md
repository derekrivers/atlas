---
title: "Application shell: navigation, theme toggle, command palette"
objective: >-
  Deliver the persistent frame every view renders inside — sidebar navigation,
  header, theme toggle, command palette, route-level error boundaries and a
  404 page — so view tickets add a route and nothing else.
context: >-
  Pre-ruled decisions (operator-ratified, reviewer session 2026-07-26): D-1
  the navigation enumerates exactly the seven ratified surfaces of
  docs/atlas/operator-ui.md; /status is NOT a navigation entry — it is the
  overview header plus a persistent footer staleness indicator, because six
  scalars do not justify a route. D-2 no navigation entry, button, or menu
  item implies a write, an approval, or a promotion; the UI is a reading
  instrument for this phase, and a disabled Approve control is a promise this
  phase does not keep. D-3 the command palette navigates and searches; it
  issues no mutation. D-4 route-level error boundaries contain a failing view
  rather than blanking the shell, and the 404 page renders the API's native
  detail body for a keyed resource without a bespoke error envelope. D-5 views
  are placeholders in this ticket; each is filled by its own ticket.
ticket_type: feature
epic_ref: ATLAS-E13
risk_level: low
component: operator-ui
relevant_docs:
- "docs/atlas/operator-ui.md"
- "docs/atlas/operator-api.md"
depends_on:
- "inbox-stub-ui-theme-contract.md"
- "inbox-stub-ui-query-layer.md"
acceptance_criteria:
- "The shell renders navigation to each ratified surface, a theme toggle, and a command palette, asserted by an end-to-end spec that reaches every route from a cold load."
- "No control in the shell implies a write, approval, promotion or retry; a test enumerates interactive elements and fails on the forbidden action vocabulary."
- "A view that throws renders its route-level error boundary while the shell remains navigable, asserted by test."
- "An unknown route renders the 404 page, and an unknown ticket key renders the API's native detail body verbatim, asserted by test."
- "The footer surfaces the /status staleness indicator on every route."
non_goals:
- "Read-only: no writes, no mutations, no authentication, no Linear or GitHub writes. No pagination, no bespoke error envelope, no parallel enum copies. No changes to Python domain models or storage. Do not implement or pre-empt any other queued Operator UI ticket. Never write to docs/planning/ (ADR-0007)."
test_requirements:
- "Vitest browser-mode component tests for rendering logic and a @playwright/test end-to-end spec where the ticket names one; the end-to-end suite runs against a real `atlas api serve` over a seeded store, never against mocked responses. ATLAS_LIVE_TESTS=0 for the Python gate sweep; seeded Python defects use assert 1 == 2 (B011)."
definition_of_done:
- "All acceptance criteria evidenced by named tests; the full Python gate sweep and the operator-UI pipeline both green; canonical docs updated in the same change where behaviour diverges from them; PR title carries the ticket key in the form (ATLAS-NN)."
---

# Application shell: navigation, theme toggle, command palette

Minted from the reviewer session of 2026-07-26; the D-x decisions in
`context` are operator-ratified. Land them; do not relitigate.
