---
title: Session-aware repository Linear skill
objective: Make `.codex/skills/linear` usable in both Symphony-dispatched and operator/Codex sessions by routing
  to the correct bounded mechanics surface for the current execution context while preserving one canonical lifecycle
  authority model.
context: The existing skill explicitly says to use Symphony's injected `linear_graphql` for every Linear read/write,
  which is correct inside Symphony but unusable outside it. Once the bounded operator adapter exists, the skill
  must select the available transport without duplicating lifecycle policy or granting broader mutation rights.
ticket_type: documentation
epic_ref: ATLAS-E10
risk_level: medium
component: linear-skill-routing
tags:
- maintenance
- ticket-minting
- agent-skills
- linear
- codex-skill
- routing
relevant_docs:
- AGENTS.md
- docs/runbooks/operator-environment.md
- docs/runbooks/symphony-agent-execution.md
- docs/atlas/playbooks/linear-sync.md
depends_on:
- inbox-stub-03-bounded-linear-operator-adapter.md
acceptance_criteria:
- The `linear` skill explicitly distinguishes Symphony-dispatched sessions, where it uses injected `linear_graphql`,
  from non-Symphony operator/Codex sessions, where it uses the repository-owned bounded adapter.
- Both routes present the same semantic operations needed by the skill—narrow issue read, workflow-state resolution,
  bounded comment operations and lifecycle state mutation—without claiming identical transport syntax.
- The skill continues to defer lifecycle decisions to canonical runbooks and never converts transport availability
  into permission to choose a state transition.
- PM-owned new-ticket creation/publication is explicitly excluded from both generic skill routes and handed to Atlas
  PM / the dedicated PM ticket publication skill.
- If neither supported route is available, the skill fails closed with a clear capability-unavailable handoff rather
  than recommending raw tokens, ad-hoc curl/GraphQL or manual UI mutation.
- Static repository skill tests later in this batch can falsify removal of either context route, the PM publication
  exclusion or the canonical lifecycle-authority reference.
non_goals:
- No runtime adapter implementation, PM sync behavior, ticket planning/apply behavior or Symphony lifecycle redesign.
- No generic connected-app abstraction across services other than Linear.
test_requirements:
- The later skill-contract ticket must pin the two execution-context routes and fail-closed no-transport behavior.
implementation_notes:
- Expected production change is primarily `.codex/skills/linear/SKILL.md`; keep transport-specific command examples
  bounded and policy-neutral.
- Do not duplicate the operator adapter's implementation contract or the Symphony execution runbook's lifecycle
  decisions.
documentation_requirements: []
definition_of_done:
- A fresh agent can use the repository `linear` skill in either supported session type without inventing its own
  Linear access method.
- The same mutation-authority boundaries apply regardless of transport.
---

# Session-aware repository Linear skill

Procedural follow-on to the bounded non-Symphony adapter.
