---
title: Persist planner calls and correlate parse, schema and gate outcomes
objective: Wire structured planner/provider observations into the telemetry repository so every attempted physical request is retained against its durable PlanningExecution and, when legally produced, correlated to the exact PlanRun and downstream outcome.
context: The contract, orchestration, provider projection and store are independently testable foundations. This ticket composes them at the planning pipeline boundary, establishing PlanningExecution only after deterministic preflight freezes exact inputs and planner identity, then correlating failures before raw output and outcomes discovered after stage parsing or whole-proposal gates without changing PlanRun rules.
ticket_type: feature
epic_ref: ATLAS-E3
risk_level: high
component: planning-telemetry-pipeline
tags:
  - knowledge-context-wave-1
  - planning
  - telemetry
  - persistence
source_anchor: docs/atlas/knowledge-context-consolidation.md#5-planner-call-telemetry
relevant_docs:
  - docs/atlas/knowledge-context-consolidation.md
  - docs/atlas/planning-engine-specification.md
  - docs/runbooks/running-atlas-plan.md
depends_on:
  - inbox-stub-02-structured-planner-call-orchestration.md
  - inbox-stub-03-anthropic-physical-request-capture.md
  - inbox-stub-04-planner-call-telemetry-storage.md
acceptance_criteria:
  - One durable PlanningExecution is established after deterministic preflight succeeds and before the first physical request, and every observable completed or failed physical attempt is persisted with exact execution/stage/logical/physical identity.
  - Exhausted transport failures after execution begins terminally record the failed PlanningExecution and its bounded telemetry without creating a PlanRun; dirty input, missing product and other pre-execution failures create neither execution nor call rows.
  - When existing parsing, gates and reconciliation legally produce a PlanRun, the PlanningExecution terminal result links its exact PlanRun ID; proposed/applied/rejected/failed semantics and atlas apply selection remain unchanged.
  - Stage parse/schema outcomes finalize only the calls that produced that stage output; whole-proposal gate outcomes are correlated without pretending a gate failure was caused by one physical attempt.
  - Successful single-call, staged and directed-logical-retry plans retain all physical calls, preserve current proposal/diff behavior and leave stubs-only runs with zero model-call and physical-attempt rows.
  - Process interruption may leave an honestly non-terminal execution or attempt; repository failures cannot silently drop observed attempts or fabricate a proposed PlanRun, and no automatic abandonment/recovery policy is introduced.
  - Persisted records exclude raw prompts, raw SDK payloads, response bodies, credentials and pricing.
non_goals:
  - No read/report CLI, cost conversion, provider cache optimisation, context narrowing, Document Role Registry, apply, Linear or runtime mutation.
test_requirements:
  - Pipeline integration tests cover single-call success, staged success, logical retry, physical retry, truncation, parse failure, gate failure, exhausted transport failure and stubs-only zero-call behavior.
  - Transaction/fault tests prove no false proposed state or silent call loss across repository failures and prove secret/raw-content exclusion.
implementation_notes:
  - Expected paths are atlas/planning/pipeline.py, atlas/planning/staged.py, the telemetry repository integration seam and focused pipeline/staged tests.
  - "One primary integration seam: planner execution to durable per-call evidence. Do not add presentation/report code."
documentation_requirements:
  - docs/atlas/planning-engine-specification.md
  - docs/runbooks/running-atlas-plan.md
definition_of_done:
  - All model-backed planner paths retain exact PlanningExecution-owned physical-call evidence and honest downstream/optional PlanRun outcomes, failure semantics are documented and tested, stubs-only remains zero-call, and no reporting or optimisation is implemented.
---

# Persist planner calls and correlate parse, schema and gate outcomes

This ticket composes collection and storage while preserving planning/apply authority.
