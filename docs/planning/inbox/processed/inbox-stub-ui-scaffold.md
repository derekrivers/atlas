---
title: "Scaffold apps/operator-ui and strip the template's demo domains"
objective: >-
  Stand up the operator UI application at apps/operator-ui with React 19,
  TypeScript, Vite, Tailwind 4 and TanStack Router, vendoring the shadcn-admin
  shell and deleting every demo domain in the same change, so no later ticket
  can build a real view on fixture data.
context: >-
  First ticket of Phase 11; docs/atlas/operator-ui.md is the governing design.
  The upstream template is satnaing/shadcn-admin v2.2.1 (MIT). Pre-ruled
  decisions (operator-ratified, reviewer session 2026-07-26): D-1 the
  application lives at apps/operator-ui/ inside this repository (OP-1) — atlas
  is already public, and in-repo is what makes the OpenAPI drift guard a
  single CI job instead of a scheduled cross-repo check; bootstrap-guide.md
  already reserves apps/ for the phase that needs it, and this is that phase.
  D-2 the template is a component collection, not a starter: Clerk
  authentication and the users, chats, tasks, apps, help-center and settings
  demo domains, together with the @faker-js/faker dependency and every fixture
  that feeds them, are deleted in THIS change, not a follow-up. Clerk in
  particular imports precisely the authentication surface operator-api.md
  defers to the writeable phase. D-3 what is kept is the shell and the
  primitive layer: sidebar, header, theme, Radix-based ui components, the cmdk
  command palette, the TanStack Table primitives, and the error and not-found
  route shapes. D-4 upstream MIT attribution is preserved in the vendored
  source and recorded for the later open-source readiness ticket. D-5 no Atlas
  data is fetched in this ticket; routes render placeholders.
ticket_type: infrastructure
epic_ref: ATLAS-E13
risk_level: medium
component: operator-ui
relevant_docs:
- "docs/atlas/operator-ui.md"
- "docs/atlas/operator-api.md"
acceptance_criteria:
- "apps/operator-ui builds, type-checks and lints clean from a cold checkout with a documented single command, evidenced by CI output on the branch."
- "React 19, TypeScript, Vite, Tailwind 4 and TanStack Router are the resolved versions in the committed lockfile, asserted by a test that reads the lockfile rather than by inspection."
- "No Clerk package, no @faker-js/faker package, and no users, chats, tasks, apps, help-center or settings route remains in the tree; a test greps the source tree for each and fails on any hit."
- "The upstream MIT licence text and attribution are present in the vendored source."
- "No file outside apps/operator-ui/ changes except the repository-level ignore and toolchain files needed to build it, evidenced by the diff."
non_goals:
- "Read-only: no writes, no mutations, no authentication, no Linear or GitHub writes. No pagination, no bespoke error envelope, no parallel enum copies. No changes to Python domain models or storage. Do not implement or pre-empt any other queued Operator UI ticket. Never write to docs/planning/ (ADR-0007)."
test_requirements:
- "Vitest browser-mode component tests for rendering logic and a @playwright/test end-to-end spec where the ticket names one; the end-to-end suite runs against a real `atlas api serve` over a seeded store, never against mocked responses. ATLAS_LIVE_TESTS=0 for the Python gate sweep; seeded Python defects use assert 1 == 2 (B011)."
definition_of_done:
- "All acceptance criteria evidenced by named tests; the full Python gate sweep and the operator-UI pipeline both green; canonical docs updated in the same change where behaviour diverges from them; PR title carries the ticket key in the form (ATLAS-NN)."
---

# Scaffold apps/operator-ui and strip the template's demo domains

Minted from the reviewer session of 2026-07-26; the D-x decisions in
`context` are operator-ratified. Land them; do not relitigate.
