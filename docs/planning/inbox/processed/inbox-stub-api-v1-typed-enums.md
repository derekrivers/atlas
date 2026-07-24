---
title: 'API contract: /api/v1 prefix and canonical StrEnum response schemas'
objective: 'Move the HTTP surface under a versioned /api/v1 prefix and replace plain-string enum fields in the response schemas with the canonical domain StrEnums, so the OpenAPI document publishes allowed values. Both changes alter every response path: landing them together breaks the contract exactly once, while the client count is zero.'
context: 'Pre-ruled decisions (operator-ratified in reviewer session 2026-07-24; land them, do not relitigate): D-1 prefix placement: routers declare resource-local prefixes (''/tickets'', ''/reviews''); create_app() mounts them via include_router(..., prefix=''/api/v1''). The version lives in exactly one place. D-2 enum single-sourcing: schema fields with a closed value set are annotated with the canonical StrEnum directly (e.g. status: TicketStatus). Presenters stop calling .value - they pass the enum member and pydantic serialises. No parallel API enum is defined for any field; if a field''s backing type is not a StrEnum, halt and flag rather than inventing one. A duplicated enum is a maintained copy on a drift timer. D-3 contract visibility: the OpenAPI document must publish allowed values for every enum-typed field - this is the point of the ticket and a test proves it. D-4 spine legality: atlas.api importing atlas.core and atlas.verification is legal (api is top of spine); lint-imports remains the arbiter and no contract edits belong in this ticket. Canonical enum homes: atlas/core/models/ticket.py (TicketStatus, TicketType), atlas/core/enums.py (RiskLevel), and the verification enums backing verdict/check_type/check status - locate them, do not redefine them.'
ticket_type: feature
epic_ref: ATLAS-E12
risk_level: low
component: api
acceptance_criteria:
- All routes serve under /api/v1/...; requests to the former unversioned paths return 404, asserted by test.
- The executable route inventory test covers the new paths and passes; no registered operation lacks a test.
- Every closed-value schema field is typed with its canonical StrEnum; no enum .value serialisation remains in atlas/api/presenters.py for those fields.
- A test asserts the OpenAPI schema publishes the enum members for status, ticket_type, risk_level, verdict and check_type.
- A test asserts an invalid status query parameter is still rejected with 422 under the new typing.
non_goals:
- 'No new endpoints. No pagination, filtering, health routes or error-model work. No back-compatibility for the old unversioned paths: no redirects, no dual registration. No new enum types - this ticket reuses canonical domain StrEnums and never defines an API-side copy. No changes to atlas/orchestration, atlas/storage or domain models.'
test_requirements:
- Fixture-driven, ATLAS_LIVE_TESTS=0; seeded defects use assert 1 == 2 (B011); enumeration pins unchanged.
definition_of_done:
- All acceptance criteria evidenced by named tests; full gate sweep green; diff limited to atlas/api/ and tests/ (plus docs/atlas/operator-api.md only if a stated endpoint path there must change with the code); PR title carries the ticket key.
---

# API contract: /api/v1 prefix and canonical StrEnum response schemas

Minted from the reviewer session of 2026-07-24; decisions in `context` are operator-ratified.
