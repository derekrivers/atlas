---
title: "Symphony scoped-validation and slot-release workflow"
objective: >-
  Make the repository-owned agent contract execute scoped local validation,
  publish once, enter CI-pending and release the Symphony slot without polling
  or reproducing CI inside the agent session.
context: >-
  Symphony may run up to `max_turns` back-to-back while an issue remains in an
  active state. The workflow must therefore end agent ownership through an
  explicit tracker transition, not an instruction to wait quietly. This ticket
  adopts the validation planner and CI reconciler while preserving exact-head
  evidence, current-branch safety and the prohibition on agent merge authority.
ticket_type: infrastructure
epic_ref: ATLAS-E10
risk_level: high
component: orchestration
tags:
  - phase-15-5
  - symphony
  - workflow
  - validation
relevant_docs:
  - "AGENTS.md"
  - "WORKFLOW.md"
  - "docs/atlas/parallel-delivery-efficiency-and-integration-control.md"
  - "docs/atlas/symphony-integration.md"
  - "docs/runbooks/agent-ticket-prompt.md"
  - "docs/runbooks/local-development.md"
  - "docs/runbooks/pr-acceptance.md"
depends_on:
  - "inbox-stub-01-tiered-local-validation.md"
  - "inbox-stub-03-system-tier-ci-handoff.md"
  - "ATLAS-252"
acceptance_criteria:
  - >-
    The binding workflow requires the deterministic validation plan, every
    selected local check and the ticket's explicit tests before publication,
    while a complete local sweep is required only for a named conservative
    profile or explicit operator instruction.
  - >-
    After one successful current-main rebase, scoped validation and PR publish,
    the agent records exact commands/results, moves the ticket to CI-pending and
    stops without polling CI, waiting for review or consuming another turn.
  - >-
    CI-pending is absent from `tracker.active_states`; a workflow-contract test
    proves Symphony will not continue or redispatch the issue while CI owns the
    next state edge.
  - >-
    Failed selected local checks prevent publication; failures after CI handoff
    are routed only by the system-tier reconciler and resume the preserved
    workspace through Changes Requested.
  - >-
    The contract retains current-main branch creation, exact repository/branch
    checks, scoped conflict handling, no agent merge/Done authority and
    historical-only evidence after any head change.
  - >-
    Documentation clearly distinguishes local agent confidence, CI handoff,
    Review Required acceptance and final completion evidence, with no claim
    that a shorter local run weakens CI.
non_goals:
  - >-
    No automatic merge, automatic rebase, CI cancellation, worker termination,
    hidden test skipping, model-selected validation or change to `max_turns`.
test_requirements:
  - >-
    Workflow-contract fixtures cover scoped success, conservative full sweep,
    local failure, CI-pending stop, Changes Requested resume and prohibited
    transitions.
  - >-
    Documentation-linter and prompt-contract tests reject language that makes
    local scoped checks repository-wide authority or instructs CI polling.
implementation_notes:
  - >-
    Keep the workflow body product-invariant. The agent supplies bounded
    handoff evidence, but only CI evidence may advance the ticket from
    CI-pending.
documentation_requirements:
  - "AGENTS.md"
  - "WORKFLOW.md"
  - "docs/atlas/parallel-delivery-efficiency-and-integration-control.md"
  - "docs/atlas/symphony-integration.md"
  - "docs/runbooks/agent-ticket-prompt.md"
  - "docs/runbooks/local-development.md"
  - "docs/runbooks/pr-acceptance.md"
definition_of_done:
  - >-
    All six criteria have executable workflow/prompt evidence, a seeded agent
    stops at CI-pending without an extra turn, CI remains authoritative, focused
    gates pass and the PR title carries the minted ticket key.
---

# Symphony scoped-validation and slot-release workflow

An agent hands work to CI; it does not become the CI runner.
