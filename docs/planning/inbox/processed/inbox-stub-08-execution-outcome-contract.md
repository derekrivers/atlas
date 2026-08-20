---
title: Phase 16 execution outcome taxonomy and fact contract
objective: Freeze the deterministic run-level execution outcome taxonomy and the bounded ExecutionOutcomeFacts input
  contract separately from AgentRun and Linear ticket workflow state.
context: A runtime attempt can succeed, be flawed, block on environment/authority/dependency/interface/infrastructure,
  hit a manual boundary, require intervention, abort safely or remain indeterminate. Those are execution facts,
  not Linear status. This ticket defines the taxonomy and classifier-input/output records only; classification logic
  and persistence are later slices.
ticket_type: feature
epic_ref: ATLAS-E10
risk_level: high
component: execution-outcome-contract
tags:
- phase-16
- track-a
- outcomes
- contract
relevant_docs:
- docs/atlas/agentic-engineering-programme-design.md
- docs/atlas/phase-16-agent-runtime-and-integration-safety.md
depends_on: []
acceptance_criteria:
- '`ExecutionOutcome` contains exactly the frozen v1 values `SUCCEEDED`, `FLAWED`, `BLOCKED_ENVIRONMENT`, `BLOCKED_AUTHORITY`,
  `BLOCKED_DEPENDENCY`, `BLOCKED_INTERFACE`, `BLOCKED_INFRASTRUCTURE`, `MANUAL_BOUNDARY`, `INTERVENTION_REQUIRED`,
  `ABORTED_SAFE`, and `INDETERMINATE`.'
- '`ExecutionOutcomeFacts` represents the bounded deterministic facts needed by section 15 precedence, including
  material-evidence completeness and the named block/failure/manual/intervention/safe-abort signals without model-selected
  terminal authority.'
- The contract can represent insufficient evidence explicitly and never aliases `SUCCEEDED` to CI pass, review acceptance,
  merge or Ticket `Done`.
- Canonical serialization/fingerprinting is deterministic and rejects contradictory mutually-exclusive facts where
  the design requires exclusivity.
- The module performs no classifier, TicketStatus write, AgentRun rewrite, storage or completion action.
non_goals:
- No package-level re-export, generated schema/export registration, database migration/repository, API/UI, shared
  registry, WORKFLOW.md/Symphony edit, external mutation, production activation or live milestone proof.
- No import from another new Phase 16 Track-A module; if implementation requires a sibling Track-A contract, stop
  and return the planning/dependency issue to the operator rather than coupling the ramp workloads.
test_requirements:
- Focused deterministic tests in `tests/test_execution_outcome.py` cover valid construction, every closed enum/value
  boundary, malformed/contradictory inputs, canonical serialization/fingerprint stability and order-independence
  where collections are semantically sets.
- An architecture/scope assertion proves the production change stays within the named contract module and imports
  only stable existing lower-layer/Pydantic/shared primitives; tests exercise zero filesystem, database, Git/GitHub,
  Linear, Symphony or network mutation.
implementation_notes:
- Planned Track-A changed-path envelope is exactly `atlas/core/models/execution_outcome.py` plus `tests/test_execution_outcome.py`.
  Do not add convenience exports or generated artifacts; Track-B integration owns those after ATLAS-253.
- Use immutable/frozen Pydantic/domain values and deterministic canonicalization consistent with the dedicated Phase
  16 design. This ticket is intended to remain independently useful even if the concurrency ramp did not exist.
documentation_requirements: []
definition_of_done:
- The named contract family exists only in `atlas/core/models/execution_outcome.py`, all focused tests in `tests/test_execution_outcome.py`
  pass, and the final diff contains no other production/test path.
- The PR completion report states the exact two-path diff, confirms no sibling Track-A import/dependency/protected-lane
  expansion, and preserves all authority/non-goal boundaries from the dedicated design.
---

# Phase 16 execution outcome taxonomy and fact contract

Authority-neutral Phase 16 Track-A foundation contract. No production activation is part of this ticket.
