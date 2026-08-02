---
title: "Exact-head acceptance-session evidence pull action"
objective: >-
  Pull and summarise canonical GitHub/CI evidence for an acceptance session's
  pinned PR head without allowing external movement, retries or partial failure
  to advance the session incorrectly.
context: >-
  Atlas already has an evidence-pull path and append-only, exact-commit
  evidence. Phase 14 must call that domain service rather than spawn the CLI or
  reimplement GitHub mapping. Pre-ruled decisions: D-1 the action accepts only
  a session ID and authenticated command context; repository, PR and head come
  from the session. D-2 it proves session freshness immediately before and
  after the bounded external pull. D-3 evidence stays in the canonical evidence
  store; the session records a bounded summary and receipt only. D-4 source
  idempotency plus the Phase 13 gateway prevents duplicate evidence or step
  advancement. D-5 a moved/indeterminate PR stales the session; transport or
  source failure does not advance it and may be retried with a new action key
  after refresh. D-6 raw payloads and GitHub credentials never enter the
  session or presentation.
ticket_type: feature
epic_ref: ATLAS-E8
risk_level: high
component: atlas.evidence
tags:
  - evidence
  - acceptance
  - exact-head
  - github
  - idempotency
relevant_docs:
  - "docs/atlas/review-acceptance-console.md"
  - "docs/atlas/evidence-pipeline.md"
  - "docs/decisions/0008-ci-sourced-evidence-with-trust-tiers.md"
depends_on:
  - "inbox-stub-08-acceptance-session-model.md"
acceptance_criteria:
  - >-
    One API-independent action resolves repository, PR, close-set and pinned
    head solely from the session, rejects a non-`preflight_passed`/retryable
    session and accepts no caller-supplied SHA, repository, ticket key or token.
  - >-
    The action runs the shared session freshness evaluator immediately before
    external work; any stale/indeterminate result performs no evidence pull and
    returns every typed reason.
  - >-
    The existing evidence-pull service is invoked directly with injected
    clients and preserves its conditional requests, bounded rate-limit
    handling, trust-tier mapping, pin-triple and source-idempotency rules; no
    CLI subprocess or duplicated mapper exists.
  - >-
    After the pull, a fresh assessment must still match the session. Movement
    marks the session stale; evidence already stored at the old head remains
    append-only history and cannot mark a new session or head evidence-ready.
  - >-
    Success advances exactly once to `evidence_ready` and stores only counts,
    trust/status/pin completeness, source timestamps and the operator-action
    receipt; raw payloads, tokens, job logs and unbounded errors are absent.
  - >-
    Same-key replay invokes neither GitHub nor the evidence service again;
    concurrent calls cause one bounded pull/advance; altered replay conflicts
    without external work.
  - >-
    Transport, authentication, rate-limit and malformed-source failures are
    distinct non-advancing outcomes. After a status refresh proves the session
    still fresh, the operator may retry with a new action key; no failure is
    silently converted to evidence-ready.
non_goals:
  - >-
    No HTTP route, browser UI, operator confirmation, verification, merge
    readiness, evidence deletion/edit, unbounded CI polling, background worker,
    GitHub write, Linear write or PR merge.
test_requirements:
  - >-
    Deterministic service tests use injected assessment/evidence fakes to cover
    success, no-op source replay, head/main movement before and after pull,
    every transport/source failure, duplicate/concurrent calls and receipt
    rollback with `ATLAS_LIVE_TESTS=0`.
  - >-
    Regression tests prove old-head evidence cannot satisfy a new session and
    raw payload/token canaries never enter session or receipt; seeded defects
    use `assert 1 == 2` (B011).
implementation_notes:
  - >-
    Extract a callable application service from the existing CLI path if one is
    not already available. Do not parse `atlas evidence pull` output.
  - >-
    Keep the external call bounded. A future asynchronous job protocol must be
    designed separately rather than leaked through partial session states.
documentation_requirements:
  - "docs/atlas/review-acceptance-console.md"
  - "docs/atlas/evidence-pipeline.md"
definition_of_done:
  - >-
    All seven acceptance criteria have named tests; evidence and exact-commit
    regressions, full Python gates and doc linter are green; canonical docs
    land in the same change; the PR title carries the minted ticket key.
---

# Exact-head acceptance-session evidence pull action

Evidence is pulled for one pinned head and never promoted across movement.
