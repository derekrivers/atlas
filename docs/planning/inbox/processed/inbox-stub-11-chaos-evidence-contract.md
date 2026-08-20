---
title: Phase 16 chaos fault and run evidence contracts
objective: Define deterministic ChaosFaultSpec and ChaosRun evidence records so later fault campaigns can prove
  that an injection fired and distinguish safe degradation from false success.
context: AgentChaos motivates crash, omission and value faults, but Phase 16 requires bounded deterministic campaign
  evidence rather than ad hoc failure testing. This ticket defines fault/run identity and outcome facts only; the
  injector and campaigns come later.
ticket_type: feature
epic_ref: ATLAS-E10
risk_level: medium
component: chaos-evidence-contract
tags:
- phase-16
- track-a
- chaos
- contract
relevant_docs:
- docs/atlas/agentic-engineering-programme-design.md
- docs/atlas/phase-16-agent-runtime-and-integration-safety.md
depends_on: []
acceptance_criteria:
- '`ChaosFaultSpec` represents the section-18 crash/omission/value fault families with deterministic seed/target/injection
  identity and bounded parameters.'
- '`ChaosRun` records exact fault spec/seed, trigger proof, retries/wall-time, resulting typed outcome, unintended
  mutation and false-success evidence required by later campaigns.'
- A campaign result cannot claim that the fault was exercised unless trigger evidence is present; contradictory
  fired/not-fired evidence is rejected.
- Canonical fingerprinting is deterministic and secret/provider raw payload material is excluded from the contract
  surface.
- The module performs no fault injection, network/provider call, live mutation or campaign orchestration.
non_goals:
- No package-level re-export, generated schema/export registration, database migration/repository, API/UI, shared
  registry, WORKFLOW.md/Symphony edit, external mutation, production activation or live milestone proof.
- No import from another new Phase 16 Track-A module; if implementation requires a sibling Track-A contract, stop
  and return the planning/dependency issue to the operator rather than coupling the ramp workloads.
test_requirements:
- Focused deterministic tests in `tests/test_chaos_run.py` cover valid construction, every closed enum/value boundary,
  malformed/contradictory inputs, canonical serialization/fingerprint stability and order-independence where collections
  are semantically sets.
- An architecture/scope assertion proves the production change stays within the named contract module and imports
  only stable existing lower-layer/Pydantic/shared primitives; tests exercise zero filesystem, database, Git/GitHub,
  Linear, Symphony or network mutation.
implementation_notes:
- Planned Track-A changed-path envelope is exactly `atlas/core/models/chaos_run.py` plus `tests/test_chaos_run.py`.
  Do not add convenience exports or generated artifacts; Track-B integration owns those after ATLAS-253.
- Use immutable/frozen Pydantic/domain values and deterministic canonicalization consistent with the dedicated Phase
  16 design. This ticket is intended to remain independently useful even if the concurrency ramp did not exist.
documentation_requirements: []
definition_of_done:
- The named contract family exists only in `atlas/core/models/chaos_run.py`, all focused tests in `tests/test_chaos_run.py`
  pass, and the final diff contains no other production/test path.
- The PR completion report states the exact two-path diff, confirms no sibling Track-A import/dependency/protected-lane
  expansion, and preserves all authority/non-goal boundaries from the dedicated design.
---

# Phase 16 chaos fault and run evidence contracts

Authority-neutral Phase 16 Track-A foundation contract. No production activation is part of this ticket.
