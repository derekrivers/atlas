---
title: Phase 16 trajectory alert contract
objective: Define replayable deterministic TrajectoryAlert output for SHADOW monitoring without enabling production
  steering.
context: Phase 16 adopts LivePlan-style deterministic monitoring but closes in SHADOW mode. The alert contract must
  identify the exact attempt/rule/evidence and remain reproducible without model chain-of-thought. Rule implementations
  and steering are later slices.
ticket_type: feature
epic_ref: ATLAS-E10
risk_level: medium
component: trajectory-alert-contract
tags:
- phase-16
- track-a
- trajectory
- shadow
relevant_docs:
- docs/atlas/agentic-engineering-programme-design.md
- docs/atlas/phase-16-agent-runtime-and-integration-safety.md
depends_on: []
acceptance_criteria:
- '`TrajectoryAlert` carries the section-16 identity, rule/version, runtime attempt/turn scope, severity/materiality,
  bounded evidence references, observation window and deterministic fingerprint needed for replay.'
- Alert values cannot embed raw transcript/chain-of-thought or unbounded command output; evidence references remain
  bounded runtime identities/digests.
- The contract can represent all initial deterministic rule families without itself implementing thresholds or rule
  execution.
- Canonical bytes/fingerprint are deterministic and invalid/stale identity combinations fail validation.
- No steering request, Symphony command, runtime mutation, production mode switch or external effect is introduced.
non_goals:
- No package-level re-export, generated schema/export registration, database migration/repository, API/UI, shared
  registry, WORKFLOW.md/Symphony edit, external mutation, production activation or live milestone proof.
- No import from another new Phase 16 Track-A module; if implementation requires a sibling Track-A contract, stop
  and return the planning/dependency issue to the operator rather than coupling the ramp workloads.
test_requirements:
- Focused deterministic tests in `tests/test_trajectory_alert.py` cover valid construction, every closed enum/value
  boundary, malformed/contradictory inputs, canonical serialization/fingerprint stability and order-independence
  where collections are semantically sets.
- An architecture/scope assertion proves the production change stays within the named contract module and imports
  only stable existing lower-layer/Pydantic/shared primitives; tests exercise zero filesystem, database, Git/GitHub,
  Linear, Symphony or network mutation.
implementation_notes:
- Planned Track-A changed-path envelope is exactly `atlas/core/models/trajectory_alert.py` plus `tests/test_trajectory_alert.py`.
  Do not add convenience exports or generated artifacts; Track-B integration owns those after ATLAS-253.
- Use immutable/frozen Pydantic/domain values and deterministic canonicalization consistent with the dedicated Phase
  16 design. This ticket is intended to remain independently useful even if the concurrency ramp did not exist.
documentation_requirements: []
definition_of_done:
- The named contract family exists only in `atlas/core/models/trajectory_alert.py`, all focused tests in `tests/test_trajectory_alert.py`
  pass, and the final diff contains no other production/test path.
- The PR completion report states the exact two-path diff, confirms no sibling Track-A import/dependency/protected-lane
  expansion, and preserves all authority/non-goal boundaries from the dedicated design.
---

# Phase 16 trajectory alert contract

Authority-neutral Phase 16 Track-A foundation contract. No production activation is part of this ticket.
