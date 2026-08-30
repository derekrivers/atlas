---
title: Planning-apply to governed publication handoff
objective: Make `atlas-planning-apply` hand its complete post-apply planning tree immediately to
  `atlas-planning-publication` post-store mode, deferring `atlas-pm-ticket-sync` until the exact apply-artifact PR
  has been reviewed and merged.
context: After an approved apply advances the Atlas store, its complete apply-owned planning tree is non-disposable
  and must be published as one exact repository candidate before PM publication begins. The current apply handoff
  points to PM too early and leaves the required apply-artifact publication boundary out of procedural order.
ticket_type: documentation
epic_ref: ATLAS-E10
risk_level: medium
component: ticket-minting-handoffs
tags:
- maintenance
- ticket-minting
- agent-skills
- codex-skill
- handoff
- publication
relevant_docs:
- AGENTS.md
- docs/runbooks/planning-phases-and-ticket-stubs.md
- docs/runbooks/running-atlas-plan.md
- docs/atlas/pm-engine-and-linear-sync.md
depends_on:
- inbox-stub-09-governed-planning-artifact-publication-skill.md
acceptance_criteria:
- '`atlas-planning-apply` names `atlas-planning-publication` post-store mode as its immediate procedural continuation
  after an approved apply has advanced the store and the complete apply-owned planning tree and exact handoff identities
  have been preserved.'
- 'The governed sequence is explicit: approved apply → preserve the complete apply-owned planning tree →
  `atlas-planning-publication` → publish the exact apply-artifact PR and stop → human review and merge →
  `atlas-pm-ticket-sync`.'
- The handoff distinguishes an applied-but-not-yet-published batch as a valid, non-disposable intermediate state
  and reports the exact PlanRun, store, minted-key and planning-tree identities required by post-store publication.
- The apply skill explicitly prohibits skipping repository publication and proceeding directly from apply to PM,
  rerunning `atlas apply` to repair a publication or PM incident, and discarding or partially preserving apply-owned
  artifacts.
- '`atlas-planning-apply` does not merge, infer publication approval, run PM sync or Linear mutation, or start ticket
  implementation; `atlas-planning-publication` owns repository publication and stops after publishing the PR.'
- '`atlas-planning-apply` replaces its raw aggregate-ADD comparison with type-qualified proposal inspection: expected
  ticket ADD count equals approved stub count, dependency ADDs exactly equal the approved DAG, aggregate ADD is not
  compared directly with stub count, unexpected added entity types stop, and every existing MODIFY, archive, conflict,
  collapse, provenance and integrity stop remains intact.'
- The real thirteen-stub, twenty-four-edge batch is accepted as thirteen ticket ADDs plus twenty-four dependency ADDs
  and thirty-seven aggregate ADD entries only when every typed entry matches the ratified batch; thirty-seven is not
  a general allowance.
non_goals:
- No runtime code, PM CLI behavior, planning integrity logic, AGENTS routing table or lifecycle orchestration skill.
- No repository merge, PM/Linear publication, delivery admission or ticket implementation.
test_requirements:
- Later repository skill-contract tests must detect removal of the immediate publication handoff, reintroduction
  of a direct apply-to-PM route or weakening of complete apply-artifact preservation.
implementation_notes:
- Expected changed path is `.codex/skills/atlas-planning-apply/SKILL.md` only.
- Keep the handoff concise and preserve the standard canonical-authority disclaimer; publication and PM policy
  remain owned by their specialist skills and canonical documents.
documentation_requirements: []
definition_of_done:
- An operator following the apply skill reaches governed post-store repository publication before any PM continuation.
- The non-disposable apply-artifact boundary remains explicit and direct apply-to-PM routing is mechanically rejected.
---

# Planning-apply to governed publication handoff

Governed maintenance input for the `ticket-minting-skills-v1` batch.
