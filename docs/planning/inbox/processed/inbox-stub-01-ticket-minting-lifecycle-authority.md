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
  publication/reconciliation and delivery admission, with the owner of every boundary named; proposal acceptance
  requires ticket ADD count to equal approved stub count, dependency ADDs to equal the approved graph and unexpected
  added entity types to stop, while aggregate ADD remains informational composition rather than ticket-count authority.
- 'The contract distinguishes ticket publication from delivery admission: creating or updating a Linear issue and
  asserting its mapped current Atlas state is not itself permission to promote that ticket to `ready_for_agent`;
  a mint-only or publish-only intent has no admission side effect and fails closed or uses a publication-only seam
  when the available runtime path could admit work.'
- Atlas PM remains the authority for publishing newly minted Atlas tickets to Linear. Raw `linear_graphql` issue
  creation or workflow mutation is explicitly not a substitute for PM publication, join-key persistence or reconciliation.
- 'Failure semantics are explicit: an applied PlanRun is never re-applied to repair a Linear incident; a retained
  `external_linear_id` is reused; create-time state assertion failure is recoverable without duplicate issue creation.'
- Every unexpected stop in planning-input preparation, validation, plan, apply, repository publication or PM publication
  receives an explicit disposition before retry or closure as an input/schema/preflight defect, skill/procedure defect,
  runtime/code defect, governance/authority defect, operational recovery/troubleshooting finding, reusable Atlas
  lesson, delivery/debt follow-up or proven transient external/system event requiring no product change; every durable
  finding names the repository/Atlas surface that absorbs it, and `retry` alone is not a disposition.
- When a pre-key failure exposes a defect in the still-unminted batch, the owning planned ticket is corrected before
  minting instead of knowingly deferring an already-understood defect to a follow-up; when that correction genuinely
  requires another canonical repository path in the same governed package, the manifest may expand only while the
  original base remains valid, complete base-to-HEAD equality is re-proven and no unrelated path is absorbed merely
  to make validation pass.
- The architecture explicitly permits a read-only planning-input preflight only by reusing the existing shared
  `validate_inbox_batch_integrity` implementation, the same planner input/processed-alias `AnchorIndex` construction,
  the same deterministic stubs-only backlog-echo and `promote_inbox_stubs` primitives and the same existing applicable
  proposal gates; it duplicates no parsing, anchor policy, gate policy, reconciliation, key authority or apply logic
  and produces no reconciliation diff, PlanRun persistence or mutation; skills remain procedural adapters beneath
  this authority, and this ticket activates no runtime, Linear or delivery-policy behavior.
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
