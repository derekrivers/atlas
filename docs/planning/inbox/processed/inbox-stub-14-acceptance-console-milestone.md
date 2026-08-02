---
title: "Acceptance console security, concurrency and live-API milestone"
objective: >-
  Prove Phase 14 end to end against a seeded exact-head PR, including movement,
  replay, cross-tab, external-failure and accessibility cases, while
  mechanically demonstrating that Atlas never performs the merge.
context: >-
  Phase 14 spans GitHub reads, canonical evidence and verification stores,
  operator confirmations, exact-head freshness, authenticated commands and a
  browser state machine. Focused ticket tests cannot alone prove the ordering
  at those seams. Pre-ruled decisions: D-1 the success milestone uses the built
  UI and live FastAPI/store with deterministic injected GitHub/CI fixtures.
  D-2 it reaches `merge_ready=true` only after preflight, evidence,
  confirmations and PASSED verification at one pinned head, then proves no
  merge/external mutation. D-3 seed head/main movement at each seam, criteria
  drift, old-head records, missing human gates, non-PASSED verification,
  timeout, duplicate/altered replay, cross-tab transition and receipt failure.
  After PASSED, movement or GitHub read failure before the next GET must revoke
  current readiness without rewriting stored history. D-4 every block returns
  all reasons and zero unintended mutation. D-5 the
  Phase 13 adversarial/session suite remains binding. D-6 closure reconciles
  roadmap/design/runbooks and records residual manual-merge race; no Phase 15
  feature enters.
ticket_type: infrastructure
epic_ref: ATLAS-E13
risk_level: high
component: operator-ui
tags:
  - acceptance
  - security
  - concurrency
  - playwright
  - exact-head
relevant_docs:
  - "docs/atlas/review-acceptance-console.md"
  - "docs/atlas/governed-operator-actions.md"
  - "docs/runbooks/pr-acceptance.md"
  - "docs/atlas/symphony-integration.md"
depends_on:
  - "inbox-stub-06-writable-surface-acceptance.md"
  - "inbox-stub-13-review-acceptance-console-ui.md"
acceptance_criteria:
  - >-
    A seeded live-API/browser milestone creates an exact-head session, pulls
    evidence, confirms every live criterion and manual approval, records
    explicit PASSED verification and reaches `merge_ready=true` for the exact
    displayed head.
  - >-
    GitHub/Git/client spies and repository assertions prove the successful
    milestone performs no PR merge, branch/rebase/push, Linear write, ticket
    transition, Symphony action, schema upgrade or PM sync.
  - >-
    PR-head and main/base movement are seeded before evidence, after evidence,
    before confirmation, before verification and after PASSED. Post-PASSED
    movement before the subsequent GET returns current `merge_ready=false` with
    all reasons while stored verification history remains unchanged; earlier
    stale/blocked results prevent later steps or old-session reuse.
  - >-
    Criteria drift, old-head evidence/confirmations/verdict, missing human
    gates and every non-PASSED verification class are exercised through the
    real API and UI; none can produce merge readiness.
  - >-
    Duplicate click, same/altered replay, two-tab concurrent transition,
    external timeout/malformed response and receipt/store failure prove one
    action owner, bounded recovery, no partial step advance and zero unaudited
    success. A GitHub failure after PASSED but before the next GET returns
    `merge_ready=false` with typed reasons and performs no session mutation.
  - >-
    Phase 13 hostile-Origin/CSRF/session/secret-redaction cases pass unchanged
    for every Phase 14 POST; token, CSRF, raw evidence and unbounded error
    canaries are absent from retained browser/API artifacts.
  - >-
    Keyboard, focus, announcement, matrix readability, long identity values and
    responsive suites pass, and Phase 14 canonical docs/route inventories agree
    on the delivered workflow, manual-merge boundary, synchronous limitation
    and residual freeze-to-manual-merge race.
non_goals:
  - >-
    No new feature, PR merge/rebase, post-merge completion, remote deployment,
    penetration-test certification, asynchronous jobs, multi-user approval or
    relaxation of Phase 12/13 gates.
test_requirements:
  - >-
    Extend the seeded live harness with deterministic GitHub assessment,
    evidence and verification fixtures, fault injection, multiple browser
    contexts and external-mutation spies. Retained traces/screenshots must be
    secret-free.
  - >-
    Run full Python gates with `ATLAS_LIVE_TESTS=0`, UI lint/type/test/build,
    Playwright, accessibility, OpenAPI/client drift and doc linter; seeded
    Python defects use `assert 1 == 2` (B011).
implementation_notes:
  - >-
    Use production services behind injected boundaries; do not add production
    bypass flags, direct SQL state fabrication after setup or client-only
    success shortcuts.
  - >-
    Keep assertions on authoritative observables: session/status responses,
    canonical repositories and external call spies. A green screenshot alone
    is not milestone evidence.
documentation_requirements:
  - "ROADMAP.md"
  - "docs/atlas/implementation-roadmap.md"
  - "docs/atlas/review-acceptance-console.md"
  - "docs/atlas/operator-api.md"
  - "docs/atlas/operator-ui.md"
  - "docs/runbooks/pr-acceptance.md"
  - "docs/atlas/symphony-integration.md"
  - "docs/closure/phase-14-closure-report.md"
definition_of_done:
  - >-
    All seven criteria pass in CI and the operator-run milestone; no secret
    artifact or unintended external write exists; Phase 14 closure evidence
    and canonical docs land together; full gates are green; the PR title
    carries the minted ticket key.
---

# Acceptance console security, concurrency and live-API milestone

Phase 14 closes only when the complete acceptance spine is exact-head,
auditable, adversarially tested and still merge-manual.
