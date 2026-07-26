---
title: "Ticket detail view: definition and metadata"
objective: >-
  Render one ticket's stored definition and execution metadata in full, so the
  operator reads what the agent was actually asked to do without opening the
  store.
context: >-
  Pre-ruled decisions (operator-ratified, reviewer session 2026-07-26): D-1
  the view is tabbed — Definition, Metadata, Evidence, Dependencies — and THIS
  ticket delivers the first two plus the tab frame; the evidence and
  dependencies tabs are their own tickets and land behind this one. D-2 the
  three underlying requests are issued independently and composed in the
  client; that is the intended shape, because operator-api.md's contains-no-
  logic rule forbids assembling them server-side. This ticket wires only the
  detail request. D-3 every list-valued definition field is rendered in full
  and in stored order — acceptance criteria, non-goals, implementation notes,
  test and documentation requirements, definition of done — with no truncation
  and no summarisation; a truncated acceptance criterion is a misleading
  artifact. D-4 an unknown key renders the API's native 404 detail body
  verbatim, with no bespoke error envelope. D-5 the source anchor and the
  Linear and GitHub external identifiers are surfaced, because they are how
  the operator crosses back to intent and to delivery.
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
- "Every field of the ticket detail projection renders, with list fields complete and in stored order, asserted by an end-to-end spec that compares the rendered content against the API response."
- "The tab frame exposes Definition, Metadata, Evidence and Dependencies, with the latter two present and empty pending their own tickets."
- "An unknown ticket key renders the API's native detail body verbatim and returns the user to a navigable shell, asserted by an end-to-end spec."
- "Source anchor and external identifiers are visible on the metadata tab."
- "A ticket with null effort, no component and empty tag list renders without layout breakage, asserted against the seed."
non_goals:
- "Read-only: no writes, no mutations, no authentication, no Linear or GitHub writes. No pagination, no bespoke error envelope, no parallel enum copies. No changes to Python domain models or storage. Do not implement or pre-empt any other queued Operator UI ticket. Never write to docs/planning/ (ADR-0007)."
test_requirements:
- "Vitest browser-mode component tests for rendering logic and a @playwright/test end-to-end spec where the ticket names one; the end-to-end suite runs against a real `atlas api serve` over a seeded store, never against mocked responses. ATLAS_LIVE_TESTS=0 for the Python gate sweep; seeded Python defects use assert 1 == 2 (B011)."
definition_of_done:
- "All acceptance criteria evidenced by named tests; the full Python gate sweep and the operator-UI pipeline both green; canonical docs updated in the same change where behaviour diverges from them; PR title carries the ticket key in the form (ATLAS-NN)."
---

# Ticket detail view: definition and metadata

Minted from the reviewer session of 2026-07-26; the D-x decisions in
`context` are operator-ratified. Land them; do not relitigate.
