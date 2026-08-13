---
title: "System-tier CI handoff reconciler"
objective: >-
  Reconcile one CI-pending PR into Review Required or Changes Requested from
  complete commit-pinned system-tier evidence without keeping an agent alive to
  poll CI or granting CI any merge authority.
context: >-
  Once an agent publishes and enters CI-pending, Atlas must own the next state
  edge. A complete required-check set at the exact PR head may advance to human
  review; a definite implementation failure may return to rework. Pending,
  missing, stale, infrastructure, malformed or contradictory evidence must not
  manufacture either success or actionable code failure.
ticket_type: feature
epic_ref: ATLAS-E8
risk_level: critical
component: delivery-control
tags:
  - phase-15-5
  - ci
  - evidence
  - reconciliation
relevant_docs:
  - "docs/atlas/evidence-pipeline.md"
  - "docs/atlas/parallel-delivery-efficiency-and-integration-control.md"
  - "docs/atlas/pm-engine-and-linear-sync.md"
  - "docs/atlas/verification-engine.md"
  - "docs/runbooks/pr-acceptance.md"
depends_on:
  - "inbox-stub-02-ci-pending-capacity.md"
acceptance_criteria:
  - >-
    The reconciler evaluates the canonical required-check matrix for one exact
    repository, PR and head and distinguishes PASSED, definite implementation
    failure, pending/missing, infrastructure, stale and indeterminate outcomes.
  - >-
    Complete current-head PASSED system-tier evidence causes at most one exact
    Linear transition from CI-pending to Review Required and records a bounded
    append-only reconciliation outcome.
  - >-
    A definite current-head implementation failure causes at most one exact
    transition to Changes Requested; infrastructure, pending, missing,
    malformed, stale or ambiguous results remain CI-pending with typed reasons.
  - >-
    Before the write Atlas re-reads the PR head, board state and policy/snapshot
    identity; any movement, lease loss or mismatch produces zero transition.
  - >-
    Duplicate ticks, concurrent owners and transport-ambiguous writes are
    idempotent or fenced so a second transition cannot occur before a fresh
    observation reconciles the external state.
  - >-
    The operation performs no GitHub merge/update, Git rebase, Symphony action,
    policy mutation, acceptance confirmation, verification waiver or Done
    transition.
non_goals:
  - >-
    No CI execution, retry button, flaky-test diagnosis, automatic merge,
    automatic rebase, browser write or model classification of failures.
test_requirements:
  - >-
    Deterministic tests cover every evidence class, head/state/policy race,
    duplicate owner, lease loss, ambiguous write and zero/one mutation bound.
  - >-
    External-call spies and architecture tests prove the reconciler has only
    the named Linear state edge and cannot invoke GitHub/Git/Symphony authority.
implementation_notes:
  - >-
    Reuse canonical evidence and required-check composition. Do not infer CI
    success from GitHub rollups, command exit codes or an agent completion
    message when required named evidence is incomplete.
documentation_requirements:
  - "docs/atlas/evidence-pipeline.md"
  - "docs/atlas/parallel-delivery-efficiency-and-integration-control.md"
  - "docs/atlas/pm-engine-and-linear-sync.md"
  - "docs/atlas/verification-engine.md"
  - "docs/runbooks/pr-acceptance.md"
definition_of_done:
  - >-
    All six criteria have named race/failure tests, the writer is exactly
    bounded, complete CI remains system-tier authority, focused gates pass and
    the PR title carries the minted ticket key.
---

# System-tier CI handoff reconciler

CI may route work to review or rework; it may never merge it.
