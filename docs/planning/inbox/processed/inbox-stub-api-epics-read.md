---
title: "GET /api/v1/epics and epic_key on the ticket board item"
objective: >-
  Expose stored epics over HTTP and add the owning epic key to the board item,
  so the operator UI can group a 162-ticket board by the dimension the
  operator actually navigates by.
context: >-
  Phase 11 is permitted exactly two additive v1 read routes and this is the
  first (OP-2); docs/atlas/operator-ui.md is the governing design and
  operator-api.md is amended in this same change. Pre-ruled decisions
  (operator-ratified, reviewer session 2026-07-26): D-1 GET /api/v1/epics is a
  single-repository projection over the epic repository, presented like every
  other collection, with repository order preserved and no pagination. D-2 the
  board item gains epic_key. That field requires a second source, so under
  operator-api.md's own rule the BOARD projection — and only the board
  projection — moves into an atlas.orchestration coordinating service; ticket
  detail stays single-source and gains nothing. D-3 the API contains-no-logic
  sensor is not weakened, suppressed or exempted: the route dependency still
  makes exactly one call, now to the coordinating service. D-4 read-only, no
  writes, no new enums, no parallel API enum copies. D-5 operator-api.md's
  route table, its 'no other v1 routes in this phase' statement and its
  architecture section are amended in the same change, and the amendment names
  Phase 11 as the authority for the two additions.
ticket_type: feature
epic_ref: ATLAS-E12
risk_level: low
component: api
relevant_docs:
- "docs/atlas/operator-ui.md"
- "docs/atlas/operator-api.md"
acceptance_criteria:
- "GET /api/v1/epics returns stored epic records in repository order; an empty store returns a successful empty collection."
- "TicketBoardItemSchema carries epic_key, and a ticket with no epic renders it null, asserted by test."
- "The board route dependency makes exactly one call, and tests/test_api_architecture.py still fires on a seeded second call in that dependency."
- "operator-api.md records both new routes, the board projection's move into atlas.orchestration, and the amended scope statement, in this same change."
- "The generated TypeScript client regenerates cleanly against the amended schema and the drift guard passes."
non_goals:
- "Read-only: no writes, no epic mutation, no Linear writes. No third v1 route enters this phase. Ticket detail does not gain epic state. No pagination, no bespoke error envelope, no parallel enum copies. Never write to docs/planning/ (ADR-0007)."
test_requirements:
- "Fixture-driven with the existing API test harness; the executable route inventory covers every registered operation; ATLAS_LIVE_TESTS=0; seeded defects use assert 1 == 2 (B011). A seeded extra call in the board route dependency must fail tests/test_api_architecture.py."
definition_of_done:
- "All acceptance criteria evidenced by named tests; the full Python gate sweep and the operator-UI pipeline both green; canonical docs updated in the same change where behaviour diverges from them; PR title carries the ticket key in the form (ATLAS-NN)."
---

# GET /api/v1/epics and epic_key on the ticket board item

Minted from the reviewer session of 2026-07-26; the D-x decisions in
`context` are operator-ratified. Land them; do not relitigate.
