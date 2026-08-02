---
title: "Symphony ceiling contract and controlled-ramp runbook"
objective: >-
  Make the 3-to-5-to-7-to-10 ceiling change a governed, reversible operator
  procedure with exact evidence gates, while keeping committed `main` at three
  until the dedicated milestone branch proves ten.
context: >-
  More available workers are safe only when Atlas admission and human review
  capacity agree. This ticket defines the config ownership, preflight,
  observation window, stop/rollback rules and evidence receipt for each level.
  It must define that the operator changes `WORKFLOW.md` only on a dedicated
  milestone branch through 5, 7 and 10 after each preceding gate passes. It
  must not perform the live Phase 15 milestone or treat ten as a target.
ticket_type: infrastructure
epic_ref: ATLAS-E10
risk_level: high
component: orchestration
tags:
  - symphony
  - capacity
  - runbook
  - governance
relevant_docs:
  - "WORKFLOW.md"
  - "docs/atlas/multi-agent-delivery-control.md"
  - "docs/atlas/symphony-integration.md"
  - "docs/runbooks/operator-environment.md"
depends_on:
  - "inbox-stub-19-fail-closed-admission-sync.md"
acceptance_criteria:
  - >-
    Canonical docs define one operator-owned ceiling value and distinguish it
    from Atlas working/review/lane budgets and actual occupied slots.
  - >-
    The runbook pins the dedicated milestone branch, prerequisites, exact
    `WORKFLOW.md` edit, preflight, observation window, evidence capture,
    success/stop conditions and rollback for ceilings 3, 5, 7 and 10; each
    branch increase occurs only after the preceding gate passes.
  - >-
    Five cannot begin without baseline admission/pause/rework proof; seven
    requires stable review and stale-write evidence; ten requires Phase 14
    closure and adequate exact-head acceptance throughput.
  - >-
    A failed level restores or retains the last proven milestone-branch value,
    records the failure, leaves Phase 15 open and merges no ceiling change to
    `main`. Lowering a branch ceiling or pausing admission never claims to
    terminate active Symphony sessions.
  - >-
    `WORKFLOW.md` remains at three in this implementation change and on
    committed `main` until milestone closure. Executable contract tests fail an
    unaccompanied or above-ten ceiling edit and require the successful closure
    change to commit and merge exactly ten.
  - >-
    The procedure contains no Atlas endpoint or agent path for editing
    `WORKFLOW.md`, Symphony configuration, policy or acceptance evidence.
non_goals:
  - >-
    No live ramp execution, automatic autoscaling, scheduler modification,
    worker cancellation, merge queue, performance scoring or Phase 16 metrics.
test_requirements:
  - >-
    Extend workflow-contract and documentation-linter tests for ceiling bounds,
    unchanged initial value, authority wording and required level gates.
  - >-
    Rehearse the runbook against deterministic fixtures only; no live worker is
    started by CI and seeded Python defects use `assert 1 == 2` (B011).
implementation_notes:
  - >-
    Keep the product-invariant Symphony workflow body intact. The operator
    edits the declared ceiling only on the dedicated milestone branch at a
    recorded live gate; intermediate ceiling edits never merge independently.
documentation_requirements:
  - "WORKFLOW.md"
  - "docs/atlas/multi-agent-delivery-control.md"
  - "docs/atlas/symphony-integration.md"
  - "docs/runbooks/operator-environment.md"
definition_of_done:
  - >-
    All six criteria have named contract/doc tests; the initial ceiling remains
    three; full Python and doc-linter gates pass; the PR title carries the
    minted ticket key.
---

# Symphony ceiling contract and controlled-ramp runbook

Ten is a proven maximum, never a utilisation target.
