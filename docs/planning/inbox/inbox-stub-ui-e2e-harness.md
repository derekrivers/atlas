---
title: "Playwright end-to-end harness over a seeded live API"
objective: >-
  Stand up @playwright/test against a real `atlas api serve` process bound to
  loopback over a seeded store, so every later view ticket has a place to
  prove itself against what Atlas actually returns.
context: >-
  Pre-ruled decisions (operator-ratified, reviewer session 2026-07-26): D-1
  end-to-end specs run against a LIVE API over a seeded store, not against
  recorded fixtures or route mocking (OP-4). Fixture replay verifies the UI
  against its own assumptions; it would have caught none of the three data-
  shape facts that most affect these views — a board that is 94 per cent
  terminal, lexicographic ticket-key ordering, and lesson references that are
  UUIDs with no resolvable key. D-2 the seed is deterministic and committed,
  and it deliberately reproduces those three shapes plus an empty review
  queue, a ticket with no evidence, and a ticket that is not ready with more
  than one failing readiness reason. D-3 the template's existing Vitest
  browser-mode setup is COMPONENT testing and is retained unchanged;
  @playwright/test is a separate runner, a separate config and a separate
  command, and the two are never conflated — a passing component suite is not
  acceptance. D-4 the harness starts and stops the API process itself so a
  developer runs one command; a hand-started server is not a prerequisite. D-5
  no view is authored here; the harness ships with one smoke spec proving the
  shell loads against the live API.
ticket_type: infrastructure
epic_ref: ATLAS-E13
risk_level: medium
component: operator-ui
relevant_docs:
- "docs/atlas/operator-ui.md"
- "docs/atlas/operator-api.md"
depends_on:
- "inbox-stub-ui-app-shell.md"
acceptance_criteria:
- "One documented command seeds a store, starts `atlas api serve` on loopback, runs the end-to-end suite against it, and tears the process down, evidenced by CI output."
- "The committed seed reproduces a majority-terminal board, non-lexicographic key ordering, an empty review queue, a ticket with no evidence, and a ticket with more than one failing readiness reason, asserted by a test over the seeded store."
- "The component suite and the end-to-end suite are separate commands with separate configs; a test fails if the end-to-end runner is invoked through the component runner or vice versa."
- "A smoke spec loads the shell against the live API and reaches every route."
- "The end-to-end suite fails when the API returns an unexpected shape, proven by a seeded schema divergence."
non_goals:
- "Read-only: no writes, no mutations, no authentication, no Linear or GitHub writes. No pagination, no bespoke error envelope, no parallel enum copies. No changes to Python domain models or storage. Do not implement or pre-empt any other queued Operator UI ticket. Never write to docs/planning/ (ADR-0007)."
test_requirements:
- "Vitest browser-mode component tests for rendering logic and a @playwright/test end-to-end spec where the ticket names one; the end-to-end suite runs against a real `atlas api serve` over a seeded store, never against mocked responses. ATLAS_LIVE_TESTS=0 for the Python gate sweep; seeded Python defects use assert 1 == 2 (B011)."
definition_of_done:
- "All acceptance criteria evidenced by named tests; the full Python gate sweep and the operator-UI pipeline both green; canonical docs updated in the same change where behaviour diverges from them; PR title carries the ticket key in the form (ATLAS-NN)."
---

# Playwright end-to-end harness over a seeded live API

Minted from the reviewer session of 2026-07-26; the D-x decisions in
`context` are operator-ratified. Land them; do not relitigate.
