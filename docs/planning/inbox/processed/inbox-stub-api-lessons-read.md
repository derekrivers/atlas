---
title: 'GET /api/v1/lessons: lessons read projection'
objective: Expose stored lessons over HTTP with status filtering, mirroring the `atlas lessons` read projections.
context: 'The governing architecture rule is canonical in docs/atlas/operator-api.md: ''A read projection stays a single-source repository read wherever its field set allows. A field requiring a second source moves the whole projection into an atlas.orchestration coordinating service, as the review queue already is.'' Read that document in full before designing. The precedents are atlas/orchestration/review_queue.py (multi-source coordinating service) and the ATLAS-037M ticket-detail dependency (single-source keyed read with a 404 mapping). Whether this projection is single-source is a GENUINE OPEN QUESTION and is yours to resolve at the plan gate: LessonRepo exposes list() and list_drafts() but no list_by_status. Propose either (a) a single-source read with a new plain LessonRepo.list_by_status, following the ticket board''s ratified pattern where an optional query parameter selects between two repository operations, or (b) an orchestration service if your field set genuinely needs a second source. State which and why. Note the ticket board''s carve-out is canonical: parameter-driven selection between repository operations is transport routing and passes the sensor, but comparing a parameter to a specific domain value is a domain branch and fires it. Pre-ruled decisions (operator-ratified in reviewer session 2026-07-25; land them, do not relitigate): D-1 read-only and promotion-safe: the operator promotion gate (ADR-0009) stays entirely in the CLI — this API never promotes, rejects, archives, or merges a lesson. D-2 status filtering uses the canonical StrEnum, never a string literal; an invalid value is rejected with 422. D-3 whichever architecture you choose, the route dependency makes exactly one call.'
ticket_type: feature
epic_ref: ATLAS-E12
risk_level: low
component: api
depends_on:
- inbox-stub-api-dependency-projection.md
acceptance_criteria:
- GET /api/v1/lessons returns stored lessons, with an optional status filter using the canonical enum.
- An invalid status value is rejected with 422, asserted by test.
- No route in the diff mutates lesson state, asserted by test.
- The chosen architecture (single-source versus orchestration) is justified in the plan and recorded in docs/atlas/operator-api.md.
- The route dependency makes exactly one call and the no-logic sensor passes.
non_goals:
- 'Read-only: no mutation endpoints, no writes to Linear, no PR-merge capability, no writes to the resource being projected. No pagination, filtering framework, error-envelope framework, or health routes. No parallel API enum copies — canonical domain StrEnums only. No changes to domain models. Do not implement or pre-empt any of the other queued projections. Never write to docs/planning/ (ADR-0007).'
test_requirements:
- Fixture-driven with the existing API test harness; the executable route inventory must cover every registered operation; ATLAS_LIVE_TESTS=0; seeded defects use assert 1 == 2 (B011). A seeded extra service or repository call in the new route dependency must fail tests/test_api_architecture.py.
definition_of_done:
- All acceptance criteria evidenced by named tests; the no-logic sensor passes; docs/atlas/operator-api.md updated in the same change with the new route row and its one-line description; full gate sweep green with ATLAS_LIVE_TESTS=0; enumeration pins unchanged; PR title carries the minted key.
---
# GET /api/v1/lessons: lessons read projection

Minted from the reviewer session of 2026-07-25; decisions in `context` are operator-ratified.
