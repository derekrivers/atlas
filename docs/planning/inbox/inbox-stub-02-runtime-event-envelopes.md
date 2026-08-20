---
title: Phase 16 runtime transport and canonical event envelope contracts
objective: Define the bounded RuntimeTransportEvent and RuntimeEvent contracts that separate Symphony-owned source
  identity from Atlas-owned canonical ticket/product identity.
context: Experiment E and the dedicated design establish that Symphony knows runtime/issue identity but must not
  open Atlas storage to invent product/ticket UUIDs. Section 9 therefore separates the source transport envelope
  from the canonical Atlas event. This ticket defines only those envelopes and canonical fingerprint primitives;
  import, duplicate reconciliation and persistence are later composition work.
ticket_type: feature
epic_ref: ATLAS-E10
risk_level: high
component: runtime-event-contract
tags:
- phase-16
- track-a
- runtime-event
- contract
relevant_docs:
- docs/atlas/agentic-engineering-programme-design.md
- docs/atlas/phase-16-agent-runtime-and-integration-safety.md
depends_on: []
acceptance_criteria:
- '`RuntimeTransportEvent` and `RuntimeEvent` carry the exact bounded identity, timing, event-family, operation,
  path/head/base, coordination and metadata fields required by dedicated-design section 9, with their distinct source-side
  versus Atlas-side schemas preserved.'
- The source event key is structurally representable as `(runtime_attempt_id, source_sequence_no)` and both envelopes
  expose deterministic fingerprints over canonical bounded data.
- Event-family and phase-classification values are restricted to the dedicated-design v1 vocabularies, including
  `UNKNOWN` where specified; arbitrary raw Codex methods do not become unbounded enum values.
- Forbidden transcript/secret/raw-payload fields are absent from the model surface, while bounded metadata and payload
  digests remain available for correlation.
- The module performs no import joining, persistence, filesystem spool access or runtime observation.
non_goals:
- No package-level re-export, generated schema/export registration, database migration/repository, API/UI, shared
  registry, WORKFLOW.md/Symphony edit, external mutation, production activation or live milestone proof.
- No import from another new Phase 16 Track-A module; if implementation requires a sibling Track-A contract, stop
  and return the planning/dependency issue to the operator rather than coupling the ramp workloads.
test_requirements:
- Focused deterministic tests in `tests/test_runtime_event.py` cover valid construction, every closed enum/value
  boundary, malformed/contradictory inputs, canonical serialization/fingerprint stability and order-independence
  where collections are semantically sets.
- An architecture/scope assertion proves the production change stays within the named contract module and imports
  only stable existing lower-layer/Pydantic/shared primitives; tests exercise zero filesystem, database, Git/GitHub,
  Linear, Symphony or network mutation.
implementation_notes:
- Planned Track-A changed-path envelope is exactly `atlas/core/models/runtime_event.py` plus `tests/test_runtime_event.py`.
  Do not add convenience exports or generated artifacts; Track-B integration owns those after ATLAS-253.
- Use immutable/frozen Pydantic/domain values and deterministic canonicalization consistent with the dedicated Phase
  16 design. This ticket is intended to remain independently useful even if the concurrency ramp did not exist.
documentation_requirements: []
definition_of_done:
- The named contract family exists only in `atlas/core/models/runtime_event.py`, all focused tests in `tests/test_runtime_event.py`
  pass, and the final diff contains no other production/test path.
- The PR completion report states the exact two-path diff, confirms no sibling Track-A import/dependency/protected-lane
  expansion, and preserves all authority/non-goal boundaries from the dedicated design.
---

# Phase 16 runtime transport and canonical event envelope contracts

Authority-neutral Phase 16 Track-A foundation contract. No production activation is part of this ticket.
