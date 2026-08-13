---
title: "Operator CI and integration-pressure console"
objective: >-
  Make CI waiting, protected-lane contention, validation scope and exact-base
  integration readiness legible to the operator without client-side inference
  or merge, rebase, retry and worker controls.
context: >-
  Worker utilisation alone is a misleading concurrency signal. The existing
  delivery-control UI must show where work is accumulating and why: coding,
  CI, integration lanes, review or rework. It consumes generated API types and
  presents server classifications as limits and evidence, never as automatic
  recommendations to fill capacity.
ticket_type: feature
epic_ref: ATLAS-E13
risk_level: high
component: operator-ui
tags:
  - phase-15-5
  - operator-ui
  - ci
  - integration
relevant_docs:
  - "docs/atlas/operator-ui.md"
  - "docs/atlas/multi-agent-delivery-control.md"
  - "docs/atlas/parallel-delivery-efficiency-and-integration-control.md"
  - "docs/atlas/governed-operator-actions.md"
depends_on:
  - "inbox-stub-08-delivery-pressure-api.md"
  - "ATLAS-251"
acceptance_criteria:
  - >-
    The control view shows working, CI/integration, review and Changes Requested
    occupancy as separate server-owned quantities, including used/available
    budgets and visibly stale or indeterminate snapshots.
  - >-
    CI-pending tickets display exact head, validation profile, required-check
    state and every typed wait/failure reason without rendering raw logs or
    recomputing the server classification.
  - >-
    Protected integration lanes show current occupants, held candidates and
    complete hold reasons, and never imply that a free Symphony slot overrides
    a saturated lane.
  - >-
    Exact-branch, exact-integration-candidate, rebase-required and indeterminate
    assessments are visibly distinct and never presented as merge approval.
  - >-
    Policy confirmation includes integration budget and protected-lane changes
    with expected revision and fresh idempotency identity; conflicts preserve
    the proposal safely and require explicit reconfirmation.
  - >-
    Executable control inventory proves there is no CI retry/cancel, ticket
    transition, GitHub update/merge, Git rebase/push, Symphony worker control or
    automatic concurrency/ramp control.
  - >-
    Keyboard, focus, announcements, dense reason lists, long identities,
    responsive viewports and WCAG checks pass against the seeded live API.
non_goals:
  - >-
    No CI log viewer, merge queue, branch updater, rebase button, worker manager,
    automatic optimiser, client-side admission calculation or Phase 16 charts.
test_requirements:
  - >-
    Component/query tests cover every occupancy and assessment class, policy
    success/conflict, stale snapshots, long values and prohibited controls.
  - >-
    Playwright and accessibility tests exercise the built UI against a seeded
    live API and prove zero external writes beyond an explicit policy command.
implementation_notes:
  - >-
    Reuse generated clients, authenticated mutation primitives and established
    design tokens. Treat server state as authoritative and keep proposal state
    separate until confirmed.
documentation_requirements:
  - "docs/atlas/operator-ui.md"
  - "docs/atlas/multi-agent-delivery-control.md"
  - "docs/atlas/parallel-delivery-efficiency-and-integration-control.md"
definition_of_done:
  - >-
    All seven criteria have named component/browser/accessibility evidence, no
    forbidden control exists, generated-client and production-build gates pass,
    and the PR title carries the minted ticket key.
---

# Operator CI and integration-pressure console

Productive concurrency is visible as flow, not as a worker-count trophy.
