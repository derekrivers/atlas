---
title: Phase 16 semantic interface contract family
objective: Define the narrow InterfaceContract family used to represent named compatibility or authority invariants
  between independently editable surfaces without duplicating dependencies or protected lanes.
context: 'Experiment F proved real file-disjoint semantic coupling in Atlas while also showing that migrations and
  other conflict-prone surfaces already have stronger controls. The dedicated design therefore makes InterfaceContract
  intentionally rare: it represents what independently editable surfaces must agree on, not generic conceptual relatedness.
  This ticket defines the complete domain family only.'
ticket_type: feature
epic_ref: ATLAS-E10
risk_level: high
component: interface-contract
tags:
- phase-16
- track-a
- interface-ownership
- contract
relevant_docs:
- docs/atlas/agentic-engineering-programme-design.md
- docs/atlas/phase-16-agent-runtime-and-integration-safety.md
depends_on: []
acceptance_criteria:
- '`InterfaceSurface`, `InterfaceContract`, `InterfaceUsage` and `InterfaceValidationResult` implement the v1 interface
  kinds, typed surface kinds, revision/owner/invariant/producers/consumers/validation evidence and change/consume/own
  usage semantics from section 13.'
- Surface identities are bounded canonical data; free-form prose cannot become a protected join key, and duplicate/ambiguous
  surface identities are rejected deterministically.
- The contract can represent ownerless/stale/compatible/incompatible validation outcomes without treating a model
  opinion as deterministic authority.
- The domain distinguishes interface compatibility from dependency ordering and protected-lane exclusivity; it contains
  no automatic admission/serialization side effect.
- The implementation is self-contained and does not create the repository interface registry, Ticket fields, persistence,
  Context Pack integration or certification command.
non_goals:
- No package-level re-export, generated schema/export registration, database migration/repository, API/UI, shared
  registry, WORKFLOW.md/Symphony edit, external mutation, production activation or live milestone proof.
- No import from another new Phase 16 Track-A module; if implementation requires a sibling Track-A contract, stop
  and return the planning/dependency issue to the operator rather than coupling the ramp workloads.
test_requirements:
- Focused deterministic tests in `tests/test_interface_contract.py` cover valid construction, every closed enum/value
  boundary, malformed/contradictory inputs, canonical serialization/fingerprint stability and order-independence
  where collections are semantically sets.
- An architecture/scope assertion proves the production change stays within the named contract module and imports
  only stable existing lower-layer/Pydantic/shared primitives; tests exercise zero filesystem, database, Git/GitHub,
  Linear, Symphony or network mutation.
implementation_notes:
- Planned Track-A changed-path envelope is exactly `atlas/core/models/interface_contract.py` plus `tests/test_interface_contract.py`.
  Do not add convenience exports or generated artifacts; Track-B integration owns those after ATLAS-253.
- Use immutable/frozen Pydantic/domain values and deterministic canonicalization consistent with the dedicated Phase
  16 design. This ticket is intended to remain independently useful even if the concurrency ramp did not exist.
documentation_requirements: []
definition_of_done:
- The named contract family exists only in `atlas/core/models/interface_contract.py`, all focused tests in `tests/test_interface_contract.py`
  pass, and the final diff contains no other production/test path.
- The PR completion report states the exact two-path diff, confirms no sibling Track-A import/dependency/protected-lane
  expansion, and preserves all authority/non-goal boundaries from the dedicated design.
---

# Phase 16 semantic interface contract family

Authority-neutral Phase 16 Track-A foundation contract. No production activation is part of this ticket.
