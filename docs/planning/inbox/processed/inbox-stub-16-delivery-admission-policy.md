---
title: "Versioned operator-owned delivery admission policy"
objective: >-
  Define one immutable-revision policy for working, review, rework, risk and
  component capacity so no agent or heuristic can silently widen delivery
  authority.
context: >-
  Phase 15 separates eligibility from admission. Policy changes belong to the
  operator and must use Phase 13's actor, idempotency, compare-and-set and
  receipt boundary. The initial revision preserves the current ceiling of
  three; over-capacity changes hold new work and never demote active tickets.
ticket_type: infrastructure
epic_ref: ATLAS-E6
risk_level: high
component: atlas.pm
tags:
  - admission
  - capacity
  - policy
  - governance
relevant_docs:
  - "docs/atlas/multi-agent-delivery-control.md"
  - "docs/atlas/governed-operator-actions.md"
  - "docs/atlas/pm-engine-and-linear-sync.md"
depends_on:
  - "inbox-stub-02-operator-action-ledger.md"
acceptance_criteria:
  - >-
    A validated policy revision stores mode, approved Symphony ceiling,
    working/review budgets, Changes Requested reserve and bounded risk/component
    lane rules for one product.
  - >-
    The bootstrap revision is explicit, running at ceiling/working budget three,
    and never increases the current `WORKFLOW.md` ceiling as a migration side
    effect.
  - >-
    Policy creation/update uses server-owned actor context, idempotency and an
    expected-revision compare-and-set; stale or altered replay returns conflict
    with no new revision.
  - >-
    A successful policy revision and append-only action receipt commit
    atomically; receipt/store failure leaves the prior revision authoritative.
  - >-
    Validation rejects budgets outside approved bounds, reserve above working
    budget, duplicate/ambiguous lanes and a ceiling above ten.
  - >-
    Paused and draining modes prohibit new admission but never demote a ticket,
    cancel an agent, delete a workspace or invoke Symphony.
non_goals:
  - >-
    No HTTP/UI, candidate ranking, Linear status write, automatic policy
    optimisation, model selection, multi-product allocation or agent control.
test_requirements:
  - >-
    Model/repository/service tests cover validation, revisions, races, replay,
    atomic receipts, bootstrap and pause/drain invariants on SQLite and
    PostgreSQL migrations.
  - >-
    Architecture tests preserve layer ownership and prove policy code cannot
    import or invoke Symphony; seeded defects use `assert 1 == 2` (B011).
implementation_notes:
  - >-
    Use immutable revisions plus one active pointer or deterministic latest
    lookup. Never update a historical policy row in place.
documentation_requirements:
  - "docs/architecture/data-model-and-schemas.md"
  - "docs/atlas/multi-agent-delivery-control.md"
  - "docs/atlas/pm-engine-and-linear-sync.md"
definition_of_done:
  - >-
    All six criteria have named deterministic tests; migrations, full Python
    gates and doc linter pass; canonical docs land together; the PR title
    carries the minted ticket key.
---

# Versioned operator-owned delivery admission policy

Capacity is an explicit human policy, not a side effect of available workers.
