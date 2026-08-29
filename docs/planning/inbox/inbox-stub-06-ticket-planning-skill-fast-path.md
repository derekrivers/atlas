---
title: Ticket-planning skill fast path
objective: Refine `atlas-ticket-planning` into a bounded one-pass batch-preparation procedure that uses the new
  read-only integrity surface and avoids broad repository exploration or irrelevant full-suite validation.
context: The planning skill has the correct authority boundary but still leaves room for an agent to spend large
  amounts of time reconstructing context or discovering integrity defects only after handoff. For ratified maintenance
  batches, the agent should consume the supplied decomposition, read the narrow authority set once, write the whole
  batch once, validate once and stop.
ticket_type: documentation
epic_ref: ATLAS-E3
risk_level: medium
component: ticket-planning-skill
tags:
- maintenance
- ticket-minting
- agent-skills
- planning
- codex-skill
- efficiency
source_anchor: AGENTS.md#repository-codex-skills
relevant_docs:
- AGENTS.md
- docs/runbooks/planning-phases-and-ticket-stubs.md
- docs/atlas/planning-engine-specification.md
depends_on:
- inbox-stub-02-planning-intake-isolation.md
- inbox-stub-05-planning-inbox-read-only-validation.md
acceptance_criteria:
- '`atlas-ticket-planning` directs the agent to use the repository-owned read-only inbox validator after the complete
  planning-input commit and treats its output as the definitive pre-handoff integrity result.'
- For an operator-ratified decomposition, the skill instructs one bounded authority-read pass and one full-batch
  write/review pass; it does not ask the agent to rediscover the programme or redesign ticket boundaries unless
  a canonical contradiction or integrity failure is found.
- After the planning candidate is committed and frozen and the read-only inbox-integrity check passes, the skill
  composes `atlas-validation` with the exact base/head and complete changed-path set and executes exactly the repository-selected
  validation plan.
- Known governed planning stubs and batch manifests are expected to select the focused `documentation` profile,
  but the skill obeys the actual deterministic plan, including any selected conservative fallback, and fails closed
  rather than narrowing or bypassing a fallback, selected-command failure, diff-proof failure or head movement.
- The skill keeps `git diff --check`, complete manifest-listed diff review and the documentation linter, and explicitly
  prohibits manually adding an unselected full sweep, pytest, Playwright, browser/UI/E2E or any other ritual validation.
- 'The stop contract remains strict: no `atlas plan`, `atlas apply`, key assignment, Atlas-store mutation, PM sync,
  Linear mutation, push, PR or ticket implementation is performed by this skill.'
- The handoff report names exact base/head, manifest, ordered stub count, dependency summary, read-only integrity
  result, selected validation profile/commands/results, diff check and doc-linter result without dumping unrelated
  corpus content.
non_goals:
- No change to plan/apply semantics, PM publication, Linear state ownership, delivery admission or implementation-agent
  validation.
- No manual validation-profile selection or removal of required canonical authority, integrity or repository-selected
  validation checks in the name of speed.
test_requirements:
- Repository skill-contract tests added later in this batch must assert the inbox validator, exact-head `atlas-validation`
  composition, selector authority, stop boundary and prohibition on unselected ritual checks.
implementation_notes:
- Expected production path is only `.codex/skills/atlas-ticket-planning/SKILL.md`; keep policy in canonical docs
  and procedure in the skill.
- Freeze the committed candidate before composing `atlas-validation`; optimise round trips and redundant reads,
  not correctness gates or repository-selected validation.
documentation_requirements: []
definition_of_done:
- A fresh Codex agent can prepare this ratified thirteen-ticket maintenance batch without inventing extra workflow
  steps or running unrelated test suites.
- The skill still fails closed on unresolved authority or batch-integrity defects.
---

# Ticket-planning skill fast path

Governed maintenance input for the `ticket-minting-skills-v1` batch.
