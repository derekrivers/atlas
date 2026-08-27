---
title: Read-only planner request economics report
objective: Expose deterministic bounded summaries of persisted planner-call telemetry by PlanningExecution, optional resulting PlanRun, stage and attempt without embedding provider pricing or revealing prompt contents.
context: Wave 1 becomes useful when the operator can answer how many physical requests ran, which stages consumed reported tokens, whether provider cache usage occurred, where retries or latency accumulated and whether an execution produced a PlanRun. The report consumes only stored observations and must not infer unavailable provider values or treat absence of a PlanRun as missing telemetry.
ticket_type: feature
epic_ref: ATLAS-E3
risk_level: medium
component: planning-telemetry-reporting
tags:
  - knowledge-context-wave-1
  - planning
  - telemetry
  - reporting
source_anchor: docs/atlas/knowledge-context-consolidation.md#5-planner-call-telemetry
relevant_docs:
  - docs/atlas/knowledge-context-consolidation.md
  - docs/runbooks/running-atlas-plan.md
depends_on:
  - inbox-stub-05-persist-planner-call-outcomes.md
acceptance_criteria:
  - A read-only CLI/report surface selects an exact PlanningExecution, an exact resulting PlanRun, or a bounded recent execution set and reports physical-call counts grouped by stage, logical attempt and transport attempt.
  - Aggregates retain provider-reported input/output/cache/reasoning token totals, wall latency, optional TTFT, retries, stop reasons, output sizes and parse/schema/gate dispositions without treating unavailable values as zero.
  - Human-readable and canonical JSON output are deterministic for the same stored rows, with stable ordering and explicit schema/version identity.
  - The report distinguishes logical retries from physical transport retries and shows non-terminal or failed executions with no PlanRun as honest first-class outcomes, alongside executions linked to proposed, failed, rejected or applied PlanRuns.
  - No prompt text, provider payload, secret, raw exception body or unbounded output is displayed.
  - Monetary cost is omitted unless an operator supplies a separately versioned price input in a future ticket; current provider prices are not embedded as timeless authority.
non_goals:
  - No mutation, pricing registry, billing claim, cache optimisation, planner selection change, Document Role Registry, API/UI, Linear, Symphony or apply behavior.
test_requirements:
  - Focused CLI/report tests cover complete and sparse telemetry, logical versus physical retries, non-terminal and failed execution without PlanRun, exact resulting-PlanRun lookup, deterministic JSON ordering, bounded selection and secret/raw-content exclusion.
  - A mutation guard proves the command performs no database, repository, Git, network or external-system write.
implementation_notes:
  - Expected paths include a small planning report/query module, atlas/cli.py or a focused CLI adapter, tests/test_planner_telemetry_report.py and docs/runbooks/running-atlas-plan.md.
  - One presentation seam over already-persisted records.
documentation_requirements:
  - docs/runbooks/running-atlas-plan.md
definition_of_done:
  - The operator can reconstruct physical-call economics for an exact PlanningExecution and any resulting PlanRun from deterministic bounded output, optional fields and missing PlanRun outcomes remain honest, all focused tests pass, and no pricing or mutation authority is introduced.
---

# Read-only planner request economics report

The report measures provider evidence; it does not calculate monetary cost.
