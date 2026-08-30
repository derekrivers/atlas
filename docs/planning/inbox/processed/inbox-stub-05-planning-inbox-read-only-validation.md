---
title: Read-only planning inbox integrity command
objective: Expose a complete deterministic pre-PlanRun planning-input preflight through a fast read-only CLI so a
  planning agent can validate integrity, promoted source anchors and applicable proposal gates before the operator-owned
  plan/apply boundary.
context: The current `atlas-ticket-planning` skill can run diff checks and the documentation linter but leaves definitive
  batch-integrity and promoted-ticket gate validation behind `atlas plan --stubs-only` and `atlas apply`. That pushes
  manifest/dependency and source-anchor failures across the operator handoff and causes avoidable failed PlanRuns and
  repair loops. The preflight must reuse, not reimplement, the existing integrity, ingestion, promotion and gate machinery.
ticket_type: feature
epic_ref: ATLAS-E3
risk_level: medium
component: planning-inbox-validation
tags:
- maintenance
- ticket-minting
- agent-skills
- planning
- validation
- cli
relevant_docs:
- docs/runbooks/planning-phases-and-ticket-stubs.md
- docs/atlas/planning-engine-specification.md
- docs/decisions/0007-generative-planning-with-deterministic-reconciliation.md
depends_on:
- inbox-stub-01-ticket-minting-lifecycle-authority.md
- inbox-stub-02-planning-intake-isolation.md
acceptance_criteria:
- A repository-owned CLI command validates the current committed planning inbox by invoking the same shared
  `validate_inbox_batch_integrity` contract used by stubs-only planning/apply, including exact-path, manifest/base
  coverage, active-inbox equality, dependency identity/order and cycle invariants.
- The command builds the same planner input `AnchorIndex`, including active-stub durable processed aliases, without
  introducing an independent YAML parser, AnchorIndex construction or anchor policy.
- The command constructs the stubs-only proposal in memory through the same deterministic backlog-echo and
  `promote_inbox_stubs` logic, with no second promotion engine or reconciliation implementation.
- The command runs the same applicable existing proposal gates over the promoted in-memory proposal, including Gate
  4 source-anchor resolution, without duplicating gate implementation or policy.
- The preflight performs no provider/model call, reconciliation diff or persistence, PlanRun persistence, key assignment,
  store mutation, planning-render write, stub retirement or PM/Linear/GitHub/network mutation.
- The command reports deterministic failures before `atlas plan --stubs-only`, exposes concise human and machine-readable
  JSON results, follows existing CLI success/precondition conventions and fails closed on dirty, stale, malformed,
  incomplete or gate-invalid planning inputs rather than repairing them.
- Focused parity tests seed representative valid and invalid batches, including an explicit `source_anchor` to a
  non-indexed document, and prove the read-only preflight fails that case with the same Gate-4 semantics as `atlas
  plan --stubs-only` without creating a PlanRun.
non_goals:
- No persisted/operator proposal generation, reconciliation diff, key minting, apply confirmation, render write,
  inbox retirement or external publication; the in-memory echo/promotion exists only for read-only preflight.
- No broad validation framework or second phase-bundle schema.
test_requirements:
- Focused deterministic tests cover valid batches, every shared integrity failure family and promoted-ticket gate
  failures, including a non-indexed explicit source anchor, without network or model access.
- CLI tests prove read-only behavior by asserting no PlanRun, reconciliation, key, store, render or retirement mutation
  on both pass and fail paths and parity with the existing Gate-4 failure semantics.
implementation_notes:
- Prefer a thin command/service wrapper over the existing integrity, ingestion, durable-alias, backlog-echo, promotion
  and gate primitives; preserve one implementation of every invariant and policy.
- Choose the final CLI spelling consistently with existing Atlas top-level command conventions and document that
  exact spelling in the planning runbook.
documentation_requirements:
- docs/runbooks/planning-phases-and-ticket-stubs.md
- docs/atlas/planning-engine-specification.md
definition_of_done:
- A planning agent can validate a committed batch and its promoted-ticket anchors/gates locally without crossing into
  `atlas plan --stubs-only` or `atlas apply`.
- Seeded-defect parity tests prove the new command fails where the existing shared integrity and proposal gates fail,
  without creating a failed PlanRun.
---

# Read-only planning inbox integrity command

Governed maintenance input for the `ticket-minting-skills-v1` batch.
