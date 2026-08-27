---
title: Structured planner-call orchestration and logical-attempt identity
objective: Route single-call and staged planning through the telemetry request/result contract so every logical planner call belongs to one durable PlanningExecution and has deterministic stage, template, input and prompt-size identity before it reaches a provider.
context: The current PlannerClient.generate(prompt) seam cannot distinguish execution, stage or logical retry, while staged projection retries are labelled outside the provider adapter. This ticket changes the planner-facing seam and call sites only, preserving generated proposal behavior while propagating the post-preflight PlanningExecution identity and making logical calls and prompt segments explicit.
ticket_type: feature
epic_ref: ATLAS-E3
risk_level: high
component: planner-call-orchestration
tags:
  - knowledge-context-wave-1
  - planning
  - telemetry
  - orchestration
source_anchor: docs/atlas/knowledge-context-consolidation.md#5-planner-call-telemetry
relevant_docs:
  - docs/atlas/knowledge-context-consolidation.md
  - docs/atlas/planning-engine-specification.md
  - docs/atlas/planning-large-corpora.md
depends_on:
  - inbox-stub-01-planner-call-telemetry-contract.md
acceptance_criteria:
  - Every single-call and staged provider invocation receives the durable PlanningExecution identity, exact stage, zero-based logical attempt, prompt/template version and deterministic exact-input fingerprint.
  - Prompt byte/character totals and named document, anchor, backlog, schema and dynamic-stage segment sizes are measured from the actual rendered inputs without retaining segment contents in telemetry.
  - Directed stage retries increment logical-attempt identity while leaving physical-transport attempt numbering to the provider boundary; ordinary stages retain one logical attempt.
  - Existing fake PlannerClient tests prove proposal bytes, stage ordering, retry eligibility, truncation classification and progress output remain behaviorally unchanged apart from the typed seam.
  - Unknown stage/template/input identity fails before a provider call rather than emitting incomplete telemetry.
non_goals:
  - No Anthropic usage extraction, physical transport retry observation, database write, PlanRun repository or lifecycle change, report command, provider pricing or registry work.
test_requirements:
  - Update focused planning client, pipeline, staged-generation and staged-retry tests to assert exact logical-call identities and segment counts for single-call, ordinary staged and directed-retry cases.
  - A fake-provider test proves no prompt or document content is copied into the telemetry request/result record.
implementation_notes:
  - Expected paths include atlas/planning/client.py, atlas/planning/pipeline.py, atlas/planning/staged.py, atlas/planning/renderer.py and their focused tests.
  - "One integration seam: planner orchestration to provider-neutral call contracts. Do not add storage or SDK-specific behavior."
documentation_requirements:
  - docs/atlas/planning-engine-specification.md
  - docs/atlas/planning-large-corpora.md
definition_of_done:
  - All planner call sites use the structured seam, logical retries are independently observable in tests, current proposal/retry behavior is preserved, and no persistence or provider-specific metric claim is made.
---

# Structured planner-call orchestration and logical-attempt identity

This ticket exposes logical call identity without interpreting provider responses.
