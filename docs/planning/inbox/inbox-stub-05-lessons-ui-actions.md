---
title: "Lessons UI promote and reject workflow"
objective: >-
  Let the authenticated operator rule on DRAFT lessons from the existing
  Lessons drawer with explicit confidence, confirmation and honest handling of
  replay, expiry and stale-state conflicts.
context: >-
  ATLAS-217 delivered the read-only Lessons draft triage view and ATLAS-221
  delivered the query/proxy/error primitives. This ticket completes the visible
  human gate without moving domain rules into React. Pre-ruled decisions:
  D-1 the shell gains a login/session-expiry flow backed by the Phase 13
  session routes; the bootstrap token is submitted once and is never stored in
  localStorage/sessionStorage, a URL, query cache or generated configuration.
  D-2 Promote is available only for DRAFT and requires finite confidence plus
  confirmation that ACTIVE lessons may enter future context packs. Reject is
  available only for DRAFT and confirms archival. D-3 one stable idempotency
  key is retained for the in-flight command and safe retry; the UI never
  invents a new key after an ambiguous response without first refreshing.
  D-4 success consumes the server lesson/receipt, updates query state and
  removes the row when the filter requires. D-5 409 refreshes and displays the
  safe current state; the UI never overwrites or silently retries. D-6 no
  edit/merge/archive/rebase/merge-PR affordance appears.
ticket_type: feature
epic_ref: ATLAS-E13
risk_level: medium
component: operator-ui
tags:
  - operator-ui
  - lessons
  - authentication
  - accessibility
  - governance
relevant_docs:
  - "docs/atlas/governed-operator-actions.md"
  - "docs/atlas/operator-ui.md"
  - "docs/atlas/learning-system.md"
depends_on:
  - "ATLAS-217"
  - "ATLAS-219"
  - "ATLAS-221"
  - "inbox-stub-04-lesson-mutation-api.md"
acceptance_criteria:
  - >-
    The application presents an accessible login/session-expired flow, submits
    the bootstrap token only to the session endpoint, holds the returned CSRF
    token in memory, and stores neither token in web storage, URL state, query
    cache, logs nor generated assets.
  - >-
    A DRAFT lesson drawer exposes Promote and Reject; non-DRAFT lessons expose
    neither. Promote requires a labelled finite `0.0..1.0` confidence value
    and a confirmation explaining future context-pack eligibility; Reject
    requires a distinct archival confirmation.
  - >-
    Every submission sends credentials, CSRF header, strict JSON and one
    generated idempotency key; controls disable while in flight and a safe
    retry of an ambiguous response reuses that key until the session state is
    refreshed.
  - >-
    Success renders the server-returned lesson/receipt, invalidates or updates
    the exact query keys, and removes the lesson from a DRAFT-only queue
    without a full-page reload; no client-side lifecycle inference supplies
    the new status.
  - >-
    A 401 returns to the session flow without retaining the bootstrap
    credential; 403 names the security refusal; 409 shows the current safe
    lesson, prevents overwrite and requires explicit re-review; 422 attaches a
    validation error to the relevant control.
  - >-
    Keyboard-only use, focus trap/return, destructive confirmation, labelled
    confidence input, disabled/busy state and success/error announcements pass
    the established automated accessibility checks.
  - >-
    No lesson edit, merge, ACTIVE archive, PR merge/rebase, Linear write or
    generic resource update affordance, client or query mutation exists in the
    delivered bundle.
non_goals:
  - >-
    No design-system rewrite, remote login, remember-me, role UI, lesson
    editing/merging/archiving, bulk disposition, optimistic state transition,
    GitHub write, Linear write or acceptance console.
test_requirements:
  - >-
    Component/query tests cover login, expiry, promote/reject validation,
    confirmation, in-flight idempotency, success cache updates and every typed
    error with generated API types only.
  - >-
    Playwright tests against a seeded live API cover promote, reject, stale CLI
    race, duplicate-click prevention, refresh/session loss, keyboard flow and
    forbidden token persistence; Python live calls remain disabled outside the
    established seeded harness.
implementation_notes:
  - >-
    Extend the existing query/mutation primitives; do not hand-write duplicate
    API enums or response shapes. Treat server responses as authority.
  - >-
    Generate idempotency keys through the browser crypto API and retain them
    only for the command lifecycle. Never place them or the CSRF token in URL
    state.
documentation_requirements:
  - "docs/atlas/operator-ui.md"
definition_of_done:
  - >-
    All seven acceptance criteria have named component and browser evidence;
    UI lint/type/test/build, generated-client drift, Python gates,
    accessibility and doc linter are green; canonical UI docs land in the same
    change; the PR title carries the minted ticket key.
---

# Lessons UI promote and reject workflow

The existing draft queue becomes Atlas's first honest browser control.
