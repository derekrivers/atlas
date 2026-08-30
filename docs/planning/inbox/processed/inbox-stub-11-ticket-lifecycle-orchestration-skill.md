---
title: Ticket lifecycle orchestration skill
objective: Add a thin `atlas-ticket-lifecycle` skill that routes high-level operator intents across planning, governed
  repository publication, apply and PM publication skills while preserving every existing human approval and authority
  stop.
context: The specialist skills are intentionally narrow. Operators still need a deterministic entry point for phrases
  such as 'prepare and publish this planning batch', 'mint these tickets', 'publish the complete apply artifacts'
  or 'publish these minted tickets'. The orchestration skill should choose the specialist skill and stop boundary,
  not copy their policies or leave either repository-publication boundary to manual choreography.
ticket_type: documentation
epic_ref: ATLAS-E10
risk_level: medium
component: ticket-lifecycle-skill
tags:
- maintenance
- ticket-minting
- agent-skills
- codex-skill
- orchestration
- routing
relevant_docs:
- AGENTS.md
- docs/runbooks/planning-phases-and-ticket-stubs.md
- docs/runbooks/running-atlas-plan.md
- docs/atlas/pm-engine-and-linear-sync.md
depends_on:
- inbox-stub-06-ticket-planning-skill-fast-path.md
- inbox-stub-08-pm-ticket-sync-skill.md
- inbox-stub-09-governed-planning-artifact-publication-skill.md
- inbox-stub-10-planning-apply-governed-publication-handoff.md
acceptance_criteria:
- A new `.codex/skills/atlas-ticket-lifecycle/SKILL.md` routes ratified decomposition/planning-input work to `atlas-ticket-planning`,
  exact committed/validated planning-input publication to `atlas-planning-publication`, exact approved plan/apply
  work to `atlas-planning-apply`, complete post-apply planning-artifact publication back to `atlas-planning-publication`,
  and minted-ticket publication/full-sync work to `atlas-pm-ticket-sync`.
- 'The skill is a composer only: it repeats no stub schema, PlanRun approval policy, PM state map, Linear GraphQL
  mutation recipe, validation selector or delivery-admission algorithm owned elsewhere.'
- A request to 'prepare' or 'draft' a batch stops at validated planning inputs unless repository publication is
  explicitly requested; publishing those inputs uses the pre-store planning-publication mode and still stops before
  plan/apply.
- A request to 'mint' cannot infer approval of an unseen PlanRun; exact-proposal approval remains a separate operator
  gate, and after apply the complete non-disposable planning artifacts use the post-store publication mode before
  any PM continuation.
- A request to 'publish' or 'sync' distinguishes planning-input publication, post-apply artifact publication, PM
  ticket publication-only and full PM sync; full sync may admit work and is never selected merely because repository
  or ticket publication is needed.
- The skill never routes directly to `linear`, `atlas-ticket-execution`, remediation, PR review or PR acceptance
  for ticket minting and never starts implementation work as a side effect.
- A failed planning, validation, repository-publication, apply or PM-publication boundary routes first to bounded
  diagnosis and explicit learning/disposition under the lifecycle authority; the router never defaults to `retry
  until success`, and after correction resumes from the last legal boundary without replaying already-completed
  stateful operations. The final report states that boundary, produced artifacts/identities, finding disposition
  and next specialist skill/gate.
non_goals:
- No autonomous approval of PlanRuns, no bypass of operator gates, no manual repository-publication gap and no
  combined mega-command that hides intermediate evidence.
- No changes to specialist skill policy beyond composition references.
test_requirements:
- Static skill-contract tests later in this batch must pin the exact allowed composition edges and forbidden direct
  routes.
implementation_notes:
- Expected new path is `.codex/skills/atlas-ticket-lifecycle/SKILL.md` only.
- Keep the skill short enough to act as a router; canonical documents and specialist skills remain the detailed
  authority/procedure.
documentation_requirements: []
definition_of_done:
- A fresh agent can classify a high-level ticket-minting request into the correct specialist operation, including
  both repository-publication boundaries, without manual operator choreography.
- Every existing approval, publication and delivery-admission boundary remains visible and independently stoppable.
---

# Ticket lifecycle orchestration skill

Governed maintenance input for the `ticket-minting-skills-v1` batch.
