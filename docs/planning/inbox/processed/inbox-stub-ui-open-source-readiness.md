---
title: "Open-source readiness for the operator UI"
objective: >-
  Make the operator UI something an outside contributor can run, understand
  and contribute to, and make its upstream attribution correct.
context: >-
  Pre-ruled decisions (operator-ratified, reviewer session 2026-07-26): D-1
  upstream attribution to satnaing/shadcn-admin under MIT is explicit and
  correct, covering the vendored source and the theme; this is a licence
  obligation and not a courtesy. D-2 the README documents running the UI
  against a real Atlas instance from a cold checkout, including seeding a
  store and starting `atlas api serve`, and a reviewer follows it verbatim on
  a clean machine as the acceptance evidence — a README that only works with
  local state is not a README. D-3 contribution guidance states the read-only
  boundary plainly, so an outside contributor does not open a pull request
  adding a write path that this phase forbids. D-4 the documentation records
  the known contract limits — no pagination, no epic on ticket detail, lesson
  ticket references unresolvable to keys, polling rather than push — so a
  contributor does not read them as bugs. D-5 no licence change to the
  repository and no package publication.
ticket_type: documentation
epic_ref: ATLAS-E13
risk_level: low
component: operator-ui
relevant_docs:
- "docs/atlas/operator-ui.md"
- "docs/atlas/operator-api.md"
depends_on:
- "inbox-stub-ui-a11y-responsive.md"
acceptance_criteria:
- "Upstream MIT attribution is present and correct for the vendored source and the theme, asserted by test over the licence files."
- "A reviewer following the README verbatim on a clean checkout seeds a store, starts the API, runs the UI and reaches every view; the run is the acceptance evidence."
- "Contribution guidance states the read-only boundary and points at the writeable-phase entry conditions."
- "The known contract limits are documented in one named place."
- "No repository licence change and no package publication occurs, evidenced by the diff."
non_goals:
- "Read-only: no writes, no mutations, no authentication, no Linear or GitHub writes. No pagination, no bespoke error envelope, no parallel enum copies. No changes to Python domain models or storage. Do not implement or pre-empt any other queued Operator UI ticket. Never write to docs/planning/ (ADR-0007)."
test_requirements:
- "Vitest browser-mode component tests for rendering logic and a @playwright/test end-to-end spec where the ticket names one; the end-to-end suite runs against a real `atlas api serve` over a seeded store, never against mocked responses. ATLAS_LIVE_TESTS=0 for the Python gate sweep; seeded Python defects use assert 1 == 2 (B011)."
definition_of_done:
- "All acceptance criteria evidenced by named tests; the full Python gate sweep and the operator-UI pipeline both green; canonical docs updated in the same change where behaviour diverges from them; PR title carries the ticket key in the form (ATLAS-NN)."
---

# Open-source readiness for the operator UI

Minted from the reviewer session of 2026-07-26; the D-x decisions in
`context` are operator-ratified. Land them; do not relitigate.
