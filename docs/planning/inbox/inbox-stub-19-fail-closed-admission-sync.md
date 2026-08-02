---
title: "Fail-closed single-write admission integration in the PM sync tick"
objective: >-
  Replace promote-everything sync behaviour with one lease-guarded,
  revalidated admission write so stale, concurrent, partial or ambiguous
  external state cannot oversubscribe delivery.
context: >-
  The current sync promotes every dependency-ready ticket. Phase 15 must route
  that sole `Ready for Agent` writer through the deterministic admission engine
  while preserving Linear-to-Atlas status ownership and request accounting.
  A promotion tick re-reads the complete board immediately before its one
  external write; ambiguity blocks further admission until reconciliation.
ticket_type: feature
epic_ref: ATLAS-E6
risk_level: critical
component: atlas.pm
tags:
  - admission
  - linear-sync
  - concurrency
  - fail-closed
relevant_docs:
  - "docs/atlas/multi-agent-delivery-control.md"
  - "docs/atlas/pm-engine-and-linear-sync.md"
  - "docs/atlas/symphony-integration.md"
depends_on:
  - "ATLAS-46"
  - "inbox-stub-18-deterministic-admission-engine.md"
acceptance_criteria:
  - >-
    The periodic and `--once` paths acquire the same database-backed admission
    lease; a concurrent owner records a typed no-write hold and cannot evaluate
    a second promotion.
  - >-
    The tick builds an initial complete snapshot, selects at most one candidate,
    then re-pulls/re-fingerprints policy and the project board immediately
    before `set_state`; any mismatch produces zero writes.
  - >-
    The only external mutation is the selected issue's state to the uniquely
    mapped Ready for Agent ID; definition fields, other issue states and Atlas
    ticket status are untouched, and the next pull remains the Atlas writer.
  - >-
    A confirmed success records the admission outcome and successful sync
    receipt. A transport-ambiguous write marks admission indeterminate, stops
    the tick and blocks later admission until a fresh pull reconciles it.
  - >-
    Partial/malformed pagination, policy revision, lease loss, candidate status
    movement and failures before `set_state` admit nobody; retry cannot promote
    a different candidate from the stale run.
  - >-
    Changes Requested tickets remain Symphony-active, consume capacity before
    new admissions and are never demoted or delayed by an Atlas status write.
  - >-
    Sync result output reports admitted, held, over-capacity, stale,
    indeterminate and policy revision details without exposing credentials or
    raw issue bodies.
non_goals:
  - >-
    No multi-issue transaction simulation, compensating status write, agent
    cancellation, ticket demotion, scheduler ownership, review transition or
    automatic policy/ceiling change.
test_requirements:
  - >-
    Deterministic sync tests inject every race before and after revalidation,
    pagination/transport failures, concurrent leases and ambiguous success;
    external mutation spies prove zero or one exact write.
  - >-
    Existing PM pull/push/promotion/completion, Linear request-budget and
    scheduler tests remain green; seeded defects use `assert 1 == 2` (B011).
implementation_notes:
  - >-
    Replace the step-3 call site rather than adding a second promotion path.
    Preserve the dedicated `LinearClient.set_state` ownership boundary.
documentation_requirements:
  - "docs/atlas/multi-agent-delivery-control.md"
  - "docs/atlas/pm-engine-and-linear-sync.md"
  - "docs/atlas/symphony-integration.md"
definition_of_done:
  - >-
    All seven criteria have named race/failure coverage; full Python, typing,
    import and doc-linter gates pass; docs land together; the PR title carries
    the minted ticket key.
---

# Fail-closed single-write admission integration in the PM sync tick

One coherent decision may cause at most one externally visible promotion.
