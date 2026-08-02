---
title: "Review queue acceptance console UI"
objective: >-
  Turn a Review Required item into a guided, exact-head acceptance panel that
  exposes each governed step and every blocking reason while leaving merge as
  a manual GitHub action.
context: >-
  ATLAS-222 delivered the read-only review queue. Phase 14 adds a focused panel
  over the generated acceptance-session client. Pre-ruled decisions: D-1 the
  queue links to one session panel showing pinned PR/head/base, close-set,
  criteria, evidence, confirmations, verification matrix, receipts and live
  readiness. D-2 only the next valid action is primary; completed steps remain
  inspectable and blocked/stale steps show all server reasons and recovery.
  D-3 the criteria confirmation form explicitly checks every server snapshot
  item and manual approval; it cannot edit/send criterion text. D-4 the UI
  never computes freshness, verdict or merge readiness and never silently
  retries a mutation. D-5 initial load and every refresh use the GET's bounded
  live-readiness result; only its current `merge_ready` displays the exact
  verified head plus instruction to merge manually in GitHub, with no merge
  button. D-6 no
  rebase/Linear/Symphony/post-merge action appears.
ticket_type: feature
epic_ref: ATLAS-E13
risk_level: high
component: operator-ui
tags:
  - operator-ui
  - acceptance
  - review-queue
  - exact-head
  - accessibility
relevant_docs:
  - "docs/atlas/review-acceptance-console.md"
  - "docs/atlas/operator-ui.md"
  - "docs/runbooks/pr-acceptance.md"
depends_on:
  - "ATLAS-219"
  - "ATLAS-221"
  - "ATLAS-222"
  - "inbox-stub-12-acceptance-workflow-api.md"
acceptance_criteria:
  - >-
    Each eligible Review Required row links to an acceptance panel that can
    create/load a session and displays repository/PR, pinned head/base SHAs and
    refs, close-set, lifecycle, timestamps and relevant action receipts from
    generated API types.
  - >-
    The panel renders the state machine in order and makes only the next
    server-permitted action primary; completed steps remain inspectable, an
    in-flight action disables conflicting controls, and no client transition
    occurs before the server response.
  - >-
    Evidence state shows trust/status/pin completeness without raw payloads.
    Confirmation renders every server-snapshot criterion as inert text with an
    explicit checkbox plus separate manual approval; request construction sends
    only indexes/fingerprint/approval.
  - >-
    Verification displays the complete canonical check matrix, explicit
    top-level verdict, verified head and every blocking reason. Initial load and
    each refresh request a new GET; the UI does not derive PASSED or merge
    readiness from individual rows or stored verification history.
  - >-
    Stale, blocked, timeout, security, replay/conflict and session-expiry
    outcomes each have a distinct accessible state. Head/main/criteria
    movement names refresh/new-session or Phase 12 rebase recovery and never
    reuses the old command key silently.
  - >-
    Only a current GET response with `merge_ready=true` displays the exact
    verified SHA and clear manual-GitHub instruction. Movement, indeterminate
    assessment or external-read failure clears that instruction and renders all
    server reasons; executable UI tests prove there is no merge, auto-merge,
    rebase, Linear status, Symphony resume, schema-upgrade or PM-sync control.
  - >-
    Keyboard order, focus management, busy/step semantics, confirmation,
    status/error announcements, long-SHA wrapping and established responsive
    viewports pass automated accessibility and visual-layout assertions.
non_goals:
  - >-
    No GitHub merge link that performs an action, embedded GitHub token,
    rebase/conflict UI, ticket status change, Changes Requested, Symphony
    resume, post-merge completion, job progress, websocket or UI framework
    redesign.
test_requirements:
  - >-
    Component/query tests cover every lifecycle and error state, generated
    request shapes, one-action-in-flight, no local readiness derivation and
    forbidden-control inventory.
  - >-
    Playwright against a seeded live API covers the successful step sequence,
    post-PASSED movement and GitHub failure before refresh, stale/new-session
    recovery, timeout/retry-key discipline, cross-tab observation, session
    expiry and keyboard/responsive flows.
implementation_notes:
  - >-
    Extend the existing Review queue/detail patterns and Phase 13 mutation
    primitive. Do not hand-maintain response types or duplicate enum metadata.
  - >-
    Render PR/ticket/criterion content as text. Do not use unsanitised HTML,
    interpolate repository data into arbitrary URLs or expose raw evidence.
documentation_requirements:
  - "docs/atlas/operator-ui.md"
definition_of_done:
  - >-
    All seven acceptance criteria have named component/browser evidence; UI
    lint/type/test/build, generated-client drift, Python gates, accessibility
    and doc linter are green; canonical UI docs land in the same change; the PR
    title carries the minted ticket key.
---

# Review queue acceptance console UI

The review queue becomes a guided cockpit without moving merge authority into
Atlas.
