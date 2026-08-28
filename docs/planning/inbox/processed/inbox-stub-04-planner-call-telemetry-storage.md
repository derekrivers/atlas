---
title: Planner-call telemetry storage and repository lifecycle
objective: Add the database schema and repository contracts that retain one durable PlanningExecution and one bounded record per physical planner request, with optional linkage to the exact resulting PlanRun.
context: Wave-1 provider evidence needs an honest pre-provider owner separate from PlanRun and from generation_stages, whose current meaning is post-output logical model-call provenance. Dedicated execution and physical-attempt storage preserves incomplete, failed and successful evidence without overloading PlanRun JSON, changing apply eligibility or fabricating missing values.
ticket_type: feature
epic_ref: ATLAS-E3
risk_level: high
component: planning-telemetry-storage
tags:
  - knowledge-context-wave-1
  - planning
  - telemetry
  - database
source_anchor: docs/atlas/knowledge-context-consolidation.md#5-planner-call-telemetry
relevant_docs:
  - docs/atlas/knowledge-context-consolidation.md
  - docs/atlas/planning-engine-specification.md
  - docs/architecture/data-model-and-schemas.md
depends_on:
  - inbox-stub-01-planner-call-telemetry-contract.md
acceptance_criteria:
  - An Alembic migration, ORM rows and repositories persist one uniquely identified PlanningExecution plus physical attempts linked through exact execution, stage, logical-call and physical-attempt identities without changing generation_stages semantics.
  - PlanningExecution stores the frozen planning-input hashes and planner/prompt execution identity needed at the post-preflight boundary, supports exactly one terminal result, and may honestly remain non-terminal after process interruption.
  - The terminal execution result carries a nullable foreign key to exactly one resulting PlanRun only when that PlanRun legally exists; a provider failure before raw output remains a failed execution with no fabricated PlanRun.
  - Nullable provider usage, cache, reasoning, stop, TTFT and retry fields round-trip unchanged; unavailable values remain null or an explicit bounded unsupported status rather than zero.
  - Repository writes are idempotent for the same execution/physical-attempt identity, reject conflicting replays, and permit one monotonic outcome finalisation for downstream parse/schema/gate disposition.
  - Database constraints reject duplicate attempt identities, invalid hierarchy/attempt numbers, negative sizes/timings/tokens and orphan optional PlanRun references; existing PlanRun schema, transitions and apply selection are unchanged.
  - Storage schema/export pins and canonical data-model documentation are updated consistently for SQLite and PostgreSQL contracts.
non_goals:
  - No provider SDK call, planner pipeline wiring, report CLI, pricing/cost table, raw prompt/provider payload persistence, registry provenance column, pre-call PlanRun, new PlanRun state, Atlas apply behavior change or automatic abandonment/recovery policy.
test_requirements:
  - Focused repository and storage-schema tests cover execution/attempt complete and sparse round trips, terminal success/failure, optional exact PlanRun linkage, honest non-terminal interruption, duplicate/conflicting replays, invalid numeric bounds, FK behavior and migration upgrade.
  - Schema drift/export tests prove all governed model/table surfaces remain synchronized.
implementation_notes:
  - Expected paths include a new core model, atlas/storage/tables.py, atlas/storage/repositories.py, one linear Alembic revision after current head, storage exports, focused tests and docs/architecture/data-model-and-schemas.md.
  - This ticket owns the database-migrations protected lane; exact class/table/status naming follows existing Atlas conventions without altering PlanRun.
documentation_requirements:
  - docs/atlas/planning-engine-specification.md
  - docs/architecture/data-model-and-schemas.md
definition_of_done:
  - The migration reaches one new head, schema/repository tests pass, physical attempts have a bounded PlanningExecution-owned home with optional exact PlanRun linkage, generation_stages remains a logical-call record, and no planner/provider/report integration or PlanRun semantic change is claimed.
---

# Planner-call telemetry storage and repository lifecycle

This storage slice owns physical-call records and no collection or reporting behavior.
