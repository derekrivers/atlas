---
title: Ticket-minting end-to-end proof and efficiency milestone
objective: Prove the refined workflow on the actual thirteen-ticket maintenance batch, including planning-input
  validation and publication, deterministic mint/apply in isolation, complete apply-artifact publication, PM publication
  without admission and resumable failure handling, and record whether the operator workflow is materially faster
  than the current multi-hour baseline.
context: The purpose of this maintenance programme is not merely to add Markdown skills. Atlas needs evidence that
  a realistic batch can be prepared and published without repeated context reconstruction, irrelevant test sweeps,
  manual Linear creation or duplicate-prone recovery. The proof should be deterministic in CI where possible and
  transparent about human/agent wall-clock observations where it is not. The initial preparation attempt was also
  blocked by an unrelated PM-generated active follow-up, so the proof must include pending-follow-up isolation as
  a first-class efficiency case. The proof must also exercise the repository Linear skill's non-Symphony route so
  operator-side publication/readback does not depend on Symphony injecting `linear_graphql`. The proof must exercise
  the governed planning-publication skill before store mutation and again over the non-disposable apply artifacts
  after the isolated Atlas store has advanced.
ticket_type: feature
epic_ref: ATLAS-E10
risk_level: high
component: ticket-minting-milestone
tags:
- maintenance
- ticket-minting
- agent-skills
- milestone
- integration
- efficiency
relevant_docs:
- AGENTS.md
- docs/runbooks/planning-phases-and-ticket-stubs.md
- docs/atlas/planning-engine-specification.md
- docs/atlas/pm-engine-and-linear-sync.md
- docs/atlas/playbooks/linear-sync.md
depends_on:
- inbox-stub-12-ticket-skill-routing-contracts.md
acceptance_criteria:
- A deterministic integration fixture representing this exact thirteen-ticket ordered maintenance batch passes
  the read-only inbox validator, calculates and runs the deterministic validation selector for the exact committed
  planning-input base/head/path set, then publishes that unchanged candidate through the governed planning-publication
  procedure to an isolated Git/PR boundary before any store mutation.
- Stubs-only plan and apply against an isolated repository/database prove thirteen ticket ADDs for thirteen approved
  stubs, twenty-four dependency ADDs for the exact approved DAG, thirty-seven aggregate ADD entries, zero MODIFY,
  PROPOSE_ARCHIVE, CONFLICT or collapse and no unexpected added entity type before proving the expected minted key
  count, renders and processed-stub retirement; the post-apply publication mode then commits and publishes the complete
  apply-owned planning tree only after a fresh deterministic validation plan passes for that new exact candidate,
  without discarding, recreating or partially selecting artifacts after the store advances.
- The resulting isolated minted tickets are published through the PM publication-only seam using an in-memory/fake
  Linear boundary; every requested ticket receives exactly one external issue identity and the mapped create-time
  state with zero delivery-admission promotion.
- The proof seeds an unrelated pending PM follow-up while the ordered batch is active and proves the pending input
  remains durable but does not enter the ordered batch's active-inbox integrity set.
- A seeded malformed-batch case fails before mint/apply; a separate seeded explicit `source_anchor` to a non-indexed
  document fails the read-only planning preflight with the same Gate-4 semantics before `atlas plan --stubs-only`
  and without creating a failed PlanRun; and a seeded create-time Linear state assertion failure retains the issue
  join key so a retry updates/reuses exactly one issue instead of creating a duplicate.
- A concise proof report demonstrates that both known planning-only candidates select focused `documentation` validation
  and that neither deterministic plan contains pytest, Playwright, browser/UI/E2E or an unselected full sweep; it
  also proves stubs-only minting performs no planner-provider/model call and records the exact planning-input/apply-artifact
  identities, selector inputs, commands/results, thirteen-ticket batch size, bounded external-call counts and observed
  agent-active/operator wall-clock, targeting a validated planning-input handoff for this actual thirteen-ticket
  ratified batch in no more than 20 minutes excluding human review; if missed, it records the evidenced dominant delay
  instead of passing on functional tests alone. The proof also exercises the bounded non-Symphony Linear adapter/skill
  route and proves no arbitrary GraphQL, raw token or PM-owned issue-creation capability is exposed.
