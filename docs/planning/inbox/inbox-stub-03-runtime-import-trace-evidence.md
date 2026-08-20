---
title: Phase 16 runtime import and trace evidence contracts
objective: Define the bounded import, trace-completeness and retention evidence contracts used later to make runtime
  gaps, duplicates, contradictions and lifecycle outcomes explicit.
context: Runtime telemetry is useful only if missing or contradictory observations remain visible. The dedicated
  design therefore separates source/canonical events from import/trace evidence and retention disposition. This
  foundation ticket defines those evidence records only; importer behavior, storage and replay are later Track-B
  slices.
ticket_type: feature
epic_ref: ATLAS-E10
risk_level: medium
component: runtime-trace-contract
tags:
- phase-16
- track-a
- runtime-replay
- contract
relevant_docs:
- docs/atlas/agentic-engineering-programme-design.md
- docs/atlas/phase-16-agent-runtime-and-integration-safety.md
depends_on: []
acceptance_criteria:
- '`RuntimeImportReceipt`, `RuntimeTraceAssessment` and `RuntimeRetentionDisposition` provide bounded immutable
  representations for one import attempt, trace completeness/gap/duplicate/contradiction assessment, and retention
  lifecycle decision respectively.'
- The trace assessment can represent healthy, incomplete and contradictory evidence without coercing missing observations
  to zero or success.
- Canonical fingerprints are deterministic and retain the exact source/runtime-attempt/import identities required
  for replay provenance.
- Validation bounds counts/reason identities and rejects internally contradictory states such as a complete trace
  carrying unresolved gaps/contradictions.
- The module imports no sibling Track-A contract and performs no storage, spool, cleanup or replay action.
non_goals:
- No package-level re-export, generated schema/export registration, database migration/repository, API/UI, shared
  registry, WORKFLOW.md/Symphony edit, external mutation, production activation or live milestone proof.
- No import from another new Phase 16 Track-A module; if implementation requires a sibling Track-A contract, stop
  and return the planning/dependency issue to the operator rather than coupling the ramp workloads.
test_requirements:
- Focused deterministic tests in `tests/test_runtime_trace.py` cover valid construction, every closed enum/value
  boundary, malformed/contradictory inputs, canonical serialization/fingerprint stability and order-independence
  where collections are semantically sets.
- An architecture/scope assertion proves the production change stays within the named contract module and imports
  only stable existing lower-layer/Pydantic/shared primitives; tests exercise zero filesystem, database, Git/GitHub,
  Linear, Symphony or network mutation.
implementation_notes:
- Planned Track-A changed-path envelope is exactly `atlas/core/models/runtime_trace.py` plus `tests/test_runtime_trace.py`.
  Do not add convenience exports or generated artifacts; Track-B integration owns those after ATLAS-253.
- Use immutable/frozen Pydantic/domain values and deterministic canonicalization consistent with the dedicated Phase
  16 design. This ticket is intended to remain independently useful even if the concurrency ramp did not exist.
documentation_requirements: []
definition_of_done:
- The named contract family exists only in `atlas/core/models/runtime_trace.py`, all focused tests in `tests/test_runtime_trace.py`
  pass, and the final diff contains no other production/test path.
- The PR completion report states the exact two-path diff, confirms no sibling Track-A import/dependency/protected-lane
  expansion, and preserves all authority/non-goal boundaries from the dedicated design.
---

# Phase 16 runtime import and trace evidence contracts

Authority-neutral Phase 16 Track-A foundation contract. No production activation is part of this ticket.
