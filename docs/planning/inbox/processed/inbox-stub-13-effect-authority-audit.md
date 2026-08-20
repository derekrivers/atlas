---
title: Phase 16 runtime policy decision and effect audit contract family
objective: Define the policy decision, execution receipt, channel claim and immutable policy-bundle contracts that
  later prove a governed effect is identity-bound, replayable and non-bypassable within its declared channel scope.
context: The dedicated design separates policy, provider execution receipt, channel inventory and bundle identity
  so no single agent claim can authorise itself. This foundation family contains those tightly coupled authority/audit
  records only; evaluator, gateway, persistence and live Linear proof are separate Track-B/S/M work.
ticket_type: feature
epic_ref: ATLAS-E10
risk_level: high
component: effect-authority-contract
tags:
- phase-16
- track-a
- runtime-policy
- audit
relevant_docs:
- docs/atlas/agentic-engineering-programme-design.md
- docs/atlas/phase-16-agent-runtime-and-integration-safety.md
depends_on: []
acceptance_criteria:
- '`RuntimePolicyBundle`, `RuntimePolicyDecision`, `EffectExecutionReceipt` and `EffectChannelClaim` represent the
  section-19 bundle/rule identity, ALLOW/DENY/INDETERMINATE decision, exact request/decision execution outcome/fence
  state, and channel-inventory/non-bypassability evidence respectively.'
- Policy decisions bind the exact request fingerprint, policy bundle fingerprint, evaluator version/identity and
  reason codes; a mismatched request or bundle cannot be represented as an authoritative decision.
- Effect receipts distinguish no-effect denial, executed effect and unresolved/ambiguous pre-effect fence states
  so absence of a terminal provider receipt cannot imply safe retry.
- Channel claims carry exact Symphony/Codex/tool/MCP/credential/shell/helper/policy/evaluator inventory fingerprints
  and become stale when any material identity changes; `generic_mutation_channel_absent` is explicit evidence, not
  inference.
- The family contains no actual policy rules, evaluator process, provider credential, generic Linear tool change,
  persistence or live effect.
non_goals:
- No package-level re-export, generated schema/export registration, database migration/repository, API/UI, shared
  registry, WORKFLOW.md/Symphony edit, external mutation, production activation or live milestone proof.
- No import from another new Phase 16 Track-A module; if implementation requires a sibling Track-A contract, stop
  and return the planning/dependency issue to the operator rather than coupling the ramp workloads.
test_requirements:
- Focused deterministic tests in `tests/test_runtime_policy.py` cover valid construction, every closed enum/value
  boundary, malformed/contradictory inputs, canonical serialization/fingerprint stability and order-independence
  where collections are semantically sets.
- An architecture/scope assertion proves the production change stays within the named contract module and imports
  only stable existing lower-layer/Pydantic/shared primitives; tests exercise zero filesystem, database, Git/GitHub,
  Linear, Symphony or network mutation.
implementation_notes:
- Planned Track-A changed-path envelope is exactly `atlas/core/models/runtime_policy.py` plus `tests/test_runtime_policy.py`.
  Do not add convenience exports or generated artifacts; Track-B integration owns those after ATLAS-253.
- Use immutable/frozen Pydantic/domain values and deterministic canonicalization consistent with the dedicated Phase
  16 design. This ticket is intended to remain independently useful even if the concurrency ramp did not exist.
documentation_requirements: []
definition_of_done:
- The named contract family exists only in `atlas/core/models/runtime_policy.py`, all focused tests in `tests/test_runtime_policy.py`
  pass, and the final diff contains no other production/test path.
- The PR completion report states the exact two-path diff, confirms no sibling Track-A import/dependency/protected-lane
  expansion, and preserves all authority/non-goal boundaries from the dedicated design.
---

# Phase 16 runtime policy decision and effect audit contract family

Authority-neutral Phase 16 Track-A foundation contract. No production activation is part of this ticket.
