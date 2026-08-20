---
title: Phase 16 PR interaction observation and advisory queue-plan contracts
objective: Define exact-head stale-aware PR relation evidence and a separate advisory QueuePlan artifact without
  creating merge, rebase, rejection or workflow authority.
context: BulkPR-style interaction reasoning is useful only when evidence identity and advisory disposition stay
  separate. Phase 16 supports deterministic relation evidence plus an advisory queue plan, while exact-head acceptance/manual
  merge remain authoritative. This ticket defines those records only.
ticket_type: feature
epic_ref: ATLAS-E10
risk_level: medium
component: pr-interaction-contract
tags:
- phase-16
- track-a
- pr-interaction
- advisory
relevant_docs:
- docs/atlas/agentic-engineering-programme-design.md
- docs/atlas/phase-16-agent-runtime-and-integration-safety.md
depends_on: []
acceptance_criteria:
- '`PRInteractionObservation` represents exact repository/PR/head pairs, the frozen relation vocabulary, evidence
  state, evidence/interface/lane/dependency references, detector identity/time and deterministic fingerprint from
  section 20.'
- '`QueuePlan` represents snapshot/candidate/interaction identities, per-PR advisory disposition, unresolved unknowns,
  planner identity/time and deterministic fingerprint from section 21.'
- Relation/evidence/disposition enums are closed to the dedicated-design vocabularies; stale/unknown/disputed evidence
  remains distinct from corroborated evidence.
- Neither contract contains an execution field or mutation authority for GitHub, Git, Linear, Symphony or Phase-14
  acceptance.
- The module performs no provider read/write, relation detection, composition, queue planning or merge-readiness
  derivation.
non_goals:
- No package-level re-export, generated schema/export registration, database migration/repository, API/UI, shared
  registry, WORKFLOW.md/Symphony edit, external mutation, production activation or live milestone proof.
- No import from another new Phase 16 Track-A module; if implementation requires a sibling Track-A contract, stop
  and return the planning/dependency issue to the operator rather than coupling the ramp workloads.
test_requirements:
- Focused deterministic tests in `tests/test_pr_interaction.py` cover valid construction, every closed enum/value
  boundary, malformed/contradictory inputs, canonical serialization/fingerprint stability and order-independence
  where collections are semantically sets.
- An architecture/scope assertion proves the production change stays within the named contract module and imports
  only stable existing lower-layer/Pydantic/shared primitives; tests exercise zero filesystem, database, Git/GitHub,
  Linear, Symphony or network mutation.
implementation_notes:
- Planned Track-A changed-path envelope is exactly `atlas/core/models/pr_interaction.py` plus `tests/test_pr_interaction.py`.
  Do not add convenience exports or generated artifacts; Track-B integration owns those after ATLAS-253.
- Use immutable/frozen Pydantic/domain values and deterministic canonicalization consistent with the dedicated Phase
  16 design. This ticket is intended to remain independently useful even if the concurrency ramp did not exist.
documentation_requirements: []
definition_of_done:
- The named contract family exists only in `atlas/core/models/pr_interaction.py`, all focused tests in `tests/test_pr_interaction.py`
  pass, and the final diff contains no other production/test path.
- The PR completion report states the exact two-path diff, confirms no sibling Track-A import/dependency/protected-lane
  expansion, and preserves all authority/non-goal boundaries from the dedicated design.
---

# Phase 16 PR interaction observation and advisory queue-plan contracts

Authority-neutral Phase 16 Track-A foundation contract. No production activation is part of this ticket.
