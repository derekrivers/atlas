---
title: Phase 16 stale-safe steering identity and receipt contracts
objective: Define the exact runtime-attempt/thread/expected-turn identity for native Codex steering plus bounded
  accepted/stale/indeterminate receipt semantics, without exposing a production steering command.
context: Experiment E proved `turn/steer` and `expectedTurnId` exist in the pinned Codex protocol. The dedicated
  design requires double stale binding through Atlas/Symphony and forbids blind retry after ambiguous transport.
  This Track-A slice defines identity and receipt contracts only.
ticket_type: feature
epic_ref: ATLAS-E10
risk_level: high
component: steering-contract
tags:
- phase-16
- track-a
- steering
- stale-safety
relevant_docs:
- docs/atlas/agentic-engineering-programme-design.md
- docs/atlas/phase-16-agent-runtime-and-integration-safety.md
depends_on: []
acceptance_criteria:
- '`SteeringRequest` requires exact ticket/runtime-attempt/Codex-thread/expected-turn identity plus bounded instruction/reason/policy
  identity as defined in section 17.'
- '`SteeringReceipt` distinguishes applied, rejected-stale and indeterminate outcomes while retaining the exact
  request/attempt/thread/turn identity and deterministic fingerprint.'
- A request cannot omit or wildcard `expected_turn_id`, and invalid/mismatched identity shapes fail validation before
  any adapter behavior could occur.
- Receipt semantics support the design rule that ambiguous transport is `INDETERMINATE` and must not imply a safe
  automatic resend.
- The module adds no Symphony adapter, JSON-RPC request, public CLI/API, steering activation or live test.
non_goals:
- No package-level re-export, generated schema/export registration, database migration/repository, API/UI, shared
  registry, WORKFLOW.md/Symphony edit, external mutation, production activation or live milestone proof.
- No import from another new Phase 16 Track-A module; if implementation requires a sibling Track-A contract, stop
  and return the planning/dependency issue to the operator rather than coupling the ramp workloads.
test_requirements:
- Focused deterministic tests in `tests/test_steering_request.py` cover valid construction, every closed enum/value
  boundary, malformed/contradictory inputs, canonical serialization/fingerprint stability and order-independence
  where collections are semantically sets.
- An architecture/scope assertion proves the production change stays within the named contract module and imports
  only stable existing lower-layer/Pydantic/shared primitives; tests exercise zero filesystem, database, Git/GitHub,
  Linear, Symphony or network mutation.
implementation_notes:
- Planned Track-A changed-path envelope is exactly `atlas/core/models/steering_request.py` plus `tests/test_steering_request.py`.
  Do not add convenience exports or generated artifacts; Track-B integration owns those after ATLAS-253.
- Use immutable/frozen Pydantic/domain values and deterministic canonicalization consistent with the dedicated Phase
  16 design. This ticket is intended to remain independently useful even if the concurrency ramp did not exist.
documentation_requirements: []
definition_of_done:
- The named contract family exists only in `atlas/core/models/steering_request.py`, all focused tests in `tests/test_steering_request.py`
  pass, and the final diff contains no other production/test path.
- The PR completion report states the exact two-path diff, confirms no sibling Track-A import/dependency/protected-lane
  expansion, and preserves all authority/non-goal boundaries from the dedicated design.
---

# Phase 16 stale-safe steering identity and receipt contracts

Authority-neutral Phase 16 Track-A foundation contract. No production activation is part of this ticket.
