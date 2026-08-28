---
title: Anthropic usage, latency, stop and physical-retry capture
objective: Project bounded evidence from each physical Anthropic request into the provider-neutral telemetry contract, including transport failures, without retaining raw provider payloads.
context: AnthropicPlannerClient currently returns assembled text and retries transport errors internally up to three times. The final message already exposes stop_reason and provider usage, but those facts and failed physical attempts are discarded. This ticket owns only the SDK boundary and its deterministic projection.
ticket_type: feature
epic_ref: ATLAS-E3
risk_level: high
component: anthropic-planner-adapter
tags:
  - knowledge-context-wave-1
  - planning
  - anthropic
  - telemetry
source_anchor: docs/atlas/knowledge-context-consolidation.md#5-planner-call-telemetry
relevant_docs:
  - docs/atlas/knowledge-context-consolidation.md
  - docs/atlas/planning-engine-specification.md
depends_on:
  - inbox-stub-02-structured-planner-call-orchestration.md
acceptance_criteria:
  - Each actual Anthropic stream attempt emits exactly one physical-attempt observation under the exact PlanningExecution and logical-call identities, with provider/model, physical attempt number, wall latency, output size, stop reason or bounded transport-failure category.
  - Provider-reported input/output, cache-creation/cache-read and reasoning/thinking token fields are retained when the installed SDK exposes them and remain explicitly unavailable when absent.
  - Time to first token is measured only when the SDK stream provides a reliable event boundary; otherwise the field remains unsupported rather than approximated from total latency.
  - A transient transport retry produces separate failed and succeeding physical-attempt observations under one logical attempt; truncation remains non-retryable and retains its stop reason.
  - Raw provider responses, content blocks, prompts, exception bodies that may contain secrets and credentials never enter the telemetry record or its fingerprint.
  - Existing output-text assembly, retry count/backoff and ModelCallError/TruncatedOutputError behavior remain compatible for callers.
non_goals:
  - No pipeline logical-retry wiring, database schema/repository, parse or gate classification, reporting CLI, cost calculation, pricing table, caching change or provider beyond Anthropic.
test_requirements:
  - Extend tests/test_planner_client.py with SDK fakes for complete usage, sparse usage, cache/reasoning fields, transport failure then success, exhausted retries, truncation and optional TTFT support.
  - Tests assert exact attempt counts and prove raw prompt/provider payload/secret values are absent from emitted telemetry and exception-safe representations.
implementation_notes:
  - Expected production surface is atlas/planning/client.py plus tests/test_planner_client.py; a small provider projection helper is permitted if it keeps SDK-specific interpretation isolated.
  - "One architectural seam: Anthropic SDK response/retry projection."
documentation_requirements:
  - docs/atlas/planning-engine-specification.md
definition_of_done:
  - Focused provider tests pass for successful, sparse, retrying and truncated streams; physical retries are visible and distinct from logical retries; no store or report integration is included.
---

# Anthropic usage, latency, stop and physical-retry capture

Provider evidence is projected into bounded contracts, never stored as raw SDK payloads.
