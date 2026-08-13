---
title: "Delivery-control CI and integration-pressure API"
objective: >-
  Expose CI-pending capacity, validation-plan provenance, protected-lane holds
  and exact-base integration state through the authenticated delivery-control
  API without creating a CI, GitHub or Symphony command surface.
context: >-
  Phase 15's API explains policy and admission. Phase 15.5 adds new pressure
  dimensions and an exact-base assessment that the operator must understand
  before raising concurrency. The API remains a thin projection over canonical
  services. Reads are observational; the existing policy replacement remains
  the only delivery-control mutation.
ticket_type: feature
epic_ref: ATLAS-E12
risk_level: high
component: atlas.api
tags:
  - phase-15-5
  - api
  - ci
  - integration
relevant_docs:
  - "docs/atlas/operator-api.md"
  - "docs/atlas/multi-agent-delivery-control.md"
  - "docs/atlas/parallel-delivery-efficiency-and-integration-control.md"
  - "docs/atlas/governed-operator-actions.md"
depends_on:
  - "inbox-stub-02-ci-pending-capacity.md"
  - "inbox-stub-03-system-tier-ci-handoff.md"
  - "inbox-stub-05-protected-integration-lanes.md"
  - "inbox-stub-07-no-rewrite-exact-base-acceptance.md"
  - "ATLAS-250"
acceptance_criteria:
  - >-
    The delivery-control projection includes integration budget/occupancy,
    CI-pending tickets and typed outcomes, protected lane occupancy/holds,
    validation-plan identity and exact-base assessment with bounded reasons.
  - >-
    Every value is returned from one coherent server-owned snapshot with exact
    policy, board, evidence and integration identities; stale or indeterminate
    inputs remain visible and are never rendered as available capacity.
  - >-
    GET remains authenticated, no-store and observational: it performs no
    Linear/GitHub refresh, validation execution, admission lease, policy write,
    receipt write, transition, rebase, merge or Symphony action.
  - >-
    Existing complete-policy replacement validates the integration budget and
    protected-lane rules through the same actor, CSRF, idempotency,
    compare-and-set and atomic receipt boundary.
  - >-
    Responses are bounded and secret-free and exclude raw CI/provider payloads,
    command output, credentials, workspace paths and unbounded exceptions.
  - >-
    OpenAPI and generated TypeScript contracts regenerate deterministically,
    while route-inventory tests reject CI retry/cancel, branch update/rebase,
    merge, worker control and individual-ticket transition endpoints.
non_goals:
  - >-
    No CI execution, ticket-state command, automatic policy change, GitHub
    mutation, Symphony control, merge queue, raw log endpoint or remote admin.
test_requirements:
  - >-
    API/service tests cover coherent, stale, over-capacity, held, failed and
    indeterminate projections plus policy validation and all security/conflict
    outcomes.
  - >-
    Architecture, route-inventory, OpenAPI/client-drift and secret-canary tests
    prove the thin read boundary and prohibited controls.
implementation_notes:
  - >-
    Extend the Phase 15 resource rather than creating a second control plane.
    API presenters must not recompute validation, admission or integration
    classifications.
documentation_requirements:
  - "docs/atlas/operator-api.md"
  - "docs/atlas/multi-agent-delivery-control.md"
  - "docs/atlas/parallel-delivery-efficiency-and-integration-control.md"
definition_of_done:
  - >-
    All six criteria have named API/architecture evidence, generated contracts
    are drift-free, the API exposes no new external command authority, full
    applicable gates pass and the PR title carries the minted ticket key.
---

# Delivery-control CI and integration-pressure API

The operator can see every queue without being given an unsafe shortcut.