- The final `docs/closure/ticket-minting-skill-proof.md` contains an `Observed findings and durable adaptations` ledger
  for every unexpected failure in the real programme exercise, recording exact evidence/identity, lifecycle boundary,
  root-cause classification, durable/transient status, owning Atlas adaptation, absorbing ticket/code/runbook/skill/lesson
  and recurrence regression/proof. It accounts at minimum for PM follow-up/ordered-inbox contention, unnecessarily
  broad governed-planning validation, invalid YAML front matter reaching the operator proposal gate, explicit anchors
  outside the planner `AnchorIndex`, the missing governed planning-input/apply-artifact publication boundary,
  apply-to-PM sequencing through repository publication, PM publication versus delivery admission, managed PM-writer
  interaction with the future apply boundary and every further issue found before this batch mints; completion is
  forbidden while any durable
  observed finding lacks a disposition, while proven transient events create no manufactured implementation work.
  The ledger specifically records PlanRun `bec35f96-aa4a-43ba-9324-40a510808d2b` as a semantically valid thirteen-ticket,
  twenty-four-edge, thirty-seven-aggregate proposal stopped by a durable governance/procedural raw-count acceptance
  defect absorbed by the canonical planning runbook and tickets 01, 10, 12 and 13. It also records the stopped attempt
  to correct that authority while freezing the obsolete fourteen-path inventory as a durable planning-procedure/operator-
  instruction finding, resolved by treating the manifest as the complete current governed overlay and permitting an
  explicitly reviewed same-batch pre-key expansion while unrelated paths remain prohibited.
non_goals:
- No live production Linear pollution or throwaway production Atlas keys solely for the test.
- No weakening of PlanRun approval, apply integrity, PM writer ownership or CI evidence policy to meet the time
  target.
test_requirements:
- Focused integration tests run entirely against temporary repository/database state, fake Git/GitHub publication
  boundaries and in-memory/fake Linear clients.
- Selector assertions cover both exact candidates, prove the focused `documentation` profile and command inventory
  for known planning paths, and fail if pytest, Playwright, browser/UI/E2E or an unselected `full-sweep` appears.
- 'Seeded defects must bite: remove the batch coverage invariant or promoted-anchor Gate-4 check and the corresponding
  malformed-batch or non-indexed explicit-source-anchor fixture must turn green only if the test is broken.'
- Type-qualified proposal-acceptance regression proves the valid thirteen-ticket/twenty-four-edge shape passes, an
  unexpected fourteenth ticket fails, and every missing, extra or reversed dependency edge fails; aggregate ADD count
  alone is never accepted as ticket-count authority.
implementation_notes:
- Use existing planning/apply test helpers and Linear fakes rather than building a second integration harness.
- The wall-clock measurement is observational evidence in the proof report, not a flaky CI timing assertion.
documentation_requirements:
- docs/closure/ticket-minting-skill-proof.md
definition_of_done:
- Functional evidence proves intake isolation, both governed repository-publication boundaries, non-Symphony Linear
  mechanics, deterministic validation selection for both exact candidates, minting/publication behavior and recovery
  without partial apply-artifact publication, admission leakage, lost follow-ups, arbitrary GraphQL or duplicate
  issue creation.
- The proof report clearly passes or fails the <=20-minute preparation objective, names any remaining bottleneck and
  leaves no durable observed finding without an explicit absorbing adaptation and recurrence proof.
---

# Ticket-minting end-to-end proof and efficiency milestone

Governed maintenance input for the `ticket-minting-skills-v1` batch.
