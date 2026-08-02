---
title: "Writable-surface security, accessibility and live-API acceptance"
objective: >-
  Prove the complete Phase 13 browser-to-store write boundary under hostile,
  concurrent and failure conditions before Atlas treats governed UI actions as
  operationally ready.
context: >-
  Authentication, receipts, domain transitions, routes and UI each have
  focused tests, but Phase 13's risk exists at their seams. This ticket owns
  the release-level adversarial and milestone proof rather than adding another
  feature. Pre-ruled decisions: D-1 run the built UI against a seeded live
  FastAPI/store process, never request mocks for the milestone. D-2 prove
  promote and reject outcomes, actor attribution, atomic receipts and
  ACTIVE-only retrieval. D-3 actively seed hostile Origin/Host, CSRF failure,
  strict-content-type bypass, token/actor injection, duplicate and altered
  replay, expired/revoked session, two-tab/CLI race and receipt failure. D-4
  every rejected path asserts zero unintended domain or external mutation and
  secret-free output. D-5 accessibility, responsive and generated-client
  drift remain binding. D-6 close the phase docs with residual risks; do not
  implement Phase 14.
ticket_type: infrastructure
epic_ref: ATLAS-E13
risk_level: high
component: operator-ui
tags:
  - security
  - acceptance
  - playwright
  - accessibility
  - operator-ui
relevant_docs:
  - "docs/atlas/governed-operator-actions.md"
  - "docs/atlas/operator-api.md"
  - "docs/atlas/operator-ui.md"
  - "docs/runbooks/operator-environment.md"
depends_on:
  - "ATLAS-209"
  - "ATLAS-212"
  - "ATLAS-215"
  - "inbox-stub-05-lessons-ui-actions.md"
acceptance_criteria:
  - >-
    A seeded live-API Playwright milestone logs in, reads the full lesson,
    promotes one DRAFT with confidence, rejects another, and proves stored
    ACTIVE/ARCHIVED states, `human/operator` receipts and ACTIVE-only context
    retrieval without direct database mutation by the test.
  - >-
    Hostile Origin/Host, missing/wrong CSRF, simple-form or wrong content type,
    unauthenticated, expired and revoked session cases are exercised through
    the real HTTP stack and each proves zero lesson/action success.
  - >-
    Duplicate submission, same-key replay, altered replay, two-browser-tab
    race and an injected CLI-versus-browser race prove exactly one domain
    transition and one success receipt, with typed replay/conflict UX.
  - >-
    An injected receipt/store commit failure proves the lesson remains DRAFT
    and no success is returned; restart/retry behaviour cannot infer or create
    an unaudited success.
  - >-
    Canary credentials, CSRF values and actor-injection payloads are absent
    from browser storage, URLs, screenshots/traces retained by CI, API/UI logs,
    response errors, receipts and generated assets.
  - >-
    Keyboard, focus, announcement, contrast and responsive suites pass for
    login, confirmation, busy, success and every error state on the established
    viewport matrix.
  - >-
    Phase 13 canonical docs agree on delivered routes, threat controls,
    supported loopback topology, residual loopback-HTTP risk and non-goals;
    executable route inventory confirms no other write exists.
non_goals:
  - >-
    No new product behaviour, acceptance console, penetration-test claim,
    remote deployment, HTTPS termination, multi-user auth, bulk actions,
    GitHub write, Linear write or PR merge.
test_requirements:
  - >-
    Extend the established seeded live harness with isolated databases,
    deterministic credentials, fault injection and browser contexts. Tests
    clean only their named fixtures and retain no secrets in failure artifacts.
  - >-
    Run full Python gates with `ATLAS_LIVE_TESTS=0`, UI lint/type/test/build,
    Playwright, accessibility, OpenAPI/client drift and doc linter; seeded
    Python defects use `assert 1 == 2` (B011).
implementation_notes:
  - >-
    Prefer test-only dependency injection/fault seams already established by
    the tickets. Do not add production bypass flags or weaken session policy to
    make browser setup easier.
  - >-
    Assert observables through API/UI/store repository reads, not raw SQL
    rewrites. Keep every failure case explicit enough to diagnose the violated
    invariant.
documentation_requirements:
  - "docs/atlas/governed-operator-actions.md"
  - "docs/atlas/operator-api.md"
  - "docs/atlas/operator-ui.md"
  - "docs/runbooks/operator-environment.md"
  - "ROADMAP.md"
  - "docs/closure/phase-13-closure-report.md"
definition_of_done:
  - >-
    All seven criteria pass in CI and the operator-run seeded milestone; no
    secret artifact is retained; Phase 13 closure evidence and canonical docs
    land together; full gates are green; the PR title carries the minted ticket
    key.
---

# Writable-surface security, accessibility and live-API acceptance

Phase 13 closes only when the complete write seam fails safely under attack,
race and audit failure.
