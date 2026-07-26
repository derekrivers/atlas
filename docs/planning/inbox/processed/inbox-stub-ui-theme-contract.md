---
title: "Theme token contract from the vendored theme.css"
objective: >-
  Establish the vendored theme.css as the single design-token contract for the
  operator UI, so a token change is a one-file change and no component carries
  a hardcoded colour, radius or font value.
context: >-
  Pre-ruled decisions (operator-ratified, reviewer session 2026-07-26): D-1
  the shadcn-admin theme IS the Atlas operator UI theme (OP-7); there is no
  separate operator-supplied palette, and src/styles/theme.css is vendored
  intact rather than rewritten. D-2 the contract is falsifiable and must be
  enforced mechanically, not by review: a test replaces the token file with a
  deliberately divergent set and asserts that no component source file
  required a change, and a second test fails on any hardcoded colour literal
  (hex, rgb, hsl, oklch) or raw radius value outside the token file. D-3 light
  and dark modes are both first-class; the toggle persists across reloads and
  honours the operating-system preference on first visit. D-4 this ticket
  introduces no view and no data fetching.
ticket_type: feature
epic_ref: ATLAS-E13
risk_level: low
component: operator-ui
relevant_docs:
- "docs/atlas/operator-ui.md"
- "docs/atlas/operator-api.md"
depends_on:
- "inbox-stub-ui-scaffold.md"
acceptance_criteria:
- "Every design token consumed by the application resolves from the single vendored token file; a test fails on any hardcoded colour or radius literal in component sources."
- "Swapping the token file for a divergent set changes the rendered theme with zero component-file edits, asserted by test."
- "Light and dark modes both render every primitive legibly, and the mode selection persists across a reload, asserted by an end-to-end spec."
- "First visit with no stored preference follows the operating-system colour-scheme preference, asserted by test."
non_goals:
- "Read-only: no writes, no mutations, no authentication, no Linear or GitHub writes. No pagination, no bespoke error envelope, no parallel enum copies. No changes to Python domain models or storage. Do not implement or pre-empt any other queued Operator UI ticket. Never write to docs/planning/ (ADR-0007)."
test_requirements:
- "Vitest browser-mode component tests for rendering logic and a @playwright/test end-to-end spec where the ticket names one; the end-to-end suite runs against a real `atlas api serve` over a seeded store, never against mocked responses. ATLAS_LIVE_TESTS=0 for the Python gate sweep; seeded Python defects use assert 1 == 2 (B011)."
definition_of_done:
- "All acceptance criteria evidenced by named tests; the full Python gate sweep and the operator-UI pipeline both green; canonical docs updated in the same change where behaviour diverges from them; PR title carries the ticket key in the form (ATLAS-NN)."
---

# Theme token contract from the vendored theme.css

Minted from the reviewer session of 2026-07-26; the D-x decisions in
`context` are operator-ratified. Land them; do not relitigate.
