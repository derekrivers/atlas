---
title: "Coherent delivery occupancy and review-pressure snapshot"
objective: >-
  Derive one immutable, fingerprinted view of working, review and lane
  occupancy from a complete Linear pull and current Atlas state before any
  ticket can be considered for admission.
context: >-
  Separate budgets are meaningful only if they are calculated from one
  coherent observation. Working occupancy includes dispatchable/active/rework
  states; review occupancy includes Review Required and Needs Human. Unknown,
  incomplete or contradictory external state must make the snapshot unusable
  for writes rather than silently reduce the count.
ticket_type: feature
epic_ref: ATLAS-E6
risk_level: high
component: atlas.pm
tags:
  - admission
  - occupancy
  - review-pressure
  - linear-sync
relevant_docs:
  - "docs/atlas/multi-agent-delivery-control.md"
  - "docs/atlas/pm-engine-and-linear-sync.md"
  - "docs/atlas/dependency-engine.md"
depends_on:
  - "ATLAS-36"
  - "inbox-stub-15-successful-sync-receipt.md"
  - "inbox-stub-16-delivery-admission-policy.md"
acceptance_criteria:
  - >-
    Working occupancy counts Ready for Agent, In Progress, PR Open and Changes
    Requested; review occupancy separately counts Review Required and Needs
    Human, using configured state IDs rather than display-name guesses.
  - >-
    Each active ticket consumes all matching risk and component lanes, and
    Changes Requested occupancy is identified separately for reserve
    calculation.
  - >-
    The snapshot pins product/project, policy revision, status-map fingerprint,
    fetched-board fingerprint, Atlas graph/store revision inputs and observation
    time in a canonical deterministic representation.
  - >-
    Pagination gaps, duplicate issue identities, unmapped/contradictory states,
    missing joined issues or an incomplete pull produce typed incompleteness
    reasons and `admission_allowed=false`.
  - >-
    Over-budget existing occupancy is reported with every breached dimension;
    snapshot construction performs no Linear write, ticket mutation, demotion
    or Symphony action.
  - >-
    Given identical policy, board and Atlas state, snapshot counts, reasons and
    fingerprint are byte-stable regardless of source iteration order.
non_goals:
  - >-
    No ranking, promotion, policy change, scheduler replacement, historical
    metrics, prediction or automatic capacity adjustment.
test_requirements:
  - >-
    Table-driven tests cover every workflow state, combined lanes, reserve,
    over-capacity, pagination/identity failures and order-independent
    fingerprinting with injected clocks.
  - >-
    Existing Linear status-map and request-budget suites remain green; seeded
    defects use `assert 1 == 2` (B011).
implementation_notes:
  - >-
    Reuse the existing project-scoped pull and canonical status map. Do not add
    a workspace-wide query or join by title/identifier.
documentation_requirements:
  - "docs/atlas/multi-agent-delivery-control.md"
  - "docs/atlas/pm-engine-and-linear-sync.md"
definition_of_done:
  - >-
    All six criteria have named deterministic coverage; Python, typing, import
    and doc-linter gates pass; docs land with code; the PR title carries the
    minted ticket key.
---

# Coherent delivery occupancy and review-pressure snapshot

Admission decisions use one complete observation or make no write.
