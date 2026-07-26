---
title: "Accessibility and responsive pass"
objective: >-
  Make every delivered view keyboard-navigable, screen-reader-legible and
  usable at laptop and tablet widths, and hold that with an automated check
  rather than a one-off review.
context: >-
  Pre-ruled decisions (operator-ratified, reviewer session 2026-07-26): D-1
  accessibility is enforced by an automated check wired into CI as a required
  stage, not by a manual audit; a manual pass decays by the next ticket. D-2
  the pass covers every view delivered by this phase and both colour modes,
  since contrast is a per-mode property and passing in light says nothing
  about dark. D-3 the target is keyboard reachability of every interactive
  element, correct semantics for the data tables and the tab frames, visible
  focus, and contrast that meets the standard the check enforces; the standard
  is named in the change rather than left implicit. D-4 responsive scope is
  laptop and tablet widths — the operator surface is a desktop instrument and
  phone layouts are out of scope for this phase. D-5 fixes land in this
  ticket; where a fix would change a view's agreed behaviour rather than its
  presentation, it is reported as a follow-up instead of absorbed silently.
ticket_type: tech_debt
epic_ref: ATLAS-E13
risk_level: low
component: operator-ui
relevant_docs:
- "docs/atlas/operator-ui.md"
- "docs/atlas/operator-api.md"
depends_on:
- "inbox-stub-ui-overview-dashboard.md"
- "inbox-stub-ui-ticket-evidence-tab.md"
- "inbox-stub-ui-ticket-dependencies-tab.md"
- "inbox-stub-ui-lessons-view.md"
- "inbox-stub-ui-epic-grouping.md"
- "inbox-stub-ui-dependency-graph-view.md"
acceptance_criteria:
- "An automated accessibility check runs over every delivered view in both colour modes as a required CI stage, and a seeded violation fails it."
- "Every interactive element is reachable and operable by keyboard with visible focus, asserted by an end-to-end spec that traverses each view by keyboard alone."
- "Data tables and tab frames expose correct roles and labels, asserted by test."
- "Every view is usable without horizontal scrolling at the named laptop and tablet widths, asserted by end-to-end specs at those viewports."
- "The enforced accessibility standard is named in the repository documentation."
non_goals:
- "Read-only: no writes, no mutations, no authentication, no Linear or GitHub writes. No pagination, no bespoke error envelope, no parallel enum copies. No changes to Python domain models or storage. Do not implement or pre-empt any other queued Operator UI ticket. Never write to docs/planning/ (ADR-0007)."
test_requirements:
- "Vitest browser-mode component tests for rendering logic and a @playwright/test end-to-end spec where the ticket names one; the end-to-end suite runs against a real `atlas api serve` over a seeded store, never against mocked responses. ATLAS_LIVE_TESTS=0 for the Python gate sweep; seeded Python defects use assert 1 == 2 (B011)."
definition_of_done:
- "All acceptance criteria evidenced by named tests; the full Python gate sweep and the operator-UI pipeline both green; canonical docs updated in the same change where behaviour diverges from them; PR title carries the ticket key in the form (ATLAS-NN)."
---

# Accessibility and responsive pass

Minted from the reviewer session of 2026-07-26; the D-x decisions in
`context` are operator-ratified. Land them; do not relitigate.
