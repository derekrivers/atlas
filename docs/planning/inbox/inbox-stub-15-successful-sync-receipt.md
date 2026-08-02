---
title: "Successful PM-sync receipt and truthful status timestamp"
objective: >-
  Persist the identity and completion time of each PM sync tick so the Operator
  API reports the last genuinely successful Linear observation, including
  successful no-op and status-only ticks.
context: >-
  Phase 11 carried forward that `last_linear_sync_at` is currently derived from
  ticket definition cursors. Phase 15 admission cannot make a stale/fresh
  decision from that value. Introduce the minimal durable sync receipt required
  for coherent admission and future Phase 16 measurement. A partial or failed
  tick must remain visible but cannot advance the successful timestamp.
ticket_type: infrastructure
epic_ref: ATLAS-E6
risk_level: high
component: atlas.pm
tags:
  - linear-sync
  - provenance
  - system-status
  - phase-15
relevant_docs:
  - "docs/atlas/multi-agent-delivery-control.md"
  - "docs/atlas/pm-engine-and-linear-sync.md"
  - "docs/atlas/operator-api.md"
  - "docs/atlas/operator-ui.md"
depends_on:
  - "ATLAS-46"
acceptance_criteria:
  - >-
    A durable sync receipt records start/finish times, product/project identity,
    status-map and fetched-board fingerprints, bounded result classification
    and counters without storing credentials or raw Linear payloads.
  - >-
    Successful definition-changing, status-only and zero-action ticks each
    advance `last_successful_linear_sync_at`; failed, cancelled, partial or
    malformed-pull ticks do not.
  - >-
    Receipt persistence is part of the tick's local completion boundary: a
    receipt write failure returns a typed failure and cannot report the tick as
    successful.
  - >-
    `GET /api/v1/status` and the Overview projection use the latest successful
    receipt rather than `Ticket.linear_synced_at`, preserving null before the
    first successful tick.
  - >-
    Repeated ticks use distinct receipt identities while deterministic tests
    with an injected clock produce stable fingerprints and ordering.
  - >-
    Migrations work on SQLite and PostgreSQL and upgrade from the current head
    without changing existing ticket definition cursors.
non_goals:
  - >-
    No delivery admission, capacity policy, historical performance analytics,
    event warehouse, webhook, extra Linear request or UI redesign.
test_requirements:
  - >-
    Repository and sync integration tests cover success classes, each failure
    seam, receipt-write rollback, API projection and migration parity with
    injected clocks and clients.
  - >-
    Existing PM request-budget, no-op and Operator API/UI tests remain green;
    seeded Python defects use `assert 1 == 2` (B011).
implementation_notes:
  - >-
    Keep the receipt append-only and bounded. Store hashes/fingerprints, not
    issue bodies, tokens or unbounded error responses.
documentation_requirements:
  - "docs/architecture/data-model-and-schemas.md"
  - "docs/atlas/pm-engine-and-linear-sync.md"
  - "docs/atlas/operator-api.md"
  - "docs/atlas/operator-ui.md"
  - "docs/atlas/multi-agent-delivery-control.md"
definition_of_done:
  - >-
    Named tests prove all six criteria; migrations, Python/API/UI drift gates
    and doc linter pass; canonical docs change with the code; the PR title
    carries the minted ticket key.
---

# Successful PM-sync receipt and truthful status timestamp

Freshness starts with an honest record of the last complete observation.
