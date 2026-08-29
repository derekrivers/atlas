---
title: PM publication-only ticket seam
objective: Add a bounded PM-owned one-shot publication operation for explicitly named minted tickets that creates
  or updates their Linear representation without executing delivery admission or unrelated PM tick phases.
context: The ordinary PM sync tick correctly combines pull, push, admission, CI/recovery, follow-up and anomaly
  work. That is too broad for the narrower operator intent 'publish these newly minted tickets but do not admit
  work'. A safe minting workflow needs a PM-owned publication seam rather than raw Linear mutations or temporarily
  relying on the active admission policy being paused.
ticket_type: feature
epic_ref: ATLAS-E6
risk_level: high
component: pm-ticket-publication
tags:
- maintenance
- ticket-minting
- agent-skills
- pm-engine
- linear-sync
- publication
source_anchor: docs/atlas/pm-engine-and-linear-sync.md#boundary
relevant_docs:
- docs/atlas/pm-engine-and-linear-sync.md
- docs/atlas/playbooks/linear-sync.md
- docs/runbooks/planning-phases-and-ticket-stubs.md
- docs/decisions/0006-source-of-truth-hierarchy.md
depends_on:
- inbox-stub-01-ticket-minting-lifecycle-authority.md
acceptance_criteria:
- Atlas exposes a PM-owned one-shot publication command for one or more explicitly named ticket keys that reuses
  the normal definition render, context-pack embedding, Linear project/team scope, join-key persistence and create-time
  mapped-state assertion semantics.
- Publication-only execution does not run delivery admission/promotion, generic Linear status pull, CI-pending reconciliation/recovery,
  completion, follow-up comment ingestion, review-cycle routing, dwell/stale-block analysis or lesson extraction.
- An already joined unchanged ticket is idempotent; an update uses the existing `external_linear_id`; a degraded
  first create retains the join key; and a create-time state-assertion failure retains the join key so retry cannot
  create a duplicate issue.
- The command requires explicit ticket selection, current schema, valid PM writer ownership and team/project/status-map
  preconditions and fails closed before external mutation when those inputs are invalid.
- The result reports per-ticket created/updated/skipped/state-assertion outcomes and issue identities without claiming
  that delivery admission, a full PM tick or agent readiness occurred.
- In-memory Linear tests prove zero admission/state-pull/follow-up side effects and include seeded create-state
  failure plus retry evidence showing exactly one Linear issue is retained.
non_goals:
- No replacement for the recurring/full `atlas pm sync` loop, no delivery-policy mutation and no generic manual
  state mover.
- No change to Symphony dispatch, CI handoff, completion or review lifecycle.
test_requirements:
- Focused PM/CLI tests exercise multi-ticket publication, unchanged retry, changed-definition update, degraded embed,
  state-assertion failure and duplicate-prevention recovery.
- A negative test proves publication-only mode cannot invoke the admission service even when a running policy would
  otherwise admit a ready ticket.
implementation_notes:
- Prefer a service boundary that reuses the existing push semantics rather than calling private sync internals from
  argparse wiring.
- A suggested CLI shape is `atlas pm publish --ticket ATLAS-N [--ticket ATLAS-M ...]`; final spelling may follow
  established CLI conventions but must require explicit ticket keys.
documentation_requirements:
- docs/atlas/pm-engine-and-linear-sync.md
- docs/atlas/playbooks/linear-sync.md
- docs/runbooks/planning-phases-and-ticket-stubs.md
definition_of_done:
- An operator can publish a batch of newly minted tickets to their mapped Linear states with delivery admission
  mechanically unreachable from that invocation.
- Retry after any documented partial publication outcome does not mint a second Linear issue for a ticket whose
  join key was retained.
---

# PM publication-only ticket seam

Governed maintenance input for the `ticket-minting-skills-v1` batch.
