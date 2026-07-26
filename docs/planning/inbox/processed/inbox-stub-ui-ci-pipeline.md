---
title: "Operator UI CI pipeline"
objective: >-
  Wire lint, type-check, component tests, build, the OpenAPI drift guard and
  the end-to-end suite into CI as required checks, so no operator UI change
  merges without them.
context: >-
  Pre-ruled decisions (operator-ratified, reviewer session 2026-07-26): D-1
  the operator UI pipeline is additive to the existing Python gate sweep and
  does not weaken, reorder or replace any part of it; the sweep in
  docs/runbooks/local-development.md remains exactly as it is for Python
  changes. D-2 every stage is a REQUIRED check, including the OpenAPI drift
  guard and the end-to-end suite; an advisory end-to-end job is not a gate.
  D-3 the end-to-end job runs against the seeded live API from the harness
  ticket, and the Playwright browser is pinned rather than resolved at run
  time, so a browser bump is a visible commit and not a silent failure. D-4
  the runbook records the local equivalent of every CI stage so a developer
  reproduces a red check without reading CI configuration. D-5 evidence tiers
  are unchanged: CI output is system-tier under ADR-0008 and the reviewer's
  local run stays reviewer-tier.
ticket_type: infrastructure
epic_ref: ATLAS-E13
risk_level: low
component: operator-ui
relevant_docs:
- "docs/atlas/operator-ui.md"
- "docs/runbooks/local-development.md"
depends_on:
- "inbox-stub-ui-e2e-harness.md"
acceptance_criteria:
- "Lint, type-check, component tests, build, OpenAPI drift and end-to-end all run in CI on any change touching apps/operator-ui or atlas/api, and each is a required check."
- "A seeded failure in each stage independently fails the pipeline, proven stage by stage."
- "The Python gate sweep is unchanged for changes that do not touch the UI, evidenced by the workflow definition and a run on a Python-only change."
- "The Playwright browser version is pinned in the committed lockfile and asserted by test."
- "docs/runbooks/local-development.md documents the local command for every CI stage."
non_goals:
- "Read-only: no writes, no mutations, no authentication, no Linear or GitHub writes. No pagination, no bespoke error envelope, no parallel enum copies. No changes to Python domain models or storage. Do not implement or pre-empt any other queued Operator UI ticket. Never write to docs/planning/ (ADR-0007)."
test_requirements:
- "Vitest browser-mode component tests for rendering logic and a @playwright/test end-to-end spec where the ticket names one; the end-to-end suite runs against a real `atlas api serve` over a seeded store, never against mocked responses. ATLAS_LIVE_TESTS=0 for the Python gate sweep; seeded Python defects use assert 1 == 2 (B011)."
definition_of_done:
- "All acceptance criteria evidenced by named tests; the full Python gate sweep and the operator-UI pipeline both green; canonical docs updated in the same change where behaviour diverges from them; PR title carries the ticket key in the form (ATLAS-NN)."
---

# Operator UI CI pipeline

Minted from the reviewer session of 2026-07-26; the D-x decisions in
`context` are operator-ratified. Land them; do not relitigate.
