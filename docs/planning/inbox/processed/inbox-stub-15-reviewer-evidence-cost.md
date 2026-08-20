---
title: Phase 16 reviewer evidence and burden proxy contracts
objective: Define the bounded ReviewerEvidenceBundle and reproducible ReviewerBurdenProxy records that later reduce
  human reconstruction work without replacing human acceptance.
context: Reviewer/operator burden is a first-class system cost. Phase 16 needs a deterministic bounded evidence
  bundle and cost proxy contract before it can expose reviewer-oriented projections. This Track-A slice defines
  the records only; projection, API/UI and measurement are post-ramp work.
ticket_type: feature
epic_ref: ATLAS-E10
risk_level: medium
component: reviewer-evidence-contract
tags:
- phase-16
- track-a
- reviewer-evidence
- metrics
relevant_docs:
- docs/atlas/agentic-engineering-programme-design.md
- docs/atlas/phase-16-agent-runtime-and-integration-safety.md
depends_on: []
acceptance_criteria:
- '`ReviewerEvidenceBundle` represents the section-22 exact repository/PR/head/base/ticket/runtime/topology/interface/validation/CI/outcome/trajectory/policy/interaction/unknown/acceptance
  identities and deterministic projector fingerprint without raw execution content.'
- '`ReviewerBurdenProxy` can represent the declared deterministic proxies: acceptance elapsed duration/action count,
  Changes Requested cycles, stale/restart and mechanical-rebase/semantic-conflict counts, evidence unknowns and
  material alert count.'
- Bundle/proxy values are bounded/canonical and reject impossible negative counts, duplicate identities and head/base
  identity malformation.
- The contracts explicitly exclude raw transcripts, full command logs, credentials, chain-of-thought, hidden evaluation
  material and any machine approval/merge-readiness substitute.
- The module performs no projection, API/UI rendering, acceptance action, review disposition or metrics persistence.
non_goals:
- No package-level re-export, generated schema/export registration, database migration/repository, API/UI, shared
  registry, WORKFLOW.md/Symphony edit, external mutation, production activation or live milestone proof.
- No import from another new Phase 16 Track-A module; if implementation requires a sibling Track-A contract, stop
  and return the planning/dependency issue to the operator rather than coupling the ramp workloads.
test_requirements:
- Focused deterministic tests in `tests/test_reviewer_evidence.py` cover valid construction, every closed enum/value
  boundary, malformed/contradictory inputs, canonical serialization/fingerprint stability and order-independence
  where collections are semantically sets.
- An architecture/scope assertion proves the production change stays within the named contract module and imports
  only stable existing lower-layer/Pydantic/shared primitives; tests exercise zero filesystem, database, Git/GitHub,
  Linear, Symphony or network mutation.
implementation_notes:
- Planned Track-A changed-path envelope is exactly `atlas/core/models/reviewer_evidence.py` plus `tests/test_reviewer_evidence.py`.
  Do not add convenience exports or generated artifacts; Track-B integration owns those after ATLAS-253.
- Use immutable/frozen Pydantic/domain values and deterministic canonicalization consistent with the dedicated Phase
  16 design. This ticket is intended to remain independently useful even if the concurrency ramp did not exist.
documentation_requirements: []
definition_of_done:
- The named contract family exists only in `atlas/core/models/reviewer_evidence.py`, all focused tests in `tests/test_reviewer_evidence.py`
  pass, and the final diff contains no other production/test path.
- The PR completion report states the exact two-path diff, confirms no sibling Track-A import/dependency/protected-lane
  expansion, and preserves all authority/non-goal boundaries from the dedicated design.
---

# Phase 16 reviewer evidence and burden proxy contracts

Authority-neutral Phase 16 Track-A foundation contract. No production activation is part of this ticket.
