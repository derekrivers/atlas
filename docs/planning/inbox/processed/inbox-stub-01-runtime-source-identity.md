---
title: Phase 16 runtime source identity contract
objective: Define the immutable RuntimeSourceDescriptor contract that pins the exact runtime, projector, Codex protocol
  and observable capability identity used by later runtime-event import and reporting.
context: 'Experiment E proved that structured runtime capture is feasible only when evidence stays bound to a precise
  Symphony/Codex configuration. Phase 16 section 8 therefore requires one versioned descriptor per source configuration.
  This is a pure foundation slice for the ATLAS-253 overlap: it creates no persistence, adapter, export or live
  runtime behavior.'
ticket_type: feature
epic_ref: ATLAS-E10
risk_level: medium
component: runtime-source-contract
tags:
- phase-16
- track-a
- runtime-telemetry
- contract
relevant_docs:
- docs/atlas/agentic-engineering-programme-design.md
- docs/atlas/phase-16-agent-runtime-and-integration-safety.md
depends_on: []
acceptance_criteria:
- '`RuntimeSourceDescriptor` represents every field required by dedicated-design section 8, including source/product
  scope, pinned Symphony/Codex/protocol/projector identities, declared sequence semantics, supported event/identity/coordination
  capabilities and the optional tool/channel inventory fingerprints.'
- The contract is immutable for one source configuration and exposes deterministic canonical bytes/fingerprint behavior
  that is independent of construction/input ordering.
- Changing any material runtime configuration identity changes the fingerprint; an unsupported or uninstrumented
  capability is represented explicitly rather than inferred from silence.
- Validation rejects malformed digest/version/bounded-list inputs and duplicate identities with named deterministic
  errors.
- The implementation remains a self-contained pure contract with zero storage, filesystem, network, runtime or external-system
  side effects.
non_goals:
- No package-level re-export, generated schema/export registration, database migration/repository, API/UI, shared
  registry, WORKFLOW.md/Symphony edit, external mutation, production activation or live milestone proof.
- No import from another new Phase 16 Track-A module; if implementation requires a sibling Track-A contract, stop
  and return the planning/dependency issue to the operator rather than coupling the ramp workloads.
test_requirements:
- Focused deterministic tests in `tests/test_runtime_source.py` cover valid construction, every closed enum/value
  boundary, malformed/contradictory inputs, canonical serialization/fingerprint stability and order-independence
  where collections are semantically sets.
- An architecture/scope assertion proves the production change stays within the named contract module and imports
  only stable existing lower-layer/Pydantic/shared primitives; tests exercise zero filesystem, database, Git/GitHub,
  Linear, Symphony or network mutation.
implementation_notes:
- Planned Track-A changed-path envelope is exactly `atlas/core/models/runtime_source.py` plus `tests/test_runtime_source.py`.
  Do not add convenience exports or generated artifacts; Track-B integration owns those after ATLAS-253.
- Use immutable/frozen Pydantic/domain values and deterministic canonicalization consistent with the dedicated Phase
  16 design. This ticket is intended to remain independently useful even if the concurrency ramp did not exist.
documentation_requirements: []
definition_of_done:
- The named contract family exists only in `atlas/core/models/runtime_source.py`, all focused tests in `tests/test_runtime_source.py`
  pass, and the final diff contains no other production/test path.
- The PR completion report states the exact two-path diff, confirms no sibling Track-A import/dependency/protected-lane
  expansion, and preserves all authority/non-goal boundaries from the dedicated design.
---

# Phase 16 runtime source identity contract

Authority-neutral Phase 16 Track-A foundation contract. No production activation is part of this ticket.
