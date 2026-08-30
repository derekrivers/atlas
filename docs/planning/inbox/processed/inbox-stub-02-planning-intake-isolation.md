---
title: Planning intake isolation for asynchronous follow-ups
objective: Decouple PM-generated follow-up capture from the single active planning inbox so unrelated follow-ups
  can remain durably pending without blocking an operator-ratified ordered planning batch.
context: Today the PM sync follow-up producer writes unnumbered `ATLAS-N-n.md` stubs directly into `docs/planning/inbox/`,
  while Atlas intentionally rejects any ordered `inbox-stub-NN-*` batch that is mixed with an unnumbered follow-up.
  The first preparation run for this maintenance batch was correctly blocked by an untracked `ATLAS-282-3.md`, demonstrating
  that asynchronous follow-up capture can serialize or delay unrelated deliberate planning work.
ticket_type: feature
epic_ref: ATLAS-E3
risk_level: high
component: planning-intake
tags:
- maintenance
- ticket-minting
- agent-skills
- planning
- pm-follow-up
- intake
relevant_docs:
- docs/runbooks/planning-phases-and-ticket-stubs.md
- docs/atlas/planning-engine-specification.md
- docs/atlas/pm-engine-and-linear-sync.md
- docs/decisions/0007-generative-planning-with-deterministic-reconciliation.md
depends_on:
- inbox-stub-01-ticket-minting-lifecycle-authority.md
acceptance_criteria:
- PM-generated `atlas:proposed-follow-up` capture is persisted through an explicit pending-intake boundary that
  is not itself part of the active planning inbox consumed by plan/apply.
- The active `docs/planning/inbox/` remains one exact operator-selected planning intent, so existing ordered-batch
  manifest coverage/order and fail-closed integrity semantics do not need to be weakened.
- A bounded operator/CLI admission operation can promote selected pending follow-up inputs into the active inbox
  atomically and without creating a ticket, minting a key, persisting a PlanRun, changing Atlas ticket state or
  mutating Linear.
- Follow-up source-comment identity and deduplication remain durable across pending, admitted and processed locations;
  a PM sync retry cannot create duplicate pending/admitted stubs for the same Linear comment.
- An unrelated pending follow-up may coexist with an ordered planning batch without making the batch integrity guard
  fail, while two simultaneously active ordered batch manifests remain rejected.
- Legacy unnumbered follow-ups already present in the active inbox are handled by an explicit compatible operator
  path; the implementation never silently deletes, auto-processes or hides them.
- Focused deterministic tests prove capture/admission idempotency, pending-vs-active isolation, no-loss recovery
  and that the existing active-inbox batch integrity guard continues to reject genuinely mixed active inputs.
non_goals:
- No concurrent PlanRuns over multiple active batches, no weakening of exact manifest coverage, and no automatic
  approval/apply of pending follow-ups.
- No ticket implementation, Linear lifecycle mutation, delivery admission or general-purpose filesystem queue.
test_requirements:
- Focused PM/planning tests seed an unrelated pending follow-up plus an active ordered batch and prove the ordered
  batch validates unchanged.
- Seeded duplicate source-comment and interrupted admission cases prove exactly-once pending/admitted identity without
  data loss.
implementation_notes:
- Choose the pending storage/admission shape under the canonical planning authority; prefer one machine-owned intake
  mechanism over teaching agents to move files manually.
- Preserve the current rule that the active inbox is exact and fail-closed. Solve contention before the active-inbox
  boundary rather than by making mixed active inputs ambiguous.
documentation_requirements:
- docs/runbooks/planning-phases-and-ticket-stubs.md
- docs/atlas/planning-engine-specification.md
- docs/atlas/pm-engine-and-linear-sync.md
definition_of_done:
- A PM follow-up can be captured while an unrelated ordered maintenance batch is being prepared without blocking
  or contaminating that batch.
- No follow-up is silently lost, and the operator retains an explicit admission gate before it becomes active planning
  input.
---

# Planning intake isolation for asynchronous follow-ups

Governed maintenance input for the `ticket-minting-skills-v1` batch.
