---
title: Ticket-minting skill routing and composition contracts
objective: Mechanically register and pin the new ticket-minting skills, authority references, allowed composition
  edges and anti-bypass rules in `AGENTS.md` and repository static contract tests.
context: Atlas already has `tests/test_skill_linear.py`, which enumerates every required repository skill, its authority
  references, `AGENTS.md` routing and exact composition edges. The new skills should extend that executable contract
  rather than rely on prose discoverability.
ticket_type: feature
epic_ref: ATLAS-E10
risk_level: medium
component: skill-routing-contracts
tags:
- maintenance
- ticket-minting
- agent-skills
- codex-skill
- contracts
- tests
relevant_docs:
- AGENTS.md
- docs/runbooks/planning-phases-and-ticket-stubs.md
- docs/atlas/pm-engine-and-linear-sync.md
depends_on:
- inbox-stub-04-session-aware-linear-skill.md
- inbox-stub-06-ticket-planning-skill-fast-path.md
- inbox-stub-08-pm-ticket-sync-skill.md
- inbox-stub-09-governed-planning-artifact-publication-skill.md
- inbox-stub-10-planning-apply-governed-publication-handoff.md
- inbox-stub-11-ticket-lifecycle-orchestration-skill.md
acceptance_criteria:
- '`AGENTS.md#Repository Codex skills` lists the new governed planning-artifact publication, PM publication/sync
  and ticket-lifecycle orchestration capabilities with unambiguous task labels while preserving all existing skill
  routes.'
- Repository skill-contract tests require every current skill directory/front matter and pin canonical authority
  references for the new skills as strictly as existing Atlas skills.
- Exact composition assertions pin `atlas-ticket-planning -> atlas-validation`, `atlas-planning-publication ->
  atlas-validation` and `atlas-planning-apply -> atlas-planning-publication -> atlas-pm-ticket-sync`, and permit
  the intended planning -> planning-publication, lifecycle -> specialist and session-aware `linear` mechanics edges;
  unexpected skill cross-references fail the test.
- Negative assertions prove the lifecycle skill cannot route directly to raw Linear or implementation skills, the
  planning-publication skill cannot merge, infer approval, run plan/apply, partially publish post-apply artifacts
  or cross into PM/Linear/implementation work, neither planning skill can hand-select, narrow or augment deterministic
  validation, the PM skill cannot teach raw Linear issue creation, and the `linear` skill cannot fall back to arbitrary
  GraphQL/raw-token access outside Symphony.
- Tests pin the planning skill's read-only integrity command, frozen-head selector execution and fast-path stop,
  both planning-artifact publication modes and their exact-head validation, both supported `linear` session routes
  and the apply-to-publication handoff so future edits cannot silently reintroduce manual validation, publication
  or minting seams.
- 'The contract-test structure remains maintainable: misleading Linear-only inventory naming is either cleanly refactored
  or clearly separated without duplicating skill inventory constants across test files.'
- Focused structural assertions require the planning and lifecycle skills to retain the failure-classification,
  explicit-disposition and resumable last-legal-boundary handoff; deleting any of those routing signals makes the
  contract test fail without pinning large prose blocks.
non_goals:
- No PM runtime behavior, planning CLI behavior, external mutation, PlanRun or ticket implementation.
- No broad rewrite of WORKFLOW/Symphony routing unrelated to ticket minting.
test_requirements:
- Run the focused repository skill-contract tests and the documentation linter; a deliberately removed composition,
  authority or failure-disposition handoff line must make the focused test red.
- Do not require Playwright, UI E2E or unrelated Python suites for this static skill-routing candidate unless the
  repository validation selector independently selects them.
implementation_notes:
- Primary existing test surface is `tests/test_skill_linear.py`; refactor only as far as needed to keep one canonical
  required-skill/composition inventory.
- This ticket owns the `AGENTS.md` skill table update so earlier tickets do not overlap that routing surface.
documentation_requirements:
- AGENTS.md
definition_of_done:
- The repository mechanically rejects missing, misrouted or unexpectedly composed ticket-minting skills.
- The focused validation surface is small and deterministic and does not require UI/E2E execution by construction.
---

# Ticket-minting skill routing and composition contracts

Governed maintenance input for the `ticket-minting-skills-v1` batch.
