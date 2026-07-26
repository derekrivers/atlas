---
title: "Lessons view with draft triage"
objective: >-
  Render stored lessons with drafts first, so the operator can work the DRAFT
  pile that ADR-0009 makes their queue.
context: >-
  Pre-ruled decisions (operator-ratified, reviewer session 2026-07-26): D-1
  DRAFT is the default filter. Agent-authored lessons are DRAFT until the
  operator promotes them (ADR-0009), so the draft pile is the operator's
  actual queue; ATLAS-204's incident — 45 fabricated DRAFT lessons produced by
  a bookkeeping status change — is the concrete case this view exists to make
  visible. D-2 lessons carry source_ticket_id and related_ticket_ids as raw
  UUIDs, and operator-api.md rules that resolving them to ticket keys would
  require a second source and move the whole projection into
  atlas.orchestration. The UUIDs are therefore displayed LITERALLY and are not
  linked. Fabricating a link would hide a contract gap; showing the UUID keeps
  it visible and fixable. D-3 the view is read-only: no promote, reject,
  archive or merge control, because `atlas lessons review` is itself read-only
  and the writeable phase owns those actions. D-4 problem, solution and
  outcome render in full in a detail drawer, untruncated, because a summarised
  lesson cannot be judged. D-5 status faceting is client-side over the full
  collection, consistent with every other view in this phase.
ticket_type: feature
epic_ref: ATLAS-E13
risk_level: low
component: operator-ui
relevant_docs:
- "docs/atlas/operator-ui.md"
- "docs/atlas/operator-api.md"
depends_on:
- "inbox-stub-ui-ci-pipeline.md"
acceptance_criteria:
- "Lessons render with category, title, status, confidence, tags, creator and timestamps, and open a drawer carrying problem, solution and outcome in full, asserted by an end-to-end spec."
- "DRAFT is the default filter on first load, and every EntityStatus is reachable by facet, asserted by test."
- "Ticket references render as literal UUIDs and are not presented as links, asserted by a test over interactive elements."
- "No promote, reject, archive or merge control exists in any state, asserted by test."
- "An empty lesson set renders the shared empty state."
non_goals:
- "Read-only: no writes, no mutations, no authentication, no Linear or GitHub writes. No pagination, no bespoke error envelope, no parallel enum copies. No changes to Python domain models or storage. Do not implement or pre-empt any other queued Operator UI ticket. Never write to docs/planning/ (ADR-0007)."
test_requirements:
- "Vitest browser-mode component tests for rendering logic and a @playwright/test end-to-end spec where the ticket names one; the end-to-end suite runs against a real `atlas api serve` over a seeded store, never against mocked responses. ATLAS_LIVE_TESTS=0 for the Python gate sweep; seeded Python defects use assert 1 == 2 (B011)."
definition_of_done:
- "All acceptance criteria evidenced by named tests; the full Python gate sweep and the operator-UI pipeline both green; canonical docs updated in the same change where behaviour diverges from them; PR title carries the ticket key in the form (ATLAS-NN)."
---

# Lessons view with draft triage

Minted from the reviewer session of 2026-07-26; the D-x decisions in
`context` are operator-ratified. Land them; do not relitigate.
