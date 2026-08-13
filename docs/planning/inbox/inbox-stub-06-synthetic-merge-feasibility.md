---
title: "Exact-base synthetic-merge evidence feasibility spike"
objective: >-
  Determine whether Atlas can bind GitHub's clean integration candidate and CI
  evidence to one exact PR head and current base strongly enough to avoid an
  unnecessary contributor-branch rebase without weakening exact-head safety.
context: >-
  The current acceptance contract requires the PR branch itself to contain
  current `main`, so every sibling merge makes trailing branches stale even
  when they merge cleanly. GitHub may expose a synthetic merge candidate and CI
  identity for an exact head/base pair, but its availability, stability,
  evidence mapping and squash-merge relationship must be proven before Atlas
  changes authority. This is a read-only feasibility spike with a governed
  PASS/FAIL report.
ticket_type: spike
epic_ref: ATLAS-E10
risk_level: high
component: integration
tags:
  - phase-15-5
  - github
  - exact-head
  - rebase
relevant_docs:
  - "docs/atlas/evidence-pipeline.md"
  - "docs/atlas/parallel-delivery-efficiency-and-integration-control.md"
  - "docs/atlas/review-acceptance-console.md"
  - "docs/atlas/symphony-integration.md"
  - "docs/runbooks/pr-acceptance.md"
depends_on:
  - "ATLAS-230"
  - "ATLAS-244"
acceptance_criteria:
  - >-
    A disposable repository harness records the exact PR head, current base,
    provider integration/merge candidate identity, tree or commit identity,
    required CI checks and live mergeability without mutating a production PR.
  - >-
    Repeated reads of an unchanged clean head/base pair are stable enough to
    reconstruct one candidate, while head movement, base movement, conflict,
    missing candidate and provider ambiguity each invalidate it explicitly.
  - >-
    The spike proves whether every required CI result can be attributed to the
    exact integration candidate rather than merely the contributor head or an
    unpinned branch ref.
  - >-
    Seeded sibling-main movement proves old candidate evidence cannot remain
    authoritative for the new base, even when the contributor head does not
    change.
  - >-
    The report compares clean merge, merge commit, squash merge and conflicted
    cases and states the exact fallback to the existing operator rebase lane.
  - >-
    The spike performs no branch publication, force push, rebase, merge, Linear
    transition, acceptance confirmation or production authority change.
non_goals:
  - >-
    No implementation of acceptance changes, no automatic merge/rebase, no
    provider-general abstraction and no conclusion based solely on documented
    GitHub behaviour without executable evidence.
test_requirements:
  - >-
    Deterministic provider fixtures and the disposable harness cover stable,
    moved, missing, conflicted, malformed and indeterminate candidate cases.
  - >-
    Mutation spies prove the spike is read-only and retained evidence excludes
    credentials, raw provider payloads and unbounded responses.
implementation_notes:
  - >-
    Record a binary PASS/FAIL decision and the exact identity algebra required
    by the next ticket. A FAIL leaves the existing rebase lane authoritative
    and blocks no-rewrite activation until the phase design is amended.
documentation_requirements:
  - "docs/atlas/parallel-delivery-efficiency-and-integration-control.md"
  - "docs/atlas/review-acceptance-console.md"
  - "docs/atlas/symphony-integration.md"
  - "docs/runbooks/pr-acceptance.md"
definition_of_done:
  - >-
    All six criteria have executable evidence, the report reaches an explicit
    PASS or FAIL without production mutation, retained artifacts are bounded,
    focused gates pass and the PR title carries the minted ticket key.
---

# Exact-base synthetic-merge evidence feasibility spike

Avoiding a rebase is safe only when the tested integration identity is exact.
