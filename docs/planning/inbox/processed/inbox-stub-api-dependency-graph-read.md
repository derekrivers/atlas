---
title: "GET /api/v1/dependencies/graph"
objective: >-
  Return the projected dependency graph — nodes and depends_on edges — in one
  response, so a whole-graph view is a single request rather than one request
  per ticket.
context: >-
  The second and last additive v1 read route this phase permits (OP-2). Pre-
  ruled decisions (operator-ratified, reviewer session 2026-07-26): D-1 no
  graph logic is reimplemented in atlas/api/ or in the new service:
  atlas.dependencies builds and validates the projection exactly as it does
  for readiness and critical path, an atlas.orchestration coordinating service
  assembles the response, and the route presents it — the precedent is the
  existing critical-path operation. D-2 nodes carry key, status and node type;
  edges carry source and target keys and the dependency type. No effort
  weighting, no layout, no positions: layout is the client's business and
  putting it in the API would make the response a rendering decision on a
  drift timer. D-3 the response is the whole graph in one call and takes no
  ticket key; the per-ticket route is unchanged and remains the right call for
  one ticket's blockers. D-4 read-only, deterministic ordering so two
  identical stores produce byte-identical responses. D-5 operator-api.md is
  amended in this same change.
ticket_type: feature
epic_ref: ATLAS-E12
risk_level: medium
component: api
relevant_docs:
- "docs/atlas/operator-ui.md"
- "docs/atlas/operator-api.md"
depends_on:
- "inbox-stub-api-epics-read.md"
acceptance_criteria:
- "GET /api/v1/dependencies/graph returns every node and every depends_on edge from the projected graph in one response, verified against the existing projection on a seeded store."
- "No graph computation is reimplemented in atlas/api/ or the new service; the existing dependencies layer is called, asserted by test and by diff review."
- "The route dependency makes exactly one call, and the architecture sensor fires on a seeded second call."
- "Node and edge ordering is deterministic; two runs over the same store are byte-identical, asserted by test."
- "The per-ticket dependencies route is unchanged, asserted by its existing tests remaining untouched and green."
- "operator-api.md records the route in this same change."
non_goals:
- "Read-only: no writes, no graph mutation. No layout, coordinates, effort weighting or rendering hints in the response. No change to the per-ticket dependencies route or to critical path. No third additive route. Never write to docs/planning/ (ADR-0007)."
test_requirements:
- "Fixture-driven with the existing API test harness; the executable route inventory covers every registered operation; ATLAS_LIVE_TESTS=0; seeded defects use assert 1 == 2 (B011)."
definition_of_done:
- "All acceptance criteria evidenced by named tests; the full Python gate sweep and the operator-UI pipeline both green; canonical docs updated in the same change where behaviour diverges from them; PR title carries the ticket key in the form (ATLAS-NN)."
---

# GET /api/v1/dependencies/graph

Minted from the reviewer session of 2026-07-26; the D-x decisions in
`context` are operator-ratified. Land them; do not relitigate.
