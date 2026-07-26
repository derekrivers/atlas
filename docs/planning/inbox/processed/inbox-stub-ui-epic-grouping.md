---
title: "Epic grouping on the ticket board"
objective: >-
  Group and filter the board by epic, so a 162-ticket board is navigable along
  the dimension the roadmap is actually organised by.
context: >-
  Depends on the epics route and the epic_key board field, which is why it is
  a separate ticket from the board itself. Pre-ruled decisions (operator-
  ratified, reviewer session 2026-07-26): D-1 grouping is a board MODE, not a
  new route; the flat table remains the default and the URL carries the mode
  so a grouped board is linkable like any other board state. D-2 the terminal-
  status default and natural key sort established by the board ticket apply
  unchanged inside every group; a grouping mode that quietly resets the
  defaults is a different view wearing the board's name. D-3 tickets with no
  epic collect under an explicit unassigned group rather than disappearing.
  D-4 epic metadata shown per group is limited to what the epics route
  returns; nothing is inferred, and per-group progress is counted from the
  board rows already fetched rather than requested separately. D-5 no epic
  write, no reassignment, no drag-and-drop.
ticket_type: feature
epic_ref: ATLAS-E13
risk_level: low
component: operator-ui
relevant_docs:
- "docs/atlas/operator-ui.md"
- "docs/atlas/operator-api.md"
depends_on:
- "inbox-stub-ui-board-view.md"
- "inbox-stub-api-epics-read.md"
acceptance_criteria:
- "The board offers a grouped-by-epic mode reachable in one interaction, with the mode carried in the URL, asserted by an end-to-end spec."
- "The terminal-status default and natural key sort hold inside every group, asserted by test."
- "Tickets with no epic render under an explicit unassigned group and are never dropped; group counts sum to the filtered row count, asserted by test."
- "Per-group counts are derived from already-fetched rows; no additional request per group is issued, asserted by an end-to-end spec that counts network calls."
- "No control mutates an epic or a ticket's epic membership."
non_goals:
- "Read-only: no writes, no mutations, no authentication, no Linear or GitHub writes. No pagination, no bespoke error envelope, no parallel enum copies. No changes to Python domain models or storage. Do not implement or pre-empt any other queued Operator UI ticket. Never write to docs/planning/ (ADR-0007)."
test_requirements:
- "Vitest browser-mode component tests for rendering logic and a @playwright/test end-to-end spec where the ticket names one; the end-to-end suite runs against a real `atlas api serve` over a seeded store, never against mocked responses. ATLAS_LIVE_TESTS=0 for the Python gate sweep; seeded Python defects use assert 1 == 2 (B011)."
definition_of_done:
- "All acceptance criteria evidenced by named tests; the full Python gate sweep and the operator-UI pipeline both green; canonical docs updated in the same change where behaviour diverges from them; PR title carries the ticket key in the form (ATLAS-NN)."
---

# Epic grouping on the ticket board

Minted from the reviewer session of 2026-07-26; the D-x decisions in
`context` are operator-ratified. Land them; do not relitigate.
