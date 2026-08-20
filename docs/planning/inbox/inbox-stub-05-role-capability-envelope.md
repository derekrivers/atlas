---
title: Phase 16 role capability envelope contract
objective: Define the explicit least-privilege RoleCapabilityEnvelope that later phases can evaluate before introducing
  specialised scouts, reviewers or advisors.
context: Phase 16 keeps production on the existing implementation executor but needs a durable contract for future
  specialised roles. A role name in a prompt must never create capability or authority. This ticket defines the
  envelope only; no new role is activated.
ticket_type: feature
epic_ref: ATLAS-E10
risk_level: medium
component: role-capability-contract
tags:
- phase-16
- track-a
- least-privilege
- contract
relevant_docs:
- docs/atlas/agentic-engineering-programme-design.md
- docs/atlas/phase-16-agent-runtime-and-integration-safety.md
depends_on: []
acceptance_criteria:
- '`RoleCapabilityEnvelope` carries the section-12 fields for role identity/kind, readable and writable workspace
  surfaces, dynamic capabilities, external-effect families, credential claims, optional budgets, forbidden capabilities
  and policy fingerprint.'
- Lists/identities are bounded and deterministically canonicalised; contradictory capability declarations such as
  the same capability being both granted and forbidden are rejected.
- The existing Phase 16 baseline executor can be represented without adding authority beyond the surrounding current
  Atlas workflow contract.
- Future specialised roles require distinct explicit envelopes; the contract contains no prompt/persona shortcut
  that grants authority.
- The module has no scheduler, credential, runtime, policy-evaluator or external-effect behavior.
non_goals:
- No package-level re-export, generated schema/export registration, database migration/repository, API/UI, shared
  registry, WORKFLOW.md/Symphony edit, external mutation, production activation or live milestone proof.
- No import from another new Phase 16 Track-A module; if implementation requires a sibling Track-A contract, stop
  and return the planning/dependency issue to the operator rather than coupling the ramp workloads.
test_requirements:
- Focused deterministic tests in `tests/test_role_capability.py` cover valid construction, every closed enum/value
  boundary, malformed/contradictory inputs, canonical serialization/fingerprint stability and order-independence
  where collections are semantically sets.
- An architecture/scope assertion proves the production change stays within the named contract module and imports
  only stable existing lower-layer/Pydantic/shared primitives; tests exercise zero filesystem, database, Git/GitHub,
  Linear, Symphony or network mutation.
implementation_notes:
- Planned Track-A changed-path envelope is exactly `atlas/core/models/role_capability.py` plus `tests/test_role_capability.py`.
  Do not add convenience exports or generated artifacts; Track-B integration owns those after ATLAS-253.
- Use immutable/frozen Pydantic/domain values and deterministic canonicalization consistent with the dedicated Phase
  16 design. This ticket is intended to remain independently useful even if the concurrency ramp did not exist.
documentation_requirements: []
definition_of_done:
- The named contract family exists only in `atlas/core/models/role_capability.py`, all focused tests in `tests/test_role_capability.py`
  pass, and the final diff contains no other production/test path.
- The PR completion report states the exact two-path diff, confirms no sibling Track-A import/dependency/protected-lane
  expansion, and preserves all authority/non-goal boundaries from the dedicated design.
---

# Phase 16 role capability envelope contract

Authority-neutral Phase 16 Track-A foundation contract. No production activation is part of this ticket.
