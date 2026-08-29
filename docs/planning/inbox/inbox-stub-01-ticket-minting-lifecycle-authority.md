---
title: Governed ticket-minting lifecycle authority
objective: Ratify the exact Atlas authority boundaries for preparing, minting, publishing, reconciling and admitting
  new tickets so agent skills can compose the existing planning and PM machinery without bypassing operator gates
  or Linear ownership.
context: Atlas already separates local ticket planning, operator plan/apply, PM synchronisation and bounded Linear
  operations, but no canonical minting workflow currently joins those boundaries. The current PM tick also combines
  definition publication with delivery admission, so a mint-only instruction must not silently become permission
  to promote work.
ticket_type: documentation
epic_ref: ATLAS-E10
risk_level: high
component: ticket-minting-governance
tags:
- maintenance
- ticket-minting
- agent-skills
- governance
- pm-boundary
source_anchor: AGENTS.md#repository-codex-skills
relevant_docs:
- AGENTS.md
- docs/runbooks/planning-phases-and-ticket-stubs.md
- docs/atlas/planning-engine-specification.md
- docs/atlas/pm-engine-and-linear-sync.md
- docs/atlas/playbooks/linear-sync.md
depends_on: []
acceptance_criteria:
- Canonical planning/PM documentation defines the lifecycle from operator-ratified intent through committed inbox
  inputs, `atlas plan --stubs-only`, exact-proposal approval, `atlas apply`, apply-artifact publication, PM Linear
  publication/reconciliation and delivery admission, with the owner of every boundary named.
- 'The contract distinguishes ticket publication from delivery admission: creating or updating a Linear issue and
  asserting its mapped current Atlas state is not itself permission to promote that ticket to `ready_for_agent`.'
- A mint-only or publish-only operator intent is defined to have no delivery-admission side effect; if the only
  available runtime path could admit work under the active policy, the operation must fail closed or use a dedicated
  publication-only seam.
- Atlas PM remains the authority for publishing newly minted Atlas tickets to Linear. Raw `linear_graphql` issue
  creation or workflow mutation is explicitly not a substitute for PM publication, join-key persistence or reconciliation.
- 'Failure semantics are explicit: an applied PlanRun is never re-applied to repair a Linear incident; a retained
  `external_linear_id` is reused; create-time state assertion failure is recoverable without duplicate issue creation.'
- The architecture explicitly permits a read-only planning-input validation command only as a wrapper over the existing
  shared stub/batch integrity implementation; it may not duplicate parsing, reconciliation, key authority or apply
  logic.
- Skills remain procedural adapters beneath canonical repository authority; this ticket changes documentation/authority
  only and activates no runtime, Linear or delivery-policy behavior.
non_goals:
- No new CLI, PM runtime path, Codex skill, Linear mutation, PlanRun, ticket key, delivery-policy change or production
  activation.
- No rewrite of the general Symphony execution, PR review or PR acceptance lifecycle.
test_requirements:
- Run `git diff --check` and `uv run python -m atlas.tools.doc_linter` over the exact documentation candidate.
- Review the changed canonical sections as an authority graph and prove they do not assign the same state edge or
  mutation boundary to two owners.
implementation_notes:
- Keep the change inside the existing planning and PM authority documents; do not create a second competing master
  workflow document.
- 'Document current behavior truthfully: the ordinary PM sync tick can include admission, while first-sync publication
  asserts the Linear state mapped to the ticket''s current Atlas status.'
documentation_requirements:
- docs/runbooks/planning-phases-and-ticket-stubs.md
- docs/atlas/planning-engine-specification.md
- docs/atlas/pm-engine-and-linear-sync.md
definition_of_done:
- A fresh agent can identify the owner, allowed transition and stop condition for every minting boundary without
  relying on conversation history.
- No implementation or external-system mutation is included in the final diff.
---

# Governed ticket-minting lifecycle authority

Governed maintenance input for the `ticket-minting-skills-v1` batch.
