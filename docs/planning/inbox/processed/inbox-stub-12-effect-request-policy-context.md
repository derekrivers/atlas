---
title: Phase 16 governed effect request and runtime policy context contracts
objective: Define the host-bound typed EffectRequest and RuntimePolicyContext that let an immutable host evaluator
  decide a bounded external effect without exposing provider credentials to the executor.
context: Experiment E proved the host dynamic-tool seam but also found unrestricted `linear_graphql` invalidates
  non-bypassability. Phase 16 therefore requires host-generated effect identity and host-generated policy context
  before any governed provider mutation. This ticket defines those two pure records only.
ticket_type: feature
epic_ref: ATLAS-E10
risk_level: high
component: effect-request-contract
tags:
- phase-16
- track-a
- runtime-policy
- effects
relevant_docs:
- docs/atlas/agentic-engineering-programme-design.md
- docs/atlas/phase-16-agent-runtime-and-integration-safety.md
depends_on: []
acceptance_criteria:
- '`EffectRequest` contains the exact host-owned request/runtime/tool-call/target/capability/argument-digest identities
  and deterministic request fingerprint required by section 19; the model cannot choose or override the idempotency
  identity.'
- '`RuntimePolicyContext` contains only bounded host-observed temporal/runtime/policy/channel facts needed for evaluation
  and excludes provider credentials or mutable hidden state.'
- Both contracts are immutable/canonical and reject missing runtime binding, malformed target/capability identity
  and altered same-request identity combinations deterministically.
- Missing or stale context can be represented explicitly so later evaluation can return `INDETERMINATE` rather than
  infer ALLOW.
- The module performs no policy evaluation, subprocess execution, provider mutation, persistence or credential access.
non_goals:
- No package-level re-export, generated schema/export registration, database migration/repository, API/UI, shared
  registry, WORKFLOW.md/Symphony edit, external mutation, production activation or live milestone proof.
- No import from another new Phase 16 Track-A module; if implementation requires a sibling Track-A contract, stop
  and return the planning/dependency issue to the operator rather than coupling the ramp workloads.
test_requirements:
- Focused deterministic tests in `tests/test_effect_request.py` cover valid construction, every closed enum/value
  boundary, malformed/contradictory inputs, canonical serialization/fingerprint stability and order-independence
  where collections are semantically sets.
- An architecture/scope assertion proves the production change stays within the named contract module and imports
  only stable existing lower-layer/Pydantic/shared primitives; tests exercise zero filesystem, database, Git/GitHub,
  Linear, Symphony or network mutation.
implementation_notes:
- Planned Track-A changed-path envelope is exactly `atlas/core/models/effect_request.py` plus `tests/test_effect_request.py`.
  Do not add convenience exports or generated artifacts; Track-B integration owns those after ATLAS-253.
- Use immutable/frozen Pydantic/domain values and deterministic canonicalization consistent with the dedicated Phase
  16 design. This ticket is intended to remain independently useful even if the concurrency ramp did not exist.
documentation_requirements: []
definition_of_done:
- The named contract family exists only in `atlas/core/models/effect_request.py`, all focused tests in `tests/test_effect_request.py`
  pass, and the final diff contains no other production/test path.
- The PR completion report states the exact two-path diff, confirms no sibling Track-A import/dependency/protected-lane
  expansion, and preserves all authority/non-goal boundaries from the dedicated design.
---

# Phase 16 governed effect request and runtime policy context contracts

Authority-neutral Phase 16 Track-A foundation contract. No production activation is part of this ticket.
