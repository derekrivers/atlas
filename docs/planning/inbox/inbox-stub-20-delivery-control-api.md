---
title: "Authenticated delivery-control policy and status API"
objective: >-
  Expose current policy, occupancy and admission reasons and allow one
  authenticated compare-and-set policy replacement without creating a generic
  ticket or Symphony control surface.
context: >-
  The operator needs to understand why eligible work is held and deliberately
  change budgets or mode. Phase 13 owns mutation security and receipts; Phase
  15 services own validation and admission. The API remains thin and exposes no
  ticket-state, agent-session or automatic-ceiling action.
ticket_type: feature
epic_ref: ATLAS-E12
risk_level: high
component: atlas.api
tags:
  - api
  - admission
  - policy
  - authentication
relevant_docs:
  - "docs/atlas/multi-agent-delivery-control.md"
  - "docs/atlas/operator-api.md"
  - "docs/atlas/governed-operator-actions.md"
depends_on:
  - "inbox-stub-01-operator-session-security.md"
  - "inbox-stub-16-delivery-admission-policy.md"
  - "inbox-stub-17-delivery-occupancy-snapshot.md"
  - "inbox-stub-19-fail-closed-admission-sync.md"
acceptance_criteria:
  - >-
    `GET /api/v1/delivery-control` returns active policy/revision, approved
    ceiling, truthful last-successful-sync time, current occupancy, latest
    decisions and every typed hold/over-capacity/indeterminate reason.
  - >-
    `POST /api/v1/delivery-control/policy` accepts one complete strict policy
    plus `expected_revision`; actor, action identity and current state remain
    server-owned.
  - >-
    POST requires the shared session, Host/Origin, JSON, CSRF and idempotency
    controls and makes exactly one policy-service call; stale revision and
    altered replay return conflict with no change.
  - >-
    GET is authenticated, no-store and observational: it never acquires an
    admission lease, refreshes Linear, writes a receipt or mutates policy.
  - >-
    Responses are bounded and secret-free; raw Linear payloads, tokens,
    session secrets and unbounded stored exceptions are never projected.
  - >-
    Route-inventory tests reject ticket-status, dispatch, cancel, merge,
    rebase, arbitrary PATCH/PUT and automatic-ceiling endpoints.
  - >-
    OpenAPI schemas and generated TypeScript types/runtime metadata regenerate
    deterministically with no unexplained drift.
non_goals:
  - >-
    No ticket promotion endpoint, Symphony worker control, GitHub/Linear
    credential input, background optimiser, multi-user role or remote hosting.
test_requirements:
  - >-
    API/service tests cover read and policy success, every validation/security/
    conflict outcome, no-write GET and route-inventory prohibitions.
  - >-
    Existing contains-no-logic, OpenAPI/client and secret-redaction tests remain
    green; seeded Python defects use `assert 1 == 2` (B011).
implementation_notes:
  - >-
    Route dependencies resolve context, call one application service and
    present. Do not compute occupancy or admission in `atlas.api`.
documentation_requirements:
  - "docs/atlas/operator-api.md"
  - "docs/atlas/multi-agent-delivery-control.md"
definition_of_done:
  - >-
    All seven criteria have named API/architecture coverage; Python, OpenAPI,
    TypeScript and doc-linter gates pass; docs land with code; the PR title
    carries the minted ticket key.
---

# Authenticated delivery-control policy and status API

The browser can govern policy, never command an individual worker.
