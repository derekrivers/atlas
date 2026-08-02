---
title: "Durable exact-head acceptance session and status projection"
objective: >-
  Represent one PR acceptance attempt as an immutable-head, durable state
  machine so evidence, confirmations and verification can never be mixed across
  PR heads, main revisions or changing acceptance criteria.
context: >-
  Phase 12's ATLAS-228 supplies one exact-head integration assessment and Phase
  13 supplies the action/idempotency ledger. Phase 14 needs a coordinating
  resource before it exposes any acceptance write. Pre-ruled decisions: D-1 a
  session pins repository, PR, close-set, head/base SHA and refs, exact-head
  assessment, live criteria fingerprint and `human/operator` actor. D-2 session
  creation performs read-only exact-head preflight before evidence,
  confirmation or approval writes and succeeds only for an open, non-draft,
  same-repository PR targeting main, current with exact main, whose close-set
  is entirely `review_required`. D-3 the state machine is append-oriented;
  head/base/repository/eligibility/criteria movement observed by a mutation
  marks the session stale; a later read reports movement without rewriting
  history. A new head requires a new session, and an old session is never
  retargeted.
  D-4 one non-terminal session may exist per repository/PR/head; idempotent
  creation replays it. D-5 the stored status projection reports historical
  step states and reasons without external reads or writes; it is an input to,
  not a substitute for, the later bounded live-readiness projection. D-6 this
  ticket adds no evidence, confirmation, verification or HTTP route.
ticket_type: infrastructure
epic_ref: ATLAS-E10
risk_level: high
component: orchestration
tags:
  - acceptance
  - exact-head
  - orchestration
  - state-machine
  - audit
relevant_docs:
  - "docs/atlas/review-acceptance-console.md"
  - "docs/atlas/symphony-integration.md"
  - "docs/runbooks/pr-acceptance.md"
  - "docs/decisions/0008-ci-sourced-evidence-with-trust-tiers.md"
depends_on:
  - "ATLAS-188"
  - "ATLAS-228"
  - "inbox-stub-02-operator-action-ledger.md"
acceptance_criteria:
  - >-
    A migration and canonical model add an acceptance session with stable ID,
    repository/PR, close-set, initial exact-head assessment, pinned head/base
    identities and SHAs, criteria fingerprint, operator actor, lifecycle/step
    summaries, typed blocking reasons and timestamps; pinned identity fields
    cannot be updated after creation.
  - >-
    The creation service calls the shared ATLAS-228 assessment and fails before
    any acceptance-domain write unless the PR is open, non-draft,
    same-repository, targets literal main, is overall current and resolves to a
    close-set wholly in `review_required`.
  - >-
    Creation snapshots current ticket acceptance criteria in deterministic
    close-set/key/index order and stores a canonical fingerprint; cached UI
    text or caller-supplied criterion content cannot become the snapshot.
  - >-
    One non-terminal session is allowed per repository/PR/head. Same
    idempotency command replays the existing session; concurrent creation
    yields one record; a different head creates a new record only after the old
    session is terminal/stale.
  - >-
    A reusable pure freshness comparator compares a live assessment and live
    criteria to the pinned session and returns every typed head/base/ref/
    repository/eligibility/criteria mismatch; indeterminate external state
    never counts as fresh. Mutation callers atomically transition the session
    to terminal stale, while read callers leave stored history unchanged.
  - >-
    The pure stored-status projection returns pinned identity, lifecycle, step
    summaries, receipts and historical readiness reasons without invoking
    GitHub, evidence pull, confirmation, verification or a store write; stored
    `merge_ready` is explicitly not current merge authority.
  - >-
    Behind/diverged/conflicted preflight returns the named Phase 12 rebase
    recovery command as bounded diagnostic data but creates no session;
    draft/fork/non-main/closed/unknown/indeterminate cases remain distinct and
    secret-free.
non_goals:
  - >-
    No HTTP route, evidence pull, confirmation, verification, merge readiness,
    Git rebase, GitHub write, Linear write, PR merge, automatic session
    retargeting, background job or browser UI.
test_requirements:
  - >-
    Migration/repository/service tests cover every eligibility class,
    close-set state, deterministic criteria fingerprint, concurrent creation,
    same/different-head creation and all freshness mismatches with injected
    GitHub/ticket fakes and `ATLAS_LIVE_TESTS=0`.
  - >-
    Append/immutability tests reject pinned-identity edits and stored-projection
    tests prove zero external calls/writes and no historical readiness result
    is labelled current authority; seeded defects use `assert 1 == 2` (B011).
implementation_notes:
  - >-
    Store structured pinned fields, not a raw GitHub payload. Reuse the typed
    ATLAS-228 assessment and existing close-set parser; do not parse CLI text.
  - >-
    Keep the pure stored projection separate from the bounded live-readiness
    service added by the verification ticket. The later GET composes them
    explicitly and remains non-mutating; there is no hidden background polling.
documentation_requirements:
  - "docs/architecture/data-model-and-schemas.md"
  - "docs/atlas/review-acceptance-console.md"
  - "docs/atlas/symphony-integration.md"
definition_of_done:
  - >-
    All seven acceptance criteria have named tests; migration, full Python and
    doc-linter gates are green; canonical schema/design docs land together; the
    PR title carries the minted ticket key.
---

# Durable exact-head acceptance session and status projection

One durable record prevents acceptance evidence from drifting across heads.
