---
title: "Ticket detail: evidence tab"
objective: >-
  Render one ticket's stored evidence with the system pin-triple state made
  unmissable, so the operator sees at a glance whether a ticket can close
  under ADR-0008.
context: >-
  Pre-ruled decisions (operator-ratified, reviewer session 2026-07-26): D-1
  has_system_pin_triple is rendered as a PROMINENT first-class state, not a
  trailing boolean column. Under ADR-0008 that flag is the difference between
  evidence that can close a ticket and evidence that cannot, and the Phase 10
  incident ledger is largely a record of tickets stranded on exactly that
  distinction. D-2 the evidence projection exposes no raw payload by design
  and the view never implies one exists; there is no expand-to-payload
  affordance. D-3 stored order is oldest-first and is preserved; the view does
  not re-sort. D-4 trust tier is rendered with its canonical actor-type value,
  and an agent-tier record is visually distinguishable from a system-tier one,
  because agent-submitted evidence is PENDING until corroborated. D-5 a ticket
  with no evidence renders the shared empty state, which is a normal condition
  and not an error.
ticket_type: feature
epic_ref: ATLAS-E13
risk_level: low
component: operator-ui
relevant_docs:
- "docs/atlas/operator-ui.md"
- "docs/atlas/operator-api.md"
depends_on:
- "inbox-stub-ui-ticket-detail.md"
acceptance_criteria:
- "Every evidence record renders with type, trust tier, status and pin-triple state, in the API's oldest-first order, asserted by an end-to-end spec."
- "The pin-triple state is rendered prominently and is distinguishable at a glance between complete and incomplete, asserted by a component test over both cases."
- "Agent-tier and system-tier records are visually distinguishable, asserted by test."
- "A ticket with no evidence renders the shared empty state and not an error state, asserted against the seed."
- "No affordance in the tab suggests a raw payload is retrievable, asserted by a test over interactive elements."
non_goals:
- "Read-only: no writes, no mutations, no authentication, no Linear or GitHub writes. No pagination, no bespoke error envelope, no parallel enum copies. No changes to Python domain models or storage. Do not implement or pre-empt any other queued Operator UI ticket. Never write to docs/planning/ (ADR-0007)."
test_requirements:
- "Vitest browser-mode component tests for rendering logic and a @playwright/test end-to-end spec where the ticket names one; the end-to-end suite runs against a real `atlas api serve` over a seeded store, never against mocked responses. ATLAS_LIVE_TESTS=0 for the Python gate sweep; seeded Python defects use assert 1 == 2 (B011)."
definition_of_done:
- "All acceptance criteria evidenced by named tests; the full Python gate sweep and the operator-UI pipeline both green; canonical docs updated in the same change where behaviour diverges from them; PR title carries the ticket key in the form (ATLAS-NN)."
---

# Ticket detail: evidence tab

Minted from the reviewer session of 2026-07-26; the D-x decisions in
`context` are operator-ratified. Land them; do not relitigate.
