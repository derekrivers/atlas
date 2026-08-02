---
title: "Acceptance and confirmation zero-action diagnostics"
objective: >-
  Make the exact blocking acceptance check immediately visible and make a
  successful zero-confirmation pass unambiguous, so the operator never retries
  or closes work from a misleading summary.
context: >-
  Phase 12 remained fail-closed but exposed two operator-facing defects: the
  close driver hid the detailed pending check behind a generic verdict, and
  `atlas confirm` printed `Recorded 0` when no decisions remained. A stale OP-A
  explanatory note could also contradict live confirmation evidence. This
  carry-forward must land before the browser acceptance workflow reuses those
  services. The fix changes diagnostics only and must preserve exact-head,
  evidence and confirmation authority.
ticket_type: bug
epic_ref: ATLAS-E9
risk_level: medium
component: atlas.verification
tags:
  - acceptance
  - diagnostics
  - verification
  - cli
relevant_docs:
  - "docs/closure/phase-12-closure-report.md"
  - "docs/atlas/verification-engine.md"
  - "docs/runbooks/pr-acceptance.md"
depends_on:
  - "ATLAS-230"
acceptance_criteria:
  - >-
    Human close-driver output names every failed or pending verification check,
    including its typed reason and exact-head identity, instead of reporting
    only a generic pending verdict.
  - >-
    JSON output preserves the complete existing machine contract and exposes
    the same ordered blocking-check details without scraping human prose.
  - >-
    `atlas confirm` distinguishes `no outstanding confirmations` from a failed
    or skipped confirmation action; a legitimate zero-action run exits
    successfully and cannot be mistaken for missing work.
  - >-
    The stale OP-A explanatory note is removed or derived from the current
    stored evidence so it cannot claim confirmations are absent when human-tier
    records exist.
  - >-
    Diagnostics remain read-only: tests prove they do not create evidence,
    confirmation or verification rows, change ticket state, call GitHub writes
    or relax exact-head acceptance.
  - >-
    Focused CLI and close-driver tests cover one pending check, several pending
    checks, no outstanding confirmations and contradictory historical records.
non_goals:
  - >-
    No acceptance-session model, HTTP route, UI, new evidence rule, automatic
    confirmation, merge/rebase action or ticket status transition.
test_requirements:
  - >-
    Add deterministic formatter and driver tests for human and JSON output,
    with exact expected reasons and exit codes; use injected stores/clients and
    `ATLAS_LIVE_TESTS=0`.
  - >-
    Retain the Phase 12 exact-head, evidence and confirmation regression suite;
    seeded Python defects use `assert 1 == 2` (B011).
implementation_notes:
  - >-
    Consume structured verifier/confirmation results. Do not parse previously
    rendered CLI text or duplicate verification logic in the close driver.
documentation_requirements:
  - "docs/atlas/verification-engine.md"
  - "docs/runbooks/pr-acceptance.md"
definition_of_done:
  - >-
    Every acceptance criterion has named deterministic coverage; CLI contracts,
    exact-head regressions, full Python gates and doc linter are green; the PR
    title carries the minted ticket key.
---

# Acceptance and confirmation zero-action diagnostics

Fail-closed acceptance must also tell the operator exactly what remains.
