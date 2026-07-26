---
title: "Overview dashboard"
objective: >-
  Give the operator one landing screen carrying instance status, board
  composition, review depth and the head of the critical path, so the first
  question of a session is answered before any navigation.
context: >-
  Pre-ruled decisions (operator-ratified, reviewer session 2026-07-26): D-1
  every aggregate on this page is derived CLIENT-SIDE from complete
  collections, because the API has no aggregation route and no pagination. The
  ticket records that fragility explicitly: if pagination ever lands, this
  view breaks first and loudest, and that is preferable to a silent partial
  aggregate. D-2 /status gets no route of its own; its six scalars are this
  page's header plus the persistent footer indicator. D-3 the sync and
  evidence-pull timestamps render as staleness — relative age with a visible
  threshold — because an absolute timestamp does not answer the question the
  operator is asking. D-4 the page reuses the board, review-queue and
  critical-path selectors rather than reimplementing their derivations; a
  duplicated derivation is a drift timer. D-5 no chart implies a trend the
  store cannot support: there is no historical series behind any of these
  numbers, so nothing renders as a time series.
ticket_type: feature
epic_ref: ATLAS-E13
risk_level: low
component: operator-ui
relevant_docs:
- "docs/atlas/operator-ui.md"
- "docs/atlas/operator-api.md"
depends_on:
- "inbox-stub-ui-board-view.md"
- "inbox-stub-ui-review-queue-view.md"
- "inbox-stub-ui-critical-path-view.md"
acceptance_criteria:
- "The page renders ticket count, evidence count, review-queue depth and critical-path total effort, each matching its API source exactly, asserted by an end-to-end spec."
- "Status distribution is derived from the complete board and its total equals the API ticket count, asserted by test."
- "Sync and evidence-pull timestamps render as relative staleness with a visible threshold, asserted by component test over fresh and stale values."
- "Derivations are imported from the board, review-queue and critical-path modules; a duplicated derivation fails a lint rule."
- "No element renders as a time series, asserted by test."
non_goals:
- "Read-only: no writes, no mutations, no authentication, no Linear or GitHub writes. No pagination, no bespoke error envelope, no parallel enum copies. No changes to Python domain models or storage. Do not implement or pre-empt any other queued Operator UI ticket. Never write to docs/planning/ (ADR-0007)."
test_requirements:
- "Vitest browser-mode component tests for rendering logic and a @playwright/test end-to-end spec where the ticket names one; the end-to-end suite runs against a real `atlas api serve` over a seeded store, never against mocked responses. ATLAS_LIVE_TESTS=0 for the Python gate sweep; seeded Python defects use assert 1 == 2 (B011)."
definition_of_done:
- "All acceptance criteria evidenced by named tests; the full Python gate sweep and the operator-UI pipeline both green; canonical docs updated in the same change where behaviour diverges from them; PR title carries the ticket key in the form (ATLAS-NN)."
---

# Overview dashboard

Minted from the reviewer session of 2026-07-26; the D-x decisions in
`context` are operator-ratified. Land them; do not relitigate.
