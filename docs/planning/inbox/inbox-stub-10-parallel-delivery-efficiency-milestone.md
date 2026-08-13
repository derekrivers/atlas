---
title: "Parallel-delivery efficiency and integration milestone"
objective: >-
  Prove that the Phase 15.5 operating model increases accepted delivery flow
  without duplicated full-suite work, stranded Symphony turns, unbounded CI or
  review queues, unsafe integration shortcuts or avoidable rebases.
context: >-
  Focused tests cannot prove that the new workflow works under concurrent
  delivery. The milestone runs a controlled independent workload before
  ATLAS-253's live ceiling ramp. It compares the current operating model with
  the Phase 15.5 model using predeclared windows and thresholds. Passing this
  milestone is the explicit operator gate for releasing ATLAS-253 from Needs
  Human; it does not itself execute or close the Phase 15 concurrency ramp.
ticket_type: infrastructure
epic_ref: ATLAS-E10
risk_level: critical
component: orchestration
tags:
  - phase-15-5
  - milestone
  - throughput
  - integration
relevant_docs:
  - "WORKFLOW.md"
  - "ROADMAP.md"
  - "docs/atlas/implementation-roadmap.md"
  - "docs/atlas/multi-agent-delivery-control.md"
  - "docs/atlas/parallel-delivery-efficiency-and-integration-control.md"
  - "docs/runbooks/local-development.md"
  - "docs/runbooks/pr-acceptance.md"
depends_on:
  - "inbox-stub-04-symphony-slot-release-workflow.md"
  - "inbox-stub-05-protected-integration-lanes.md"
  - "inbox-stub-07-no-rewrite-exact-base-acceptance.md"
  - "inbox-stub-09-integration-pressure-console.md"
  - "ATLAS-252"
acceptance_criteria:
  - >-
    Before execution the operator records fixed observation windows, workload
    independence evidence and numerical PASS/FAIL thresholds for agent active
    time, local validation, CI queue/run time, duplicated complete sweeps,
    integration occupancy, review dwell, conflicts, rebases and accepted flow.
  - >-
    A controlled workload proves agents execute their deterministic scoped
    plans, publish once, enter CI-pending and release Symphony slots; no agent
    polls CI or repeats a complete suite for an unchanged SHA without a recorded
    conservative-profile reason.
  - >-
    System-tier CI alone advances CI-pending work, every failure/indeterminate
    class routes as designed, and no queue exceeds its active operator policy.
  - >-
    Protected lanes prevent concurrent ownership of seeded hotspots while
    independent work remains admissible, with reproducible holds and no
    unintended Linear promotion.
  - >-
    Clean exact-base candidates complete acceptance without branch rewrite;
    seeded conflict, base/head movement and provider ambiguity route safely to
    rebase-required or indeterminate with no stale authority.
  - >-
    Repository and external-call spies prove no automatic merge/rebase/push,
    worker cancellation, CI mutation, plan approval, permission expansion,
    deployment or secret-bearing retained evidence.
  - >-
    Phase 15.5 closes only when every threshold and authority invariant passes;
    a lower or ambiguous result records FAIL, leaves ATLAS-253 in Needs Human
    and does not change the committed Symphony ceiling.
non_goals:
  - >-
    No ATLAS-253 live ramp, no change to `max_concurrent_agents`, no Phase 16
    comparative model scoring, no automatic merge queue, autoscaling or claim
    that arbitrary interacting PRs are safe.
test_requirements:
  - >-
    Run the complete deterministic Python/API/UI/workflow/documentation matrix
    in CI plus a seeded live delivery exercise with CI, Linear, Symphony and
    GitHub fault injection.
  - >-
    Retain bounded receipts for every metric/threshold and adversarial case;
    credential canaries, raw provider responses and workspace secrets must be
    absent.
implementation_notes:
  - >-
    Compare accepted completed flow rather than PR count or worker utilisation.
    Predeclare thresholds before the run and do not tune them after observing
    results. The operator releases ATLAS-253 only after the closure report is
    reviewed and merged.
documentation_requirements:
  - "WORKFLOW.md"
  - "ROADMAP.md"
  - "docs/atlas/implementation-roadmap.md"
  - "docs/atlas/multi-agent-delivery-control.md"
  - "docs/atlas/parallel-delivery-efficiency-and-integration-control.md"
  - "docs/runbooks/local-development.md"
  - "docs/runbooks/pr-acceptance.md"
  - "docs/closure/phase-15.5-closure-report.md"
definition_of_done:
  - >-
    All seven criteria pass with complete bounded receipts, Phase 15.5 closure
    and canonical docs land together, ATLAS-253 remains operator-gated until
    that merge, no Symphony ceiling change occurs and the PR title carries the
    minted ticket key.
---

# Parallel-delivery efficiency and integration milestone

Concurrency succeeds only when more work is accepted with less duplicated effort.
