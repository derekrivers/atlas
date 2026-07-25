---
title: GET /api/v1/tickets/{key}/dependencies and the critical path
objective: Expose dependency readiness and the critical path over HTTP, so an operator can see what is dispatchable without running `atlas deps`.
context: 'The governing architecture rule is canonical in docs/atlas/operator-api.md: ''A read projection stays a single-source repository read wherever its field set allows. A field requiring a second source moves the whole projection into an atlas.orchestration coordinating service, as the review queue already is.'' Read that document in full before designing. The precedents are atlas/orchestration/review_queue.py (multi-source coordinating service) and the ATLAS-037M ticket-detail dependency (single-source keyed read with a 404 mapping). This projection is NOT single-source: readiness is computed over the projected dependency graph, which is built from storage. Reuse the existing dependency layer — atlas.dependencies readiness (is_ready, ready_tickets) and the existing view payload builders — via a coordinating service in atlas.orchestration. Pre-ruled decisions (operator-ratified in reviewer session 2026-07-25; land them, do not relitigate): D-1 no graph logic is reimplemented in atlas/api/ or in the orchestration service: the existing dependencies layer computes, the service assembles, the route presents. D-2 the ticket route reports blockers, blocked-by and readiness, including the REASONS a ticket is not ready (is_ready collects every failing condition — surface them all, not just the first). D-3 the critical-path route is a separate operation over the whole graph and takes no ticket key. D-4 read-only. D-5 an unknown ticket key returns 404 per the established convention.'
ticket_type: feature
epic_ref: ATLAS-E12
risk_level: low
component: api
depends_on:
- inbox-stub-api-evidence-projection.md
acceptance_criteria:
- GET /api/v1/tickets/{key}/dependencies returns blockers, blocked-by, readiness and every not-ready reason for the ticket.
- A critical-path route returns the ordered path from the existing dependency projection.
- An unknown ticket key returns 404 with the documented native body, asserted by test.
- No graph computation is reimplemented in atlas/api/ or the new service — the existing dependencies layer is called, asserted by review of the diff and by test.
- Both route dependencies make exactly one call each.
non_goals:
- 'Read-only: no mutation endpoints, no writes to Linear, no PR-merge capability, no writes to the resource being projected. No pagination, filtering framework, error-envelope framework, or health routes. No parallel API enum copies — canonical domain StrEnums only. No changes to domain models. Do not implement or pre-empt any of the other queued projections. Never write to docs/planning/ (ADR-0007).'
test_requirements:
- Fixture-driven with the existing API test harness; the executable route inventory must cover every registered operation; ATLAS_LIVE_TESTS=0; seeded defects use assert 1 == 2 (B011). A seeded extra service or repository call in the new route dependency must fail tests/test_api_architecture.py.
definition_of_done:
- All acceptance criteria evidenced by named tests; the no-logic sensor passes; docs/atlas/operator-api.md updated in the same change with the new route row and its one-line description; full gate sweep green with ATLAS_LIVE_TESTS=0; enumeration pins unchanged; PR title carries the minted key.
---
# GET /api/v1/tickets/{key}/dependencies and the critical path

Minted from the reviewer session of 2026-07-25; decisions in `context` are operator-ratified.
