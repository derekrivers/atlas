---
title: "Dependency graph view"
objective: >-
  Render the whole dependency graph in one screen with the critical path
  highlighted, so the operator sees the shape of the backlog rather than
  walking it one ticket at a time.
context: >-
  Depends on the bulk graph route, without which this view is 162 requests.
  Pre-ruled decisions (operator-ratified, reviewer session 2026-07-26): D-1
  layout is entirely CLIENT-side; the API returns nodes and edges and no
  coordinates, because a layout in the response is a rendering decision on a
  drift timer. D-2 the graph renders the whole projection, and readability is
  achieved by filtering — by status, by epic once available — rather than by
  silently sampling; if the view ever caps what it draws, the cap is stated on
  screen, because a truncated graph that looks complete is worse than no
  graph. D-3 the critical path is highlighted within the graph, and the
  dedicated critical-path route remains; this view does not replace it. D-4
  nodes link to ticket detail and terminal statuses follow the board's default
  of being hidden until revealed. D-5 read-only: no edge creation, no
  deletion, no drag-to-reparent.
ticket_type: feature
epic_ref: ATLAS-E13
risk_level: medium
component: operator-ui
relevant_docs:
- "docs/atlas/operator-ui.md"
- "docs/atlas/operator-api.md"
depends_on:
- "inbox-stub-ui-ci-pipeline.md"
- "inbox-stub-api-dependency-graph-read.md"
acceptance_criteria:
- "The graph renders every node and edge the API returns for the seeded store, asserted by an end-to-end spec comparing counts against the API response."
- "Layout is computed client-side; the API response contains no coordinates, asserted by a Python-side schema test."
- "The critical path is visually distinguishable within the graph, asserted by test."
- "If any cap on rendered nodes exists it is stated on screen and asserted by test; no silent sampling occurs."
- "Nodes link to ticket detail, terminal statuses are hidden until revealed, and no control mutates an edge."
non_goals:
- "Read-only: no writes, no mutations, no authentication, no Linear or GitHub writes. No pagination, no bespoke error envelope, no parallel enum copies. No changes to Python domain models or storage. Do not implement or pre-empt any other queued Operator UI ticket. Never write to docs/planning/ (ADR-0007)."
test_requirements:
- "Vitest browser-mode component tests for rendering logic and a @playwright/test end-to-end spec where the ticket names one; the end-to-end suite runs against a real `atlas api serve` over a seeded store, never against mocked responses. ATLAS_LIVE_TESTS=0 for the Python gate sweep; seeded Python defects use assert 1 == 2 (B011)."
definition_of_done:
- "All acceptance criteria evidenced by named tests; the full Python gate sweep and the operator-UI pipeline both green; canonical docs updated in the same change where behaviour diverges from them; PR title carries the ticket key in the form (ATLAS-NN)."
---

# Dependency graph view

Minted from the reviewer session of 2026-07-26; the D-x decisions in
`context` are operator-ratified. Land them; do not relitigate.
