---
title: "Exact-head verification and manual-merge readiness evaluator"
objective: >-
  Run canonical verification for one acceptance session and emit
  `merge_ready` only when the PASSED verdict, live PR and current main still
  match every identity pinned by the session.
context: >-
  ATLAS-230 makes exact current-main freshness and acceptance restart binding
  in the CLI spine. Phase 14 must consume the same services and cannot weaken
  their ordering. Pre-ruled decisions: D-1 verification is available only
  after evidence and confirmations are ready. D-2 it invokes the canonical
  verification engine in process and requires explicit top-level PASSED with a
  valid `head_commit` equal to the session head. D-3 immediately afterward a
  fresh ATLAS-228 assessment and live criteria fingerprint must match the
  session's repository, PR, branch, head, base and criteria. D-4
  `merge_ready=true` is a typed session result only when every gate passes; all
  blocking reasons are preserved. D-5 one bounded, read-only live-readiness
  service re-evaluates readiness for every later GET and revokes it on
  movement, indeterminate assessment or external-read failure. D-6 verification
  may persist its historical exact-head result and receipt; the later
  live-readiness service is strictly non-mutating. Neither service writes to
  GitHub or Linear, and Atlas only displays authority for a manual GitHub
  action.
ticket_type: feature
epic_ref: ATLAS-E9
risk_level: high
component: atlas.verification
tags:
  - verification
  - acceptance
  - exact-head
  - merge-readiness
  - orchestration
relevant_docs:
  - "docs/atlas/review-acceptance-console.md"
  - "docs/atlas/verification-engine.md"
  - "docs/runbooks/pr-acceptance.md"
  - "docs/decisions/0008-ci-sourced-evidence-with-trust-tiers.md"
depends_on:
  - "ATLAS-230"
  - "inbox-stub-09-acceptance-evidence-action.md"
  - "inbox-stub-10-acceptance-confirmation-action.md"
acceptance_criteria:
  - >-
    The action refuses before verification unless the session is fresh and
    both evidence and confirmation steps are complete for the pinned head;
    every failing prerequisite is returned and no verifier call occurs.
  - >-
    The canonical verification engine is invoked in process over the session
    close-set/PR. Only explicit top-level PASSED with a valid `head_commit`
    exactly equal to the pinned session head is accepted; exit code, CI green
    or a stale stored verdict is insufficient.
  - >-
    Immediately after PASSED, a fresh shared integration assessment must be
    overall current and match repository, PR, head/base refs and SHAs from the
    session; live criteria must reproduce the pinned fingerprint.
  - >-
    The evaluator returns and stores every typed blocking reason across
    evidence, confirmations, verification, head/base movement, eligibility,
    criteria drift and indeterminate external state; it does not collapse to a
    first failure.
  - >-
    `merge_ready=true` is persisted only with the verified head, verdict ID,
    final assessment identity and receipt in one transaction. Receipt/store
    failure returns no readiness success.
  - >-
    A bounded read-only live-readiness application service combines stored
    session history with a fresh shared Phase 12 assessment and current
    criteria fingerprint. Any later head/main/repository/eligibility/criteria
    movement, indeterminate assessment, timeout, malformed response or other
    external-read failure returns `merge_ready=false` with every typed reason,
    performs no store write and never presents cached true as authority.
  - >-
    Tests and executable client spies prove the operation performs no GitHub
    mutation/merge, Linear write, ticket status transition, Git command,
    schema upgrade or PM sync.
non_goals:
  - >-
    No HTTP route, UI, automatic merge, rebase, conflict resolution,
    post-merge proof, schema migration, PM sync, Changes Requested transition,
    Symphony resume, background worker or merge queue.
test_requirements:
  - >-
    Deterministic evaluator/action tests cover every prerequisite and verdict
    class, verified-head mismatch, head/main/criteria races before and after
    verification, all-reasons presentation, replay and receipt rollback with
    injected fakes and `ATLAS_LIVE_TESTS=0`. After PASSED, movement and GitHub
    failure before a subsequent live-readiness call must close the gate.
  - >-
    Regression tests prove old-head evidence/confirmations/verdicts cannot
    produce readiness and external mutation spies remain untouched; seeded
    defects use `assert 1 == 2` (B011).
implementation_notes:
  - >-
    Reuse ATLAS-230's in-process assessment and acceptance-ordering services;
    do not call or parse `scripts/close_ticket.py` or CLI JSON.
  - >-
    Expose the bounded live-readiness operation for the API GET to call once.
    Separate stored milestone result from current readiness: a previously
    reached state is history, and live movement or read failure closes the gate
    without mutating that history.
documentation_requirements:
  - "docs/atlas/review-acceptance-console.md"
  - "docs/atlas/verification-engine.md"
  - "docs/runbooks/pr-acceptance.md"
  - "docs/atlas/symphony-integration.md"
definition_of_done:
  - >-
    All seven acceptance criteria have named tests; verification/acceptance
    regressions, full Python gates and doc linter are green; canonical docs
    agree in the same change; the PR title carries the minted ticket key.
---

# Exact-head verification and manual-merge readiness evaluator

The console may advise a manual merge only for the exact head it verified.
