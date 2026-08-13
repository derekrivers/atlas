---
title: "CI-pending delivery state and bounded integration capacity"
objective: >-
  Represent work that has left an agent but has not yet earned system-tier CI
  handoff as a distinct non-Symphony-active state with an operator-owned
  integration budget.
context: >-
  `PR Open` is currently a Symphony-active working state, so an agent can remain
  claimed for repeated turns while CI runs. Treating that time as active coding
  wastes worker capacity, while treating it as free capacity can flood CI and
  review. Introduce an explicit CI-pending lifecycle and budget: it does not
  consume a Symphony working slot, but it does consume integration capacity
  until required CI evidence reaches a terminal classification.
ticket_type: infrastructure
epic_ref: ATLAS-E6
risk_level: critical
component: delivery-control
tags:
  - phase-15-5
  - ci-pending
  - capacity
  - state-ownership
relevant_docs:
  - "WORKFLOW.md"
  - "docs/architecture/data-model-and-schemas.md"
  - "docs/atlas/multi-agent-delivery-control.md"
  - "docs/atlas/parallel-delivery-efficiency-and-integration-control.md"
  - "docs/atlas/pm-engine-and-linear-sync.md"
  - "docs/atlas/symphony-integration.md"
depends_on:
  - "ATLAS-249"
acceptance_criteria:
  - >-
    The canonical ticket/status contract adds one CI-pending state with exact
    Linear mapping and transition ownership: the agent may enter it only after
    publishing a PR, Atlas owns every exit, and no browser or Symphony path can
    impersonate those exits.
  - >-
    CI-pending is excluded from Symphony active states and working occupancy,
    counted separately as integration occupancy, and bounded by a validated
    operator-owned integration budget.
  - >-
    Policy validation rejects negative, zero-invalid, above-ceiling or
    internally incoherent integration budgets, and immutable revisions retain
    compare-and-set, idempotency and atomic receipt behaviour.
  - >-
    Existing active work is never cancelled or demoted when the budget is
    lowered; over-capacity is reported and new admission fails closed until
    occupancy is coherent.
  - >-
    Snapshot fingerprints include CI-pending identities and integration-budget
    inputs, and incomplete, duplicated or unmapped CI-pending board state makes
    admission unavailable.
  - >-
    Migrations upgrade from the current head on SQLite and PostgreSQL without
    rewriting historical delivery-policy revisions or migration `0025`.
non_goals:
  - >-
    No CI result interpretation, workflow prompt change, automatic review,
    Symphony cancellation, GitHub merge/rebase or historical policy rewrite.
test_requirements:
  - >-
    Model, migration, policy and snapshot tests cover every transition owner,
    budget bound, over-capacity case, fingerprint, stale revision and database
    backend.
  - >-
    Workflow/state-inventory tests prove CI-pending is non-active for Symphony
    and cannot be confused with Review Required, Changes Requested or Done.
implementation_notes:
  - >-
    Keep working, CI/integration and review pressure as separate quantities.
    The new budget is a maximum queue bound, never a target and never inferred
    from available Symphony workers.
documentation_requirements:
  - "WORKFLOW.md"
  - "docs/architecture/data-model-and-schemas.md"
  - "docs/atlas/multi-agent-delivery-control.md"
  - "docs/atlas/parallel-delivery-efficiency-and-integration-control.md"
  - "docs/atlas/pm-engine-and-linear-sync.md"
  - "docs/atlas/symphony-integration.md"
definition_of_done:
  - >-
    All six criteria have named model/migration/contract evidence, historical
    policy remains immutable, full focused Python and documentation gates pass,
    and the PR title carries the minted ticket key.
---

# CI-pending delivery state and bounded integration capacity

Waiting for CI is delivery pressure, not active engineering work.
