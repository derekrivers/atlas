---
title: "Query layer, dev proxy, and API-unreachable primitives"
objective: >-
  Give every later view one shared data-access layer: typed query hooks over
  the generated client, the development proxy to the loopback API, a polling
  policy, and the loading, empty, error and API-unreachable states each view
  will reuse.
context: >-
  Pre-ruled decisions (operator-ratified, reviewer session 2026-07-26): D-1 no
  CORS middleware is added to the Python API (OP-3); the Vite development
  server proxies /api to the loopback API, keeping the origin question — a
  security-boundary question operator-api.md binds to the writeable phase —
  closed. D-2 freshness is polling only; there is no SSE or websocket surface
  to consume, and the /status timestamps are the honest staleness signal
  rather than a spinner. D-3 the API-unreachable state is a named, first-class
  state, not a generic error: a loopback API that is not running is the
  likeliest failure the operator will meet, and the UI must say the API is
  unreachable at the configured URL and that `atlas api serve` may not be
  running. D-4 the API base URL is configurable at build time with the
  loopback default; no remote default ships. D-5 this ticket adds no route and
  no view; it delivers hooks and state primitives that later view tickets
  consume.
ticket_type: feature
epic_ref: ATLAS-E13
risk_level: low
component: operator-ui
relevant_docs:
- "docs/atlas/operator-ui.md"
- "docs/atlas/operator-api.md"
depends_on:
- "inbox-stub-ui-openapi-client.md"
acceptance_criteria:
- "A typed query hook exists for each of the nine v1 routes, each returning the generated response type with no `any` in the path, asserted by type-level test."
- "The development server proxies API calls same-origin; no CORSMiddleware is added to atlas/api/app.py, asserted by a Python test over the application's middleware stack."
- "With the API process stopped, the UI renders the named API-unreachable state carrying the configured URL and the `atlas api serve` hint, asserted by an end-to-end spec that stops the server."
- "Loading, empty-collection and request-error states are shared primitives, and a view importing its own ad-hoc replacement fails a lint rule."
- "The polling interval is configured in one place and documented; no view sets its own."
non_goals:
- "Read-only: no writes, no mutations, no authentication, no Linear or GitHub writes. No pagination, no bespoke error envelope, no parallel enum copies. No changes to Python domain models or storage. Do not implement or pre-empt any other queued Operator UI ticket. Never write to docs/planning/ (ADR-0007)."
test_requirements:
- "Vitest browser-mode component tests for rendering logic and a @playwright/test end-to-end spec where the ticket names one; the end-to-end suite runs against a real `atlas api serve` over a seeded store, never against mocked responses. ATLAS_LIVE_TESTS=0 for the Python gate sweep; seeded Python defects use assert 1 == 2 (B011)."
definition_of_done:
- "All acceptance criteria evidenced by named tests; the full Python gate sweep and the operator-UI pipeline both green; canonical docs updated in the same change where behaviour diverges from them; PR title carries the ticket key in the form (ATLAS-NN)."
---

# Query layer, dev proxy, and API-unreachable primitives

Minted from the reviewer session of 2026-07-26; the D-x decisions in
`context` are operator-ratified. Land them; do not relitigate.
