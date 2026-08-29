---
title: Governed planning artifact publication skill
objective: Add a narrow `atlas-planning-publication` procedural skill that publishes exact planning-input candidates
  for human review and complete post-apply planning-artifact candidates after the Atlas store has advanced, without
  crossing approval, merge, PM, Linear or implementation boundaries.
context: >-
  `atlas-ticket-planning` correctly stops at a validated planning-input commit and `atlas-planning-apply`
  correctly leaves apply-owned renders and retired inbox inputs in the working tree after store mutation. Repository
  publication at both boundaries is currently manual choreography. The pre-store candidate is safely reproducible
  from committed inputs, while post-store artifacts are coupled to an already-advanced store and must never be discarded,
  partially committed or recreated by rerunning apply.
ticket_type: documentation
epic_ref: ATLAS-E10
risk_level: high
component: planning-publication-skill
tags:
- maintenance
- ticket-minting
- agent-skills
- codex-skill
- planning
- publication
- recovery
source_anchor: docs/runbooks/planning-phases-and-ticket-stubs.md#validation-and-commit-boundary
relevant_docs:
- AGENTS.md
- docs/runbooks/planning-phases-and-ticket-stubs.md
- docs/runbooks/running-atlas-plan.md
- docs/runbooks/operational-practice.md
- docs/runbooks/operator-environment.md
- docs/atlas/pm-engine-and-linear-sync.md
depends_on:
- inbox-stub-06-ticket-planning-skill-fast-path.md
- inbox-stub-08-pm-ticket-sync-skill.md
acceptance_criteria:
- A new `.codex/skills/atlas-planning-publication/SKILL.md` follows the repository skill authority contract and
  exposes two explicit modes only—committed planning-input publication before store mutation and complete apply-artifact
  publication after an approved apply has advanced the store—and requires repository-selected exact-head validation
  before either mode may push or open a PR.
- Pre-store mode requires an exact committed planning-input head, current base identity, clean working tree and
  manifest-equal changed-path set. It may reuse still-current `atlas-ticket-planning` validation evidence only when
  that evidence pins the same exact base/head and complete path set; absent, stale or different-head evidence requires
  composing `atlas-validation` before the unchanged head is pushed and opened for review.
- Post-store mode requires the exact applied PlanRun/store handoff identity and the complete apply-owned planning
  working tree, including all renders, retired stubs, manifest movement, additions and deletions; it refuses unrelated,
  missing or partial paths, commits the complete set once, then calculates and runs fresh `atlas-validation` for
  that new exact apply-artifact head before push/PR publication and stop.
- Both modes verify repository/origin, base, branch, local/remote head, candidate path scope and clean publication
  prerequisites appropriate to that mode, execute every command selected by the deterministic plan without hand-selection
  or narrowing, then report the exact validated/published head and PR identity, stop, and name `atlas-pm-ticket-sync`
  only as the later continuation after human review and merge of the apply-artifact PR.
- Post-store interruption and recovery remain bound to the existing advanced store and preserved working tree;
  the skill never discards or partially commits apply artifacts, reconstructs them by hand or reruns `atlas apply`.
- The skill never merges, infers PlanRun or PR approval, runs plan/apply, PM sync or Linear mutation, starts ticket
  implementation, or substitutes publication for any human/operator gate.
- Static skill-contract and fake Git/GitHub tests prove both modes, exact-path and identity refusal, complete post-apply
  staging, no force/bare push, no merge and every prohibited cross-boundary action without external publication.
non_goals:
- No plan generation, apply execution, key minting, store mutation, PM/Linear publication, delivery admission,
  ticket implementation, semantic PR review or merge.
- No generic GitHub/PR skill and no recovery by resetting, deleting, stashing or regenerating apply-owned artifacts.
test_requirements:
- Repository skill-contract tests pin both publication modes, authority references, stop conditions and forbidden
  downstream operations, plus exact-head `atlas-validation` composition and current-evidence reuse rules.
- Focused fake-command tests cover clean pre-store publication, complete dirty post-store staging/commit publication,
  current/stale/missing validation evidence, local/remote/base races, unrelated or missing paths, interrupted retry
  and exact validated/published-head readback.
implementation_notes:
- Expected production path is `.codex/skills/atlas-planning-publication/SKILL.md`; keep planning/apply policy in canonical
  documents and publication procedure in the skill.
- Use explicit expected remote identities and ordinary non-force publication. The post-store mode may create the
  one complete apply-artifact commit but must not rewrite, split or selectively stage the apply-owned tree. Never
  hand-select, narrow or augment the repository-selected validation plan; obey any conservative fallback.
documentation_requirements: []
definition_of_done:
- A validated planning-input candidate and a complete post-apply planning-artifact candidate each have a governed,
  resumable repository-publication path with exact identity and changed-scope evidence.
- The post-store path makes the non-disposable store/tree coupling explicit and mechanically rejects partial or
  recreated publication.
---

# Governed planning artifact publication skill

Governed maintenance input for the `ticket-minting-skills-v1` batch.
