---
title: "Operator delivery-control and admission-explanation UI"
objective: >-
  Give the operator a clear control panel for working/review capacity, policy
  revisions and admit/hold reasons while keeping every delivery action and
  authority boundary server-owned.
context: >-
  Phase 15 needs an instrument, not an autonomous optimiser. The UI consumes
  generated delivery-control schemas, displays ceilings as limits rather than
  targets and submits explicit full-policy revisions through the Phase 13
  mutation primitive. It never changes a ticket status or controls Symphony.
ticket_type: feature
epic_ref: ATLAS-E13
risk_level: high
component: operator-ui
tags:
  - operator-ui
  - admission
  - capacity
  - explainability
relevant_docs:
  - "docs/atlas/multi-agent-delivery-control.md"
  - "docs/atlas/operator-ui.md"
  - "docs/atlas/governed-operator-actions.md"
depends_on:
  - "ATLAS-209"
  - "ATLAS-219"
  - "ATLAS-220"
  - "ATLAS-221"
  - "inbox-stub-20-delivery-control-api.md"
acceptance_criteria:
  - >-
    The control view shows approved ceiling, mode, policy revision, truthful
    sync freshness and used/available working, review, risk, component and
    Changes Requested reserve capacity from generated API types.
  - >-
    Eligible tickets display deterministic rank inputs and all admitted/held,
    stale, over-capacity or indeterminate reasons without recomputing a server
    decision.
  - >-
    A complete policy form validates obvious bounds, requires an explicit
    summary confirmation and submits `expected_revision` with a fresh
    idempotency key through the shared authenticated mutation client.
  - >-
    Stale revision, altered replay, session expiry, security failure and API
    unavailability each preserve the entered proposal, show an accessible
    recovery state and never silently retry.
  - >-
    Paused and draining modes explain that active work is preserved; executable
    control-inventory tests prove there is no promote, demote, cancel, dispatch,
    terminate, merge, rebase or automatic-ramp control.
  - >-
    Loading/refetch keeps the last truthful server snapshot visibly stale until
    replacement and never presents a client clock or optimistic policy as
    authoritative.
  - >-
    Keyboard flow, focus/confirmation handling, announcements, dense reason
    lists, long component names and established responsive viewports pass
    accessibility and layout tests.
non_goals:
  - >-
    No ticket board redesign, direct Linear write, Symphony session UI,
    automatic recommendations, charts requiring Phase 16 metrics or remote
    administration.
test_requirements:
  - >-
    Component/query tests cover every mode/reason, policy success/conflict,
    session/security failures, stale snapshots and forbidden controls.
  - >-
    Playwright against a seeded live API covers policy revision, pause/drain,
    cross-tab stale revision, keyboard and responsive flows without external
    writes beyond the intended local policy command.
implementation_notes:
  - >-
    Reuse the generated client, query layer, mutation confirmation and design
    tokens. Never hand-maintain policy enums or calculate admission locally.
documentation_requirements:
  - "docs/atlas/operator-ui.md"
  - "docs/atlas/multi-agent-delivery-control.md"
definition_of_done:
  - >-
    All seven criteria have named component/browser evidence; UI lint/type/
    test/build, Playwright, accessibility, client drift and doc linter pass;
    the PR title carries the minted ticket key.
---

# Operator delivery-control and admission-explanation UI

Capacity becomes legible and governable without becoming automatic.
