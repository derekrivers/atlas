---
title: Read-only planning inbox integrity command
objective: Expose the existing Atlas stub/batch integrity guard through a fast read-only CLI so a planning agent
  can validate a complete committed inbox batch before the operator-owned plan/apply boundary.
context: The current `atlas-ticket-planning` skill can run diff checks and the documentation linter but states that
  the definitive batch-integrity guard is only reachable through `atlas plan --stubs-only` and `atlas apply`. That
  pushes basic manifest/dependency failures across the operator handoff and causes avoidable repair loops. The validator
  must reuse, not reimplement, the existing guard.
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
source_anchor: docs/runbooks/planning-phases-and-ticket-stubs.md#atlas-owned-integrity-gate
relevant_docs:
- docs/runbooks/planning-phases-and-ticket-stubs.md
- docs/atlas/planning-engine-specification.md
- docs/decisions/0007-generative-planning-with-deterministic-reconciliation.md
depends_on:
- inbox-stub-01-ticket-minting-lifecycle-authority.md
- inbox-stub-02-planning-intake-isolation.md
acceptance_criteria:
- A repository-owned CLI command validates the current committed planning inbox by invoking the same stub/batch
  integrity implementation used by stubs-only planning/apply; the command contains no independent parser or duplicated
  policy.
- The command performs no model call, persists no PlanRun, mutates no Atlas store, writes no `docs/planning/` render,
  retires no stub and performs no Linear/GitHub/network mutation.
- Validation covers the existing exact-path, manifest/base coverage, active-inbox equality, existing/sibling dependency
  identity, backward sibling order and cycle invariants and reports all deterministic violations with named paths/identities.
- The command exposes concise human output plus a machine-readable JSON form containing at least the validated head
  identity, manifest identity when present, stub count and pass/failure details.
- Exit behavior follows the Atlas CLI's existing success/precondition conventions and fails closed on dirty, stale,
  malformed or incomplete planning inputs rather than repairing them.
- Focused parity tests seed representative valid and invalid batches and prove the read-only surface agrees with
  the integrity decisions reached by the existing stubs-only/apply path.
non_goals:
- No proposal generation, reconciliation diff, key minting, apply confirmation, render write, inbox retirement or
  external publication.
- No broad validation framework or second phase-bundle schema.
test_requirements:
- Focused deterministic tests cover valid batches and every shared integrity failure family without network or model
  access.
- CLI tests prove read-only behavior by asserting no PlanRun/store/render mutation on both pass and fail paths.
implementation_notes:
- Prefer a thin command/service wrapper over `atlas.planning.stub_integrity`; preserve one implementation of the
  invariants.
- Choose the final CLI spelling consistently with existing Atlas top-level command conventions and document that
  exact spelling in the planning runbook.
documentation_requirements:
- docs/runbooks/planning-phases-and-ticket-stubs.md
- docs/atlas/planning-engine-specification.md
definition_of_done:
- A planning agent can validate a committed batch locally without crossing into `atlas plan --stubs-only` or `atlas
  apply`.
- Seeded-defect parity tests prove the new command fails where the existing shared guard fails.
---

# Read-only planning inbox integrity command

Governed maintenance input for the `ticket-minting-skills-v1` batch.
