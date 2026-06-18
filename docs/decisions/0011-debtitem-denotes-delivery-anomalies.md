# ADR-0011: DebtItem denotes delivery anomalies; code-quality debt register deferred

## Status

Accepted

## Context

`data-model-and-schemas.md` §6 originally defined `DebtItem` / the
`debt_items` table as a code-quality **technical-debt register**: a
`DebtCategory` taxonomy (`test_coverage`, `duplication`, `large_file`,
`stale_docs`, `security`, `performance`, `architecture`, `dependency`), a
mutable row carrying `severity`, `detected_by`, a `status` lifecycle, a
`remediation_ticket_id`, and an `updated_at`.

ATLAS-116 (Phase 4, PM Engine) needed an append-only delivery-anomaly
record — one row per observed out-of-ownership transition, written by the
system from deterministic observation, with recurrence computed at query
time (`pm-engine-and-linear-sync.md`, "Anomaly and dwell detection"). It
landed that record under the same name and table, `DebtItem` /
`debt_items`, and rewrote §6 accordingly. Two different concepts had
collided on one name; only one can hold it. This ADR records which one
won and what becomes of the other, so the resolution is governed rather
than implicit in a model diff.

## Decision

- **D1 — `DebtItem` denotes the delivery-anomaly record.** As of
  ATLAS-116, `DebtItem` and the `debt_items` table denote the append-only
  delivery-anomaly record: one row per observation, system-written
  (`created_by_type = system`), with recurrence a query-time predicate
  (`DebtItemRepo.recurring(ticket_id, anomaly_type, threshold=3)`), never a
  stored counter and never a creation gate. The prior §6 code-quality
  technical-debt register — the `DebtCategory` taxonomy (`test_coverage`,
  `duplication`, `large_file`, `stale_docs`, `security`, `performance`,
  `architecture`, `dependency`), mutable, `status`-lifecycled, carrying
  `remediation_ticket_id` — is removed from the canonical model.

- **D2 — the code-quality register returns as a distinct entity, later.**
  The code-quality technical-debt register is reintroduced as a **distinct
  entity — not named `DebtItem`, not the table `debt_items`** — when the
  first steal-list sensor ships: mutation/coverage, a duplication or
  large-file linter, KB-freshness / doc-gardening, or architecture fitness
  (import-linter / dependency-cruiser). That entity is a **named
  precondition** of the first such sensor — the sensor that produces
  code-quality debt defines the entity that stores it. Until then Atlas has
  no code-quality-debt entity. Tracked as ATLAS-117.

## Rationale

The name and table already shipped to the delivery-anomaly meaning
(ATLAS-116: model, migration `0008`, repository, generated schema), so the
delivery anomaly holds the name by possession; renaming a shipped table to
free `DebtItem` for a register that has no writer yet is churn for no gain.
Reintroducing the register only when a sensor writes to it is YAGNI applied
honestly: an entity with no producer is dead schema, and deferring it keeps
one unambiguous meaning per name (a `DebtItem` is always a delivery
anomaly) instead of overloading the term again. Tying the register to its
first sensor (D2) makes its return a governed precondition rather than a
silent re-collision.

## Consequences

- `atlas-master-plan.md`'s "technical debt is actively managed" (§14
  "Technical Debt Steward", line ~342; foundational principle 9, line ~480;
  and the capability bullets at ~47, ~82, ~160, ~332) is a **deferred
  capability, not a current one**, until D2 lands. Atlas today records
  delivery anomalies, not code-quality debt; the steward capability waits
  on the first steal-list sensor.
- `learning-system.md` (Phase 9) "pattern detection over the same
  `DebtItem` category recurring across tickets" must re-point at the
  reintroduced code-quality register, **not** at the delivery anomaly's
  `anomaly_type`. This is a re-target to a different entity, not a
  `category` → `anomaly_type` rename: anomaly recurrence and code-quality
  debt patterns are distinct signals.
- The `DebtCategory` taxonomy is no longer canonical; any future register
  defines its own taxonomy when it ships under D2.

## Alternatives considered

- **Rename the anomaly log to `DeliveryAnomaly` and keep `DebtItem` as the
  code-quality register.** Rejected: the `DebtItem` name and `debt_items`
  table already shipped to the delivery-anomaly meaning under ATLAS-116
  (model, migration, repository, generated schema). Renaming the shipped
  artifact to reserve the name for an entity with no current writer is pure
  churn.
- **Keep both entities now** (delivery anomaly under one name, code-quality
  register under another, both defined immediately). Rejected: the
  code-quality register has no current writer — no sensor produces its rows
  yet — so it would be dead schema. YAGNI: define it when D2's first sensor
  needs it.
