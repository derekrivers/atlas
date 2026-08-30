---
title: Bounded Linear operator adapter outside Symphony
objective: Provide a repository-owned, credential-safe Linear access surface for operator/Codex sessions that are
  not Symphony-dispatched, covering the bounded issue reads, workflow-state resolution, comments and authorised
  lifecycle mutations needed by Atlas skills without exposing arbitrary GraphQL or PM-owned issue creation.
context: The distinct ATLAS-282 follow-up identified that `.codex/skills/linear` is currently usable only when Symphony
  injects its `linear_graphql` client. Operator-side Codex sessions therefore cannot follow the same repository
  skill without an unsupported/manual tool substitution. The solution must preserve Atlas lifecycle authority and
  must not turn operator access into a generic Linear mutation backdoor.
ticket_type: feature
epic_ref: ATLAS-E10
risk_level: high
component: linear-operator-adapter
tags:
- maintenance
- ticket-minting
- agent-skills
- linear
- operator-access
- adapter
relevant_docs:
- AGENTS.md
- docs/atlas/pm-engine-and-linear-sync.md
- docs/atlas/playbooks/linear-sync.md
- docs/runbooks/operator-environment.md
- docs/runbooks/symphony-agent-execution.md
depends_on:
- inbox-stub-01-ticket-minting-lifecycle-authority.md
acceptance_criteria:
- Atlas exposes a repository-owned non-Symphony Linear adapter/CLI that loads Linear credentials inside the trusted
  process boundary and never prints, returns or requires the agent to read the raw token.
- 'The surface is capability-bounded rather than arbitrary GraphQL: it supports narrow issue readback, team workflow-state
  resolution, bounded comment read/create and the lifecycle state-update primitive needed by canonical agent/operator
  procedures, with machine-readable results and named failures.'
- The adapter explicitly excludes Linear issue creation, Atlas-owned definition push, `external_linear_id` persistence
  and PM ticket publication; those remain owned by Atlas PM.
- Mutation calls require exact issue identity and current/target state evidence defined by the owning lifecycle
  procedure; a stale or ambiguous issue/state resolution fails closed instead of guessing by title.
- No arbitrary GraphQL document, mutation-name passthrough or introspection escape hatch is exposed to the calling
  agent; adding a new operation requires an explicit repository capability.
- Focused fake-client/CLI tests prove allowed reads/comments/state mutation, stale/ambiguous-state failure, credential
  non-disclosure and rejection of PM-owned issue-creation behavior without live Linear access.
non_goals:
- No replacement for Symphony's `linear_graphql` inside dispatched sessions and no removal of that tool in this
  ticket.
- No PM publication, delivery admission, ticket planning/apply, generic GraphQL console or Phase 16 non-bypassable
  effect-gateway claim.
test_requirements:
- Focused tests use injected/in-memory Linear boundaries only and assert zero raw-token exposure.
- A negative capability test proves issue creation/arbitrary GraphQL cannot be invoked through the adapter.
implementation_notes:
- Prefer a small `atlas linear ...` operator-facing CLI/service family reusing existing `atlas.linear` DTO/client
  primitives; final command spelling should follow current CLI conventions.
- Keep semantic lifecycle authority in the canonical execution/PM runbooks; the adapter owns safe mechanics only.
documentation_requirements:
- docs/runbooks/operator-environment.md
- docs/atlas/playbooks/linear-sync.md
definition_of_done:
- A non-Symphony Codex/operator session has a supported repository-owned Linear mechanics surface without arbitrary
  GraphQL access.
- PM-owned new-ticket publication remains mechanically outside the adapter.
---

<!-- atlas-source-comment-id: 3e52c499-9aef-4e9d-a055-5e9d6b055728 -->

# Bounded Linear operator adapter outside Symphony

Source follow-up: ATLAS-282 / ATL-461. This ordered replacement retains the PM follow-up dedup identity.
