---
title: 'GET /api/v1/status: operator system status'
objective: Serve the operator-relevant system snapshot — versions, schema revision, counts and freshness timestamps — so the state of the instance is readable without a store query.
context: 'The governing architecture rule is canonical in docs/atlas/operator-api.md: ''A read projection stays a single-source repository read wherever its field set allows. A field requiring a second source moves the whole projection into an atlas.orchestration coordinating service, as the review queue already is.'' Read that document in full before designing. The precedents are atlas/orchestration/review_queue.py (multi-source coordinating service) and the ATLAS-037M ticket-detail dependency (single-source keyed read with a 404 mapping). This projection is NOT single-source: it spans several repositories, so assembly belongs in an atlas.orchestration coordinating service and the route makes exactly one call. It is deliberately the honest subset of a status endpoint for a single-operator read surface: everything reportable WITHOUT a job system or worker. Pre-ruled decisions (operator-ratified in reviewer session 2026-07-25; land them, do not relitigate): D-1 the payload covers package version, schema revision (the Alembic head the store is at), ticket and evidence counts, and the last Linear sync and last evidence-pull timestamps. D-2 anything requiring a queue, worker, or background job is out of scope and must not be invented. D-3 no secret, token, credential, connection string, or file path from the environment appears in the payload — asserted by test. D-4 read-only; the endpoint performs no migration, no sync, and no write of any kind. D-5 this is a collection-free singleton resource: there is no 404 case.'
ticket_type: feature
epic_ref: ATLAS-E12
risk_level: low
component: api
depends_on:
- inbox-stub-api-lessons-read.md
acceptance_criteria:
- GET /api/v1/status returns package version, schema revision, ticket and evidence counts, and last-sync and last-evidence-pull timestamps.
- Assembly lives in atlas.orchestration; the route dependency makes exactly one call.
- A test asserts no credential, token, or environment secret value appears in the response.
- The endpoint performs no write of any kind, asserted by test.
non_goals:
- 'Read-only: no mutation endpoints, no writes to Linear, no PR-merge capability, no writes to the resource being projected. No pagination, filtering framework, error-envelope framework, or health routes. No parallel API enum copies — canonical domain StrEnums only. No changes to domain models. Do not implement or pre-empt any of the other queued projections. Never write to docs/planning/ (ADR-0007).'
test_requirements:
- Fixture-driven with the existing API test harness; the executable route inventory must cover every registered operation; ATLAS_LIVE_TESTS=0; seeded defects use assert 1 == 2 (B011). A seeded extra service or repository call in the new route dependency must fail tests/test_api_architecture.py.
definition_of_done:
- All acceptance criteria evidenced by named tests; the no-logic sensor passes; docs/atlas/operator-api.md updated in the same change with the new route row and its one-line description; full gate sweep green with ATLAS_LIVE_TESTS=0; enumeration pins unchanged; PR title carries the minted key.
---
# GET /api/v1/status: operator system status

Minted from the reviewer session of 2026-07-25; decisions in `context` are operator-ratified.
