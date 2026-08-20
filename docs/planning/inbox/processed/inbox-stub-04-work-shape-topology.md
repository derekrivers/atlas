---
title: Phase 16 work-shape and execution-topology contracts
objective: Define WorkShapeClassification and ExecutionTopology while preserving the Phase 16 ruling that work shape
  is observable coordination structure, not permission to spawn extra production roles.
context: The programme needs to distinguish independent leaves, pipelines, shared specifications, shared interfaces,
  co-delivery groups and unknown work. Phase 16 records those shapes but production remains BASELINE_SINGLE_ROLE.
  This ticket defines the two pure contracts only and must not select, schedule or activate a topology.
ticket_type: feature
epic_ref: ATLAS-E10
risk_level: medium
component: execution-topology-contract
tags:
- phase-16
- track-a
- topology
- contract
relevant_docs:
- docs/atlas/agentic-engineering-programme-design.md
- docs/atlas/phase-16-agent-runtime-and-integration-safety.md
depends_on: []
acceptance_criteria:
- '`WorkShapeClassification` carries the exact v1 class vocabulary, evidence/interface/dependency/lane references,
  unknown reasons, classifier identity and deterministic fingerprint required by section 11.'
- '`ExecutionTopology` represents one immutable measured attempt with work-shape identity, execution mode, roles/handoffs/shared
  artifacts/interfaces, parallel-role bound, review/validation requirements and policy fingerprint.'
- The Phase 16 production baseline `BASELINE_SINGLE_ROLE` with one `implementation_executor` and `max_parallel_roles=1`
  is representable without making other fixture-only topology classes production defaults.
- Materially incomplete classification facts can be represented as `UNKNOWN`; no model-supplied advisory value can
  masquerade as a protected deterministic classification field.
- Construction and fingerprinting perform no admission, worker scheduling, model selection, Linear mutation or Symphony
  action.
non_goals:
- No package-level re-export, generated schema/export registration, database migration/repository, API/UI, shared
  registry, WORKFLOW.md/Symphony edit, external mutation, production activation or live milestone proof.
- No import from another new Phase 16 Track-A module; if implementation requires a sibling Track-A contract, stop
  and return the planning/dependency issue to the operator rather than coupling the ramp workloads.
test_requirements:
- Focused deterministic tests in `tests/test_execution_topology.py` cover valid construction, every closed enum/value
  boundary, malformed/contradictory inputs, canonical serialization/fingerprint stability and order-independence
  where collections are semantically sets.
- An architecture/scope assertion proves the production change stays within the named contract module and imports
  only stable existing lower-layer/Pydantic/shared primitives; tests exercise zero filesystem, database, Git/GitHub,
  Linear, Symphony or network mutation.
implementation_notes:
- Planned Track-A changed-path envelope is exactly `atlas/core/models/execution_topology.py` plus `tests/test_execution_topology.py`.
  Do not add convenience exports or generated artifacts; Track-B integration owns those after ATLAS-253.
- Use immutable/frozen Pydantic/domain values and deterministic canonicalization consistent with the dedicated Phase
  16 design. This ticket is intended to remain independently useful even if the concurrency ramp did not exist.
documentation_requirements: []
definition_of_done:
- The named contract family exists only in `atlas/core/models/execution_topology.py`, all focused tests in `tests/test_execution_topology.py`
  pass, and the final diff contains no other production/test path.
- The PR completion report states the exact two-path diff, confirms no sibling Track-A import/dependency/protected-lane
  expansion, and preserves all authority/non-goal boundaries from the dedicated design.
---

# Phase 16 work-shape and execution-topology contracts

Authority-neutral Phase 16 Track-A foundation contract. No production activation is part of this ticket.
