---
title: "Deterministic capacity-aware admission decision engine"
objective: >-
  Convert dependency-ready candidates, policy and one coherent snapshot into
  reproducible admit/hold decisions with complete typed reasons and at most one
  selected external promotion.
context: >-
  Phase 3 readiness remains the eligibility authority. Phase 15 adds a pure
  policy decision after it, protecting working/review budgets, Changes
  Requested reserve and risk/component lanes. Linear has no batch transaction,
  so one evaluation may select at most one write while still explaining every
  candidate.
ticket_type: feature
epic_ref: ATLAS-E6
risk_level: high
component: atlas.pm
tags:
  - admission
  - deterministic
  - capacity
  - explainability
relevant_docs:
  - "docs/atlas/multi-agent-delivery-control.md"
  - "docs/atlas/dependency-engine.md"
  - "docs/atlas/pm-engine-and-linear-sync.md"
depends_on:
  - "ATLAS-35"
  - "inbox-stub-17-delivery-occupancy-snapshot.md"
acceptance_criteria:
  - >-
    Candidates come exclusively from the existing readiness predicate; the
    admission layer never weakens acceptance-criteria, ADR, dependency or
    status eligibility.
  - >-
    Stable ranking follows unlock count, critical-path membership/position,
    priority, risk, continuously-eligible age and natural ticket key in the
    documented order, with no model or Linear-list-order input.
  - >-
    Paused/draining mode, incomplete snapshot, full review/working budget,
    Changes Requested reserve and every matched lane each produce explicit
    typed hold reasons; all reasons are retained.
  - >-
    A run selects zero or one candidate only, never a multi-issue write batch,
    and simulates the selection against all budgets before returning `admit`.
  - >-
    An append-only admission run pins policy/snapshot fingerprints and records
    every considered candidate, rank inputs, decision and reasons without raw
    Linear payloads.
  - >-
    Identical inputs produce byte-identical ordering and decisions; source
    iteration order, timestamps outside the injected clock and random UUIDs do
    not affect the decision.
non_goals:
  - >-
    No Linear write, API/UI, Symphony dispatch, learned scoring, throughput
    prediction, policy mutation or ticket demotion.
test_requirements:
  - >-
    Pure decision tests exhaust ranking tie-breaks, combined budget/lane holds,
    reserve behaviour, incomplete snapshots and zero/one selection.
  - >-
    Property tests vary input order and prove determinism plus no budget
    oversubscription; seeded defects use `assert 1 == 2` (B011).
implementation_notes:
  - >-
    Keep calculation side-effect free. Persist the returned run in the PM
    orchestration layer, not inside ranking functions.
documentation_requirements:
  - "docs/atlas/multi-agent-delivery-control.md"
  - "docs/atlas/pm-engine-and-linear-sync.md"
  - "docs/atlas/dependency-engine.md"
definition_of_done:
  - >-
    All six criteria have named deterministic/property coverage; full Python,
    typing, import and doc-linter gates pass; docs land together; the PR title
    carries the minted ticket key.
---

# Deterministic capacity-aware admission decision engine

Readiness identifies possibility; policy decides whether work enters now.
