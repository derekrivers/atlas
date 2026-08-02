---
title: "Acceptance-session criteria confirmation and manual approval action"
objective: >-
  Let the authenticated operator confirm every live acceptance criterion and
  the manual approval gate for one pinned session head without accepting
  caller-authored criteria or stale definitions.
context: >-
  `atlas confirm` and the verification evaluators already define the human-tier
  gate. Phase 14 must reuse those semantics while moving the interaction into a
  governed session. Pre-ruled decisions: D-1 the UI/server request identifies
  criteria only by session-bound stable index plus the pinned criteria
  fingerprint; it cannot submit replacement criterion text, head SHA or actor.
  D-2 every criterion and the manual approval must be explicitly true; partial
  confirmation is a validation result, not step success. D-3 the server
  re-reads live ticket definitions and exact-head freshness before writing.
  Drift stales the session and writes no confirmation. D-4 confirmations use
  the existing human-tier append-only records pinned to the session head and
  server-owned `human/operator` actor. D-5 idempotency and receipt atomicity
  come from the Phase 13 gateway. D-6 the action does not run verification.
ticket_type: feature
epic_ref: ATLAS-E9
risk_level: high
component: atlas.verification
tags:
  - confirmation
  - acceptance
  - operator
  - exact-head
  - verification
relevant_docs:
  - "docs/atlas/review-acceptance-console.md"
  - "docs/atlas/verification-engine.md"
  - "docs/runbooks/pr-acceptance.md"
  - "docs/decisions/0009-single-operator-governance.md"
depends_on:
  - "ATLAS-84"
  - "inbox-stub-01-operator-session-security.md"
  - "inbox-stub-08-acceptance-session-model.md"
acceptance_criteria:
  - >-
    One API-independent action accepts a session ID, the exact pinned criteria
    fingerprint, a complete set of stable criterion indexes and explicit
    manual approval; request types contain no criterion text, actor,
    repository, ticket key or head SHA.
  - >-
    The action requires every criterion in every close-set ticket exactly once
    and manual approval true; missing, duplicate, unknown or extra indexes and
    a mismatched fingerprint fail validation with no confirmation or session
    advance.
  - >-
    Immediately before writing, the service re-reads the ticket definitions
    and shared exact-head freshness. Criteria/head/base/eligibility drift
    atomically stales the session and writes no human-tier record.
  - >-
    Success delegates to the existing confirmation domain service and persists
    the evaluator-compatible acceptance-criterion confirmations and
    `MANUAL_APPROVAL` at the pinned head with server-resolved
    `human/operator` attribution.
  - >-
    Confirmation records, the `confirmations_ready` session advance and action
    receipt commit atomically. A seeded write/receipt failure leaves no partial
    set that the verification evaluator can treat as complete.
  - >-
    Same-key replay returns the original outcome without duplicate human-tier
    records; concurrent or altered replays cannot double-advance or replace the
    criterion set.
  - >-
    Existing CLI confirmation output/evaluator behaviour remains compatible
    and delegates to the same underlying service or record writer; no parallel
    browser-only human-gate model is introduced.
non_goals:
  - >-
    No HTTP route, UI, criterion editing/waiver, partial save, verification,
    merge readiness, GitHub review/approval, GitHub write, Linear write, PR
    merge or client-supplied actor.
test_requirements:
  - >-
    Service/repository tests cover complete success, missing/duplicate/extra
    criteria, fingerprint drift, head/main drift, concurrent replay, partial
    write/receipt failure and CLI/evaluator compatibility with
    `ATLAS_LIVE_TESTS=0`.
  - >-
    Canary tests prove submitted text/actor/head fields are impossible or
    rejected and old-head confirmations cannot satisfy a new session; seeded
    defects use `assert 1 == 2` (B011).
implementation_notes:
  - >-
    Use a deterministic criteria identity derived from close-set ticket key and
    stored list index. Do not depend on browser display order alone.
  - >-
    Keep operator intent explicit; do not infer confirmation from opening a
    drawer, checking a subset or a prior session at another head.
documentation_requirements:
  - "docs/atlas/review-acceptance-console.md"
  - "docs/atlas/verification-engine.md"
  - "docs/runbooks/pr-acceptance.md"
definition_of_done:
  - >-
    All seven acceptance criteria have named tests; confirmation/evaluator
    regressions, full Python gates and doc linter are green; canonical docs
    land in the same change; the PR title carries the minted ticket key.
---

# Acceptance-session criteria confirmation and manual approval action

Human approval is explicit, exact-head and bound to the criteria actually
reviewed.
