---
title: "Authenticated lesson promote and reject API commands"
objective: >-
  Expose the governed lesson disposition service through two explicit v1
  commands with session, CSRF, idempotency and typed conflict semantics, while
  preserving Atlas's no-domain-logic API boundary.
context: >-
  ATLAS-201 delivered the read-only lessons projection. Phase 13 now adds the
  first resource writes. Pre-ruled decisions: D-1 the only routes are
  `POST /api/v1/lessons/{lesson_id}/promote` and `/reject`; there is no generic
  lesson PATCH/PUT. D-2 both routes require the shared authenticated mutation
  dependency and `Idempotency-Key`; actor identity is absent from request
  schemas. D-3 promote's body is exactly finite confidence, reject's body is an
  empty strict object. D-4 the route resolves context, calls one disposition
  application operation and presents; it does not load a lesson, branch on
  status, create receipts or implement replay. D-5 success returns the safe
  updated lesson plus action receipt. Unknown is 404, validation 422,
  unauthenticated 401, security refusal 403, stale/altered replay 409. D-6 read
  routes retain existing compatibility and no read becomes auth-dependent.
ticket_type: feature
epic_ref: ATLAS-E12
risk_level: high
component: atlas.api
tags:
  - api
  - lessons
  - authentication
  - idempotency
  - openapi
relevant_docs:
  - "docs/atlas/governed-operator-actions.md"
  - "docs/atlas/operator-api.md"
  - "docs/decisions/0009-single-operator-governance.md"
depends_on:
  - "ATLAS-201"
  - "inbox-stub-01-operator-session-security.md"
  - "inbox-stub-03-governed-lesson-disposition.md"
acceptance_criteria:
  - >-
    The v1 route table adds exactly the two explicit POST routes at the
    documented paths; executable route inventory and OpenAPI tests reject a
    generic PATCH/PUT, an extra lesson action or an unversioned duplicate.
  - >-
    Both routes require the shared authenticated mutation context,
    `X-Atlas-CSRF`, allowed Host/Origin, strict JSON and `Idempotency-Key`
    before their application-service dependency is invoked.
  - >-
    Promote accepts exactly `{"confidence": number}` with the canonical finite
    `0.0..1.0` validation; reject accepts exactly `{}`. Actor, status, content
    and unknown fields are rejected with 422 and cannot reach the service.
  - >-
    Each route dependency makes exactly one call to the shared disposition
    operation and presenters map typed success, unknown, stale-state, replay,
    altered-replay and security outcomes to the documented 2xx/401/403/404/
    409/422 responses without reparsing domain state.
  - >-
    Success returns the updated safe lesson representation and bounded action
    receipt with server-owned `human/operator` attribution; no credential,
    CSRF value, raw request body or internal exception is present.
  - >-
    Same-key/same-body retry returns the byte-equivalent semantic outcome and
    receipt without a second mutation; same-key/different-confidence or action
    returns 409 with no mutation.
  - >-
    Existing GET lesson route behaviour, ordering, optional canonical status
    filter, 422 invalid-filter response and generated-client compatibility
    remain unchanged apart from the additive command types.
non_goals:
  - >-
    No lesson edit, merge, ACTIVE archive, re-promotion, generic update route,
    browser UI, remote binding, GitHub write, Linear write, PR operation or
    client-supplied actor.
test_requirements:
  - >-
    Fixture-driven API tests cover success and every status mapping, security
    preconditions, strict schemas, duplicate/altered replay, stale CLI race and
    secret-free errors; `ATLAS_LIVE_TESTS=0`.
  - >-
    Extend route-inventory/no-logic architecture tests and OpenAPI generated
    client drift tests; a seeded extra repository/service call or route-level
    state branch must fail; seeded Python defects use `assert 1 == 2` (B011).
implementation_notes:
  - >-
    Keep presenter schemas additive and reuse the canonical Lesson/EntityStatus
    types. Do not invoke CLI commands or construct actor context from JSON.
  - >-
    The route layer does not own transactions. The disposition/gateway
    operation must return everything needed to present success or conflict in
    one typed result.
documentation_requirements:
  - "docs/atlas/operator-api.md"
  - "docs/atlas/governed-operator-actions.md"
definition_of_done:
  - >-
    All seven acceptance criteria are evidenced by named tests; Python,
    OpenAPI/TypeScript drift, UI compile compatibility and doc-linter gates are
    green; canonical API docs land in the same change; the PR title carries
    the minted ticket key.
---

# Authenticated lesson promote and reject API commands

The first v1 writes are explicit commands over the existing governed domain
service.
