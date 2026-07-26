---
title: "Ticket board view"
objective: >-
  Render the whole ticket board as a filterable, sortable, linkable table so
  the operator finds the live work in one screen instead of paging a CLI.
context: >-
  Pre-ruled decisions (operator-ratified, reviewer session 2026-07-26): D-1
  terminal statuses — done and rejected — are HIDDEN by default with a one-
  interaction reveal (OP-5). At design time 156 of 162 records are terminal; a
  default that shows all of them is a log file rather than an instrument. D-2
  ticket keys are sorted NATURALLY on the numeric segment. Storage orders by
  key lexicographically, which yields ATLAS-1, ATLAS-10, ATLAS-100 before
  ATLAS-2; a view that inherits that order is silently wrong and looks right.
  D-3 the board is fetched unfiltered ONCE and all faceting is client-side,
  because the status query parameter accepts a single value and the API has no
  pagination; the ticket records that this couples the view to complete
  collections and that pagination would break it first. D-4 filter and sort
  state is synchronised to the URL so a filtered board is a linkable artifact.
  D-5 the board renders only the six fields the projection carries; epic
  grouping is a separate ticket behind the epics route, and a kanban mode is
  deliberately out of scope for this phase.
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
- "The board renders every field of the board projection and links each row to its ticket detail route, asserted by an end-to-end spec against the seeded live API."
- "Done and rejected are excluded on first load and revealed in one interaction, asserted by an end-to-end spec over the majority-terminal seed."
- "Keys sort naturally: over a seed containing ATLAS-1, ATLAS-2, ATLAS-10 and ATLAS-100 the rendered order is numeric, asserted by test."
- "Filter and sort state round-trips through the URL: a copied URL reproduces the same view on a cold load, asserted by an end-to-end spec."
- "Exactly one board request is issued per load, asserted by an end-to-end spec that counts network calls."
non_goals:
- "Read-only: no writes, no mutations, no authentication, no Linear or GitHub writes. No pagination, no bespoke error envelope, no parallel enum copies. No changes to Python domain models or storage. Do not implement or pre-empt any other queued Operator UI ticket. Never write to docs/planning/ (ADR-0007)."
test_requirements:
- "Vitest browser-mode component tests for rendering logic and a @playwright/test end-to-end spec where the ticket names one; the end-to-end suite runs against a real `atlas api serve` over a seeded store, never against mocked responses. ATLAS_LIVE_TESTS=0 for the Python gate sweep; seeded Python defects use assert 1 == 2 (B011)."
definition_of_done:
- "All acceptance criteria evidenced by named tests; the full Python gate sweep and the operator-UI pipeline both green; canonical docs updated in the same change where behaviour diverges from them; PR title carries the ticket key in the form (ATLAS-NN)."
---

# Ticket board view

Minted from the reviewer session of 2026-07-26; the D-x decisions in
`context` are operator-ratified. Land them; do not relitigate.
