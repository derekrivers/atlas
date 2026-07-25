---
title: 'GET /api/v1/tickets/{key}/evidence: evidence projection'
objective: Expose a ticket's stored evidence over HTTP — type, tier, status and pin-triple completeness — so evidence review needs no direct store query.
context: 'The governing architecture rule is canonical in docs/atlas/operator-api.md: ''A read projection stays a single-source repository read wherever its field set allows. A field requiring a second source moves the whole projection into an atlas.orchestration coordinating service, as the review queue already is.'' Read that document in full before designing. The precedents are atlas/orchestration/review_queue.py (multi-source coordinating service) and the ATLAS-037M ticket-detail dependency (single-source keyed read with a 404 mapping). This projection is NOT single-source: EvidenceRepo.list_for_ticket takes the ticket''s UUID, so serving a key-addressed route requires resolving the key first (TicketRepo.get_by_key) and then reading evidence. That is two sources, so a coordinating read service in atlas.orchestration is required — the route dependency calls it once and presents. Pre-ruled decisions (operator-ratified in reviewer session 2026-07-25; land them, do not relitigate): D-1 assembly lives in atlas.orchestration, mirroring review_queue; the route makes exactly one call. D-2 read-only: the API never pulls, ingests, or mutates evidence — `atlas evidence pull` remains the only writer. D-3 the projection reports whether each record carries the full system-tier pin triple (commit_sha, external_run_id, payload_hash) as a derived boolean; raw payloads are NOT exposed. D-4 closed-value fields use canonical StrEnums (evidence type, status, tier). D-5 an unknown ticket key returns 404 using the established convention in docs/atlas/operator-api.md; a KNOWN ticket with no evidence returns a successful EMPTY collection, never 404 — the distinction is between an absent resource and an empty one.'
ticket_type: feature
epic_ref: ATLAS-E12
risk_level: low
component: api
acceptance_criteria:
- GET /api/v1/tickets/{key}/evidence returns the ticket's evidence records with type, tier, status and pin-triple completeness.
- A known ticket with no evidence returns a successful empty collection, asserted by test.
- An unknown ticket key returns 404 with the documented native body, asserted by test.
- No raw evidence payload appears in the response, asserted by test.
- Assembly lives in atlas.orchestration and the route dependency makes exactly one call.
non_goals:
- 'Read-only: no mutation endpoints, no writes to Linear, no PR-merge capability, no writes to the resource being projected. No pagination, filtering framework, error-envelope framework, or health routes. No parallel API enum copies — canonical domain StrEnums only. No changes to domain models. Do not implement or pre-empt any of the other queued projections. Never write to docs/planning/ (ADR-0007).'
test_requirements:
- Fixture-driven with the existing API test harness; the executable route inventory must cover every registered operation; ATLAS_LIVE_TESTS=0; seeded defects use assert 1 == 2 (B011). A seeded extra service or repository call in the new route dependency must fail tests/test_api_architecture.py.
definition_of_done:
- All acceptance criteria evidenced by named tests; the no-logic sensor passes; docs/atlas/operator-api.md updated in the same change with the new route row and its one-line description; full gate sweep green with ATLAS_LIVE_TESTS=0; enumeration pins unchanged; PR title carries the minted key.
---
# GET /api/v1/tickets/{key}/evidence: evidence projection

Minted from the reviewer session of 2026-07-25; decisions in `context` are operator-ratified.
