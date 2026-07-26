---
title: "Generated OpenAPI TypeScript client with a CI drift guard"
objective: >-
  Generate the operator UI's TypeScript types from the FastAPI OpenAPI
  document and fail CI on any drift, so the UI cannot compile against a stale
  view of the /api/v1 contract.
context: >-
  The UI's only coupling to Atlas is the HTTP contract; docs/atlas/operator-
  ui.md rules that the coupling must be mechanical. Pre-ruled decisions
  (operator-ratified, reviewer session 2026-07-26): D-1 types are GENERATED
  from the running application's OpenAPI document and COMMITTED; they are
  never hand-written and never partially hand-edited. D-2 the drift guard
  regenerates in CI and fails on any diff against the committed output — a
  generated file that is merely regenerable is not a guard. D-3 the generator
  reads the schema from atlas.api.app.create_app rather than from a checked-in
  copy of the schema, so a route or field added in Python without regeneration
  fails the build. D-4 response fields with closed value sets arrive as the
  canonical domain StrEnum members that operator-api.md already publishes; no
  parallel TypeScript enum is authored by hand. D-5 no data fetching, no React
  Query, and no view code enters this ticket — types and the generation seam
  only.
ticket_type: infrastructure
epic_ref: ATLAS-E13
risk_level: low
component: operator-ui
relevant_docs:
- "docs/atlas/operator-ui.md"
- "docs/atlas/operator-api.md"
depends_on:
- "inbox-stub-ui-scaffold.md"
acceptance_criteria:
- "A single documented command regenerates the TypeScript client from the live FastAPI OpenAPI document, and the output is committed."
- "CI regenerates and fails on any diff against the committed output; a seeded Python-side field addition without regeneration makes the guard fire, evidenced by the seeded probe."
- "Every one of the nine v1 routes is represented in the generated types, asserted by a test that enumerates them rather than by inspection."
- "Closed-value response fields are typed from the published canonical enum members, with no hand-authored TypeScript enum in the tree, asserted by test."
non_goals:
- "Read-only: no writes, no mutations, no authentication, no Linear or GitHub writes. No pagination, no bespoke error envelope, no parallel enum copies. No changes to Python domain models or storage. Do not implement or pre-empt any other queued Operator UI ticket. Never write to docs/planning/ (ADR-0007)."
test_requirements:
- "Vitest browser-mode component tests for rendering logic and a @playwright/test end-to-end spec where the ticket names one; the end-to-end suite runs against a real `atlas api serve` over a seeded store, never against mocked responses. ATLAS_LIVE_TESTS=0 for the Python gate sweep; seeded Python defects use assert 1 == 2 (B011)."
definition_of_done:
- "All acceptance criteria evidenced by named tests; the full Python gate sweep and the operator-UI pipeline both green; canonical docs updated in the same change where behaviour diverges from them; PR title carries the ticket key in the form (ATLAS-NN)."
---

# Generated OpenAPI TypeScript client with a CI drift guard

Minted from the reviewer session of 2026-07-26; the D-x decisions in
`context` are operator-ratified. Land them; do not relitigate.
