---
title: PM ticket publication and sync skill
objective: Add a dedicated `atlas-pm-ticket-sync` Codex skill that owns the procedure for publishing minted tickets,
  verifying Atlas/Linear identity and choosing between publication-only and full policy-governed PM sync.
context: Current skills jump from planning apply to a generic `linear` skill even though Atlas PM owns ticket publication,
  `external_linear_id`, create-time state assertion and broader reconciliation. The new skill must make PM the normal
  path and make the side-effect difference between publication-only and full sync explicit.
ticket_type: documentation
epic_ref: ATLAS-E6
risk_level: high
component: pm-ticket-sync-skill
tags:
- maintenance
- ticket-minting
- agent-skills
- pm-engine
- codex-skill
- linear-sync
relevant_docs:
- AGENTS.md
- docs/atlas/pm-engine-and-linear-sync.md
- docs/atlas/playbooks/linear-sync.md
- docs/runbooks/planning-phases-and-ticket-stubs.md
- docs/runbooks/operator-environment.md
depends_on:
- inbox-stub-04-session-aware-linear-skill.md
- inbox-stub-07-pm-publication-only-seam.md
acceptance_criteria:
- A new `.codex/skills/atlas-pm-ticket-sync/SKILL.md` follows the repository skill authority contract and names
  the canonical PM, planning-handoff, Linear-sync and operator-environment authorities it must read.
- For mint-only/publish-only intent, the skill uses the PM publication-only operation and never substitutes raw
  Linear issue creation or workflow mutation.
- For an explicitly requested full PM sync, the skill states that normal sync may execute delivery admission and
  other PM phases and therefore requires the exact runtime/database/policy preconditions owned by the PM runbooks
  rather than presenting it as a harmless publication command.
- The skill verifies each requested ticket's Atlas key/status, retained `external_linear_id` when present, publication
  result and mapped Linear state; bounded `linear` usage, if composed for readback, is read-only and may not repair
  PM-owned state by mutation.
- Recovery rules prohibit rerunning `atlas apply` after publication failure, prohibit duplicate issue creation when
  a join key exists, and route ambiguous/mismatched identity to diagnosis/reconciliation.
- The completion report distinguishes `published`, `full-sync-observed` and `admitted/ready` outcomes and never
  implies one from another.
non_goals:
- No planning decomposition, PlanRun approval/apply, ticket implementation, PR work or generic Symphony lifecycle
  mutation.
- No new PM runtime behavior beyond the publication seam delivered by its prerequisite.
test_requirements:
- Repository skill-contract tests later in this batch must assert the skill's authority references, PM-first routing,
  read-only Linear composition and anti-apply/anti-duplicate rules.
implementation_notes:
- Expected new path is `.codex/skills/atlas-pm-ticket-sync/SKILL.md` only; canonical behavioral policy stays in
  existing PM/planning docs.
- Use exact Atlas CLI names and distinguish observed state from requested state in every example.
documentation_requirements: []
definition_of_done:
- A fresh operator-side agent can publish/reconcile a minted ticket without guessing between PM and raw Linear operations.
- The skill fails closed rather than turning a mint-only instruction into admission or implementation authority.
---

# PM ticket publication and sync skill

Governed maintenance input for the `ticket-minting-skills-v1` batch.
