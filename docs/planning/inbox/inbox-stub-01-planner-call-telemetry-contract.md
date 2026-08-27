---
title: Planner call telemetry and PlanningExecution identity contracts
objective: Define the bounded provider-neutral execution, logical-call and physical-attempt contracts that let Atlas describe every planner provider request without retaining prompt contents, fabricating a PlanRun or inventing unavailable provider data.
context: Wave 1 begins with measurement. Today PlannerClient returns only text, transport retries are hidden inside the Anthropic adapter, and PlanRun is legally created only after provider output exists. A request that exhausts transport retries therefore has no durable evidence owner. This ticket defines a separate PlanningExecution identity after deterministic preflight freezes the exact inputs and planner identity, while preserving the existing PlanRun lifecycle and apply semantics.
ticket_type: feature
epic_ref: ATLAS-E3
risk_level: high
component: planning-telemetry-contract
tags:
  - knowledge-context-wave-1
  - planning
  - telemetry
  - contract
source_anchor: docs/atlas/knowledge-context-consolidation.md#5-planner-call-telemetry
relevant_docs:
  - docs/atlas/knowledge-context-consolidation.md
  - docs/atlas/planning-engine-specification.md
  - docs/architecture/data-model-and-schemas.md
depends_on: []
acceptance_criteria:
  - Immutable, versioned execution/logical-call/physical-attempt/outcome contracts represent PlanningExecution identity, stage, logical attempt, physical transport attempt, provider/model, prompt/template version, exact input identity, prompt byte/character count, named segment sizes, output size, provider usage families, stop reason, wall latency, optional time-to-first-token, retry category and parse/schema/gate disposition.
  - Provider-specific usage fields are optional with an explicit unsupported or unavailable representation; missing cache, reasoning, timing or token values are never converted to zero or estimated.
  - After deterministic planning preflight succeeds and exact input hashes, planner identity, prompt/template identity and execution parameters are frozen, one durable PlanningExecution identity exists before the first physical provider request; failures before that boundary create neither an execution nor physical-attempt evidence.
  - PlanningExecution is not applyable, may remain visibly non-terminal after interruption, records a terminal failed outcome when execution ends before a PlanRun may legally exist, and records the exact optional resulting PlanRun identity when one is produced.
  - The existing PlanRun proposed/applied/rejected/failed vocabulary, post-output insertion contract, immutable provenance fields and atlas apply eligibility are unchanged; provider failure before raw output continues to create no PlanRun.
  - Contract serialization and fingerprints are deterministic and exclude raw prompts, raw provider payloads, credentials, secret-derived hashes and mutable pricing data.
  - PlanningExecution, logical-call and physical-transport-attempt identities are structurally distinct, reject invalid or contradictory hierarchy/numbering, and allow every physical request to link to a PlanRun only when the execution produced that exact PlanRun.
non_goals:
  - No Anthropic SDK extraction, retry-loop change, database migration, persistence wiring, CLI/reporting surface, provider pricing, caching optimisation, planner-context reduction or automatic interruption recovery/abandonment policy.
  - No pre-call PlanRun, new PlanRun running state, nullable PlanRun provenance, or change to atlas apply eligibility.
  - No raw prompt body, raw response envelope, credential, cookie, environment dump or process command line in any durable contract.
test_requirements:
  - Focused tests in tests/test_planner_call_telemetry.py cover execution/logical/physical hierarchy, complete and sparse provider evidence, deterministic fingerprints, optional resulting PlanRun identity, invalid attempt identities and secret/raw-content exclusion.
  - Focused model tests cover the post-preflight execution boundary, terminal failure without PlanRun, terminal linkage to one exact PlanRun and honest non-terminal interruption; PlanRun regression tests prove its enum, required fields and apply/reject/fail finalisation rules are unchanged.
implementation_notes:
  - Expected production envelope is a provider-neutral core PlanningExecution/planner-call telemetry model family, with focused tests and the owning planning/data-model documentation; exact class/table names follow established Atlas conventions.
  - "One primary concept: the execution/evidence identity hierarchy. Persistence and provider interpretation remain separate tickets, and PlanRun stays unchanged."
documentation_requirements:
  - docs/atlas/planning-engine-specification.md
  - docs/architecture/data-model-and-schemas.md
definition_of_done:
  - The contract is exported only through established core-model conventions, focused tests pass, canonical docs distinguish durable pre-call PlanningExecution from optional post-output PlanRun linkage, and no provider/storage/report integration or PlanRun semantic change is present.
---

# Planner call telemetry and PlanningExecution identity contracts

Wave-1 measurement contract only; no provider call or store mutation is introduced here.
