---
title: "Three-to-five-to-seven-to-ten delivery-control milestone"
objective: >-
  Prove the complete Phase 15 admission and review-pressure system under a
  controlled live workload before authorising the ten-agent ceiling.
context: >-
  Focused tests cannot prove the operational relationship between Atlas
  admission, Linear state, Symphony occupancy and Phase 14 acceptance
  throughput. The milestone uses more than ten independent tickets and moves
  one governed level at a time. Any breach stops at the last proven ceiling;
  Atlas never merges, cancels workers or rewrites policy on its own. Phase 15
  remains open below ten. Committed `main` stays at three while the operator
  advances `WORKFLOW.md` on a dedicated milestone branch through 5, 7 and 10.
  Only after the ten-agent gate succeeds does this milestone/closure change
  commit and merge `max_concurrent_agents: 10` to `main`.
ticket_type: infrastructure
epic_ref: ATLAS-E10
risk_level: critical
component: orchestration
tags:
  - phase-15
  - milestone
  - symphony
  - admission
  - acceptance
relevant_docs:
  - "WORKFLOW.md"
  - "docs/atlas/multi-agent-delivery-control.md"
  - "docs/atlas/review-acceptance-console.md"
  - "docs/atlas/symphony-integration.md"
  - "docs/runbooks/pr-acceptance.md"
depends_on:
  - "inbox-stub-14-acceptance-console-milestone.md"
  - "inbox-stub-21-delivery-control-ui.md"
  - "inbox-stub-22-symphony-capacity-ramp-contract.md"
acceptance_criteria:
  - >-
    A controlled live fixture with more than ten independent tickets records
    exact policy, board, sync/admission, Symphony and acceptance evidence at
    ceilings 3, 5, 7 and 10. The operator changes `WORKFLOW.md` on the dedicated
    milestone branch for each next level only when the prior gate passes;
    committed `main` remains at three throughout the exercise.
  - >-
    At every level working, review, risk, component and rework-reserve occupancy
    remains within policy; a full review budget stops new admission while
    Changes Requested work remains dispatchable and unstarved.
  - >-
    Candidate choice is reproduced from the recorded snapshot and policy, and
    every non-selected ticket has complete typed hold reasons visible through
    the API and UI.
  - >-
    Paused and draining exercises admit nobody and preserve active tickets,
    agents and workspaces; returning to running requires an explicit
    operator-attributed policy revision.
  - >-
    Seeded stale board/policy, concurrent tick, pagination/partial-read,
    ambiguous single write and duplicate/altered policy command cases never
    admit an unselected ticket or allow a second admission before reconciliation.
  - >-
    Phase 14 exact-head acceptance is exercised at the higher ceilings and the
    recorded review throughput/pressure must support ten. If any gate fails,
    the last proven milestone-branch ceiling is restored or retained, the
    failure is recorded honestly, Phase 15 remains open and no ceiling change
    merges to `main`; closure below ten is prohibited.
  - >-
    External and repository assertions prove Atlas performs no GitHub merge or
    rebase, Linear review-state mutation, Symphony scheduling/cancellation,
    plan approval, permission expansion or deployment. After the ten-agent gate
    passes, the milestone/closure change commits and merges `WORKFLOW.md` at
    exactly `max_concurrent_agents: 10`; intermediate branch values never merge
    independently.
non_goals:
  - >-
    No Phase 16 scoring/analytics, automatic model choice, autoscaling,
    multi-product allocation, merge queue, remote deployment or relaxation of
    Phase 13/14 security and evidence gates.
test_requirements:
  - >-
    Extend the seeded live harness with deterministic Linear/Symphony fault
    injection, multiple tick owners and external-mutation spies; retained
    evidence must contain no credentials or raw secret-bearing payloads.
  - >-
    Run full Python, API/UI, OpenAPI/client, Playwright, accessibility,
    workflow-contract and doc-linter gates plus the operator-controlled live
    exercise.
implementation_notes:
  - >-
    Capture exact identities and timestamps at each level. A screenshot or
    configured value alone is not proof of bounded occupancy or review
    throughput. Preserve the dedicated milestone branch and exact
    `WORKFLOW.md` value in the evidence for every gate.
documentation_requirements:
  - "WORKFLOW.md"
  - "ROADMAP.md"
  - "docs/atlas/implementation-roadmap.md"
  - "docs/atlas/multi-agent-delivery-control.md"
  - "docs/atlas/review-acceptance-console.md"
  - "docs/atlas/symphony-integration.md"
  - "docs/runbooks/pr-acceptance.md"
  - "docs/runbooks/operator-environment.md"
  - "docs/closure/phase-15-closure-report.md"
definition_of_done:
  - >-
    All seven criteria pass in CI and the controlled operator exercise; exact
    evidence proves ten; `WORKFLOW.md` lands at `max_concurrent_agents: 10` with
    Phase 15 closure and canonical docs; the PR title carries the minted ticket
    key. Any lower result leaves the phase open.
---

# Three-to-five-to-seven-to-ten delivery-control milestone

Scale advances only as fast as admission and human acceptance can remain safe.
