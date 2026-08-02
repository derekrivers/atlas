---
title: "Append-only operator action ledger and idempotent command gateway"
objective: >-
  Give every current and future Operator API write one transactional,
  server-attributed idempotency and audit boundary so retries cannot duplicate
  mutations and an unaudited mutation cannot be reported as success.
context: >-
  Phase 13's lesson disposition and Phase 14's acceptance workflow need the
  same command envelope. Implementing replay and audit independently in each
  route would create a drift-prone security boundary. Pre-ruled decisions:
  D-1 one application-layer gateway accepts server-resolved actor context,
  action name, target identity, idempotency key and a canonical request
  fingerprint; routes do not implement replay policy. D-2 idempotency keys are
  unique within the operator action namespace. Same key/same fingerprint
  returns the first stored terminal outcome without rerunning the command;
  same key/different fingerprint returns conflict. D-3 an append-only
  `OperatorActionReceipt` stores bounded non-secret metadata, actor, target,
  before/after state where applicable, outcome and timestamps; it never copies
  credentials, raw request bodies, evidence payloads or lesson content. D-4 a
  successful domain mutation and receipt commit atomically; receipt failure
  rolls back the mutation. D-5 authenticated refusals/conflicts may record a
  bounded outcome but can never masquerade as mutation success. D-6 this is a
  generic internal foundation with deterministic fakes; it exposes no HTTP
  resource action by itself.
ticket_type: infrastructure
epic_ref: ATLAS-E12
risk_level: high
component: orchestration
tags:
  - idempotency
  - audit
  - operator
  - orchestration
  - storage
relevant_docs:
  - "docs/atlas/governed-operator-actions.md"
  - "docs/architecture/data-model-and-schemas.md"
  - "docs/decisions/0006-source-of-truth-hierarchy.md"
  - "docs/decisions/0009-single-operator-governance.md"
depends_on:
  - "ATLAS-187"
acceptance_criteria:
  - >-
    A migration and canonical model add an append-only operator-action record
    with stable receipt/correlation IDs, action, target type/ID,
    server-resolved actor, idempotency key identity, canonical request
    fingerprint, bounded outcome, optional before/after status and
    created/completed timestamps; database and repository guards reject update
    and delete.
  - >-
    The gateway reserves an idempotency key atomically. Same key and same
    fingerprint replays the original terminal response without invoking the
    injected command; the same key with a different fingerprint returns a
    typed conflict and invokes no command.
  - >-
    Canonical fingerprints are deterministic across JSON key order and reject
    unsupported/non-finite values; they cover action, target and the complete
    validated command payload so two semantically different commands cannot
    collide through omitted fields.
  - >-
    A successful injected domain mutation and its success receipt commit in
    one database transaction. A seeded receipt insert/commit failure leaves
    domain state unchanged, returns a typed failure and leaves no replayable
    success.
  - >-
    Concurrent calls using one key are serialised by a database uniqueness or
    equivalent transaction boundary: exactly one command invocation occurs
    and every caller observes the same terminal outcome or a named in-progress
    conflict; no polling loop is unbounded.
  - >-
    Receipt presentation excludes token/session/CSRF values, full request
    bodies, raw evidence, lesson content and exception traces; secret-shaped
    fixture values do not appear in persisted fields, logs or rendered JSON.
  - >-
    The gateway and repository are usable without FastAPI imports and have
    injected clock/ID/command seams, so lesson disposition and acceptance
    sessions can reuse them without depending on `atlas.api`.
non_goals:
  - >-
    No session authentication, HTTP routes, resource-specific state machine,
    distributed lock service, message queue, global exactly-once claim across
    external systems, receipt mutation/deletion UI, GitHub write, Linear write
    or merge.
test_requirements:
  - >-
    Repository and transaction tests cover append-only enforcement, replay,
    altered replay, concurrent duplicate calls, in-progress recovery,
    mutation failure, receipt failure and secret redaction on SQLite and the
    existing supported store matrix with `ATLAS_LIVE_TESTS=0`.
  - >-
    Property/table tests cover canonical fingerprint stability and
    differentiation; architecture tests reject FastAPI imports below the API
    layer; seeded defects use `assert 1 == 2` (B011).
implementation_notes:
  - >-
    Reuse the repository unit-of-work/transaction boundary. Do not commit the
    resource mutation in one service and attempt the receipt afterward.
  - >-
    Model terminal success, terminal refusal and in-progress ownership
    explicitly. Recovery must never infer that a missing receipt means a
    previously committed mutation is safe to repeat.
documentation_requirements:
  - "docs/architecture/data-model-and-schemas.md"
  - "docs/atlas/governed-operator-actions.md"
definition_of_done:
  - >-
    All seven acceptance criteria are evidenced by named tests; migration
    upgrade and downgrade tests, full Python gates and doc linter are green;
    canonical schema docs land in the same change; the PR title carries the
    minted ticket key.
---

# Append-only operator action ledger and idempotent command gateway

One reusable transaction boundary makes browser commands replay-safe and
auditable.
