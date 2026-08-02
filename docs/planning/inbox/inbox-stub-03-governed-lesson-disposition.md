---
title: "Governed lesson disposition service with atomic stale-state protection"
objective: >-
  Reuse one lesson-domain command service for CLI and HTTP promotion/rejection
  so a DRAFT lesson is ruled once, with operator confidence and compare-and-set
  protection against stale or concurrent decisions.
context: >-
  ATLAS-100 delivered the lesson promotion CLI and DRAFT/ACTIVE/ARCHIVED
  lifecycle; ATLAS-173 made the full lesson readable before the operator rules
  on it. Phase 13 must expose that behaviour without duplicating it in the API.
  Pre-ruled decisions: D-1 explicit promote and reject commands are the only
  v1 dispositions. Promote requires finite confidence in `0.0..1.0`; reject
  carries no editable lesson fields. D-2 only DRAFT is a valid starting state:
  promote yields ACTIVE and reject yields ARCHIVED. D-3 the repository update
  is compare-and-set on the observed DRAFT state, so a CLI/browser race has one
  winner and a typed stale-state loser. D-4 actor comes from command context
  and the service delegates idempotency/receipt atomicity to the Phase 13
  gateway. D-5 the existing CLI calls this service and preserves its public
  command/output contract. D-6 editing, merging, archiving ACTIVE lessons and
  stale re-confirmation remain deferred.
ticket_type: feature
epic_ref: ATLAS-E11
risk_level: high
component: atlas.learning
tags:
  - lessons
  - governance
  - concurrency
  - idempotency
  - learning
relevant_docs:
  - "docs/atlas/governed-operator-actions.md"
  - "docs/atlas/learning-system.md"
  - "docs/decisions/0009-single-operator-governance.md"
depends_on:
  - "ATLAS-100"
  - "ATLAS-173"
  - "inbox-stub-02-operator-action-ledger.md"
acceptance_criteria:
  - >-
    One API-independent lesson disposition service accepts a typed promote or
    reject command plus command context, loads the exact lesson once inside the
    unit of work and returns a typed updated lesson/outcome suitable for both
    CLI and HTTP presenters.
  - >-
    Promote accepts only finite confidence from 0.0 through 1.0 inclusive and
    changes DRAFT to ACTIVE while preserving lesson identity, content,
    provenance and creation metadata; invalid confidence performs no write.
  - >-
    Reject accepts no editable lesson fields and changes DRAFT to ARCHIVED
    while retaining the lesson for audit and excluding it from ACTIVE context
    retrieval.
  - >-
    Both writes use a repository compare-and-set whose predicate includes the
    observed DRAFT state. Two concurrent dispositions produce exactly one
    state change; the loser receives a typed stale-state conflict carrying a
    safe current representation and no second receipt.
  - >-
    Unknown lesson, non-DRAFT lesson, altered idempotency replay and receipt
    persistence failure are distinct typed outcomes and leave the lesson
    unchanged except for a previously completed replayed success.
  - >-
    `atlas lessons promote` and `atlas lessons reject` delegate to the shared
    service and preserve existing CLI arguments, exit behaviour and operator
    attribution; tests prove there is no second lifecycle implementation in
    CLI or API modules.
  - >-
    ACTIVE-only context retrieval includes a newly promoted lesson and excludes
    a rejected lesson, while DRAFT/ACTIVE pattern detection semantics remain
    unchanged.
non_goals:
  - >-
    No HTTP route, browser UI, lesson content edit, merge, ACTIVE archive,
    re-promotion, confidence decay, generic update command, GitHub write,
    Linear write or actor supplied by the caller's payload.
test_requirements:
  - >-
    Unit/repository tests cover boundary confidence values, NaN/infinity,
    unknown/non-DRAFT lessons, promote/reject success, CLI compatibility,
    concurrent race, replay and receipt rollback with `ATLAS_LIVE_TESTS=0`.
  - >-
    Retrieval regression tests prove the context-pack gate, and an architecture
    test rejects lesson lifecycle decisions in `atlas.api`; seeded defects use
    `assert 1 == 2` (B011).
implementation_notes:
  - >-
    Extract/refactor the existing CLI behaviour rather than wrapping the CLI
    process or parsing its output. Keep the domain transition in
    `atlas.learning` and transaction coordination in `atlas.orchestration`.
  - >-
    Do not use a read-then-unconditional-save sequence. The stale-state rule
    must be enforced at the database write boundary.
documentation_requirements:
  - "docs/atlas/learning-system.md"
  - "docs/atlas/governed-operator-actions.md"
definition_of_done:
  - >-
    All seven acceptance criteria have named tests; full Python and
    doc-linter gates are green; CLI compatibility and ACTIVE-only retrieval are
    evidenced; canonical docs land in the same change; the PR title carries
    the minted ticket key.
---

# Governed lesson disposition service with atomic stale-state protection

CLI and browser become two adapters over one human-governed lesson command.
